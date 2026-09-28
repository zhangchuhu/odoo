import base64
import hashlib
import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode
import requests
from markupsafe import escape
from odoo import fields, http
from odoo.http import request
from odoo.exceptions import ValidationError
from odoo.addons.meli_oerp.controllers.main import MercadoLibreLogin

KEY = 'meli_independent_oauth'
GROUP = 'meli_oerp.group_mercadolibre_manager'

def reply(message, status=400):
    return request.make_response(str(escape(message)), status=status, headers=[('Content-Type', 'text/plain; charset=utf-8'), ('Cache-Control', 'no-store')])

def store_for_user(store_id):
    if not request.env.user.has_group(GROUP):
        return None
    return request.env['meli.independent.store'].search([('id', '=', store_id)], limit=1)

class StoreAuthorization(MercadoLibreLogin):
    @http.route('/meli/stores/<int:store_id>/authorize', type='http', auth='user', methods=['GET'])
    def start(self, store_id, **kw):
        store = store_for_user(store_id)
        if not store:
            return request.not_found()
        company = store.company_id.sudo()
        if not company.mercadolibre_client_id or not company.mercadolibre_secret_key:
            return reply('The Mercado Libre application credentials are missing.')
        state = 'store2_' + secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        redirect_uri = company.mercadolibre_redirect_uri
        request.session[KEY] = {'store': store.id, 'uid': request.env.uid, 'state': state,
                                'verifier': verifier, 'created': time.time(), 'redirect_uri': redirect_uri}
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        url = 'https://global-selling.mercadolibre.com/authorization?' + urlencode({
            'client_id': company.mercadolibre_client_id, 'response_type': 'code',
            'redirect_uri': redirect_uri, 'state': state, 'code_challenge': challenge,
            'code_challenge_method': 'S256'})
        return request.redirect(url, local=False)

    @http.route(['/meli_login'], type='http', auth='user', methods=['GET'], website=True)
    def index(self, **codes):
        pending = request.session.get(KEY)
        state = codes.get('state', '')
        if not pending and not state.startswith('store2_'):
            return super().index(**codes)
        if (not pending or pending['uid'] != request.env.uid or
                time.time() - pending['created'] > 900 or
                not secrets.compare_digest(state, pending['state'])):
            return reply('Authorization session invalid or expired. Reopen the second store authorization link.')
        request.session.pop(KEY, None)
        store = store_for_user(pending['store'])
        if not store:
            return request.not_found()
        if codes.get('error') or not codes.get('code'):
            return reply('Authorization was cancelled or denied. Please retry from the store page.')
        company = store.company_id.sudo()
        try:
            response = requests.post('https://api.mercadolibre.com/oauth/token', data={
                'grant_type': 'authorization_code', 'client_id': company.mercadolibre_client_id,
                'client_secret': company.mercadolibre_secret_key, 'code': codes['code'],
                'redirect_uri': pending['redirect_uri'], 'code_verifier': pending['verifier']}, timeout=30)
            response.raise_for_status()
            token = response.json()
            seller = str(token.get('user_id') or '')
            store._check_seller(seller)
            if not token.get('access_token') or not token.get('refresh_token'):
                return reply('Authorization returned incomplete credentials. Please retry.')
            response = requests.get('https://api.mercadolibre.com/users/me', headers={
                'Authorization': 'Bearer ' + token['access_token']}, timeout=30)
            response.raise_for_status()
            profile = response.json()
            if str(profile.get('id')) != seller or profile.get('site_id') != 'CBT':
                return reply('Please authorize a Global Selling account matching this store.')
            store.sudo().write({'seller_id': seller, 'nickname': profile.get('nickname'),
                'access_token': token['access_token'], 'refresh_token': token['refresh_token'],
                'authorized_at': fields.Datetime.now(),
                'expires_at': fields.Datetime.now() + timedelta(seconds=int(token.get('expires_in', 21600))),
                'state': 'connected'})
        except ValidationError as error:
            return reply(str(error))
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return reply('Mercado Libre authorization could not be completed. Please retry from the store page.', 502)
        return request.redirect('/odoo/meli.independent.store/%s' % store.id)

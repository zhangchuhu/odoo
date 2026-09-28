"""Persist rotating OAuth credentials without committing the caller's work."""
from datetime import timedelta

import requests
from psycopg2.errors import LockNotAvailable, SerializationFailure

from odoo import api, fields, SUPERUSER_ID
from odoo.exceptions import AccessError, UserError

API = 'https://api.mercadolibre.com'


def credential_token(store):
    store.ensure_one()
    store.check_access('read')
    # Retry only a database conflict occurring BEFORE any OAuth request.
    for attempt in range(3):
        sent = False
        try:
            with store.env.registry.cursor() as cr:
                cr.execute("SET LOCAL lock_timeout = '2s'")
                env = api.Environment(cr, store.env.uid, dict(store.env.context), su=store.env.su)
                if not env.user.active and not (env.su and env.uid == SUPERUSER_ID):
                    raise AccessError('Inactive user')
                current = env['meli.independent.store'].browse(store.id).exists()
                if not current:
                    raise UserError('店铺尚未保存，请保存后重试。')
                current.check_access('read')
                if not env.su and current.company_id not in env.companies:
                    raise AccessError('Shop is outside the allowed companies')
                cr.execute('SELECT id FROM meli_independent_store WHERE id=%s FOR UPDATE', [store.id])
                current = current.sudo()
                current.invalidate_recordset()
                if current.state != 'connected':
                    raise UserError('请先授权目标店铺。')
                if current.access_token and current.expires_at and current.expires_at > fields.Datetime.now() + timedelta(minutes=2):
                    return current.access_token
                if not current.refresh_token:
                    raise UserError('店铺授权已失效，请重新授权此店铺。')
                company = current.company_id
                try:
                    with requests.Session() as session:
                        session.trust_env = False
                        sent = True
                        response = session.post(API + '/oauth/token', data={
                            'grant_type': 'refresh_token',
                            'client_id': company.mercadolibre_client_id,
                            'client_secret': company.mercadolibre_secret_key,
                            'refresh_token': current.refresh_token,
                        }, timeout=30, allow_redirects=False)
                        try:
                            data = response.json()
                            if not 200 <= response.status_code < 300 or not isinstance(data, dict):
                                raise ValueError()
                            expiry = data.get('expires_in')
                            if isinstance(expiry, bool) or not isinstance(expiry, int) or not 0 < expiry <= 31536000:
                                raise ValueError()
                            if any(not isinstance(data.get(k), str) or not data[k] for k in ('access_token', 'refresh_token')):
                                raise ValueError()
                            if str(data.get('user_id')) != current.seller_id:
                                raise ValueError()
                        finally:
                            response.close()
                except (requests.RequestException, ValueError):
                    raise UserError('店铺授权刷新失败，请重新授权或稍后重试。') from None
                current.write({
                    'access_token': data['access_token'], 'refresh_token': data['refresh_token'],
                    'expires_at': fields.Datetime.now() + timedelta(seconds=expiry),
                })
                cr.commit()
                return data['access_token']
        except LockNotAvailable:
            raise UserError('店铺正在更新，请在当前操作完成后重试。') from None
        except SerializationFailure:
            if sent or attempt == 2:
                raise UserError('店铺授权状态发生变化，请重新检查授权。') from None

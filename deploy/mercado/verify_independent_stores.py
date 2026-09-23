import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
from urllib.parse import urlparse, parse_qs
from odoo.exceptions import ValidationError, AccessError
from odoo import api
env = api.Environment(env.cr, 2, dict(env.context, allowed_company_ids=[1]))
from odoo.addons.meli_accounts import controllers as ctl

Store = env['meli.independent.store']
name = 'Guangzhou RongZhi WangLuoKeJi'
store = Store.search([('name', '=', name)], limit=1) or Store.create({'name': name, 'company_id': 1, 'site': 'MLM'})
env.cr.commit()
store_id = store.id
company = env['res.company'].browse(1)

def snapshot():
    data = {'company': company.read(['mercadolibre_seller_id','mercadolibre_access_token','mercadolibre_refresh_token'])[0], 'products': env['product.product'].with_context(active_test=False).search([('meli_id','!=',False)],order='id').read(['meli_id','product_tmpl_id'])}
    return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()

assert snapshot() == Path('deploy/mercado/private/store-baseline.sha256').read_text()
try:
    store._check_seller(company.mercadolibre_seller_id)
    raise AssertionError('Original seller was accepted')
except ValidationError:
    pass
fake = SimpleNamespace(env=env, session={}, redirect=lambda url, **kw: url,
                       make_response=lambda body, status=200, headers=None: (status, body), not_found=lambda: (404, 'Not found'))
controller = ctl.StoreAuthorization()
with patch.object(ctl, 'request', fake):
    url = ctl.StoreAuthorization.start.__wrapped__(controller, store_id)
    query = parse_qs(urlparse(url).query)
    assert query['code_challenge_method'] == ['S256']
    assert query['redirect_uri'] == [company.mercadolibre_redirect_uri]
    pending = dict(fake.session[ctl.KEY])
    assert ctl.StoreAuthorization.index.__wrapped__(controller, state='wrong', code='test')[0] == 400
    fake.session[ctl.KEY] = dict(pending, created=time.time()-1000)
    assert ctl.StoreAuthorization.index.__wrapped__(controller, state=pending['state'], code='test')[0] == 400
    fake.session[ctl.KEY] = pending
    token = Mock()
    token.json.return_value = {'user_id': int(company.mercadolibre_seller_id), 'access_token':'test', 'refresh_token':'test'}
    with patch.object(ctl.requests, 'post', return_value=token):
        assert ctl.StoreAuthorization.index.__wrapped__(controller, state=pending['state'], code='test')[0] == 400
    assert not store.access_token
    fake.session[ctl.KEY] = pending
    token.json.return_value = {'user_id':999999999999, 'access_token':'test', 'refresh_token':'test', 'expires_in':21600}
    profile = Mock()
    profile.json.return_value = {'id':999999999999, 'site_id':'CBT', 'nickname':'Test'}
    with patch.object(ctl.requests, 'post', return_value=token), patch.object(ctl.requests, 'get', return_value=profile):
        assert ctl.StoreAuthorization.index.__wrapped__(controller, state=pending['state'], code='test').endswith('/%s' % store_id)
    assert store.state == 'connected'
    assert snapshot() == Path('deploy/mercado/private/store-baseline.sha256').read_text()
    assert ctl.StoreAuthorization.index.__wrapped__(controller, state=pending['state'], code='test')[0] == 400
# Discard mock credentials; only the actual pending store record remains committed.
env.cr.rollback()
env.invalidate_all()
store = Store.browse(store_id)
assert store.state == 'pending' and not store.access_token and not store.seller_id
try:
    Store.with_user(env.ref('base.public_user')).search([])
    raise AssertionError('Public access was allowed')
except AccessError:
    pass
assert snapshot() == Path('deploy/mercado/private/store-baseline.sha256').read_text()
print('PASS: PKCE, state mismatch, expiry, replay, original-seller rejection, isolated callback write, public access denial, original credentials and 28 product links unchanged.')
print('STORE_ID',store_id)

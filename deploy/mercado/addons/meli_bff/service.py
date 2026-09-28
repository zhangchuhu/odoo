"""Authorize shop and resource identities before acquiring credentials."""
import math

from odoo import fields
from odoo.exceptions import AccessError

from . import client
from .protocol import BffError, prepare_call, validate_query


def authorize(env, operation):
    group = 'meli_bff.group_writer' if operation.permission == 'write' else 'meli_bff.group_reader'
    if not env.user.active or not env.user.has_group(group):
        raise BffError(403, 'Insufficient BFF permission')
    if operation.store_id is None:
        return None
    store = env['meli.independent.store'].browse(operation.store_id)
    try:
        if not store.exists():
            raise BffError(403, 'Shop is not accessible')
        store.check_access('read')
        if store.company_id not in env.companies:
            raise BffError(403, 'Shop is not accessible')
    except AccessError:
        raise BffError(403, 'Shop is not accessible') from None
    return store


def shop_status(store):
    return {
        'id': store.id, 'name': store.name, 'company_id': store.company_id.id,
        'site_id': store.site, 'logistic_type': store.logistic_type,
        'seller_id': store.seller_id, 'marketplace_seller_id': store.marketplace_seller_id,
        'state': store.state, 'expires_at': fields.Datetime.to_string(store.expires_at) or None,
        'authorization_url': '/meli/stores/%s/authorize' % store.id,
    }


def execute(env, operation, pairs, body, content_type=None, accept=None, encoded_query=None):
    store = authorize(env, operation)
    validate_query(operation, pairs)
    if operation.name == 'shops':
        stores = env['meli.independent.store'].search([('company_id', 'in', env.companies.ids)])
        return {'shops': [shop_status(s) for s in stores]}
    if operation.name == 'shop':
        return shop_status(store)
    call = prepare_call(operation, store, pairs, body)
    if operation.permission == 'write' and operation.name != 'pictures':
        if (content_type or '').split(';')[0].strip().lower() != 'application/json':
            raise BffError(415, 'JSON content type is required')
    token = store._token()
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120

    def metadata(path):
        result = client.send('GET', path, token=token, timeout=timeout)
        if not 200 <= result.status < 300:
            return result, None
        return None, result.json()

    if operation.item_id:
        response, item = metadata('/items/' + operation.item_id)
        if response:
            return response
        sellers = {s for s in (store.seller_id, store.marketplace_seller_id) if s}
        if str(item.get('seller_id')) not in sellers:
            raise BffError(403, 'Item does not belong to this shop')
    if operation.name == 'items' and operation.method == 'POST':
        response, user = metadata('/users/me')
        if response:
            return response
        if str(user.get('id')) != store.seller_id:
            raise BffError(403, 'Authorized seller does not match this shop')
        tags = user.get('tags', [])
        if not isinstance(tags, list):
            raise BffError(502, 'Invalid Mercado Libre metadata response')
        if 'user_products_seller' in tags or 'user_product_seller' in tags:
            raise BffError(409, 'User Products creation is not supported by this endpoint')
    wire_query = call.query if encoded_query is None or operation.name == 'description' else encoded_query
    return client.send(call.method, call.path, token=token, query=wire_query, body=call.body,
                       content_type=call.content_type or content_type, accept=accept, timeout=timeout)

"""Authorize shop and resource identities before acquiring credentials."""
import math
import io
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from odoo import fields
from odoo.exceptions import AccessError

from odoo.addons.meli_platform_adapter import transport as client
from .protocol import BffError, Operation, prepare_call, validate_query


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
        item_response = client.send('GET', '/items/' + operation.item_id, token=token, timeout=timeout)
        if not 200 <= item_response.status < 300:
            return item_response
        try:
            item = item_response.peek_json()
            sellers = {str(s) for s in (store.seller_id, store.marketplace_seller_id) if s}
            if item.get('id') != operation.item_id or str(item.get('seller_id')) not in sellers:
                raise BffError(403, 'Item does not belong to this shop')
            if operation.name == 'item' and operation.method == 'GET':
                return item_response
        except Exception:
            item_response.close()
            raise
        item_response.close()
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


def item_batch_read(env, store, item_ids, accept=None):
    """Authorize every global item and revalidate the complete batch snapshot."""
    for item_id in item_ids:
        item = execute(env, Operation('item', 'GET', store.id, item_id), [], b'')
        if not 200 <= item.status < 300:
            return item
        item.close()
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    result = client.send('GET', '/items', token=store._token(), query=[('ids', ','.join(item_ids))],
                         timeout=timeout, accept=accept)
    if not 200 <= result.status < 300:
        return result
    try:
        items = result.peek_json_array()
        sellers = {str(s) for s in (store.seller_id, store.marketplace_seller_id) if s}
        seen = set()
        for entry in items:
            body = entry.get('body') if isinstance(entry, dict) else None
            if (not isinstance(body, dict) or entry.get('code') != 200 or body.get('id') not in item_ids
                    or body.get('id') in seen or str(body.get('seller_id')) not in sellers):
                raise BffError(403, 'Batch item ownership cannot be established')
            seen.add(body['id'])
        if seen != set(item_ids):
            raise BffError(403, 'Batch item ownership cannot be established')
        return result
    except Exception:
        result.close()
        raise


def item_mappings(env, store, item_id):
    """Filter global-to-marketplace mappings to verified items in this shop."""
    source = execute(env, Operation('item', 'GET', store.id, item_id), [], b'')
    if not 200 <= source.status < 300:
        return source
    source.close()
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    result = client.send('GET', '/items/' + item_id + '/marketplace_items', token=store._token(), timeout=timeout)
    if not 200 <= result.status < 300:
        return result
    data = result.json()
    mappings = data.get('marketplace_items')
    if data.get('item_id') != item_id or not isinstance(mappings, list) or len(mappings) > 100:
        raise BffError(403, 'Marketplace mappings cannot be established')
    filtered = []
    for mapping in mappings:
        if not isinstance(mapping, dict):
            raise BffError(403, 'Marketplace mappings cannot be established')
        if mapping.get('site_id') != store.site:
            continue
        local_id = mapping.get('item_id')
        if not isinstance(local_id, str) or not re.fullmatch(r'[A-Z]{3}[0-9]{1,37}', local_id):
            raise BffError(403, 'Marketplace mappings cannot be established')
        local = marketplace_item_read(env, store, local_id)
        if not 200 <= local.status < 300:
            return local
        local.close()
        filtered.append({'item_id': local_id, 'site_id': store.site})
    body = json.dumps({'item_id': item_id, 'marketplace_items': filtered}).encode('utf-8')
    return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(body))


def marketplace_item_read(env, store, item_id, accept=None):
    """Return the exact marketplace item snapshot only for the bound seller/site."""
    seller = str(store.marketplace_seller_id or '')
    if not item_id.startswith(store.site or '') or not store.site or not re.fullmatch(r'[1-9][0-9]{0,19}', seller):
        raise BffError(403, 'Marketplace item does not belong to this shop')
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    result = client.send('GET', '/marketplace/items/' + item_id, token=store._token(), timeout=timeout, accept=accept)
    if not 200 <= result.status < 300:
        return result
    try:
        item = result.peek_json()
        if item.get('id') != item_id or item.get('site_id') != store.site or str(item.get('seller_id')) != seller:
            raise BffError(403, 'Marketplace item does not belong to this shop')
        return result
    except Exception:
        result.close()
        raise


def fulfillment_stock_batch(env, store, arguments, accept=None):
    """Validate all Full inventory/item pairs before a bounded multi-inventory search."""
    try:
        start = date.fromisoformat(arguments['date_from'])
        end = date.fromisoformat(arguments['date_to'])
        if not 0 <= (end - start).days <= 60:
            raise ValueError()
    except ValueError:
        raise BffError(400, 'Invalid inventory date range') from None
    seller = str(store.marketplace_seller_id or '')
    if store.site not in ('MLM', 'MLC') or store.logistic_type != 'fulfillment' or not re.fullmatch(r'[1-9][0-9]{0,19}', seller):
        raise BffError(403, 'Fulfillment shop is not supported')
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    token = store._token()
    ids = []
    for entry in arguments['items']:
        item_id, inventory_id = entry['item_id'], entry['inventory_id']
        if not item_id.startswith(store.site) or inventory_id in ids:
            raise BffError(403, 'Inventory ownership cannot be established')
        item_response = client.send('GET', '/marketplace/items/' + item_id, token=token, timeout=timeout)
        if not 200 <= item_response.status < 300:
            return item_response
        item = item_response.json()
        variations, shipping = item.get('variations', []), item.get('shipping')
        if (item.get('id') != item_id or item.get('site_id') != store.site or str(item.get('seller_id')) != seller
                or not isinstance(shipping, dict) or shipping.get('logistic_type') != 'fulfillment' or not isinstance(variations, list)):
            raise BffError(403, 'Fulfillment item ownership cannot be established')
        valid = item.get('inventory_id') == inventory_id or any(isinstance(v, dict) and v.get('inventory_id') == inventory_id for v in variations)
        if not valid:
            raise BffError(403, 'Inventory does not belong to this item')
        ids.append(inventory_id)
    query = [('seller_id', seller), ('inventory_id', ','.join(ids)), ('date_from', arguments['date_from']), ('date_to', arguments['date_to'])]
    query += [(key, str(arguments[key])) for key in ('limit', 'scroll', 'type') if key in arguments]
    if 'shipment_id' in arguments:
        query.append(('external_references.shipment_id', arguments['shipment_id']))
    result = client.send('GET', '/marketplace/stock/fulfillment/operations/search', token=token,
                         query=query, timeout=timeout, accept=accept)
    if not 200 <= result.status < 300:
        return result
    try:
        rows = result.peek_json().get('results')
        if not isinstance(rows, list) or any(not isinstance(row, dict) or str(row.get('seller_id')) != seller
                                              or row.get('inventory_id') not in ids for row in rows):
            raise BffError(403, 'Inventory operations do not match the items')
        return result
    except Exception:
        result.close()
        raise


def fulfillment_stock(env, store, operation_id, arguments, accept=None):
    """Bind inventory to the authorized marketplace item or one of its variations."""
    item_id, inventory_id = arguments['item_id'], arguments['inventory_id']
    if operation_id == 'stock.fulfillment_operations':
        try:
            date_from = date.fromisoformat(arguments['date_from'])
            date_to = date.fromisoformat(arguments['date_to'])
            if not 0 <= (date_to - date_from).days <= 60:
                raise ValueError()
        except ValueError:
            raise BffError(400, 'Invalid inventory date range') from None
    seller = str(store.marketplace_seller_id or '')
    if (store.site not in ('MLM', 'MLC') or store.logistic_type != 'fulfillment'
            or not item_id.startswith(store.site) or not re.fullmatch(r'[1-9][0-9]{0,19}', seller)):
        raise BffError(403, 'Fulfillment item or shop is not supported')
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    token = store._token()
    result = client.send('GET', '/marketplace/items/' + item_id, token=token, timeout=timeout)
    if not 200 <= result.status < 300:
        return result
    item = result.json()
    variations = item.get('variations', [])
    shipping = item.get('shipping')
    if (item.get('id') != item_id or item.get('site_id') != store.site or str(item.get('seller_id')) != seller
            or not isinstance(shipping, dict) or shipping.get('logistic_type') != 'fulfillment'
            or not isinstance(variations, list)):
        raise BffError(403, 'Fulfillment item ownership cannot be established')
    inventories = {item.get('inventory_id')}
    inventories.update(v.get('inventory_id') for v in variations if isinstance(v, dict))
    if inventory_id not in inventories:
        raise BffError(403, 'Inventory does not belong to this item')
    query = [('seller_id', seller)]
    if operation_id == 'stock.fulfillment_operations':
        path = '/marketplace/stock/fulfillment/operations/search'
        query += [('inventory_id', inventory_id), ('date_from', arguments['date_from']), ('date_to', arguments['date_to'])]
        query += [(key, str(arguments[key])) for key in ('limit', 'scroll', 'type') if key in arguments]
        if 'shipment_id' in arguments:
            query.append(('external_references.shipment_id', arguments['shipment_id']))
    elif operation_id == 'stock.fulfillment_operation_read':
        path = '/marketplace/stock/fulfillment/operations/' + arguments['operation_id']
    else:
        path = '/marketplace/inventories/' + inventory_id + '/stock/fulfillment'
        if arguments.get('include_conditions'):
            query.append(('include_attributes', 'conditions'))
    response = client.send('GET', path,
                           token=token, query=query, timeout=timeout, accept=accept)
    if not 200 <= response.status < 300:
        return response
    try:
        data = response.peek_json()
        if operation_id == 'stock.fulfillment_operations':
            results = data.get('results')
            if not isinstance(results, list) or any(not isinstance(row, dict) or str(row.get('seller_id')) != seller
                                                    or row.get('inventory_id') != inventory_id for row in results):
                raise BffError(403, 'Inventory operations do not match the item')
        elif operation_id == 'stock.fulfillment_operation_read':
            if (str(data.get('id')) != arguments['operation_id'] or str(data.get('seller_id')) != seller
                    or data.get('inventory_id') != inventory_id):
                raise BffError(403, 'Inventory operation does not match the item')
        elif data.get('inventory_id') != inventory_id:
            raise BffError(403, 'Inventory response does not match the item')
        return response
    except Exception:
        response.close()
        raise


def seller_profile(env, store, operation_id):
    """Bind seller identity and never return other shops' marketplace mappings."""
    seller = str(store.seller_id or '')
    if not re.fullmatch(r'[1-9][0-9]{0,19}', seller):
        raise BffError(403, 'Shop seller is not configured')
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    path = '/users/me' if operation_id == 'users.self' else '/marketplace/users/' + seller
    result = client.send('GET', path, token=store._token(), timeout=timeout)
    if not 200 <= result.status < 300:
        return result
    try:
        data = result.peek_json()
        if str(data.get('id' if operation_id == 'users.self' else 'user_id')) != seller:
            raise BffError(403, 'Seller identity does not match the shop')
        if operation_id == 'users.self':
            return result
        mappings = data.get('marketplaces')
        if not isinstance(mappings, list):
            raise BffError(502, 'Invalid Mercado Libre metadata response')
        filtered = [m for m in mappings if isinstance(m, dict) and str(m.get('user_id')) == str(store.marketplace_seller_id)
                    and m.get('site_id') == store.site and m.get('logistic_type') == store.logistic_type]
        result.close()
        body = json.dumps({'user_id': data['user_id'], 'site_id': data.get('site_id'), 'marketplaces': filtered}, ensure_ascii=False).encode('utf-8')
        return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(body))
    except Exception:
        result.close()
        raise


def read_seller_promotions(env, store, accept=None):
    seller = str(store.marketplace_seller_id or '')
    if not re.fullmatch(r'[1-9][0-9]{0,19}', seller):
        raise BffError(403, 'Shop marketplace seller is not configured')
    return client.send('GET', '/marketplace/seller-promotions/users/' + seller,
                       token=store._token(), accept=accept, extra_headers={'version': 'v2'})


def read_reference(env, store, path, arguments, accept=None):
    """Read a fixed reference endpoint using the authorized shop's site."""
    if not isinstance(store.site, str) or not re.fullmatch(r'[A-Z]{3}', store.site):
        raise BffError(403, 'Shop site is not configured')
    path = path.replace('{site_id}', store.site)
    if '{payment_method}' in path:
        path = path.replace('{payment_method}', arguments['payment_method'])
    if '{reason_id}' in path:
        path = path.replace('{reason_id}', arguments['reason_id'])
    if '{category_id}' in path:
        path = path.replace('{category_id}', arguments['category_id'])
    if '{domain_id}' in path:
        path = path.replace('{domain_id}', arguments['domain_id'])
    query = None
    if path == '/marketplace/sizechart/equivalences':
        query = [('site_id', store.site), ('domain_id', arguments['domain_id']), ('gender', arguments['gender'])]
    elif path == '/marketplace/domain_discovery/search':
        query = [('q', arguments['query'])]
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    return client.send('GET', path, token=store._token(), query=query, accept=accept, timeout=timeout)


def read_item_resource(env, store, arguments, path, accept=None):
    """Authorize the marketplace item and site before reading its related resource."""
    item_id = arguments['item_id']
    if item_id[:3] != store.site:
        raise BffError(403, 'Item does not belong to this shop site')
    token = store._token()
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    response = client.send('GET', '/marketplace/items/' + item_id, token=token, timeout=timeout)
    if not 200 <= response.status < 300:
        return response
    item = response.json()
    seller = str(store.marketplace_seller_id or '')
    if item.get('id') != item_id or item.get('site_id') != store.site or str(item.get('seller_id')) != seller:
        raise BffError(403, 'Item does not belong to this shop')
    query = [(key, str(arguments[key])) for key in ('limit', 'offset') if key in arguments]
    extra_headers = None
    if path.startswith('/marketplace/seller-promotions/items/'):
        seller = str(store.marketplace_seller_id or '')
        if not re.fullmatch(r'[1-9][0-9]{0,19}', seller):
            raise BffError(403, 'Shop marketplace seller is not configured')
        query = [('user_id', seller)]
        extra_headers = {'version': 'v2'}
    return client.send('GET', path, token=token, query=query, accept=accept, timeout=timeout, extra_headers=extra_headers)


def read_product_ads(env, store, arguments, accept=None):
    """Resolve the advertiser from the token and bind the requested item to the shop."""
    item_id = arguments['item_id']
    seller = str(store.marketplace_seller_id or '')
    if not re.fullmatch(r'[1-9][0-9]{0,19}', seller) or not item_id.startswith(store.site):
        raise BffError(403, 'Shop or item is not supported')
    token = store._token()
    item_response = client.send('GET', '/marketplace/items/' + item_id, token=token)
    if not 200 <= item_response.status < 300:
        return item_response
    item = item_response.json()
    if item.get('id') != item_id or item.get('site_id') != store.site or str(item.get('seller_id')) != seller:
        raise BffError(403, 'Item does not belong to this shop')
    advertisers = client.send('GET', '/advertising/advertisers', token=token,
                              query=[('product_id', 'PADS')], extra_headers={'api-version': '1'})
    if not 200 <= advertisers.status < 300:
        return advertisers
    data = advertisers.json()
    entries = data.get('advertisers')
    if not isinstance(entries, list):
        raise BffError(502, 'Invalid advertiser response')
    matches = [str(a.get('advertiser_id')) for a in entries if isinstance(a, dict) and a.get('site_id') == store.site]
    if len(matches) != 1 or not re.fullmatch(r'[1-9][0-9]{0,19}', matches[0]):
        raise BffError(409, 'Cannot uniquely identify a Product Ads advertiser for this shop site')
    path = '/marketplace/advertising/' + store.site + '/advertisers/' + matches[0] + '/product_ads/ad_groups/search'
    query = [('filters[item_ids]', item_id)] + [(k, str(arguments[k])) for k in ('limit', 'offset') if k in arguments]
    result = client.send('GET', path, token=token, query=query, accept=accept, extra_headers={'api-version': '2'})
    if not 200 <= result.status < 300:
        return result
    try:
        body = result.peek_json()
        rows = body.get('results')
        if not isinstance(rows, list) or any(not isinstance(row, dict) or str(row.get('advertiser_id')) != matches[0] for row in rows):
            raise BffError(403, 'Ad groups do not match the authorized advertiser')
        return result
    except Exception:
        result.close()
        raise


def read_shipment(env, store, shipment_id, path, accept=None):
    """Verify the shipment belongs to this shop before reading any shipment resource."""
    token = store._token()
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    headers = {'x-format-new': 'true'}
    result = client.send('GET', '/marketplace/shipments/' + shipment_id, token=token,
                         extra_headers=headers, timeout=timeout)
    if not 200 <= result.status < 300:
        return result
    try:
        shipment = result.peek_json()
        origin = shipment.get('origin')
        sender = origin.get('sender_id') if isinstance(origin, dict) else None
        sellers = {str(value) for value in (store.seller_id, store.marketplace_seller_id) if value}
        if str(shipment.get('id')) != shipment_id or sender is None or str(sender) not in sellers:
            raise BffError(403, 'Shipment does not belong to this shop')
        source = shipment.get('source')
        if isinstance(source, dict) and source.get('site_id') and source['site_id'] != store.site:
            raise BffError(403, 'Shipment does not belong to this shop site')
        if path == '/marketplace/shipments/' + shipment_id:
            return result
    except Exception:
        result.close()
        raise
    result.close()
    return client.send('GET', path, token=token, extra_headers=headers, accept=accept, timeout=timeout)


def shipment_orders(env, store, shipment_id, accept=None):
    """Verify a shipment and every seller and order before returning the exact list."""
    shipment = read_shipment(env, store, shipment_id, '/marketplace/shipments/' + shipment_id)
    if not 200 <= shipment.status < 300:
        return shipment
    shipment.close()
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    token = store._token()
    result = client.send('GET', '/shipments/' + shipment_id + '/orders', token=token, timeout=timeout,
                         extra_headers={'X-New-Domain': 'true'}, accept=accept)
    if result.status == 204 or not 200 <= result.status < 300:
        return result
    try:
        records = result.peek_json_array()
        sellers = {str(s) for s in (store.seller_id, store.marketplace_seller_id) if s}
        order_ids = set()
        if len(records) > 100:
            raise BffError(403, 'Shipment order ownership cannot be established')
        for record in records:
            order_id = str(record.get('order_id')) if isinstance(record, dict) else ''
            if (not isinstance(record, dict) or str(record.get('seller_id')) not in sellers
                    or not re.fullmatch(r'[1-9][0-9]{0,19}', order_id)):
                raise BffError(403, 'Shipment order ownership cannot be established')
            order_ids.add(order_id)
        for order_id in sorted(order_ids):
            order = client.send('GET', '/marketplace/orders/' + order_id, token=token, timeout=timeout)
            if not 200 <= order.status < 300:
                result.close()
                return order
            verify_order_ownership(order.json(), order_id, store)
        return result
    except Exception:
        result.close()
        raise


def shipment_tracking(env, store, operation_id, arguments, accept=None):
    """Only custom logistics shipment owners may update tracking or its final status."""
    shipment_id = arguments['shipment_id']
    base = '/marketplace/shipments/' + shipment_id
    response = read_shipment(env, store, shipment_id, base)
    if not 200 <= response.status < 300:
        return response
    shipment = response.json()
    logistic = shipment.get('logistic')
    if not isinstance(logistic, dict) or logistic.get('mode') != 'me1' or logistic.get('type') != 'default':
        raise BffError(409, 'Custom tracking requires me1/default logistics')
    if operation_id == 'shipments.tracking_update':
        path = base + '/tracking'
        body = {key: arguments[key] for key in ('tracking_id', 'tracking_url', 'carrier')}
    elif operation_id == 'shipments.tracking_status':
        path = base + '/tracking/status'
        body = {key: arguments[key] for key in ('tracking_id', 'status')}
    else:
        raise BffError(404, 'Unknown shipment tracking operation')
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    return client.send('POST', path, token=store._token(), body=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                       content_type='application/json', accept=accept, timeout=timeout)


def order_shipments(env, store, order_id, accept=None):
    """Authorize the order and return only its hosted shipment IDs and types."""
    order = read_order(env, store, order_id, '/marketplace/orders/' + order_id)
    if not 200 <= order.status < 300:
        return order
    order.close()
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    token = store._token()
    result = client.send('GET', '/orders/' + order_id + '/shipments', token=token, query=[('hosted', 'true')],
                         extra_headers={'X-New-Domain': 'true'}, timeout=timeout, accept=accept)
    if result.status == 204 or not 200 <= result.status < 300:
        return result
    try:
        shipments = result.peek_json_array()
        if len(shipments) > 20:
            raise BffError(403, 'Shipment associations cannot be established')
        seen = set()
        filtered = []
        for entry in shipments:
            sid = str(entry.get('id')) if isinstance(entry, dict) else ''
            if (not re.fullmatch(r'[1-9][0-9]{0,19}', sid) or sid in seen
                    or entry.get('type') not in ('forward', 'return', 'return_to_buyer')):
                raise BffError(403, 'Shipment associations cannot be established')
            seen.add(sid)
            filtered.append({'id': entry['id'], 'type': entry['type']})
        result.close()
        body = json.dumps(filtered).encode('utf-8')
        return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(body))
    except Exception:
        result.close()
        raise


def search_orders(env, store, arguments):
    """Verify every order in a bounded seller/site search page before returning it."""
    seller = str(store.marketplace_seller_id or '')
    if not re.fullmatch(r'[1-9][0-9]{0,19}', seller) or not re.fullmatch(r'[A-Z]{3}', store.site or ''):
        raise BffError(403, 'Shop seller or site is not configured')
    token = store._token()
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    limit = arguments.get('limit', 10)
    offset = arguments.get('offset', 0)
    query = [('seller.id', seller), ('site', store.site), ('limit', str(limit)), ('offset', str(offset))]
    for key, wire_key in (('status', 'order.status'), ('sort', 'sort')):
        if key in arguments:
            query.append((wire_key, arguments[key]))
    response = client.send('GET', '/marketplace/orders/search', token=token, query=query, timeout=timeout)
    if not 200 <= response.status < 300:
        return response
    data = response.json()
    results = data.get('results')
    if not isinstance(results, list) or len(results) > limit:
        raise BffError(403, 'Order search ownership cannot be established')
    ids = []
    for entry in results:
        orders = entry.get('orders') if isinstance(entry, dict) else None
        if not isinstance(orders, list) or not orders:
            raise BffError(403, 'Order search ownership cannot be established')
        for order in orders:
            order_id = str(order.get('id')) if isinstance(order, dict) else ''
            if not re.fullmatch(r'[1-9][0-9]{0,19}', order_id) or order_id in ids or len(ids) >= 20:
                raise BffError(403, 'Order search ownership cannot be established')
            ids.append(order_id)
    for order_id in ids:
        order_response = client.send('GET', '/marketplace/orders/' + order_id, token=token, timeout=timeout)
        if not 200 <= order_response.status < 300:
            return order_response
        verify_order_ownership(order_response.json(), order_id, store)
    body = json.dumps({'results': results, 'limit': limit, 'offset': offset}, ensure_ascii=False).encode('utf-8')
    return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(body))


def read_order(env, store, order_id, path, accept=None):
    """Verify order seller and optional marketplace before returning order resources."""
    token = store._token()
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    result = client.send('GET', '/marketplace/orders/' + order_id, token=token, timeout=timeout)
    if not 200 <= result.status < 300:
        return result
    try:
        order = result.peek_json()
        verify_order_ownership(order, order_id, store)
        if path == '/marketplace/orders/' + order_id:
            return result
    except Exception:
        result.close()
        raise
    result.close()
    return client.send('GET', path, token=token, accept=accept, timeout=timeout)


def verify_order_ownership(order, order_id, store):
    seller = order.get('seller')
    seller_id = seller.get('id') if isinstance(seller, dict) else None
    sellers = {str(value) for value in (store.seller_id, store.marketplace_seller_id) if value}
    if str(order.get('id')) != order_id or seller_id is None or str(seller_id) not in sellers:
        raise BffError(403, 'Order does not belong to this shop')
    context = order.get('context')
    if isinstance(context, dict) and context.get('site') and context['site'] != store.site:
        raise BffError(403, 'Order does not belong to this shop site')


def claim_partial_refund(env, store, arguments, accept=None):
    """Require an authorized PDD claim and an explicitly offered refund percentage."""
    base = '/marketplace/v2/claims/' + arguments['claim_id']
    response = read_claim(env, store, arguments['claim_id'], base)
    if not 200 <= response.status < 300:
        return response
    claim = response.json()
    respondents = [p for p in claim.get('players', []) if isinstance(p, dict) and p.get('role') == 'respondent']
    actions = respondents[0].get('available_actions') if len(respondents) == 1 else None
    if (not str(claim.get('reason_id', '')).startswith('PDD') or not isinstance(actions, list)
            or not any(isinstance(a, dict) and a.get('action') == 'allow_partial_refund' for a in actions)):
        raise BffError(409, 'Partial refund is not available')
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    token = store._token()
    resolutions = client.send('GET', base + '/expected-resolutions', token=token, timeout=timeout)
    if not 200 <= resolutions.status < 300:
        return resolutions
    try:
        pending = resolutions.peek_json_array()
        if not any(isinstance(r, dict) and r.get('expected_resolution') == 'return_product'
                   and r.get('status') == 'pending' and r.get('player_role') == 'complainant' for r in pending):
            raise BffError(409, 'No pending product return resolution')
    finally:
        resolutions.close()
    offers = client.send('GET', base + '/partial-refund/available-offers', token=token, timeout=timeout)
    if not 200 <= offers.status < 300:
        return offers
    entries = offers.json().get('available_offers')
    try:
        selected = Decimal(str(arguments['percentage']))
        available = isinstance(entries, list) and any(isinstance(e, dict) and e.get('percentage') is not None
                    and Decimal(str(e['percentage'])) == selected for e in entries)
    except (InvalidOperation, ValueError):
        available = False
    if not available:
        raise BffError(409, 'Refund percentage is not currently offered')
    body = json.dumps({'percentage': arguments['percentage']}).encode('utf-8')
    return client.send('POST', base + '/expected-resolutions/partial-refund', token=token, body=body,
                       content_type='application/json', accept=accept, timeout=timeout)


def claim_resolution(env, store, operation_id, arguments, accept=None):
    """Run an explicit available seller action on a verified order claim."""
    operations = {'claims.refund': ('refund', '/expected-resolutions/refund'),
                  'claims.allow_return': ('allow_return', '/expected-resolutions/allow-return'),
                  'claims.open_dispute': ('open_dispute', '/actions/open-dispute')}
    if operation_id not in operations:
        raise BffError(404, 'Unknown claim resolution')
    action, suffix = operations[operation_id]
    base = '/marketplace/v2/claims/' + arguments['claim_id']
    response = read_claim(env, store, arguments['claim_id'], base)
    if not 200 <= response.status < 300:
        return response
    claim = response.json()
    respondents = [p for p in claim.get('players', []) if isinstance(p, dict) and p.get('role') == 'respondent']
    actions = respondents[0].get('available_actions') if len(respondents) == 1 else None
    if not isinstance(actions, list) or not any(isinstance(a, dict) and a.get('action') == action for a in actions):
        raise BffError(409, 'Claim resolution action is not available')
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    return client.send('POST', base + suffix, token=store._token(), accept=accept, timeout=timeout)


def send_claim_message(env, store, arguments, accept=None):
    """Send only if the authorized seller has the specific action available."""
    claim_id = arguments['claim_id']
    base = '/marketplace/v2/claims/' + claim_id
    response = read_claim(env, store, claim_id, base)
    if not 200 <= response.status < 300:
        return response
    claim = response.json()
    respondents = [p for p in claim.get('players', []) if isinstance(p, dict) and p.get('role') == 'respondent']
    actions = respondents[0].get('available_actions') if len(respondents) == 1 else None
    required_action = 'send_message_to_' + arguments['receiver_role']
    if not isinstance(actions, list) or not any(isinstance(a, dict) and a.get('action') == required_action for a in actions):
        raise BffError(409, 'Claim message action is not available')
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    body = {'receiver_role': arguments['receiver_role'], 'message': arguments['message'], 'attachments': []}
    return client.send('POST', base + '/actions/send-message', token=store._token(),
                       body=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                       content_type='application/json', accept=accept, timeout=timeout)


def search_claims(env, store, arguments):
    """Re-fetch and authorize each bounded claim; never return search snapshots."""
    seller = str(store.marketplace_seller_id or '')
    if not re.fullmatch(r'[1-9][0-9]{0,19}', seller) or not re.fullmatch(r'[A-Z]{3}', store.site or ''):
        raise BffError(403, 'Shop seller or site is not configured')
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    limit, offset = arguments.get('limit', 10), arguments.get('offset', 0)
    query = [('user_id', seller), ('site_id', store.site), ('resource', 'order'), ('limit', str(limit)), ('offset', str(offset))]
    if 'status' in arguments:
        query.append(('status', arguments['status']))
    result = client.send('GET', '/marketplace/v2/claims/search', token=store._token(), query=query, timeout=timeout)
    if not 200 <= result.status < 300:
        return result
    data = result.json().get('data')
    if not isinstance(data, list) or len(data) > limit:
        raise BffError(403, 'Claim search ownership cannot be established')
    claims, seen = [], set()
    for item in data:
        claim_id = str(item.get('id')) if isinstance(item, dict) else ''
        if not re.fullmatch(r'[1-9][0-9]{0,19}', claim_id) or claim_id in seen:
            raise BffError(403, 'Claim search ownership cannot be established')
        seen.add(claim_id)
        response = read_claim(env, store, claim_id, '/marketplace/v2/claims/' + claim_id)
        if not 200 <= response.status < 300:
            return response
        claims.append(response.json())
    body = json.dumps({'data': claims, 'limit': limit, 'offset': offset}, ensure_ascii=False).encode('utf-8')
    return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(body))


def read_claim(env, store, claim_id, path, accept=None, query=None):
    """Authorize order claims using both seller participant and related order."""
    token = store._token()
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    base = '/marketplace/v2/claims/' + claim_id
    response = client.send('GET', base, token=token, timeout=timeout)
    if not 200 <= response.status < 300:
        return response
    try:
        claim = response.peek_json()
        sellers = {str(value) for value in (store.seller_id, store.marketplace_seller_id) if value}
        players = claim.get('players')
        order_id = str(claim.get('resource_id'))
        respondents = [p for p in players if isinstance(p, dict) and p.get('role') == 'respondent'] if isinstance(players, list) else []
        if (str(claim.get('id')) != claim_id or claim.get('resource') != 'order'
                or not re.fullmatch(r'[1-9][0-9]{0,19}', order_id)
                or len(respondents) != 1 or respondents[0].get('type') != 'seller'
                or str(respondents[0].get('user_id')) not in sellers
                or (claim.get('site_id') and claim['site_id'] != store.site)):
            raise BffError(403, 'Claim ownership cannot be established')
        order_response = client.send('GET', '/marketplace/orders/' + order_id, token=token, timeout=timeout)
        if not 200 <= order_response.status < 300:
            response.close()
            return order_response
        verify_order_ownership(order_response.json(), order_id, store)
        if path == base:
            return response
        response.close()
        return client.send('GET', path, token=token, query=query or (), accept=accept, timeout=timeout)
    except Exception:
        response.close()
        raise


def read_pack(env, store, pack_id, accept=None):
    """Return the exact pack snapshot only after every associated order is authorized."""
    token = store._token()
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    result = client.send('GET', '/marketplace/orders/pack/' + pack_id, token=token, timeout=timeout, accept=accept)
    if not 200 <= result.status < 300:
        return result
    try:
        pack = result.peek_json()
        orders = pack.get('orders')
        if str(pack.get('id')) != pack_id or not isinstance(orders, list) or not 1 <= len(orders) <= 100:
            raise BffError(403, 'Pack ownership cannot be established')
        seen = set()
        for item in orders:
            order_id = str(item.get('id')) if isinstance(item, dict) else ''
            if not re.fullmatch(r'[1-9][0-9]{0,19}', order_id) or order_id in seen:
                raise BffError(403, 'Pack ownership cannot be established')
            seen.add(order_id)
            order_response = client.send('GET', '/marketplace/orders/' + order_id, token=token, timeout=timeout)
            if not 200 <= order_response.status < 300:
                result.close()
                return order_response
            order = order_response.json()
            verify_order_ownership(order, order_id, store)
        return result
    except Exception:
        result.close()
        raise


def search_questions(env, store, arguments):
    """Return only a bounded page whose questions and items are owned by the shop."""
    seller = str(store.marketplace_seller_id or '')
    if not re.fullmatch(r'[1-9][0-9]{0,19}', seller):
        raise BffError(403, 'Shop marketplace seller is not configured')
    token = store._token()
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    limit = arguments.get('limit', 10)
    offset = arguments.get('offset', 0)
    query = [('seller_id', seller), ('limit', str(limit)), ('offset', str(offset))]
    response = client.send('GET', '/marketplace/questions/search', token=token, query=query, timeout=timeout)
    if not 200 <= response.status < 300:
        return response
    data = response.json()
    questions = data.get('questions')
    if not isinstance(questions, list) or len(questions) > limit:
        raise BffError(403, 'Question search ownership cannot be established')
    sellers = {str(value) for value in (store.seller_id, store.marketplace_seller_id) if value}
    items = set()
    for question in questions:
        if not isinstance(question, dict):
            raise BffError(403, 'Question search ownership cannot be established')
        item = question.get('item_id')
        if (str(question.get('seller_id')) != seller or question.get('site_id') != store.site
                or not isinstance(item, str) or not re.fullmatch(r'[A-Z]{3}[0-9]+', item)):
            raise BffError(403, 'Question search ownership cannot be established')
        items.add(item)
    for item in items:
        item_response = client.send('GET', '/items/' + item, token=token, timeout=timeout)
        if not 200 <= item_response.status < 300:
            return item_response
        item_data = item_response.json()
        if item_data.get('id') != item or str(item_data.get('seller_id')) not in sellers:
            raise BffError(403, 'Question item does not belong to this shop')
    body = json.dumps({'questions': questions, 'limit': limit, 'offset': offset}, ensure_ascii=False).encode('utf-8')
    return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(body))


def question_access(env, store, operation_id, arguments, accept=None):
    """Verify both question seller and underlying item before revealing or modifying it."""
    question_id = arguments['question_id']
    token = store._token()
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    response = client.send('GET', '/marketplace/questions/' + question_id, token=token, timeout=timeout)
    if not 200 <= response.status < 300:
        return response
    try:
        question = response.peek_json()
        sellers = {str(value) for value in (store.seller_id, store.marketplace_seller_id) if value}
        item_id = question.get('item_id')
        if (str(question.get('id')) != question_id or str(question.get('seller_id')) not in sellers
                or not isinstance(item_id, str) or not re.fullmatch(r'[A-Z]{3}[0-9]+', item_id)):
            raise BffError(403, 'Question does not belong to this shop')
        if question.get('site_id') and question['site_id'] != store.site:
            raise BffError(403, 'Question does not belong to this shop site')
        item_response = client.send('GET', '/items/' + item_id, token=token, timeout=timeout)
        if not 200 <= item_response.status < 300:
            response.close()
            return item_response
        item = item_response.json()
        if item.get('id') != item_id or str(item.get('seller_id')) not in sellers:
            raise BffError(403, 'Question item does not belong to this shop')
        if operation_id == 'questions.read':
            return response
        response.close()
        if operation_id == 'questions.answer':
            body = {'question_id': int(question_id), 'text': arguments['text']}
            if 'text_translated' in arguments:
                body['text_translated'] = arguments['text_translated']
            return client.send('POST', '/marketplace/answers', token=token,
                               body=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                               content_type='application/json', accept=accept, timeout=timeout)
        if operation_id == 'questions.delete':
            return client.send('DELETE', '/marketplace/questions/' + question_id,
                               token=token, accept=accept, timeout=timeout)
        raise BffError(404, 'Unknown question operation')
    except Exception:
        response.close()
        raise


def pack_messages(env, store, operation_id, arguments, accept=None):
    """Check every pack order before reading or posting a seller's conversation."""
    pack_id = arguments['pack_id']
    pack = read_pack(env, store, pack_id)
    if not 200 <= pack.status < 300:
        return pack
    pack.close()
    token = store._token()
    try:
        timeout = float(env['ir.config_parameter'].sudo().get_param('meli_bff.timeout_seconds', '120'))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        timeout = 120
    path = '/marketplace/messages/packs/' + pack_id
    if operation_id == 'messages.read_pack':
        query = [(key, str(arguments[key])) for key in ('limit', 'offset') if key in arguments]
        return client.send('GET', path, token=token, query=query, accept=accept, timeout=timeout)
    if operation_id == 'messages.send_pack':
        data = {'text': arguments['text']}
        if 'text_translated' in arguments:
            data['text_translated'] = arguments['text_translated']
        return client.send('POST', path, token=token, body=json.dumps(data, ensure_ascii=False).encode('utf-8'),
                           content_type='application/json', accept=accept, timeout=timeout)
    raise BffError(404, 'Unknown message operation')

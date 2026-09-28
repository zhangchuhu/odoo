"""Explicit API contracts; no caller-controlled upstream URLs or identities."""
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl

MAX_BODY = 200 * 1024 * 1024
MAX_PICTURE = 10_000_000


class BffError(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class Operation:
    name: str
    method: str
    store_id: int | None = None
    item_id: str | None = None

    @property
    def permission(self):
        return 'read' if self.method in ('GET', 'HEAD') else 'write'


@dataclass(frozen=True)
class PreparedCall:
    method: str
    path: str
    query: list
    body: bytes
    content_type: str | None = None


ROUTES = (
    ('shops', r'/shops', ('GET', 'HEAD')),
    ('shop', r'/shops/(?P<store_id>[1-9][0-9]*)', ('GET', 'HEAD')),
    ('items', r'/shops/(?P<store_id>[1-9][0-9]*)/items', ('GET', 'HEAD', 'POST')),
    ('item', r'/shops/(?P<store_id>[1-9][0-9]*)/items/(?P<item_id>[A-Z]{3}[0-9]+)', ('GET', 'HEAD', 'PUT')),
    ('description', r'/shops/(?P<store_id>[1-9][0-9]*)/items/(?P<item_id>CBT[0-9]+)/description', ('GET', 'HEAD', 'PUT')),
    ('pictures', r'/shops/(?P<store_id>[1-9][0-9]*)/pictures', ('POST',)),
)


def resolve_route(method, suffix):
    if len(suffix) > 2048:
        raise BffError(404, 'Unsupported Mercado Libre path')
    for name, pattern, methods in ROUTES:
        match = re.fullmatch(pattern, suffix)
        if match:
            if method not in methods:
                raise BffError(405, 'Method not allowed')
            values = match.groupdict()
            sid = values.get('store_id')
            if sid and len(sid) > 10:
                break
            return Operation(name, method, int(sid) if sid else None, values.get('item_id'))
    raise BffError(404, 'Unsupported Mercado Libre path')


QUERY_KEYS = {
    'items': {'q', 'status', 'offset', 'limit', 'search_type', 'scroll_id', 'category_id',
              'sku', 'seller_sku', 'missing_product_identifiers', 'reputation_health_gauge',
              'include_filters', 'orders', 'listing_type_id', 'labels'},
    'item': {'include_attributes', 'attributes'},
    'description': set(), 'pictures': set(), 'shop': set(), 'shops': set(),
}


def validate_query(operation, pairs):
    query, scopes = [], []
    for key, value in pairs:
        if key in ('shopId', 'shop_id') and operation.store_id is not None:
            if value != str(operation.store_id):
                raise BffError(403, 'Conflicting shop identity')
        elif key == 'scope' and operation.name == 'items' and operation.permission == 'read':
            scopes.append(value)
        elif key in QUERY_KEYS[operation.name] and operation.permission == 'read':
            query.append((key, value))
        else:
            raise BffError(400, 'Unsupported query parameter')
    if len(scopes) > 1 or (scopes and scopes[0] not in ('global', 'marketplace')):
        raise BffError(400, 'Invalid listing scope')
    return query, scopes[0] if scopes else 'global'


def parse_raw_query(operation, raw):
    try:
        encoded = raw.decode('ascii')
        if re.search(r'[^\x21-\x7e]|#|%(?![0-9a-fA-F]{2})', encoded):
            raise ValueError()
        pairs = parse_qsl(encoded, keep_blank_values=True, encoding='utf-8', errors='strict')
        validate_query(operation, pairs)
        segments = []
        for segment in encoded.split('&'):
            decoded = parse_qsl(segment, keep_blank_values=True, encoding='utf-8', errors='strict')
            if not decoded or decoded[0][0] not in ('scope', 'shopId', 'shop_id'):
                segments.append(segment)
        return pairs, '&'.join(segments)
    except (ValueError, UnicodeError):
        raise BffError(400, 'Invalid query encoding') from None


def json_object(body):
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        raise BffError(400, 'Invalid JSON body') from None
    if not isinstance(data, dict):
        raise BffError(400, 'JSON body must be an object')
    return data


def check_site(data, store, required=False):
    for key, expected in [('site_id', store.site), ('logistic_type', store.logistic_type)]:
        if (required or key in data) and (not expected or data.get(key) != expected):
            raise BffError(403, 'Payload does not match the shop site and logistics')


def validate_native_body(body, store, creating):
    data = json_object(body)
    if any(k in data for k in ('seller_id', 'user_id', 'access_token', 'shopId', 'shop_id', 'caller.id')):
        raise BffError(400, 'Identity fields are server controlled')
    check_site(data, store)
    sites = data.get('sites_to_sell')
    if creating and (not isinstance(sites, list) or not sites):
        raise BffError(400, 'sites_to_sell is required for Global Selling')
    if sites is not None:
        if not isinstance(sites, list) or not sites:
            raise BffError(400, 'Invalid sites_to_sell')
        for site in sites:
            if not isinstance(site, dict):
                raise BffError(400, 'Invalid site payload')
            check_site(site, store, required=True)


def prepare_call(operation, store, query_pairs, body):
    query, scope = validate_query(operation, query_pairs)
    method = 'GET' if operation.method == 'HEAD' else operation.method
    content_type = None
    if operation.name == 'items':
        if method == 'POST':
            validate_native_body(body, store, creating=True)
            path = '/global/items'
        else:
            seller = store.marketplace_seller_id if scope == 'marketplace' else store.seller_id
            if not seller or not re.fullmatch(r'[0-9]+', seller):
                raise BffError(409, 'Shop seller is not configured')
            path = '/marketplace/users/' + seller + '/items/search'
    elif operation.name == 'item':
        path = ('/global/items/' if method == 'PUT' else '/items/') + operation.item_id
        if method == 'PUT':
            validate_native_body(body, store, creating=False)
    elif operation.name == 'description':
        if not store.site or not store.logistic_type:
            raise BffError(409, 'Shop site and logistics must be configured')
        if method == 'PUT':
            data = json_object(body)
            if set(data) != {'plain_text'} or not isinstance(data['plain_text'], str):
                raise BffError(400, 'Description requires only a plain_text string')
            path = '/global/items/' + operation.item_id
            body = json.dumps({'site_id': store.site, 'logistic_type': store.logistic_type,
                               'description': data}, ensure_ascii=False).encode()
            content_type = 'application/json'
        else:
            path = '/marketplace/items/' + operation.item_id + '/description'
            query.extend([('site_id', store.site), ('logistic_type', store.logistic_type)])
    elif operation.name == 'pictures':
        path = '/pictures/items/upload'
    else:
        raise BffError(404, 'Not a platform operation')
    return PreparedCall(method, path, query, body, content_type)


def filtered_response_headers(headers):
    blocked = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
               'te', 'trailer', 'trailers', 'transfer-encoding', 'upgrade', 'host',
               'content-length', 'set-cookie'}
    for key, value in headers:
        if key.lower() == 'connection':
            blocked.update(part.strip().lower() for part in value.split(','))
    return [(key, value) for key, value in headers if key.lower() not in blocked]

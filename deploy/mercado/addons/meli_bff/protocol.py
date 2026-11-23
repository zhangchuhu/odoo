"""BFF HTTP routing and raw query preservation."""
import re
from urllib.parse import parse_qsl
from odoo.addons.meli_platform_adapter.errors import PlatformError as BffError
from odoo.addons.meli_platform_adapter.mapping import (Operation, PreparedCall, validate_query, prepare_call, json_object, check_site, validate_native_body)
from odoo.addons.meli_platform_adapter.headers import filtered_response_headers

MAX_BODY = 200 * 1024 * 1024
MAX_PICTURE = 10_000_000

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

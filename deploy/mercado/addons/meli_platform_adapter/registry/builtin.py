"""The first verified contract. Extend only after source and ownership review."""
from . import OperationRegistry

_DEFINITIONS = ({
    'operation_id': 'items.read',
    'domain': 'items',
    'title': 'Read a shop item',
    'description': 'Read an item after checking its seller against the selected shop.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/authentication-and-authorization-global-selling/specific-regulations',
    'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'item_id': {'type': 'string', 'pattern': '^[A-Z]{3}[0-9]+$', 'maxLength': 40}}, 'required': ['item_id'], 'additionalProperties': False},
    'permission': 'read',
    'scope': 'shop',
    'capability_requirements': [],
    'ownership_policy': 'item',
    'handler_id': 'legacy_item_get',
    'upstream': {'method': 'GET', 'path': '/items/{item_id}'},
    'request_kind': 'json',
    'response_kind': 'json',
    'exposure': 'mcp',
    'retry_policy': 'never',
},)

_ITEM_SCHEMA = {'type': 'string', 'pattern': '^CBT[0-9]+$', 'maxLength': 40}
_DEFINITIONS += ({
    'operation_id': 'items.batch_read', 'domain': 'items', 'title': 'Read global items in a batch',
    'description': 'Read up to 10 global items after verifying every item and response seller.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/items-and-searches-global-selling', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'item_ids': {'type': 'array', 'items': _ITEM_SCHEMA,
        'minItems': 1, 'maxItems': 10, 'uniqueItems': True}}, 'required': ['item_ids'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'item',
    'handler_id': 'item_batch_read', 'upstream': {'method': 'GET', 'path': '/items'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += ({
    'operation_id': 'items.marketplace_mappings', 'domain': 'items', 'title': 'Read shop marketplace item mappings',
    'description': 'Return only mappings to marketplace items verified for the selected shop.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/items-and-searches-global-selling', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'item_id': _ITEM_SCHEMA}, 'required': ['item_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'item',
    'handler_id': 'item_mappings', 'upstream': {'method': 'GET', 'path': '/items/{item_id}/marketplace_items'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += ({
    'operation_id': 'items.marketplace_read', 'domain': 'items', 'title': 'Read marketplace item',
    'description': 'Read the authorized marketplace item snapshot for the selected shop and site.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/fulfillment-stock-gs', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'item_id': {'type': 'string', 'pattern': '^[A-Z]{3}[0-9]+$', 'maxLength': 40}}, 'required': ['item_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'item',
    'handler_id': 'marketplace_item_read', 'upstream': {'method': 'GET', 'path': '/marketplace/items/{item_id}'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += ({
    'operation_id': 'stock.fulfillment', 'domain': 'stock', 'title': 'Read fulfillment inventory stock',
    'description': 'Read stock for an inventory ID verified against a shop-owned fulfillment item or variation; Mexico and Chile only.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/fulfillment-stock-gs', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {
        'item_id': {'type': 'string', 'pattern': '^[A-Z]{3}[0-9]+$', 'maxLength': 40},
        'inventory_id': {'type': 'string', 'pattern': '^[A-Z0-9]{1,40}$'},
        'include_conditions': {'type': 'boolean'}}, 'required': ['item_id', 'inventory_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'item',
    'handler_id': 'fulfillment_stock', 'upstream': {'method': 'GET', 'path': '/marketplace/inventories/{inventory_id}/stock/fulfillment'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += ({
    'operation_id': 'stock.fulfillment_operations', 'domain': 'stock', 'title': 'Read fulfillment inventory operations',
    'description': 'Read inventory operations for an authorized fulfillment item, within a maximum 60-day range.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/fulfillment-stock-gs', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {
        'item_id': {'type': 'string', 'pattern': '^[A-Z]{3}[0-9]+$', 'maxLength': 40},
        'inventory_id': {'type': 'string', 'pattern': '^[A-Z0-9]{1,40}$'},
        'date_from': {'type': 'string', 'pattern': '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'},
        'date_to': {'type': 'string', 'pattern': '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 1000},
        'scroll': {'type': 'string', 'pattern': '^[A-Za-z0-9_+/=:-]+$', 'maxLength': 2048},
        'type': {'type': 'string', 'pattern': '^[A-Z_]{1,64}$'},
        'shipment_id': {'type': 'string', 'pattern': '^[1-9][0-9]{0,19}$'}},
        'required': ['item_id', 'inventory_id', 'date_from', 'date_to'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'item',
    'handler_id': 'fulfillment_stock', 'upstream': {'method': 'GET', 'path': '/marketplace/stock/fulfillment/operations/search'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += ({
    'operation_id': 'stock.fulfillment_operation_read', 'domain': 'stock', 'title': 'Read a fulfillment inventory operation',
    'description': 'Read one stock movement after validating its seller, inventory and marketplace item.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/fulfillment-stock-gs', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {
        'item_id': {'type': 'string', 'pattern': '^[A-Z]{3}[0-9]+$', 'maxLength': 40},
        'inventory_id': {'type': 'string', 'pattern': '^[A-Z0-9]{1,40}$'},
        'operation_id': {'type': 'string', 'pattern': '^[1-9][0-9]{0,19}$'}},
        'required': ['item_id', 'inventory_id', 'operation_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'item',
    'handler_id': 'fulfillment_stock', 'upstream': {'method': 'GET', 'path': '/marketplace/stock/fulfillment/operations/{operation_id}'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += ({
    'operation_id': 'stock.fulfillment_operations_batch', 'domain': 'stock', 'title': 'Search multiple fulfillment inventories',
    'description': 'Search up to 10 inventories after verifying each marketplace item and inventory association.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/fulfillment-stock-gs', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {
        'items': {'type': 'array', 'minItems': 1, 'maxItems': 10, 'uniqueItems': True, 'items': {'type': 'object',
            'properties': {'item_id': {'type': 'string', 'pattern': '^[A-Z]{3}[0-9]+$', 'maxLength': 40},
                           'inventory_id': {'type': 'string', 'pattern': '^[A-Z0-9]{1,40}$'}},
            'required': ['item_id', 'inventory_id'], 'additionalProperties': False}},
        'date_from': {'type': 'string', 'pattern': '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'},
        'date_to': {'type': 'string', 'pattern': '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 1000},
        'scroll': {'type': 'string', 'pattern': '^[A-Za-z0-9_+/=:-]+$', 'maxLength': 2048},
        'type': {'type': 'string', 'pattern': '^[A-Z_]{1,64}$'},
        'shipment_id': {'type': 'string', 'pattern': '^[1-9][0-9]{0,19}$'}},
        'required': ['items', 'date_from', 'date_to'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'item',
    'handler_id': 'fulfillment_stock_batch', 'upstream': {'method': 'GET', 'path': '/marketplace/stock/fulfillment/operations/search'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += ({
    'operation_id': 'shipments.item_cost', 'domain': 'shipments', 'title': 'Read marketplace item shipping cost',
    'description': 'Read shipping cost for an active marketplace item belonging to the selected shop.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-shipments', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'item_id': {'type': 'string', 'pattern': '^[A-Z]{3}[0-9]+$', 'maxLength': 40}},
                     'required': ['item_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'item',
    'handler_id': 'item_resource_read', 'upstream': {'method': 'GET', 'path': '/marketplace/items/{item_id}/shipping_options/cost'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += ({
    'operation_id': 'ads.item_groups', 'domain': 'ads', 'title': 'Read Product Ads groups for an item',
    'description': 'Find Product Ads groups for an owned item; the advertiser is resolved from the authorized account.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/new-product-ads', 'verified_at': '2026-11-08',
    'input_schema': {'type': 'object', 'properties': {
        'item_id': {'type': 'string', 'pattern': '^[A-Z]{3}[0-9]+$', 'maxLength': 40},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50},
        'offset': {'type': 'integer', 'minimum': 0, 'maximum': 9950}},
        'required': ['item_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'item',
    'handler_id': 'product_ads_read', 'upstream': {'method': 'GET', 'path': '/marketplace/advertising/{site}/advertisers/{advertiser}/product_ads/ad_groups/search'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += ({
    'operation_id': 'promotions.seller_list', 'domain': 'promotions', 'title': 'List seller promotions',
    'description': 'List promotions for the selected shop marketplace seller.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-promotions-gs', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'shop',
    'handler_id': 'seller_promotions', 'upstream': {'method': 'GET', 'path': '/marketplace/seller-promotions/users/{seller_id}'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += ({
    'operation_id': 'promotions.item_candidates', 'domain': 'promotions', 'title': 'Read item promotions',
    'description': 'Read promotions and candidate campaigns for a marketplace item owned by the selected shop.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/clearance', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'item_id': {'type': 'string', 'pattern': '^[A-Z]{3}[0-9]+$', 'maxLength': 40}},
                     'required': ['item_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'item',
    'handler_id': 'item_resource_read', 'upstream': {'method': 'GET', 'path': '/marketplace/seller-promotions/items/{item_id}'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += tuple({
    'operation_id': 'items.' + name, 'domain': 'items', 'title': title,
    'description': 'Read a marketplace item belonging to the selected shop and site.',
    'docs_url': url, 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'item_id': {'type': 'string', 'pattern': '^[A-Z]{3}[0-9]+$', 'maxLength': 40},
        **({'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50}, 'offset': {'type': 'integer', 'minimum': 0, 'maximum': 10000}} if name == 'reviews' else {})},
        'required': ['item_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [],
    'ownership_policy': 'item', 'handler_id': 'item_resource_read',
    'upstream': {'method': 'GET', 'path': path}, 'request_kind': 'json',
    'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
} for name, title, path, url in (
    ('performance', 'Read item performance', '/item/{item_id}/performance', 'https://global-selling.mercadolibre.com/devsite/en_us/listings-quality-gs'),
    ('reviews', 'Read item reviews', '/reviews/item/{item_id}', 'https://global-selling.mercadolibre.com/devsite/en_us/product-reviews'),
))
_DEFINITIONS += ({
    'operation_id': 'items.description_read', 'domain': 'items',
    'title': 'Read item description', 'description': 'Read the description bound to the shop site and logistics.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/seller-campaign/item-description',
    'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'item_id': _ITEM_SCHEMA}, 'required': ['item_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'item',
    'handler_id': 'legacy_description_get', 'upstream': {'method': 'GET', 'path': '/marketplace/items/{item_id}/description'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
}, {
    'operation_id': 'items.update', 'domain': 'items',
    'title': 'Update shop item', 'description': 'Update a shop item; changed variation lists can remove omitted variants.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/manage-questions-answers-global-selling/sync-and-modify-listings-gs',
    'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'item_id': {'type': 'string', 'pattern': '^[A-Z]{3}[0-9]+$', 'maxLength': 40}, 'item': {'type': 'object'}}, 'required': ['item_id', 'item'], 'additionalProperties': False},
    'permission': 'write', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'item',
    'handler_id': 'legacy_item_update', 'upstream': {'method': 'PUT', 'path': '/global/items/{item_id}'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
from ..mapping import QUERY_KEYS
_QUERY_VALUE = {'anyOf': [{'type': 'string', 'maxLength': 2048}, {'type': 'integer'}, {'type': 'boolean'}]}
_QUERY_ARRAY = {'type': 'array', 'items': _QUERY_VALUE, 'minItems': 1, 'maxItems': 100}
_DEFINITIONS += ({
    'operation_id': 'items.search', 'domain': 'items', 'title': 'Search shop items',
    'description': 'Search the global or marketplace seller bound to the shop.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/messaging-after-sale-global-selling/items-and-searches-global-selling',
    'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {
        'scope': {'type': 'string', 'enum': ['global', 'marketplace']},
        'query': {'type': 'object', 'properties': {key: {'anyOf': [_QUERY_VALUE, _QUERY_ARRAY]} for key in sorted(QUERY_KEYS['items'])}, 'additionalProperties': False}},
        'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'shop',
    'handler_id': 'legacy_items_search', 'upstream': {'method': 'GET', 'path': '/marketplace/users/{seller_id}/items/search'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
}, {
    'operation_id': 'items.create', 'domain': 'items', 'title': 'Create global item',
    'description': 'Create a traditional Global Selling item for the selected shop; User Products and Fully Managed are not supported by this operation.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/api-docs/global-selling-item-create-update-global-items',
    'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'item': {'type': 'object'}}, 'required': ['item'], 'additionalProperties': False},
    'permission': 'write', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'shop',
    'handler_id': 'legacy_item_create', 'upstream': {'method': 'POST', 'path': '/global/items'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
}, {
    'operation_id': 'items.description_update', 'domain': 'items', 'title': 'Update item description',
    'description': 'Update a CBT item description using the selected shop site and logistics.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/seller-campaign/item-description',
    'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'item_id': _ITEM_SCHEMA, 'plain_text': {'type': 'string'}}, 'required': ['item_id', 'plain_text'], 'additionalProperties': False},
    'permission': 'write', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'item',
    'handler_id': 'legacy_description_update', 'upstream': {'method': 'PUT', 'path': '/global/items/{item_id}'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_SHIPMENT_ID = {'type': 'string', 'pattern': '^[1-9][0-9]{0,19}$'}
_DEFINITIONS += ({
    'operation_id': 'shipments.orders', 'domain': 'shipments', 'title': 'Read shipment orders',
    'description': 'Read shipment orders only after verifying the shipment and every listed order and seller.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-shipments', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'shipment_id': _SHIPMENT_ID}, 'required': ['shipment_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'shipment_sender',
    'handler_id': 'shipment_orders', 'upstream': {'method': 'GET', 'path': '/shipments/{shipment_id}/orders'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += tuple({
    'operation_id': 'shipments.' + name, 'domain': 'shipments', 'title': title,
    'description': 'Custom logistics me1/default only. Delivery status changes are final; Mercado Libre performs final validation.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-shipments', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {
        'shipment_id': _SHIPMENT_ID, 'tracking_id': {'type': 'string', 'minLength': 1, 'maxLength': 200},
        **({'tracking_url': {'type': 'string', 'pattern': '^https?://[^\\s]+$', 'maxLength': 2048}, 'carrier': {'type': 'string', 'minLength': 1, 'maxLength': 200}} if name == 'tracking_update' else
           {'status': {'type': 'string', 'enum': ['delivered', 'not_delivered']}})},
        'required': ['shipment_id', 'tracking_id'] + (['tracking_url', 'carrier'] if name == 'tracking_update' else ['status']),
        'additionalProperties': False},
    'permission': 'write', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'shipment_sender',
    'handler_id': 'shipment_tracking', 'upstream': {'method': 'POST', 'path': path},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
} for name, title, path in (
    ('tracking_update', 'Report custom shipment tracking', '/marketplace/shipments/{shipment_id}/tracking'),
    ('tracking_status', 'Report final shipment status', '/marketplace/shipments/{shipment_id}/tracking/status'),
))
_SHIPMENT_OPS = (
    ('read', '', 'Get shipment details'),
    ('costs', '/costs', 'Get shipment costs'),
    ('sla', '/sla', 'Get shipment dispatch deadline'),
    ('items', '/items', 'Get shipment items'),
    ('history', '/history', 'Get shipment status history'),
    ('carrier', '/carrier', 'Get shipment carrier'),
    ('lead_time', '/lead_time', 'Get shipment lead times'),
)
_DEFINITIONS += tuple({
    'operation_id': 'shipments.' + name, 'domain': 'shipments', 'title': title,
    'description': title + '; requires sender ownership and the configured shop.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-shipments',
    'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'shipment_id': _SHIPMENT_ID},
                     'required': ['shipment_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [],
    'ownership_policy': 'shipment_sender', 'handler_id': 'shipment_read',
    'upstream': {'method': 'GET', 'path': '/marketplace/shipments/{shipment_id}' + suffix},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
} for name, suffix, title in _SHIPMENT_OPS)
_ORDER_OPS = (('read', '', 'Read order', 'json'), ('invoice_read', '/invoice', 'Read order invoice', 'binary'))
_DEFINITIONS += ({
    'operation_id': 'orders.shipments', 'domain': 'orders', 'title': 'Read order shipments',
    'description': 'Read hosted forward and return shipment IDs directly linked to an authorized order.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-orders-cbt', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'order_id': _SHIPMENT_ID}, 'required': ['order_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'order_seller',
    'handler_id': 'order_shipments', 'upstream': {'method': 'GET', 'path': '/orders/{order_id}/shipments'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += ({
    'operation_id': 'orders.search', 'domain': 'orders', 'title': 'Search shop orders',
    'description': 'Search the bound marketplace seller and site; verify every order in each result. Aggregate metadata is omitted.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-orders-cbt', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 10},
        'offset': {'type': 'integer', 'minimum': 0, 'maximum': 10000},
        'status': {'type': 'string', 'enum': ['paid', 'cancelled', 'payment_required']},
        'sort': {'type': 'string', 'enum': ['date_asc', 'date_desc', 'updated_asc', 'updated_desc', 'closed_asc', 'closed_desc']}},
        'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'order_seller',
    'handler_id': 'order_search', 'upstream': {'method': 'GET', 'path': '/marketplace/orders/search'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += tuple({
    'operation_id': 'orders.' + name, 'domain': 'orders', 'title': title,
    'description': title + '; requires order seller ownership and the configured shop.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-orders-cbt',
    'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'order_id': _SHIPMENT_ID},
                     'required': ['order_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [],
    'ownership_policy': 'order_seller', 'handler_id': 'order_read',
    'upstream': {'method': 'GET', 'path': '/marketplace/orders/{order_id}' + suffix},
    'request_kind': 'json', 'response_kind': response, 'exposure': 'mcp', 'retry_policy': 'never',
} for name, suffix, title, response in _ORDER_OPS)
_DEFINITIONS += ({
    'operation_id': 'shipments.label', 'domain': 'shipments', 'title': 'Read shipment label',
    'description': 'Download the shipping label after verifying the shipment sender and site. Availability depends on shipment status and logistics type.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-shipments',
    'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'shipment_id': _SHIPMENT_ID}, 'required': ['shipment_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [],
    'ownership_policy': 'shipment_sender', 'handler_id': 'shipment_read',
    'upstream': {'method': 'GET', 'path': '/marketplace/shipments/{shipment_id}/labels'},
    'request_kind': 'json', 'response_kind': 'binary', 'exposure': 'mcp', 'retry_policy': 'never',
}, {
    'operation_id': 'orders.billing_info', 'domain': 'orders', 'title': 'Read order billing information',
    'description': 'Read buyer billing information after verifying order seller and site.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/gs-billing-data',
    'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'order_id': _SHIPMENT_ID}, 'required': ['order_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [],
    'ownership_policy': 'order_seller', 'handler_id': 'order_read',
    'upstream': {'method': 'GET', 'path': '/marketplace/orders/{order_id}/billing_info'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
})
_DEFINITIONS += ({
    'operation_id': 'packs.read', 'domain': 'orders', 'title': 'Read pack',
    'description': 'Read a pack after verifying every associated order belongs to the selected shop.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/packs',
    'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'pack_id': _SHIPMENT_ID},
                     'required': ['pack_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [],
    'ownership_policy': 'pack_orders', 'handler_id': 'pack_read',
    'upstream': {'method': 'GET', 'path': '/marketplace/orders/pack/{pack_id}'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_QUESTION_ID = {'type': 'string', 'pattern': '^[1-9][0-9]{0,19}$'}
_QUESTION_OPS = (
    ('read', 'GET', '/marketplace/questions/{question_id}', 'read'),
    ('answer', 'POST', '/marketplace/answers', 'write'),
    ('delete', 'DELETE', '/marketplace/questions/{question_id}', 'write'),
)
_DEFINITIONS += tuple({
    'operation_id': 'questions.' + name, 'domain': 'questions', 'title': 'Question: ' + name,
    'description': 'Requires question and item ownership; Mercado Libre restricts Model 6 sellers.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-questions-answers-global-selling',
    'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'question_id': _QUESTION_ID,
                     **({'text': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
                         'text_translated': {'type': 'string', 'maxLength': 2000}} if name == 'answer' else {})},
                     'required': ['question_id', 'text'] if name == 'answer' else ['question_id'],
                     'additionalProperties': False},
    'permission': permission, 'scope': 'shop', 'capability_requirements': [],
    'ownership_policy': 'question_item', 'handler_id': 'question_access',
    'upstream': {'method': method, 'path': path},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
} for name, method, path, permission in _QUESTION_OPS)
_DEFINITIONS += ({
    'operation_id': 'questions.search', 'domain': 'questions', 'title': 'Search shop questions',
    'description': 'Search the shop marketplace seller; rejects any result whose site or item ownership cannot be established. Aggregate metadata is omitted.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-questions-answers-global-selling',
    'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 10},
        'offset': {'type': 'integer', 'minimum': 0, 'maximum': 1000}}, 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [],
    'ownership_policy': 'question_item', 'handler_id': 'question_search',
    'upstream': {'method': 'GET', 'path': '/marketplace/questions/search'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_MESSAGE_OPS = (('read_pack', 'GET', 'read'), ('send_pack', 'POST', 'write'))
_DEFINITIONS += tuple({
    'operation_id': 'messages.' + name, 'domain': 'messages', 'title': 'Pack messages: ' + name,
    'description': 'Requires ownership of every order in the pack. Model 6 sellers are restricted by Mercado Libre. Attachments are not supported.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/messaging-after-sale-global-selling',
    'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'pack_id': _SHIPMENT_ID,
                     **({'text': {'type': 'string', 'minLength': 1, 'maxLength': 10000},
                         'text_translated': {'type': 'string', 'maxLength': 10000}} if name == 'send_pack' else
                        {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50},
                         'offset': {'type': 'integer', 'minimum': 0, 'maximum': 10000}})},
                     'required': ['pack_id', 'text'] if name == 'send_pack' else ['pack_id'],
                     'additionalProperties': False},
    'permission': permission, 'scope': 'shop', 'capability_requirements': [],
    'ownership_policy': 'pack_orders', 'handler_id': 'pack_messages',
    'upstream': {'method': method, 'path': '/marketplace/messages/packs/{pack_id}'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
} for name, method, permission in _MESSAGE_OPS)
_CLAIM_OPS = (('read', ''), ('detail', '/detail'), ('actions_history', '/actions-history'),
              ('status_history', '/status-history'), ('affects_reputation', '/affects-reputation'))
_DEFINITIONS += ({
    'operation_id': 'claims.return_cost', 'domain': 'claims', 'title': 'Read claim return shipping cost',
    'description': 'Read return shipping cost for a verified order claim with optional USD conversion.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-returns', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'claim_id': _SHIPMENT_ID, 'calculate_amount_usd': {'type': 'boolean'}},
                     'required': ['claim_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'claim_order',
    'handler_id': 'claim_return_cost', 'upstream': {'method': 'GET', 'path': '/post-purchase/v1/claims/{claim_id}/charges/return-cost'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += ({
    'operation_id': 'claims.partial_refund', 'domain': 'claims', 'title': 'Offer partial refund for claim',
    'description': 'Offer an explicitly selected currently available percentage for an authorized PDD order claim.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-claim-resolutions', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'claim_id': _SHIPMENT_ID,
        'percentage': {'type': 'number', 'exclusiveMinimum': 0, 'exclusiveMaximum': 100}},
        'required': ['claim_id', 'percentage'], 'additionalProperties': False},
    'permission': 'write', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'claim_order',
    'handler_id': 'claim_partial_refund', 'upstream': {'method': 'POST', 'path': '/marketplace/v2/claims/{claim_id}/expected-resolutions/partial-refund'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += tuple({
    'operation_id': 'claims.' + name, 'domain': 'claims', 'title': title,
    'description': 'Order claims only. Requires an available seller action; this can change the claim or issue a refund.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-claim-resolutions', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'claim_id': _SHIPMENT_ID}, 'required': ['claim_id'], 'additionalProperties': False},
    'permission': 'write', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'claim_order',
    'handler_id': 'claim_resolution_write', 'upstream': {'method': 'POST', 'path': '/marketplace/v2/claims/{claim_id}' + suffix},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
} for name, title, suffix in (
    ('refund', 'Issue full refund for claim', '/expected-resolutions/refund'),
    ('allow_return', 'Allow product return for claim', '/expected-resolutions/allow-return'),
    ('open_dispute', 'Request claim mediation', '/actions/open-dispute'),
))
_DEFINITIONS += ({
    'operation_id': 'claims.send_message', 'domain': 'claims', 'title': 'Send claim message',
    'description': 'Send a text message on an authorized order claim when the seller action is available; no attachments.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-claims-messages', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'claim_id': _SHIPMENT_ID,
        'receiver_role': {'type': 'string', 'enum': ['complainant', 'mediator']},
        'message': {'type': 'string', 'minLength': 1, 'maxLength': 10000}},
        'required': ['claim_id', 'receiver_role', 'message'], 'additionalProperties': False},
    'permission': 'write', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'claim_order',
    'handler_id': 'claim_message_write', 'upstream': {'method': 'POST', 'path': '/marketplace/v2/claims/{claim_id}/actions/send-message'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += ({
    'operation_id': 'claims.search', 'domain': 'claims', 'title': 'Search order claims',
    'description': 'Search order claims for the bound seller and site, returning individually authorized claim snapshots. Model 6 sellers are restricted.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-claims', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'status': {'type': 'string', 'enum': ['opened', 'closed']},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 10}, 'offset': {'type': 'integer', 'minimum': 0, 'maximum': 10000}}, 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'claim_order',
    'handler_id': 'claim_search', 'upstream': {'method': 'GET', 'path': '/marketplace/v2/claims/search'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += tuple({
    'operation_id': 'claims.' + name, 'domain': 'claims', 'title': 'Claim: ' + name,
    'description': 'Order claims only; validates the respondent and order seller. Model 6 sellers are restricted.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-claims',
    'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'claim_id': _SHIPMENT_ID},
                     'required': ['claim_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [],
    'ownership_policy': 'claim_order', 'handler_id': 'claim_read',
    'upstream': {'method': 'GET', 'path': '/marketplace/v2/claims/{claim_id}' + suffix},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
} for name, suffix in _CLAIM_OPS)
_DEFINITIONS += tuple({
    'operation_id': 'claims.' + name, 'domain': 'claims', 'title': title,
    'description': 'Order claims only; validates respondent and order seller. Model 6 sellers are restricted.',
    'docs_url': url, 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'claim_id': _SHIPMENT_ID}, 'required': ['claim_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'claim_order',
    'handler_id': 'claim_read', 'upstream': {'method': 'GET', 'path': '/marketplace/v2/claims/{claim_id}' + suffix},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
} for name, title, suffix, url in (
    ('messages', 'Read claim messages', '/messages', 'https://global-selling.mercadolibre.com/devsite/en_us/manage-claims-messages'),
    ('returns', 'Read claim return', '/returns', 'https://global-selling.mercadolibre.com/devsite/en_us/manage-returns'),
    ('expected_resolutions', 'Read claim expected resolutions', '/expected-resolutions', 'https://global-selling.mercadolibre.com/devsite/en_us/manage-claim-resolutions'),
    ('refund_offers', 'Read partial refund offers', '/partial-refund/available-offers', 'https://global-selling.mercadolibre.com/devsite/en_us/manage-claim-resolutions'),
))
_DEFINITIONS += tuple({
    'operation_id': operation, 'domain': domain, 'title': title,
    'description': 'Reference data for the selected shop; site is bound by the server.',
    'docs_url': url, 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': ({'payment_method': {'type': 'string', 'pattern': '^[a-zA-Z0-9_-]{1,50}$'}} if operation == 'payments.method' else {}),
                     'required': ['payment_method'] if operation == 'payments.method' else [], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [],
    'ownership_policy': 'shop', 'handler_id': 'reference_read',
    'upstream': {'method': 'GET', 'path': path}, 'request_kind': 'json', 'response_kind': 'json',
    'exposure': 'mcp', 'retry_policy': 'never',
} for operation, domain, title, path, url in (
    ('payments.methods', 'payments', 'List site payment methods', '/sites/{site_id}/payment_methods', 'https://global-selling.mercadolibre.com/devsite/en_us/manage-orders-cbt'),
    ('payments.method', 'payments', 'Read site payment method', '/sites/{site_id}/payment_methods/{payment_method}', 'https://global-selling.mercadolibre.com/devsite/en_us/manage-orders-cbt'),
    ('size_chart.active_domains', 'size_chart', 'List active size chart domains', '/catalog/charts/CBT/configurations/active_domains', 'https://global-selling.mercadolibre.com/devsite/en_us/first-steps'),
))
_DEFINITIONS += ({
    'operation_id': 'size_chart.equivalences', 'domain': 'size_chart', 'title': 'Read size equivalences',
    'description': 'Read size equivalences for the selected shop site.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-size-chart', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {
        'domain_id': {'type': 'string', 'pattern': '^[A-Z0-9_]{1,100}$'},
        'gender': {'type': 'string', 'enum': ['Woman', 'Man', 'Gender neutral', 'Girls', 'Boys', 'Gender neutral kid', 'Babies']}},
        'required': ['domain_id', 'gender'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'shop',
    'handler_id': 'reference_read', 'upstream': {'method': 'GET', 'path': '/marketplace/sizechart/equivalences'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
}, {
    'operation_id': 'claims.reason', 'domain': 'claims', 'title': 'Read claim reason',
    'description': 'Read reference information for a claim reason; Model 6 sellers are restricted.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-claims', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {'reason_id': {'type': 'string', 'pattern': '^[A-Z0-9]{1,40}$'}},
        'required': ['reason_id'], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'shop',
    'handler_id': 'reference_read', 'upstream': {'method': 'GET', 'path': '/marketplace/v2/claims/reasons/{reason_id}'},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
})
_DEFINITIONS += tuple({
    'operation_id': operation, 'domain': domain, 'title': title,
    'description': 'Read Global Selling category and domain reference information.',
    'docs_url': url, 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {field: schema}, 'required': [field], 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'shop',
    'handler_id': 'reference_read', 'upstream': {'method': 'GET', 'path': path},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
} for operation, domain, title, path, url, field, schema in (
    ('categories.attributes', 'categories', 'Read category attributes', '/categories/{category_id}/attributes', 'https://global-selling.mercadolibre.com/devsite/en_us/atributtes-global-selling', 'category_id', {'type': 'string', 'pattern': '^CBT[0-9]{1,20}$'}),
    ('categories.input_specs', 'categories', 'Read category input specifications', '/categories/{category_id}/technical_specs/input', 'https://global-selling.mercadolibre.com/devsite/en_us/atributtes-global-selling', 'category_id', {'type': 'string', 'pattern': '^CBT[0-9]{1,20}$'}),
    ('domains.technical_specs', 'domains', 'Read domain technical specifications', '/domains/{domain_id}/technical_specs', 'https://global-selling.mercadolibre.com/devsite/en_us/first-steps', 'domain_id', {'type': 'string', 'pattern': '^CBT-[A-Z0-9_]{1,100}$'}),
    ('domains.search', 'domains', 'Discover Global Selling domains', '/marketplace/domain_discovery/search', 'https://global-selling.mercadolibre.com/devsite/en_us/first-steps', 'query', {'type': 'string', 'minLength': 1, 'maxLength': 200}),
))
_DEFINITIONS += ({
    'operation_id': 'pictures.upload', 'domain': 'pictures', 'title': 'Upload item picture',
    'description': 'Upload one JPEG or PNG image up to 10 MB for the selected shop.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/pictures', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    'permission': 'write', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'shop',
    'handler_id': 'picture_upload', 'upstream': {'method': 'POST', 'path': '/pictures/items/upload'},
    'request_kind': 'multipart', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
},)
_DEFINITIONS += tuple({
    'operation_id': operation, 'domain': 'users', 'title': title,
    'description': 'Seller identity is bound to the selected shop; marketplace mappings are filtered to that shop.',
    'docs_url': 'https://global-selling.mercadolibre.com/devsite/en_us/manage-users-global-selling', 'verified_at': '2026-11-07',
    'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    'permission': 'read', 'scope': 'shop', 'capability_requirements': [], 'ownership_policy': 'shop',
    'handler_id': 'seller_profile', 'upstream': {'method': 'GET', 'path': path},
    'request_kind': 'json', 'response_kind': 'json', 'exposure': 'mcp', 'retry_policy': 'never',
} for operation, title, path in (
    ('users.self', 'Read authorized seller profile', '/users/me'),
    ('users.marketplace_mapping', 'Read shop marketplace mapping', '/marketplace/users/{seller_id}'),
))
REGISTRY = OperationRegistry(_DEFINITIONS, handlers={'product_ads_read', 'seller_promotions', 'legacy_item_get', 'legacy_description_get', 'legacy_item_update', 'legacy_items_search', 'legacy_item_create', 'legacy_description_update', 'marketplace_item_read', 'item_mappings', 'item_batch_read', 'item_resource_read', 'fulfillment_stock', 'fulfillment_stock_batch', 'reference_read', 'shipment_read', 'shipment_orders', 'shipment_tracking', 'order_read', 'order_search', 'order_shipments', 'pack_read', 'question_access', 'question_search', 'pack_messages', 'claim_read', 'claim_return_cost', 'claim_search', 'claim_message_write', 'claim_resolution_write', 'claim_partial_refund', 'picture_upload', 'seller_profile'}, policies={'item', 'shop', 'shipment_sender', 'order_seller', 'pack_orders', 'question_item', 'claim_order'})

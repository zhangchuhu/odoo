"""Fail-closed screening. Counts paid gross units; refunds/returns are not deducted."""
from datetime import datetime
from zoneinfo import ZoneInfo

SITE_ZONES = {'MLM': 'America/Mexico_City', 'MLB': 'America/Sao_Paulo', 'MLC': 'America/Santiago', 'MCO': 'America/Bogota', 'MPE': 'America/Lima'}


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Order/window timestamp unavailable')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timezone offset required')
    return parsed


def parse_criteria(data):
    required = {'site_id', 'timezone', 'start', 'end', 'sales_max', 'stock_min', 'sales_policy'}
    if not isinstance(data, dict) or set(data) != required:
        raise ValueError('Explicit site, timezone, start/end, sales_max, stock_min and sales_policy required')
    if data['timezone'] != SITE_ZONES.get(data['site_id']) or data['sales_policy'] != 'paid_gross_units':
        raise ValueError('Unsupported site timezone or sales policy')
    for key in ('sales_max', 'stock_min'):
        if type(data[key]) is not int or data[key] < 0:
            raise ValueError('Thresholds must be explicit nonnegative integers')
    start, end = timestamp(data['start']), timestamp(data['end'])
    if start >= end or (end - start).days > 366:
        raise ValueError('Window must be positive and at most 366 days')
    for value in (start, end):
        if value.utcoffset() != value.astimezone(ZoneInfo(data['timezone'])).utcoffset():
            raise ValueError('Offset does not match site timezone')
    return dict(data)


def collect_pages(fetch):
    """Provider-normalized pages require total and a stable observation identity."""
    cursor, seen_cursors, records, ids, identity = None, set(), [], set(), None
    for _ in range(2000):
        page = fetch(cursor)
        if not isinstance(page, dict) or type(page.get('total')) is not int or page['total'] < 0 or not page.get('snapshot') or 'next' not in page or not isinstance(page.get('rows'), list):
            raise ValueError('Incomplete pagination evidence')
        signature = (page['total'], page['snapshot'])
        if identity is not None and signature != identity:
            raise ValueError('Pagination changed during collection')
        identity = signature
        for row in page['rows']:
            identifier = row.get('id') if isinstance(row, dict) else None
            if not identifier or identifier in ids:
                raise ValueError('Duplicate or missing row identity')
            ids.add(identifier); records.append(row)
        cursor = page['next']
        if len(records) > page['total']:
            raise ValueError('Pagination total exceeded')
        if cursor is None:
            if len(records) != page['total']:
                raise ValueError('Truncated pagination')
            return records
        if not page['rows'] or cursor in seen_cursors:
            raise ValueError('Pagination did not advance')
        seen_cursors.add(cursor)
    raise ValueError('Pagination budget exceeded; completeness unknown')


def screen_products(products, orders, criteria, seller_id, orders_complete):
    criteria = parse_criteria(criteria)
    if orders_complete is not True:
        raise ValueError('Complete sales window unavailable')
    sales, unknown_sales, product_ids, order_ids = {}, set(), set(), set()
    start, end = timestamp(criteria['start']), timestamp(criteria['end'])
    for order in orders:
        if order.get('id') in order_ids or not order.get('id') or str(order.get('seller_id')) != str(seller_id) or order.get('site_id') != criteria['site_id']:
            raise ValueError('Order ownership or identity mismatch')
        order_ids.add(order['id'])
        if order.get('status') not in {'paid', 'cancelled', 'invalid', 'payment_required', 'payment_in_process', 'partially_paid'} or order.get('lines_complete') is not True or not isinstance(order.get('lines'), list) or not order['lines']:
            raise ValueError('Order status or line completeness unknown')
        created = timestamp(order['date_created'])
        if not start <= created < end or order['status'] != 'paid':
            continue
        for line in order['lines']:
            item = line.get('item_id')
            if not item:
                raise ValueError('Order item missing')
            quantity = line.get('quantity')
            if type(quantity) is not int or quantity <= 0:
                unknown_sales.add(item)
            else:
                sales[item] = sales.get(item, 0) + quantity
    matches, unknown = [], []
    for product in products:
        item = product.get('id')
        if not item or item in product_ids or str(product.get('seller_id')) != str(seller_id) or product.get('site_id') != criteria['site_id']:
            raise ValueError('Product ownership or identity mismatch')
        product_ids.add(item)
        variations = product.get('variations')
        if not isinstance(variations, list) or any(not isinstance(s, dict) for s in variations):
            unknown.append({'item_id': item, 'reason': 'sku_list_unknown'})
            continue
        skus = variations if isinstance(variations, list) and variations else [product]
        quantities = [s.get('available_quantity') for s in skus]
        sku_ids = [str(s.get('id')) for s in skus]
        if item in unknown_sales or any(type(q) is not int or q < 0 for q in quantities) or len(sku_ids) != len(set(sku_ids)) or (variations and any(s.get('id') is None for s in skus)):
            unknown.append({'item_id': item, 'reason': 'sales_or_sku_stock_unknown'})
            continue
        units, stock = sales.get(item, 0), sum(quantities)
        if units <= criteria['sales_max'] and stock >= criteria['stock_min']:
            matches.append(dict(product, sales_units=units, available_quantity=stock))
    return {'matches': matches, 'unknown': unknown, 'evaluated': len(products)}


def collect_shop(get, seller_id, criteria):
    """Official CBT endpoints; GET only. Two passes detect observable search drift.

    See manage-orders-cbt and items-and-searches-global-selling. We reject
    accounts/windows exceeding the verified offset envelope instead of truncating.
    """
    import re
    import hashlib
    import json
    criteria = parse_criteria(criteria)
    if not re.fullmatch(r'[1-9][0-9]{0,19}', str(seller_id)):
        raise ValueError('Marketplace seller not configured')
    def paged(path, query, products=False):
        def fetch(cursor):
            offset = 0 if cursor is None else cursor
            raw = get(path, list(query) + [('limit', '50'), ('offset', str(offset))])
            paging, rows = raw.get('paging', {}), raw.get('results')
            total = paging.get('total')
            if type(total) is not int or not 0 <= total <= 1000 or paging.get('limit') != 50 or paging.get('offset', offset) != offset or not isinstance(rows, list) or len(rows) > 50:
                raise ValueError('Search paging unavailable or exceeds supported 1000-result window; narrow window/shop scope')
            if products:
                if str(raw.get('seller_id')) != str(seller_id):
                    raise ValueError('Product search seller mismatch')
                rows = [{'id': i} for i in rows]
            return dict(rows=rows, total=total, snapshot=str(total), next=offset + len(rows) if offset + len(rows) < total else None)
        return collect_pages(fetch)
    product_path = '/marketplace/users/' + str(seller_id) + '/items/search'
    order_query = [('seller.id', str(seller_id)), ('site', criteria['site_id']), ('sort', 'date_asc'), ('date_created.from', criteria['start']), ('date_created.to', criteria['end'])]
    def read_orders():
        groups = paged('/marketplace/orders/search', order_query)
        orders, ids = [], set()
        for group in groups:
            if not isinstance(group.get('orders'), list) or not group['orders']:
                raise ValueError('Order group incomplete')
            for ref in group['orders']:
                oid = str(ref.get('id'))
                if not re.fullmatch(r'[1-9][0-9]{0,19}', oid) or oid in ids:
                    raise ValueError('Order identities overlap or are missing')
                ids.add(oid)
                raw = get('/marketplace/orders/' + oid)
                context = raw.get('context') or {}
                if str(raw.get('id')) != oid or str((raw.get('seller') or {}).get('id')) != str(seller_id) or context.get('site') != criteria['site_id']:
                    raise ValueError('Order seller/site ownership unavailable')
                items = raw.get('order_items')
                if not isinstance(items, list) or not items:
                    raise ValueError('Complete order quantities unavailable for this account')
                lines = []
                for line in items:
                    item = line.get('item') or {}
                    if not isinstance(item.get('id'), str) or not item['id'].startswith(criteria['site_id']):
                        raise ValueError('Order item site unknown')
                    lines.append(dict(item_id=item['id'], variation_id=item.get('variation_id'), quantity=line.get('quantity')))
                orders.append(dict(id=oid, seller_id=str(seller_id), site_id=criteria['site_id'], date_created=raw.get('date_created'), status=raw.get('status'), lines_complete=True, lines=lines))
        return orders
    identities = paged(product_path, [], True)
    products = []
    for ref in identities:
        item = ref['id']
        if not isinstance(item, str) or not re.fullmatch(criteria['site_id'] + r'[0-9]+', item):
            raise ValueError('Search returned non-site item; marketplace seller mapping unsupported')
        product = get('/items/' + item)
        if product.get('id') != item or product.get('site_id') != criteria['site_id'] or str(product.get('seller_id')) != str(seller_id):
            raise ValueError('Product ownership mismatch')
        # Keep only fields needed by the immutable snapshot; never persist buyer data.
        products.append({k: product.get(k) for k in ('id', 'seller_id', 'site_id', 'title', 'available_quantity', 'variations', 'thumbnail', 'seller_custom_field')})
    orders = read_orders()
    if orders != read_orders() or identities != paged(product_path, [], True):
        raise ValueError('Shop changed during collection; retry a fresh observation')
    digest = hashlib.sha256(json.dumps(orders, sort_keys=True).encode()).hexdigest()
    return products, orders, dict(orders_complete=True, provider='mercadolibre-cbt-v1', order_count=len(orders), orders_digest=digest, pagination='offset-total-two-pass', consistency='two-pass-observation-not-atomic-platform-snapshot')

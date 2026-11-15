"""Ordered, sanitized read checkpoints. Repeated paths in pass two stay distinct."""
import json


class CollectionPending(Exception):
    pass


def minimized(path, data):
    if path == '/users/me':
        return {'id': data.get('id')}
    if path.startswith('/marketplace/users/') and not path.endswith('/items/search'):
        markets = data.get('marketplaces')
        return {'user_id': data.get('user_id'), 'marketplaces': [{k: m.get(k) for k in ('user_id', 'site_id', 'logistic_type')} for m in markets] if isinstance(markets, list) else None}
    if path.endswith('/items/search'):
        return {'seller_id': data.get('seller_id'), 'results': data.get('results'), 'paging': {k: (data.get('paging') or {}).get(k) for k in ('total', 'limit', 'offset')}}
    if path == '/marketplace/orders/search':
        rows = data.get('results')
        return {'paging': {k: (data.get('paging') or {}).get(k) for k in ('total', 'limit', 'offset') if k in (data.get('paging') or {})}, 'results': [{'id': r.get('id'), 'orders': [{'id': o.get('id')} for o in r['orders']] if isinstance(r.get('orders'), list) else None} for r in rows] if isinstance(rows, list) else None}
    if path.startswith('/marketplace/orders/'):
        lines = data.get('order_items')
        return {'id': data.get('id'), 'seller': {'id': (data.get('seller') or {}).get('id')}, 'context': {'site': (data.get('context') or {}).get('site')},
            'date_created': data.get('date_created'), 'status': data.get('status'),
            'order_items': [{'quantity': line.get('quantity'), 'item': {k: (line.get('item') or {}).get(k) for k in ('id', 'variation_id')}} for line in lines] if isinstance(lines, list) else None}
    if path.startswith('/marketplace/items/'):
        def attrs(values):
            return [{k: value.get(k) for k in ('id', 'name', 'value_id', 'value_name')} for value in values] if isinstance(values, list) else None
        result = {k: data.get(k) for k in ('id', 'seller_id', 'site_id', 'title', 'available_quantity', 'thumbnail', 'seller_custom_field')}
        result['attributes'] = attrs(data.get('attributes'))
        variations = data.get('variations')
        result['variations'] = [dict(id=v.get('id'), available_quantity=v.get('available_quantity'), attributes=attrs(v.get('attributes', [])), attribute_combinations=attrs(v.get('attribute_combinations', []))) for v in variations] if isinstance(variations, list) else None
        return result
    raise ValueError('Unsupported checkpoint resource')


class ReplayReads:
    def __init__(self, checkpoint, fetch):
        self.checkpoint, self.fetch, self.index = checkpoint, fetch, 0
        checkpoint.setdefault('responses', [])

    def get(self, path, query=()):
        query = [list(pair) for pair in query]
        responses = self.checkpoint['responses']
        if self.index < len(responses):
            row = responses[self.index]
            self.index += 1
            if row['path'] != path or row['query'] != query:
                raise ValueError('Checkpoint request sequence changed')
            return row['data']
        data = minimized(path, self.fetch(path, query))
        responses.append({'path': path, 'query': query, 'data': data})
        if len(json.dumps(self.checkpoint, ensure_ascii=False).encode()) > 8 * 1024 * 1024:
            raise ValueError('Screening checkpoint exceeds 8 MiB; no partial batch created')
        # Always yield after exactly one new read, even if it was the final read.
        raise CollectionPending()

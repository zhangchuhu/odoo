import io
import http.client
from contextlib import closing
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import HttpCase, tagged
from odoo.addons.meli_platform_adapter import transport as client


@tagged('post_install', '-at_install')
class TestHttp(HttpCase):
    def test_seller_promotions_use_selected_shop_identity(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertIn('promotions.seller_list', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id, 'arguments': {}}
        calls = []
        def send(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(b'{"results": []}'))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            response = self.call('/operations/promotions.seller_list/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(calls[0][1], '/marketplace/seller-promotions/users/900002')
            self.assertEqual(calls[0][2]['extra_headers'], {'version': 'v2'})
            calls.clear()
            payload['arguments']['user_id'] = '999999'
            self.assertEqual(self.call('/operations/promotions.seller_list/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.store = cls.env['meli.independent.store'].create({
            'name': 'HTTP shop', 'seller_id': '900001', 'marketplace_seller_id': '900002',
            'state': 'connected', 'logistic_type': 'fulfillment',
        })
        cls.other_company = cls.env['res.company'].create({'name': 'Other BFF company'})
        cls.other = cls.env['meli.independent.store'].create({
            'name': 'Other shop', 'company_id': cls.other_company.id,
        })
        cls.writer = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'BFF writer', 'login': 'bff-http-writer',
            'group_ids': [(6, 0, [cls.env.ref('meli_oerp.group_mercadolibre_manager').id])],
        })
        reader_group = cls.env.ref('meli_bff.group_reader', raise_if_not_found=False) or cls.env.ref('base.group_user')
        cls.reader = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'BFF reader', 'login': 'bff-http-reader',
            'group_ids': [(6, 0, [reader_group.id])],
        })
        cls.writer_key = cls.env['res.users.apikeys'].with_user(cls.writer).sudo()._generate(None, 'test', fields.Datetime.now() + timedelta(days=1))
        cls.reader_key = cls.env['res.users.apikeys'].with_user(cls.reader).sudo()._generate(None, 'test', fields.Datetime.now() + timedelta(days=1))

    def call(self, suffix, method='GET', body=None, key=None, content_type='application/json'):
        headers = {'Content-Type': content_type}
        if key is not False:
            headers['Authorization'] = 'Bearer ' + (key or self.writer_key)
        return self.url_open('/api/meli' + suffix, data=body, headers=headers, method=method)

    def test_registry_catalog_auth_and_execution_guards(self):
        response = self.call('/operations', key=False)
        self.assertEqual(response.status_code, 401)
        catalog = self.call('/operations?shop_id=%s' % self.store.id, key=self.reader_key)
        self.assertEqual(catalog.status_code, 200, catalog.text)
        self.assertIn('no-store', catalog.headers.get('Cache-Control', ''))
        data = catalog.json()
        self.assertTrue(data['contract_version'])
        entry = next(x for x in data['operations'] if x['operation_id'] == 'items.read')
        self.assertNotIn('handler_id', entry)
        self.assertNotIn('upstream', entry)
        self.assertEqual(self.call('/operations?shop_id=%s' % self.other.id).status_code, 403)
        payload = {'contract_version': data['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'item_id': 'CBT123'}}
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=self.fake_send):
            self.calls, self.seller, self.tags = [], 900001, []
            result = self.call('/operations/items.read/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual([x[1] for x in self.calls], ['/items/CBT123'])
            self.calls.clear()
            payload['arguments']['identity'] = 99
            result = self.call('/operations/items.read/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(result.status_code, 400, result.text)
            self.assertEqual(self.calls, [])
            payload['arguments'] = {'item_id': 'CBT123'}
            payload['contract_version'] = 'old'
            result = self.call('/operations/items.read/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(result.status_code, 409, result.text)
            self.assertEqual(self.calls, [])
            payload['contract_version'] = data['contract_version']
            payload['shop_id'] = self.other.id
            result = self.call('/operations/items.read/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(result.status_code, 403, result.text)
            self.assertEqual(self.calls, [])
            payload['shop_id'] = self.store.id
            self.seller = 999999
            result = self.call('/operations/items.read/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(result.status_code, 403, result.text)
            self.assertEqual(len(self.calls), 1)

    def test_registry_filters_writes_and_checks_write_ownership(self):
        reader_catalog = self.call('/operations', key=self.reader_key).json()
        writer_catalog = self.call('/operations').json()
        self.assertNotIn('items.update', [e['operation_id'] for e in reader_catalog['operations']])
        self.assertIn('items.update', [e['operation_id'] for e in writer_catalog['operations']])
        self.assertIn('items.description_read', [e['operation_id'] for e in reader_catalog['operations']])
        payload = {'contract_version': writer_catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'item_id': 'CBT123', 'item': {'price': 12}}}
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=self.fake_send):
            self.calls, self.seller, self.tags = [], 900001, []
            body = json.dumps(payload).encode()
            self.assertEqual(self.call('/operations/items.update/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(self.calls, [])
            result = self.call('/operations/items.update/execute', 'POST', body)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(self.calls[-1][0:2], ('PUT', '/global/items/CBT123'))
            self.assertEqual(json.loads(self.calls[-1][2]['body']), {'price': 12})
            self.calls.clear()
            self.seller = 999999
            self.assertEqual(self.call('/operations/items.update/execute', 'POST', body).status_code, 403)
            self.assertEqual(len(self.calls), 1)
            self.calls.clear()
            payload['arguments']['item']['seller_id'] = 999999
            self.seller = 900001
            body = json.dumps(payload).encode()
            self.assertEqual(self.call('/operations/items.update/execute', 'POST', body).status_code, 400)
            self.assertEqual(self.calls, [])
            payload['arguments'] = {'item_id': 'CBT123'}
            result = self.call('/operations/items.description_read/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(self.calls[-1][1], '/marketplace/items/CBT123/description')
            self.assertEqual(self.calls[-1][2]['query'], [('site_id', 'MLM'), ('logistic_type', 'fulfillment')])

    def test_registry_search_create_and_description_write(self):
        catalog = self.call('/operations').json()
        expected = {'items.search', 'items.create', 'items.description_update'}
        self.assertTrue(expected.issubset({e['operation_id'] for e in catalog['operations']}))
        self.calls, self.seller, self.tags = [], 900001, []
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'scope': 'marketplace', 'query': {'status': ['active', 'paused']}}}
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=self.fake_send):
            result = self.call('/operations/items.search/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(self.calls[-1][1], '/marketplace/users/900002/items/search')
            self.assertEqual(self.calls[-1][2]['query'], [('status', 'active'), ('status', 'paused')])
            self.calls.clear()
            payload['arguments']['query']['seller_id'] = '999999'
            self.assertEqual(self.call('/operations/items.search/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(self.calls, [])
            payload['arguments'] = {'item': {'title': 'Example', 'sites_to_sell': [{'site_id': 'MLM', 'logistic_type': 'fulfillment'}]}}
            body = json.dumps(payload).encode()
            self.assertEqual(self.call('/operations/items.create/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(self.calls, [])
            self.assertEqual(self.call('/operations/items.create/execute', 'POST', body).status_code, 200)
            self.assertEqual(self.calls[-1][1], '/global/items')
            self.assertEqual(json.loads(self.calls[-1][2]['body'])['title'], 'Example')
            self.calls.clear()
            payload['arguments'] = {'item_id': 'CBT123', 'plain_text': 'Lamp'}
            result = self.call('/operations/items.description_update/execute', 'POST', json.dumps(payload).encode())
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(json.loads(self.calls[-1][2]['body']), {'site_id': 'MLM', 'logistic_type': 'fulfillment', 'description': {'plain_text': 'Lamp'}})

    def test_registered_shipment_access_proves_store_identity_and_required_headers(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        ids = {entry['operation_id'] for entry in catalog['operations']}
        wanted = {'shipments.read', 'shipments.costs', 'shipments.sla', 'shipments.items',
                  'shipments.history', 'shipments.carrier', 'shipments.lead_time'}
        self.assertTrue(wanted.issubset(ids))
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'shipment_id': '123456'}}
        calls = []
        owner = ['900002']
        def send(method, path, **kwargs):
            calls.append((method, path, kwargs))
            data = {'id': 123456, 'origin': {'sender_id': owner[0]}} if path == '/marketplace/shipments/123456' else {'ok': True}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            result = self.call('/operations/shipments.sla/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual([x[1] for x in calls], ['/marketplace/shipments/123456', '/marketplace/shipments/123456/sla'])
            self.assertTrue(all(x[2]['extra_headers'] == {'x-format-new': 'true'} for x in calls))
            calls.clear()
            result = self.call('/operations/shipments.read/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(len(calls), 1, 'Return the exact authorized shipment snapshot')
            calls.clear()
            owner[0] = '999999'
            result = self.call('/operations/shipments.sla/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(result.status_code, 403)
            self.assertEqual(len(calls), 1)
            calls.clear()
            payload['shop_id'] = self.other.id
            self.assertEqual(self.call('/operations/shipments.sla/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 403)
            self.assertEqual(calls, [])
            payload['shop_id'] = self.store.id
            payload['arguments']['shipment_id'] = '../other'
            self.assertEqual(self.call('/operations/shipments.sla/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])

    def test_registered_orders_require_matching_order_seller_and_site(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertTrue({'orders.read', 'orders.invoice_read'}.issubset({e['operation_id'] for e in catalog['operations']}))
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'order_id': '123456'}}
        calls = []
        owner, site = ['900002'], ['MLM']
        def send(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path == '/marketplace/orders/123456':
                data = {'id': 123456, 'seller': {'id': owner[0]}, 'context': {'site': site[0]}}
                return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
            return client.PlatformResponse(200, [('Content-Type', 'application/pdf')], io.BytesIO(b'%PDF-test'))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            result = self.call('/operations/orders.invoice_read/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.content, b'%PDF-test')
            self.assertEqual([x[1] for x in calls], ['/marketplace/orders/123456', '/marketplace/orders/123456/invoice'])
            calls.clear()
            result = self.call('/operations/orders.read/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(len(calls), 1, 'Return the exact authorized order snapshot')
            calls.clear(); owner[0] = '999999'
            result = self.call('/operations/orders.invoice_read/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(result.status_code, 403)
            self.assertEqual(len(calls), 1)
            calls.clear(); owner[0] = '900002'; site[0] = 'MLB'
            self.assertEqual(self.call('/operations/orders.read/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 1)
            calls.clear(); payload['shop_id'] = self.other.id
            self.assertEqual(self.call('/operations/orders.read/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 403)
            self.assertEqual(calls, [])
            payload['shop_id'] = self.store.id; payload['arguments']['order_id'] = '../other'
            self.assertEqual(self.call('/operations/orders.read/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])

    def test_global_item_batch_checks_every_item_and_batch_response(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertIn('items.batch_read', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'item_ids': ['CBT11', 'CBT22']}}
        calls = []; owner = ['900001']; batch_owner = ['900001']
        def send(method, path, **kwargs):
            calls.append((path, kwargs))
            if path == '/items':
                data = [{'code': 200, 'body': {'id': 'CBT11', 'seller_id': batch_owner[0]}},
                        {'code': 200, 'body': {'id': 'CBT22', 'seller_id': batch_owner[0]}}]
            else:
                data = {'id': path.rsplit('/', 1)[-1], 'seller_id': owner[0]}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            result = self.call('/operations/items.batch_read/execute', 'POST', body, self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual([c[0] for c in calls], ['/items/CBT11', '/items/CBT22', '/items'])
            self.assertEqual(calls[-1][1]['query'], [('ids', 'CBT11,CBT22')])
            calls.clear(); owner[0] = '999999'
            self.assertEqual(self.call('/operations/items.batch_read/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 1)
            calls.clear(); owner[0] = '900001'; batch_owner[0] = '999999'
            self.assertEqual(self.call('/operations/items.batch_read/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 3)
            calls.clear(); payload['arguments']['item_ids'] = ['CBT11', 'CBT11']
            self.assertEqual(self.call('/operations/items.batch_read/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])

    def test_global_item_mappings_are_filtered_to_authorized_shop(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertIn('items.marketplace_mappings', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'item_id': 'CBT123'}}
        calls = []; global_owner = ['900001']; market_owner = ['900002']
        def send(method, path, **kwargs):
            calls.append(path)
            if path == '/items/CBT123':
                data = {'id': 'CBT123', 'seller_id': global_owner[0]}
            elif path == '/items/CBT123/marketplace_items':
                data = {'item_id': 'CBT123', 'marketplace_items': [{'item_id': 'MLM123', 'site_id': 'MLM'}, {'item_id': 'MLB999', 'site_id': 'MLB'}]}
            else:
                data = {'id': 'MLM123', 'site_id': 'MLM', 'seller_id': market_owner[0]}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            result = self.call('/operations/items.marketplace_mappings/execute', 'POST', body, self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['marketplace_items'], [{'item_id': 'MLM123', 'site_id': 'MLM'}])
            self.assertEqual(calls, ['/items/CBT123', '/items/CBT123/marketplace_items', '/marketplace/items/MLM123'])
            calls.clear(); market_owner[0] = '999999'
            self.assertEqual(self.call('/operations/items.marketplace_mappings/execute', 'POST', body, self.reader_key).status_code, 403)
            calls.clear(); global_owner[0] = '999999'
            self.assertEqual(self.call('/operations/items.marketplace_mappings/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(calls, ['/items/CBT123'])

    def test_marketplace_item_returns_only_authorized_snapshot(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertIn('items.marketplace_read', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'item_id': 'MLM123'}}
        calls = []; owner = ['900002']; site = ['MLM']
        def send(method, path, **kwargs):
            calls.append(path)
            data = {'id': 'MLM123', 'site_id': site[0], 'seller_id': owner[0], 'title': 'Item'}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            result = self.call('/operations/items.marketplace_read/execute', 'POST', body, self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['title'], 'Item')
            self.assertEqual(calls, ['/marketplace/items/MLM123'])
            calls.clear(); owner[0] = '999999'
            self.assertEqual(self.call('/operations/items.marketplace_read/execute', 'POST', body, self.reader_key).status_code, 403)
            calls.clear(); owner[0] = '900002'; site[0] = 'MLB'
            self.assertEqual(self.call('/operations/items.marketplace_read/execute', 'POST', body, self.reader_key).status_code, 403)
            calls.clear(); payload['arguments']['item_id'] = 'MLB123'
            self.assertEqual(self.call('/operations/items.marketplace_read/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 403)
            self.assertEqual(calls, [])

    def test_claim_return_cost_requires_ownership_and_optional_currency_conversion(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertIn('claims.return_cost', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'claim_id': '123456', 'calculate_amount_usd': True}}
        calls = []; owner = ['900002']
        def send(method, path, **kwargs):
            calls.append((path, kwargs))
            if path == '/marketplace/v2/claims/123456':
                data = {'id': 123456, 'resource': 'order', 'resource_id': 11, 'site_id': 'MLM',
                        'players': [{'role': 'respondent', 'type': 'seller', 'user_id': owner[0]}]}
            elif path == '/marketplace/orders/11':
                data = {'id': 11, 'seller': {'id': '900001'}, 'context': {'site': 'MLM'}}
            else: data = {'amount': 10, 'currency_id': 'USD'}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            result = self.call('/operations/claims.return_cost/execute', 'POST', body, self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(calls[-1][0], '/post-purchase/v1/claims/123456/charges/return-cost')
            self.assertEqual(calls[-1][1]['query'], [('calculate_amount_usd', 'true')])
            calls.clear(); owner[0] = '999999'
            self.assertEqual(self.call('/operations/claims.return_cost/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 1)

    def test_partial_refund_requires_verified_current_offer(self):
        catalog = self.call('/operations').json()
        self.assertIn('claims.partial_refund', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'claim_id': '123456', 'percentage': 50}}
        calls = []; percentages = [[{'percentage': 50, 'amount': 10.10}]]
        def send(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path == '/marketplace/v2/claims/123456':
                data = {'id': 123456, 'resource': 'order', 'resource_id': 11, 'site_id': 'MLM', 'reason_id': 'PDD9939',
                        'players': [{'role': 'respondent', 'type': 'seller', 'user_id': '900002', 'available_actions': [{'action': 'allow_partial_refund'}]}]}
            elif path == '/marketplace/orders/11':
                data = {'id': 11, 'seller': {'id': '900001'}, 'context': {'site': 'MLM'}}
            elif path.endswith('/partial-refund/available-offers'):
                data = {'currency_id': 'USD', 'available_offers': percentages[0]}
            elif path.endswith('/expected-resolutions'):
                data = [{'expected_resolution': 'return_product', 'status': 'pending', 'player_role': 'complainant'}]
            else:
                data = {'status': 'pending'}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            self.assertEqual(self.call('/operations/claims.partial_refund/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(calls, [])
            result = self.call('/operations/claims.partial_refund/execute', 'POST', body)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(calls[-1][1], '/marketplace/v2/claims/123456/expected-resolutions/partial-refund')
            self.assertEqual(json.loads(calls[-1][2]['body']), {'percentage': 50})
            calls.clear(); percentages[0] = [{'percentage': 40, 'amount': 8.08}]
            self.assertEqual(self.call('/operations/claims.partial_refund/execute', 'POST', body).status_code, 409)
            self.assertEqual(len(calls), 4)
            calls.clear(); payload['arguments']['percentage'] = 100
            self.assertEqual(self.call('/operations/claims.partial_refund/execute', 'POST', json.dumps(payload).encode()).status_code, 400)
            self.assertEqual(calls, [])

    def test_claim_resolutions_require_writer_and_current_available_action(self):
        catalog = self.call('/operations').json()
        wanted = {'claims.refund', 'claims.allow_return', 'claims.open_dispute'}
        self.assertTrue(wanted.issubset({e['operation_id'] for e in catalog['operations']}))
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id, 'arguments': {'claim_id': '123456'}}
        calls = []; actions = [[{'action': 'refund'}]]; seller = ['900002']
        def send(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path == '/marketplace/v2/claims/123456':
                data = {'id': 123456, 'resource': 'order', 'resource_id': 11, 'site_id': 'MLM',
                        'players': [{'role': 'respondent', 'type': 'seller', 'user_id': seller[0], 'available_actions': actions[0]}]}
            elif path == '/marketplace/orders/11':
                data = {'id': 11, 'seller': {'id': '900001'}, 'context': {'site': 'MLM'}}
            else:
                data = {'status': 'pending'}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            self.assertEqual(self.call('/operations/claims.refund/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(calls, [])
            for operation, action, suffix in [('claims.refund', 'refund', '/expected-resolutions/refund'),
                                              ('claims.allow_return', 'allow_return', '/expected-resolutions/allow-return'),
                                              ('claims.open_dispute', 'open_dispute', '/actions/open-dispute')]:
                calls.clear(); actions[0] = [{'action': action}]
                result = self.call('/operations/' + operation + '/execute', 'POST', body)
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(calls[-1][1], '/marketplace/v2/claims/123456' + suffix)
            calls.clear(); actions[0] = []
            self.assertEqual(self.call('/operations/claims.refund/execute', 'POST', body).status_code, 409)
            self.assertEqual(len(calls), 2)
            calls.clear(); actions[0] = [{'action': 'refund'}]; seller[0] = '999999'
            self.assertEqual(self.call('/operations/claims.refund/execute', 'POST', body).status_code, 403)
            self.assertEqual(len(calls), 1)

    def test_claim_message_write_requires_writer_ownership_and_available_action(self):
        catalog = self.call('/operations').json()
        self.assertIn('claims.send_message', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'claim_id': '123456', 'receiver_role': 'complainant', 'message': 'Hello'}}
        calls = []; owner = ['900002']; actions = [[{'action': 'send_message_to_complainant'}]]
        def send(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path == '/marketplace/v2/claims/123456':
                data = {'id': 123456, 'resource': 'order', 'resource_id': 11, 'site_id': 'MLM',
                        'players': [{'role': 'respondent', 'type': 'seller', 'user_id': owner[0], 'available_actions': actions[0]}]}
            elif path == '/marketplace/orders/11':
                data = {'id': 11, 'seller': {'id': '900001'}, 'context': {'site': 'MLM'}}
            else:
                data = {'status': 'created'}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            self.assertEqual(self.call('/operations/claims.send_message/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(calls, [])
            result = self.call('/operations/claims.send_message/execute', 'POST', body)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(calls[-1][1], '/marketplace/v2/claims/123456/actions/send-message')
            self.assertEqual(json.loads(calls[-1][2]['body']), {'receiver_role': 'complainant', 'message': 'Hello', 'attachments': []})
            calls.clear(); actions[0] = []
            self.assertEqual(self.call('/operations/claims.send_message/execute', 'POST', body).status_code, 409)
            self.assertEqual(len(calls), 2)
            calls.clear(); actions[0] = [{'action': 'send_message_to_complainant'}]; owner[0] = '999999'
            self.assertEqual(self.call('/operations/claims.send_message/execute', 'POST', body).status_code, 403)
            self.assertEqual(len(calls), 1)
            calls.clear(); payload['arguments']['attachments'] = ['other.jpg']
            self.assertEqual(self.call('/operations/claims.send_message/execute', 'POST', json.dumps(payload).encode()).status_code, 400)
            self.assertEqual(calls, [])

    def test_claim_related_resources_require_claim_and_order_ownership(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        expected = {'claims.messages', 'claims.returns', 'claims.expected_resolutions', 'claims.refund_offers'}
        self.assertTrue(expected.issubset({e['operation_id'] for e in catalog['operations']}))
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'claim_id': '123456'}}
        calls = []; owner = ['900002']
        def send(method, path, **kwargs):
            calls.append(path)
            if path == '/marketplace/v2/claims/123456':
                data = {'id': 123456, 'resource': 'order', 'resource_id': 11, 'site_id': 'MLM',
                        'players': [{'role': 'respondent', 'type': 'seller', 'user_id': owner[0]}]}
            elif path == '/marketplace/orders/11':
                data = {'id': 11, 'seller': {'id': '900001'}, 'context': {'site': 'MLM'}}
            else:
                data = [] if path.endswith('/messages') else {'ok': True}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            for operation, suffix in [('claims.messages', '/messages'), ('claims.returns', '/returns'),
                                      ('claims.expected_resolutions', '/expected-resolutions'),
                                      ('claims.refund_offers', '/partial-refund/available-offers')]:
                calls.clear()
                result = self.call('/operations/' + operation + '/execute', 'POST', body, self.reader_key)
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(calls[-1], '/marketplace/v2/claims/123456' + suffix)
                self.assertEqual(len(calls), 3)
            calls.clear(); owner[0] = '999999'
            self.assertEqual(self.call('/operations/claims.messages/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 1)

    def test_fulfillment_batch_movements_validate_all_inventories_before_search(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertIn('stock.fulfillment_operations_batch', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'items': [{'item_id': 'MLM11', 'inventory_id': 'INV11'}, {'item_id': 'MLM22', 'inventory_id': 'INV22'}],
                                 'date_from': '2026-01-01', 'date_to': '2026-02-01'}}
        calls = []; foreign = [False]; foreign_result = [False]
        def send(method, path, **kwargs):
            calls.append((path, kwargs))
            if path.startswith('/marketplace/items/'):
                item_id = path.rsplit('/', 1)[-1]; suffix = item_id[-2:]
                data = {'id': item_id, 'site_id': 'MLM', 'seller_id': '999999' if foreign[0] and suffix == '22' else '900002',
                        'inventory_id': 'INV' + suffix, 'shipping': {'logistic_type': 'fulfillment'}, 'variations': []}
            else:
                data = {'results': [{'seller_id': '900002', 'inventory_id': 'INV11'}, {'seller_id': '999999' if foreign_result[0] else '900002', 'inventory_id': 'INV22'}]}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            result = self.call('/operations/stock.fulfillment_operations_batch/execute', 'POST', body, self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertIn(('inventory_id', 'INV11,INV22'), calls[-1][1]['query'])
            calls.clear(); foreign_result[0] = True
            self.assertEqual(self.call('/operations/stock.fulfillment_operations_batch/execute', 'POST', body, self.reader_key).status_code, 403)
            calls.clear(); foreign_result[0] = False
            calls.clear(); foreign[0] = True
            self.assertEqual(self.call('/operations/stock.fulfillment_operations_batch/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual([c[0] for c in calls], ['/marketplace/items/MLM11', '/marketplace/items/MLM22'])

    def test_order_shipments_use_hosted_array_without_deprecated_order_id(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertIn('orders.shipments', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'order_id': '123456'}}
        calls = []; invalid = [False]
        def send(method, path, **kwargs):
            calls.append((path, kwargs))
            if path == '/marketplace/orders/123456':
                data = {'id': 123456, 'seller': {'id': '900002'}, 'context': {'site': 'MLM'}}
            elif path == '/orders/123456/shipments':
                data = [{'id': 11, 'type': 'forward', 'untrusted': 'not returned'}, {'id': 22, 'type': 'return'}, *([{'id': '../bad', 'type': 'forward'}] if invalid[0] else [])]
            else:
                data = {'id': int(path.rsplit('/', 1)[-1]), 'source': {'site_id': 'MLM'}}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            response = self.call('/operations/orders.shipments/execute', 'POST', body, self.reader_key)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([c[0] for c in calls], ['/marketplace/orders/123456', '/orders/123456/shipments'])
            self.assertEqual(calls[1][1]['extra_headers'], {'X-New-Domain': 'true'})
            self.assertEqual(calls[1][1]['query'], [('hosted', 'true')])
            self.assertEqual(len(response.json()), 2)
            self.assertEqual(response.json()[0], {'id': 11, 'type': 'forward'})
            calls.clear(); invalid[0] = True
            self.assertEqual(self.call('/operations/orders.shipments/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 2)

    def test_fulfillment_operations_support_bounded_pagination_and_type_filter(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'item_id': 'MLM123', 'inventory_id': 'RJMD90854', 'date_from': '2026-01-01',
                                 'date_to': '2026-02-01', 'limit': 50, 'scroll': 'abc==:def', 'type': 'INBOUND_RECEPTION'}}
        payload['arguments']['shipment_id'] = '123456'
        calls = []
        def send(method, path, **kwargs):
            calls.append((path, kwargs))
            if path == '/marketplace/items/MLM123':
                data = {'id': 'MLM123', 'site_id': 'MLM', 'seller_id': '900002', 'inventory_id': 'RJMD90854',
                        'shipping': {'logistic_type': 'fulfillment'}, 'variations': []}
            else:
                data = {'results': [{'seller_id': '900002', 'inventory_id': 'RJMD90854'}], 'paging': {'scroll': 'next'}}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            result = self.call('/operations/stock.fulfillment_operations/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(calls[-1][1]['query'][-4:], [('limit', '50'), ('scroll', 'abc==:def'), ('type', 'INBOUND_RECEPTION'), ('external_references.shipment_id', '123456')])
            self.assertEqual(result.json()['paging']['scroll'], 'next')
            calls.clear(); payload['arguments']['limit'] = 1001
            self.assertEqual(self.call('/operations/stock.fulfillment_operations/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])

    def test_fulfillment_operation_detail_checks_item_inventory_and_result(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertIn('stock.fulfillment_operation_read', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'item_id': 'MLM123', 'inventory_id': 'RJMD90854', 'operation_id': '123456'}}
        calls = []; owner = ['900002']; inventory = ['RJMD90854']; op_id = ['123456']
        def send(method, path, **kwargs):
            calls.append((path, kwargs))
            if path == '/marketplace/items/MLM123':
                data = {'id': 'MLM123', 'site_id': 'MLM', 'seller_id': '900002', 'inventory_id': 'RJMD90854',
                        'shipping': {'logistic_type': 'fulfillment'}, 'variations': []}
            else:
                data = {'id': op_id[0], 'seller_id': owner[0], 'inventory_id': inventory[0], 'type': 'SALE_CONFIRMATION'}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            result = self.call('/operations/stock.fulfillment_operation_read/execute', 'POST', body, self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual([c[0] for c in calls], ['/marketplace/items/MLM123', '/marketplace/stock/fulfillment/operations/123456'])
            self.assertEqual(calls[-1][1]['query'], [('seller_id', '900002')])
            calls.clear(); owner[0] = '999999'
            self.assertEqual(self.call('/operations/stock.fulfillment_operation_read/execute', 'POST', body, self.reader_key).status_code, 403)
            calls.clear(); owner[0] = '900002'; inventory[0] = 'OTHER123'
            self.assertEqual(self.call('/operations/stock.fulfillment_operation_read/execute', 'POST', body, self.reader_key).status_code, 403)
            calls.clear(); inventory[0] = 'RJMD90854'; op_id[0] = '123457'
            self.assertEqual(self.call('/operations/stock.fulfillment_operation_read/execute', 'POST', body, self.reader_key).status_code, 403)

    def test_fulfillment_operations_checks_inventory_ownership_and_date_range(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertIn('stock.fulfillment_operations', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'item_id': 'MLM123', 'inventory_id': 'RJMD90854', 'date_from': '2026-01-01', 'date_to': '2026-02-01'}}
        calls = []; seller = ['900002']
        def send(method, path, **kwargs):
            calls.append((path, kwargs))
            if path == '/marketplace/items/MLM123':
                data = {'id': 'MLM123', 'site_id': 'MLM', 'seller_id': '900002', 'inventory_id': 'RJMD90854',
                        'shipping': {'logistic_type': 'fulfillment'}, 'variations': []}
            else:
                data = {'results': [{'seller_id': seller[0], 'inventory_id': 'RJMD90854'}], 'paging': {'total': 1}}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            result = self.call('/operations/stock.fulfillment_operations/execute', 'POST', body, self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(calls[-1][0], '/marketplace/stock/fulfillment/operations/search')
            self.assertEqual(calls[-1][1]['query'], [('seller_id', '900002'), ('inventory_id', 'RJMD90854'), ('date_from', '2026-01-01'), ('date_to', '2026-02-01')])
            calls.clear(); seller[0] = '999999'
            self.assertEqual(self.call('/operations/stock.fulfillment_operations/execute', 'POST', body, self.reader_key).status_code, 403)
            calls.clear(); payload['arguments']['date_to'] = '2026-04-01'
            self.assertEqual(self.call('/operations/stock.fulfillment_operations/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])
            payload['arguments']['date_to'] = 'invalid'
            self.assertEqual(self.call('/operations/stock.fulfillment_operations/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])

    def test_fulfillment_stock_requires_item_inventory_and_seller_binding(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertIn('stock.fulfillment', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'item_id': 'MLM123', 'inventory_id': 'RJMD90854'}}
        calls = []; inventory = ['RJMD90854']; seller = ['900002']; variations = [[]]
        def send(method, path, **kwargs):
            calls.append((path, kwargs))
            if path == '/marketplace/items/MLM123':
                data = {'id': 'MLM123', 'site_id': 'MLM', 'seller_id': seller[0], 'inventory_id': inventory[0],
                        'shipping': {'logistic_type': 'fulfillment'}, 'variations': variations[0]}
            else:
                data = {'inventory_id': 'RJMD90854', 'total': 20}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            result = self.call('/operations/stock.fulfillment/execute', 'POST', body, self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual([c[0] for c in calls], ['/marketplace/items/MLM123', '/marketplace/inventories/RJMD90854/stock/fulfillment'])
            self.assertEqual(calls[-1][1]['query'], [('seller_id', '900002')])
            calls.clear(); inventory[0] = None; variations[0] = [{'inventory_id': 'RJMD90854'}]
            payload['arguments']['include_conditions'] = True
            self.assertEqual(self.call('/operations/stock.fulfillment/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 200)
            self.assertEqual(calls[-1][1]['query'], [('seller_id', '900002'), ('include_attributes', 'conditions')])
            calls.clear(); variations[0] = []; payload['arguments'].pop('include_conditions')
            calls.clear(); inventory[0] = 'OTHER123'
            self.assertEqual(self.call('/operations/stock.fulfillment/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 1)
            calls.clear(); inventory[0] = 'RJMD90854'; seller[0] = '999999'
            self.assertEqual(self.call('/operations/stock.fulfillment/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 1)
            calls.clear(); payload['arguments']['seller_id'] = '999999'
            self.assertEqual(self.call('/operations/stock.fulfillment/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])

    def test_claim_search_returns_only_individually_verified_claim_snapshots(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertIn('claims.search', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'status': 'opened', 'limit': 2}}
        calls = []; owner = ['900002']
        def send(method, path, **kwargs):
            calls.append((path, kwargs))
            if path == '/marketplace/v2/claims/search':
                data = {'paging': {'total': 50}, 'data': [{'id': 123, 'untrusted': 'do not return'}]}
            elif path == '/marketplace/v2/claims/123':
                data = {'id': 123, 'resource': 'order', 'resource_id': 11, 'site_id': 'MLM',
                        'players': [{'role': 'respondent', 'type': 'seller', 'user_id': owner[0]}]}
            else:
                data = {'id': 11, 'seller': {'id': '900001'}, 'context': {'site': 'MLM'}}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            result = self.call('/operations/claims.search/execute', 'POST', body, self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(calls[0][1]['query'][:3], [('user_id', '900002'), ('site_id', 'MLM'), ('resource', 'order')])
            self.assertEqual([c[0] for c in calls], ['/marketplace/v2/claims/search', '/marketplace/v2/claims/123', '/marketplace/orders/11'])
            self.assertNotIn('paging', result.json())
            self.assertNotIn('untrusted', result.json()['data'][0])
            calls.clear(); owner[0] = '999999'
            self.assertEqual(self.call('/operations/claims.search/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 2)

    def test_seller_profile_and_marketplace_mapping_are_scoped_to_shop(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertTrue({'users.self', 'users.marketplace_mapping'}.issubset({e['operation_id'] for e in catalog['operations']}))
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id, 'arguments': {}}
        calls = []; user_id = ['900001']
        def send(method, path, **kwargs):
            calls.append(path)
            if path == '/users/me':
                data = {'id': user_id[0], 'nickname': 'Seller'}
            else:
                data = {'user_id': '900001', 'site_id': 'CBT', 'marketplaces': [
                    {'user_id': '900002', 'site_id': 'MLM', 'logistic_type': 'fulfillment'},
                    {'user_id': '999999', 'site_id': 'MLB', 'logistic_type': 'remote'}]}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            self.assertEqual(self.call('/operations/users.self/execute', 'POST', body, self.reader_key).status_code, 200)
            self.assertEqual(calls, ['/users/me'])
            calls.clear(); user_id[0] = '999999'
            self.assertEqual(self.call('/operations/users.self/execute', 'POST', body, self.reader_key).status_code, 403)
            calls.clear(); user_id[0] = '900001'
            result = self.call('/operations/users.marketplace_mapping/execute', 'POST', body, self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(calls, ['/marketplace/users/900001'])
            self.assertEqual(len(result.json()['marketplaces']), 1)
            self.assertEqual(result.json()['marketplaces'][0]['user_id'], '900002')
            calls.clear(); payload['arguments'] = {'user_id': '999999'}
            self.assertEqual(self.call('/operations/users.marketplace_mapping/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])

    def test_shipment_orders_reject_mixed_shop_orders(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertIn('shipments.orders', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'shipment_id': '123456'}}
        calls = []; owner = ['900002']
        def send(method, path, **kwargs):
            calls.append((path, kwargs))
            if path == '/marketplace/shipments/123456':
                data = {'id': 123456, 'origin': {'sender_id': '900002'}, 'source': {'site_id': 'MLM'}}
            elif path == '/shipments/123456/orders':
                data = [{'order_id': '11', 'seller_id': '900002', 'item_id': 'MLM123'},
                        {'order_id': '22', 'seller_id': owner[0], 'item_id': 'MLM124'}]
            else:
                data = {'id': int(path.rsplit('/', 1)[-1]), 'seller': {'id': owner[0]}, 'context': {'site': 'MLM'}}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            result = self.call('/operations/shipments.orders/execute', 'POST', body, self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(len(result.json()), 2)
            self.assertEqual(calls[1][1]['extra_headers'], {'X-New-Domain': 'true'})
            calls.clear(); owner[0] = '999999'
            self.assertEqual(self.call('/operations/shipments.orders/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual([c[0] for c in calls], ['/marketplace/shipments/123456', '/shipments/123456/orders'])

    def test_registered_picture_upload_authenticates_and_forwards_only_file(self):
        catalog = self.call('/operations').json()
        self.assertIn('pictures.upload', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id, 'arguments': {}}
        def multipart(request_data, extra=b''):
            return (b'--BFF\r\nContent-Disposition: form-data; name="request"\r\n\r\n' + json.dumps(request_data).encode() +
                    b'\r\n--BFF\r\nContent-Disposition: form-data; name="file"; filename="a.jpg"\r\nContent-Type: image/jpeg\r\n\r\nimagebytes\r\n' + extra + b'--BFF--\r\n')
        calls = []
        def send(method, path, **kwargs):
            calls.append((method, path, kwargs))
            return client.PlatformResponse(201, [('Content-Type', 'application/json')], io.BytesIO(b'{"id":"pic"}'))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = multipart(payload)
            ct = 'multipart/form-data; boundary=BFF'
            self.assertEqual(self.call('/operations/pictures.upload/execute', 'POST', body, self.reader_key, ct).status_code, 403)
            self.assertEqual(calls, [])
            result = self.call('/operations/pictures.upload/execute', 'POST', body, self.writer_key, ct)
            self.assertEqual(result.status_code, 201, result.text)
            self.assertEqual(calls[-1][1], '/pictures/items/upload')
            self.assertIn(b'imagebytes', calls[-1][2]['body'])
            self.assertNotIn(b'contract_version', calls[-1][2]['body'])
            calls.clear(); payload['shop_id'] = self.other.id
            self.assertEqual(self.call('/operations/pictures.upload/execute', 'POST', multipart(payload), self.writer_key, ct).status_code, 403)
            self.assertEqual(calls, [])
            payload['shop_id'] = self.store.id; payload['contract_version'] = 'outdated'
            self.assertEqual(self.call('/operations/pictures.upload/execute', 'POST', multipart(payload), self.writer_key, ct).status_code, 409)
            self.assertEqual(calls, [])
            payload['contract_version'] = catalog['contract_version']
            from .. import controllers
            with patch.object(controllers, 'MAX_PICTURE', 3):
                self.assertEqual(self.call('/operations/pictures.upload/execute', 'POST', multipart(payload), self.writer_key, ct).status_code, 413)
            self.assertEqual(calls, [])
            self.assertEqual(self.call('/operations/pictures.upload/execute', 'POST', multipart(payload).replace(b'image/jpeg', b'text/plain'), self.writer_key, ct).status_code, 400)
            duplicate = b'--BFF\r\nContent-Disposition: form-data; name="other"\r\n\r\nnot allowed\r\n'
            self.assertEqual(self.call('/operations/pictures.upload/execute', 'POST', duplicate + multipart(payload), self.writer_key, ct).status_code, 400)
            self.assertEqual(calls, [])

    def test_custom_tracking_writes_require_writer_ownership_and_logistics_mode(self):
        catalog = self.call('/operations').json()
        self.assertTrue({'shipments.tracking_update', 'shipments.tracking_status'}.issubset({e['operation_id'] for e in catalog['operations']}))
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'shipment_id': '123456', 'tracking_id': 'ABC123', 'tracking_url': 'https://carrier.example/ABC123', 'carrier': 'Carrier'}}
        calls = []; seller = ['900002']; mode = ['me1']; kind = ['default']
        def send(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path == '/marketplace/shipments/123456':
                data = {'id': 123456, 'origin': {'sender_id': seller[0]}, 'source': {'site_id': 'MLM'}, 'logistic': {'mode': mode[0], 'type': kind[0]}}
            else:
                data = {'status': 'success'}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            self.assertEqual(self.call('/operations/shipments.tracking_update/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(calls, [])
            self.assertEqual(self.call('/operations/shipments.tracking_update/execute', 'POST', body).status_code, 200)
            self.assertEqual([c[1] for c in calls], ['/marketplace/shipments/123456', '/marketplace/shipments/123456/tracking'])
            self.assertEqual(json.loads(calls[-1][2]['body']), {'tracking_id': 'ABC123', 'tracking_url': 'https://carrier.example/ABC123', 'carrier': 'Carrier'})
            calls.clear(); mode[0] = 'me2'
            self.assertEqual(self.call('/operations/shipments.tracking_update/execute', 'POST', body).status_code, 409)
            self.assertEqual(len(calls), 1)
            calls.clear(); mode[0] = 'me1'; seller[0] = '999999'
            self.assertEqual(self.call('/operations/shipments.tracking_update/execute', 'POST', body).status_code, 403)
            self.assertEqual(len(calls), 1)
            calls.clear(); seller[0] = '900002'; payload['arguments'] = {'shipment_id': '123456', 'tracking_id': 'ABC123', 'status': 'delivered'}
            self.assertEqual(self.call('/operations/shipments.tracking_status/execute', 'POST', json.dumps(payload).encode()).status_code, 200)
            self.assertEqual(calls[-1][1], '/marketplace/shipments/123456/tracking/status')
            calls.clear(); payload['arguments']['status'] = 'unknown'
            self.assertEqual(self.call('/operations/shipments.tracking_status/execute', 'POST', json.dumps(payload).encode()).status_code, 400)
            self.assertEqual(calls, [])

    def test_category_reference_endpoints_validate_ids_and_query(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertTrue({'categories.attributes', 'categories.input_specs', 'domains.technical_specs', 'domains.search'}.issubset({e['operation_id'] for e in catalog['operations']}))
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'category_id': 'CBT1055'}}
        calls = []
        def send(method, path, **kwargs):
            calls.append((path, kwargs))
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(b'{"ok": true}'))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            for operation, path in [('categories.attributes', '/categories/CBT1055/attributes'),
                                    ('categories.input_specs', '/categories/CBT1055/technical_specs/input')]:
                result = self.call('/operations/' + operation + '/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(calls[-1][0], path)
            payload['arguments'] = {'domain_id': 'CBT-T_SHIRTS'}
            self.assertEqual(self.call('/operations/domains.technical_specs/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 200)
            self.assertEqual(calls[-1][0], '/domains/CBT-T_SHIRTS/technical_specs')
            payload['arguments'] = {'query': 'vestido de mujer'}
            self.assertEqual(self.call('/operations/domains.search/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 200)
            self.assertEqual(calls[-1][0], '/marketplace/domain_discovery/search')
            self.assertEqual(calls[-1][1]['query'], [('q', 'vestido de mujer')])
            calls.clear(); payload['arguments'] = {'category_id': 'CBT1055/../../users'}
            self.assertEqual(self.call('/operations/categories.attributes/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])

    def test_order_search_verifies_every_nested_order_before_returning_page(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertIn('orders.search', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'limit': 2, 'offset': 0}}
        calls = []; owners = {'11': '900002', '22': '900001'}
        def send(method, path, **kwargs):
            calls.append((path, kwargs))
            if path == '/marketplace/orders/search':
                data = {'paging': {'total': 100}, 'results': [{'id': 111, 'orders': [{'id': 11}, {'id': 22}]}]}
            else:
                order_id = path.rsplit('/', 1)[-1]
                data = {'id': int(order_id), 'seller': {'id': owners[order_id]}, 'context': {'site': 'MLM'}}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            response = self.call('/operations/orders.search/execute', 'POST', body, self.reader_key)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(calls[0][1]['query'][:2], [('seller.id', '900002'), ('site', 'MLM')])
            self.assertEqual([c[0] for c in calls], ['/marketplace/orders/search', '/marketplace/orders/11', '/marketplace/orders/22'])
            self.assertNotIn('paging', response.json())
            self.assertEqual(len(response.json()['results']), 1)
            calls.clear(); owners['22'] = '999999'
            self.assertEqual(self.call('/operations/orders.search/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 3)
            calls.clear(); payload['arguments']['seller.id'] = '999999'
            self.assertEqual(self.call('/operations/orders.search/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])

    def test_size_equivalences_and_claim_reasons_use_checked_arguments(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertTrue({'size_chart.equivalences', 'claims.reason'}.issubset({e['operation_id'] for e in catalog['operations']}))
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'domain_id': 'T_SHIRTS', 'gender': 'Woman'}}
        calls = []
        def send(method, path, **kwargs):
            calls.append((path, kwargs))
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(b'{"ok": true}'))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            result = self.call('/operations/size_chart.equivalences/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(calls[-1][0], '/marketplace/sizechart/equivalences')
            self.assertEqual(calls[-1][1]['query'], [('site_id', 'MLM'), ('domain_id', 'T_SHIRTS'), ('gender', 'Woman')])
            calls.clear(); payload['arguments']['site_id'] = 'MLB'
            self.assertEqual(self.call('/operations/size_chart.equivalences/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])
            payload['arguments'] = {'reason_id': 'PNR9502'}
            self.assertEqual(self.call('/operations/claims.reason/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 200)
            self.assertEqual(calls[-1][0], '/marketplace/v2/claims/reasons/PNR9502')
            calls.clear(); payload['arguments'] = {'reason_id': '../other'}
            self.assertEqual(self.call('/operations/claims.reason/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])

    def test_reference_data_is_bound_to_shop_and_rejects_caller_paths(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertTrue({'payments.methods', 'payments.method', 'size_chart.active_domains'}.issubset({e['operation_id'] for e in catalog['operations']}))
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id, 'arguments': {}}
        calls = []
        def send(method, path, **kwargs):
            calls.append(path)
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(b'{"ok": true}'))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            self.assertEqual(self.call('/operations/payments.methods/execute', 'POST', body, self.reader_key).status_code, 200)
            self.assertEqual(calls[-1], '/sites/MLM/payment_methods')
            self.assertEqual(self.call('/operations/size_chart.active_domains/execute', 'POST', body, self.reader_key).status_code, 200)
            self.assertEqual(calls[-1], '/catalog/charts/CBT/configurations/active_domains')
            payload['arguments'] = {'payment_method': 'visa'}
            self.assertEqual(self.call('/operations/payments.method/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 200)
            self.assertEqual(calls[-1], '/sites/MLM/payment_methods/visa')
            calls.clear(); payload['arguments']['payment_method'] = '../other'
            self.assertEqual(self.call('/operations/payments.method/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])
            payload['arguments'] = {'site_id': 'MLB'}
            self.assertEqual(self.call('/operations/payments.methods/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])
            payload['arguments'] = {}; payload['shop_id'] = self.other.id
            self.assertEqual(self.call('/operations/size_chart.active_domains/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 403)
            self.assertEqual(calls, [])

    def test_item_performance_and_reviews_require_item_ownership(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertTrue({'items.performance', 'items.reviews'}.issubset({e['operation_id'] for e in catalog['operations']}))
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'item_id': 'MLM123'}}
        calls = []; seller = ['900002']
        def send(method, path, **kwargs):
            calls.append((path, kwargs))
            data = {'id': 'MLM123', 'seller_id': seller[0], 'site_id': 'MLM'} if path == '/marketplace/items/MLM123' else {'result': 'ok'}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            response = self.call('/operations/items.performance/execute', 'POST', body, self.reader_key)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([c[0] for c in calls], ['/marketplace/items/MLM123', '/item/MLM123/performance'])
            calls.clear(); payload['arguments'].update({'offset': 10, 'limit': 5})
            response = self.call('/operations/items.reviews/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(calls[-1][0], '/reviews/item/MLM123')
            self.assertEqual(calls[-1][1]['query'], [('limit', '5'), ('offset', '10')])
            calls.clear(); seller[0] = '999999'
            self.assertEqual(self.call('/operations/items.reviews/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 1)
            calls.clear(); payload['arguments']['item_id'] = '../other'
            self.assertEqual(self.call('/operations/items.reviews/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])

    def test_item_promotions_bind_seller_and_check_item_ownership(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertIn('promotions.item_candidates', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'item_id': 'MLM123'}}
        calls = []; seller = ['900002']
        def send(method, path, **kwargs):
            calls.append((path, kwargs))
            data = {'id': 'MLM123', 'seller_id': seller[0], 'site_id': 'MLM'} if path == '/marketplace/items/MLM123' else {'promotions': []}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            result = self.call('/operations/promotions.item_candidates/execute', 'POST', body, self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(calls[-1][0], '/marketplace/seller-promotions/items/MLM123')
            self.assertEqual(calls[-1][1]['query'], [('user_id', '900002')])
            self.assertEqual(calls[-1][1]['extra_headers'], {'version': 'v2'})
            calls.clear(); seller[0] = '999999'
            self.assertEqual(self.call('/operations/promotions.item_candidates/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 1)
            calls.clear(); payload['arguments']['user_id'] = '999999'
            self.assertEqual(self.call('/operations/promotions.item_candidates/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])

    def test_item_shipping_cost_requires_shop_item_ownership(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertIn('shipments.item_cost', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id, 'arguments': {'item_id': 'MLM123'}}
        calls = []; seller = ['900002']
        def send(method, path, **kwargs):
            calls.append(path)
            data = {'id': 'MLM123', 'seller_id': seller[0], 'site_id': 'MLM'} if path == '/marketplace/items/MLM123' else {'shipping_fee': 20, 'site_id': 'MLM'}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            result = self.call('/operations/shipments.item_cost/execute', 'POST', body, self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(calls, ['/marketplace/items/MLM123', '/marketplace/items/MLM123/shipping_options/cost'])
            calls.clear(); seller[0] = '999999'
            self.assertEqual(self.call('/operations/shipments.item_cost/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(calls, ['/marketplace/items/MLM123'])

    def test_shipment_label_and_order_billing_require_resource_ownership(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertTrue({'shipments.label', 'orders.billing_info'}.issubset({e['operation_id'] for e in catalog['operations']}))
        owner = ['900002']; calls = []
        def send(method, path, **kwargs):
            calls.append((path, kwargs))
            if path == '/marketplace/shipments/123456':
                data = {'id': 123456, 'origin': {'sender_id': owner[0]}, 'source': {'site_id': 'MLM'}}
            elif path == '/marketplace/orders/123456':
                data = {'id': 123456, 'seller': {'id': owner[0]}, 'context': {'site': 'MLM'}}
            elif path.endswith('/labels'):
                return client.PlatformResponse(200, [('Content-Type', 'application/pdf')], io.BytesIO(b'%PDF-test'))
            else:
                data = {'site_id': 'MLM', 'buyer': {'billing_info': {'name': 'Example'}}}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                       'arguments': {'shipment_id': '123456'}}
            response = self.call('/operations/shipments.label/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.content, b'%PDF-test')
            self.assertEqual([c[0] for c in calls], ['/marketplace/shipments/123456', '/marketplace/shipments/123456/labels'])
            calls.clear(); payload['arguments'] = {'order_id': '123456'}
            response = self.call('/operations/orders.billing_info/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([c[0] for c in calls], ['/marketplace/orders/123456', '/marketplace/orders/123456/billing_info'])
            calls.clear(); owner[0] = '999999'
            self.assertEqual(self.call('/operations/orders.billing_info/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 1)
            calls.clear(); payload['arguments'] = {'shipment_id': '123456'}
            self.assertEqual(self.call('/operations/shipments.label/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 1)

    def test_registered_pack_checks_every_contained_order(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertIn('packs.read', {entry['operation_id'] for entry in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'pack_id': '123456'}}
        owners = {'11': '900002', '22': '900001'}
        calls = []
        def send(method, path, **kwargs):
            calls.append(path)
            if path == '/marketplace/orders/pack/123456':
                data = {'id': 123456, 'orders': [{'id': 11}, {'id': 22}]}
            else:
                order_id = path.rsplit('/', 1)[-1]
                data = {'id': int(order_id), 'seller': {'id': owners[order_id]}, 'context': {'site': 'MLM'}}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            result = self.call('/operations/packs.read/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(calls, ['/marketplace/orders/pack/123456', '/marketplace/orders/11', '/marketplace/orders/22'])
            calls.clear(); owners['22'] = '999999'
            result = self.call('/operations/packs.read/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(result.status_code, 403)
            self.assertEqual(calls, ['/marketplace/orders/pack/123456', '/marketplace/orders/11', '/marketplace/orders/22'])
            calls.clear(); payload['shop_id'] = self.other.id
            self.assertEqual(self.call('/operations/packs.read/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 403)
            self.assertEqual(calls, [])

    def test_questions_are_bound_to_shop_and_item_before_read_or_write(self):
        catalog = self.call('/operations').json()
        self.assertTrue({'questions.read', 'questions.answer', 'questions.delete'}.issubset({e['operation_id'] for e in catalog['operations']}))
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'question_id': '123456', 'text': 'Hello'}}
        calls = []
        owner = ['900002']; item_owner = ['900001']
        def send(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path == '/marketplace/questions/123456':
                data = {'id': 123456, 'seller_id': owner[0], 'item_id': 'CBT123', 'site_id': 'MLM'}
            elif path == '/items/CBT123':
                data = {'id': 'CBT123', 'seller_id': item_owner[0]}
            else:
                data = {'ok': True}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            self.assertEqual(self.call('/operations/questions.answer/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(calls, [])
            self.assertEqual(self.call('/operations/questions.answer/execute', 'POST', body).status_code, 200)
            self.assertEqual([c[1] for c in calls], ['/marketplace/questions/123456', '/items/CBT123', '/marketplace/answers'])
            self.assertEqual(json.loads(calls[-1][2]['body']), {'question_id': 123456, 'text': 'Hello'})
            calls.clear(); item_owner[0] = '999999'
            self.assertEqual(self.call('/operations/questions.answer/execute', 'POST', body).status_code, 403)
            self.assertEqual(len(calls), 2)
            calls.clear(); item_owner[0] = '900001'; owner[0] = '999999'
            self.assertEqual(self.call('/operations/questions.answer/execute', 'POST', body).status_code, 403)
            self.assertEqual(len(calls), 1)
            calls.clear(); owner[0] = '900002'
            payload['arguments'] = {'question_id': '123456'}
            result = self.call('/operations/questions.read/execute', 'POST', json.dumps(payload).encode(), self.reader_key)
            self.assertEqual(result.status_code, 200)
            self.assertEqual([c[1] for c in calls], ['/marketplace/questions/123456', '/items/CBT123'])
            calls.clear(); payload['shop_id'] = self.other.id
            self.assertEqual(self.call('/operations/questions.delete/execute', 'POST', json.dumps(payload).encode()).status_code, 403)
            self.assertEqual(calls, [])

    def test_pack_messages_require_pack_and_order_ownership(self):
        catalog = self.call('/operations').json()
        self.assertTrue({'messages.read_pack', 'messages.send_pack'}.issubset({e['operation_id'] for e in catalog['operations']}))
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'pack_id': '123456', 'text': 'Hello'}}
        calls = []; owner = ['900002']
        def send(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path == '/marketplace/orders/pack/123456': data = {'id': 123456, 'orders': [{'id': 11}]}
            elif path == '/marketplace/orders/11': data = {'id': 11, 'seller': {'id': owner[0]}, 'context': {'site': 'MLM'}}
            else: data = {'messages': []}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            self.assertEqual(self.call('/operations/messages.send_pack/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(calls, [])
            self.assertEqual(self.call('/operations/messages.send_pack/execute', 'POST', body).status_code, 200)
            self.assertEqual([x[1] for x in calls], ['/marketplace/orders/pack/123456', '/marketplace/orders/11', '/marketplace/messages/packs/123456'])
            self.assertEqual(json.loads(calls[-1][2]['body']), {'text': 'Hello'})
            calls.clear(); owner[0] = '999999'
            self.assertEqual(self.call('/operations/messages.send_pack/execute', 'POST', body).status_code, 403)
            self.assertEqual(len(calls), 2)
            calls.clear(); owner[0] = '900002'; payload['arguments'] = {'pack_id': '123456', 'limit': 2, 'offset': 0}
            self.assertEqual(self.call('/operations/messages.read_pack/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 200)
            self.assertEqual(calls[-1][2]['query'], [('limit', '2'), ('offset', '0')])
            calls.clear(); payload['arguments']['seller_id'] = '999999'
            self.assertEqual(self.call('/operations/messages.read_pack/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])

    def test_question_search_is_scoped_and_never_leaks_other_shops(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        self.assertIn('questions.search', {e['operation_id'] for e in catalog['operations']})
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'limit': 2, 'offset': 0}}
        calls = []; owner = ['900002']; site = ['MLM']
        def send(method, path, **kwargs):
            calls.append((path, kwargs))
            if path == '/marketplace/questions/search':
                data = {'total': 99, 'questions': [{'id': 9, 'seller_id': owner[0], 'item_id': 'CBT123', 'site_id': site[0]}]}
            else:
                data = {'id': 'CBT123', 'seller_id': '900001'}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            response = self.call('/operations/questions.search/execute', 'POST', body, self.reader_key)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(('seller_id', '900002'), calls[0][1]['query'])
            self.assertNotIn('total', response.json())
            self.assertEqual(len(response.json()['questions']), 1)
            calls.clear(); owner[0] = '999999'
            self.assertEqual(self.call('/operations/questions.search/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 1)
            calls.clear(); owner[0] = '900002'; site[0] = 'MLB'
            self.assertEqual(self.call('/operations/questions.search/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 1)
            calls.clear(); payload['arguments']['seller_id'] = '999999'
            self.assertEqual(self.call('/operations/questions.search/execute', 'POST', json.dumps(payload).encode(), self.reader_key).status_code, 400)
            self.assertEqual(calls, [])

    def test_claim_reads_require_claim_and_order_ownership(self):
        catalog = self.call('/operations', key=self.reader_key).json()
        wanted = {'claims.read', 'claims.detail', 'claims.actions_history', 'claims.status_history', 'claims.affects_reputation'}
        self.assertTrue(wanted.issubset({e['operation_id'] for e in catalog['operations']}))
        payload = {'contract_version': catalog['contract_version'], 'shop_id': self.store.id,
                   'arguments': {'claim_id': '123456'}}
        calls = []; respondent = ['900002']; owner = ['900001']; resource = ['order']
        def send(method, path, **kwargs):
            calls.append(path)
            if path == '/marketplace/v2/claims/123456':
                data = {'id': 123456, 'resource': resource[0], 'resource_id': 11, 'site_id': 'MLM',
                        'players': [{'role': 'respondent', 'type': 'seller', 'user_id': respondent[0]}]}
            elif path == '/marketplace/orders/11':
                data = {'id': 11, 'seller': {'id': owner[0]}, 'context': {'site': 'MLM'}}
            else:
                data = {'detail': 'verified'}
            return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=send):
            body = json.dumps(payload).encode()
            response = self.call('/operations/claims.read/execute', 'POST', body, self.reader_key)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(calls, ['/marketplace/v2/claims/123456', '/marketplace/orders/11'])
            self.assertEqual(response.json()['resource'], 'order')
            calls.clear()
            response = self.call('/operations/claims.detail/execute', 'POST', body, self.reader_key)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(calls[-1], '/marketplace/v2/claims/123456/detail')
            calls.clear(); respondent[0] = '999999'
            self.assertEqual(self.call('/operations/claims.detail/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 1)
            calls.clear(); respondent[0] = '900002'; owner[0] = '999999'
            self.assertEqual(self.call('/operations/claims.detail/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 2)
            calls.clear(); owner[0] = '900001'; resource[0] = 'payment'
            self.assertEqual(self.call('/operations/claims.detail/execute', 'POST', body, self.reader_key).status_code, 403)
            self.assertEqual(len(calls), 1)

    def test_authentication_and_status(self):
        for key in [False, 'invalid']:
            response = self.call('/shops', key=key)
            self.assertEqual(response.status_code, 401)
            self.assertFalse(response.json()['success'])
        response = self.call('/shops', key=self.reader_key)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('access_token', response.text)
        self.assertNotIn('refresh_token', response.text)
        self.assertIn(self.store.id, [s['id'] for s in response.json()['shops']])
        self.assertNotIn(self.other.id, [s['id'] for s in response.json()['shops']])

    def test_permissions_and_path_identity_before_upstream(self):
        with patch.object(client, 'send', side_effect=AssertionError('unauthorized upstream call')):
            self.assertEqual(self.call('/shops/%s/items' % self.store.id, 'POST', b'{}', self.reader_key).status_code, 403)
            self.assertEqual(self.call('/shops/%s' % self.other.id).status_code, 403)
            self.assertEqual(self.call('/shops/%s/items?shopId=%s' % (self.store.id, self.other.id)).status_code, 403)
            self.assertEqual(self.call('/unimplemented').status_code, 404)

    def fake_send(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        data = {'seller_id': self.seller, 'id': 'CBT123'}
        if path == '/users/me':
            data = {'id': '900001', 'tags': self.tags}
        if path == '/pictures/items/upload':
            return client.PlatformResponse(201, [('Content-Type', 'application/json')], io.BytesIO(b'{"id":"pic"}'))
        return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))

    def test_multipart_and_description_keep_bound_shop(self):
        self.calls, self.seller, self.tags = [], 900001, []
        body = b'--BFF\r\nContent-Disposition: form-data; name="file"; filename="a.jpg"\r\nContent-Type: image/jpeg\r\n\r\n\xff\x00\xfe\r\n--BFF--\r\n'
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=self.fake_send):
            response = self.call('/shops/%s/pictures' % self.store.id, 'POST', body, content_type='multipart/form-data; boundary=BFF')
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(self.calls[-1][2]['body'], body)
            self.assertEqual(self.calls[-1][2]['content_type'], 'multipart/form-data; boundary=BFF')
            response = self.call('/shops/%s/items/CBT123/description' % self.store.id, 'PUT', b'{"plain_text":"Lamp"}')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.calls[-1][1], '/global/items/CBT123')
            self.assertEqual(json.loads(self.calls[-1][2]['body']), {'site_id': 'MLM', 'logistic_type': 'fulfillment', 'description': {'plain_text': 'Lamp'}})

    def test_foreign_seller_and_user_products_are_rejected(self):
        self.calls, self.seller, self.tags = [], 999999, ['user_products_seller']
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=self.fake_send):
            response = self.call('/shops/%s/items/CBT123' % self.store.id, 'PUT', b'{"price":12}')
            self.assertEqual(response.status_code, 403)
            self.assertEqual(len(self.calls), 1)
            response = self.call('/shops/%s/items' % self.store.id, 'POST', b'{"sites_to_sell":[{"site_id":"MLM","logistic_type":"fulfillment"}]}')
            self.assertEqual(response.status_code, 409)
            self.assertEqual(len(self.calls), 2)

    def test_revoked_inactive_and_cookie_only_authentication(self):
        self.reader.active = False
        self.assertEqual(self.call('/shops', key=self.reader_key).status_code, 401)
        self.env['res.users.apikeys'].search([('user_id', '=', self.writer.id)]).unlink()
        self.assertEqual(self.call('/shops').status_code, 401)
        self.authenticate('admin', 'admin')
        self.assertEqual(self.call('/shops', key=False).status_code, 401)

    def test_body_and_image_limits_and_form_identity(self):
        from .. import ir_http, controllers
        body = b'--BFF\r\nContent-Disposition: form-data; name="file"; filename="a.jpg"\r\n\r\n1234\r\n--BFF--\r\n'
        with patch.object(client, 'send', side_effect=AssertionError('invalid upload reached platform')):
            with patch.object(ir_http, 'MAX_BODY', 16):
                self.assertEqual(self.call('/shops/%s/items' % self.store.id, 'POST', b'x' * 17).status_code, 413)
            with patch.object(controllers, 'MAX_PICTURE', 3):
                self.assertEqual(self.call('/shops/%s/pictures' % self.store.id, 'POST', body, content_type='multipart/form-data; boundary=BFF').status_code, 413)
            form = b'--BFF\r\nContent-Disposition: form-data; name="shopId"\r\n\r\n999\r\n' + body
            self.assertEqual(self.call('/shops/%s/pictures' % self.store.id, 'POST', form, content_type='multipart/form-data; boundary=BFF').status_code, 400)

    def test_upstream_status_and_network_errors_are_safe(self):
        from ..protocol import BffError
        with patch.object(type(self.store), '_token', return_value='test-token'):
            with patch.object(client, 'send', return_value=client.PlatformResponse(429, [('Content-Type', 'application/json'), ('Retry-After', '10')], io.BytesIO(b'{"error":"rate_limit"}'))):
                response = self.call('/shops/%s/items' % self.store.id)
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.content, b'{"error":"rate_limit"}')
                self.assertEqual(response.headers['Retry-After'], '10')
            with patch.object(client, 'send', side_effect=BffError(502, 'Mercado Libre request failed or timed out')):
                response = self.call('/shops/%s/items' % self.store.id)
                self.assertEqual(response.status_code, 502)
                self.assertNotIn('test-token', response.text)

    def test_access_log_omits_query_values(self):
        with self.assertLogs('werkzeug', level='INFO') as logs:
            response = self.call('/shops?access_token=should-never-be-logged')
            self.assertEqual(response.status_code, 400)
        self.assertNotIn('should-never-be-logged', '\n'.join(logs.output))

    def test_encoded_separator_is_rejected_before_platform(self):
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', return_value=client.PlatformResponse(200, [], io.BytesIO(b'{"seller_id":900001}'))):
            response = self.call('/shops/%s/items/CBT123%%2fdescription' % self.store.id)
            self.assertEqual(response.status_code, 404)

    def test_raw_query_survives_http_and_bff_selector_removal(self):
        from .test_client import Handler
        from http.server import ThreadingHTTPServer
        import threading
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.received = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.cr.flush()
        self.cr.clear()
        with self.allow_requests(), patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'API', 'http://127.0.0.1:%s' % server.server_port):
            with closing(http.client.HTTPConnection('127.0.0.1', self.http_port(), timeout=10)) as connection:
                cookies = '; '.join('%s=%s' % pair for pair in self.opener.cookies.items())
                connection.request('GET', '/api/meli/shops/%s/items?scope=global&q=one%%20two&status=active&q=%%2f%%41' % self.store.id,
                                   headers={'Authorization': 'Bearer ' + self.writer_key, 'Cookie': cookies})
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 422)
        self.assertEqual(server.received[-1][0], '/marketplace/users/900001/items/search?q=one%20two&status=active&q=%2f%41')

    def test_secondary_company_is_correctly_attributed_in_audit(self):
        self.writer.company_ids |= self.other_company
        with self.assertLogs('odoo.addons.meli_bff.ir_http', level='INFO') as logs:
            response = self.call('/shops/%s' % self.other.id)
            self.assertEqual(response.status_code, 200)
        self.assertIn('company=%s shop=%s' % (self.other_company.id, self.other.id), '\n'.join(logs.output))

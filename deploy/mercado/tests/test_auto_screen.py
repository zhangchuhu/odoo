"""Pure complete-data screening tests; never connects to a platform or database."""
import importlib.util
import pathlib
import unittest

PATH = pathlib.Path(__file__).resolve().parents[1] / 'addons/meli_optimization/domain/screening.py'

class ScreeningTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PATH.exists(), 'screening contract must exist')
        spec = importlib.util.spec_from_file_location('screening', PATH)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.criteria = dict(site_id='MLM', timezone='America/Mexico_City', start='2026-11-01T00:00:00-06:00', end='2026-11-08T00:00:00-06:00', sales_max=2, stock_min=10, sales_policy='paid_gross_units')
        self.product = dict(id='MLM1', seller_id='9', site_id='MLM', title='Dress', variations=[dict(id='v1', available_quantity=20)])

    def test_explicit_thresholds_and_window(self):
        for key in self.criteria:
            data = dict(self.criteria); del data[key]
            with self.assertRaises(ValueError): self.m.parse_criteria(data)
        for key, val in [('sales_max', True), ('stock_min', -1), ('start', '2026-11-01'), ('end', self.criteria['start']), ('timezone', 'UTC'), ('sales_policy', 'net')]:
            with self.assertRaises(ValueError): self.m.parse_criteria(dict(self.criteria, **{key: val}))

    def test_pages_complete_deduplicated_and_consistent(self):
        pages = {None: dict(rows=[dict(id='a')], next='c', total=2, snapshot='stable'), 'c': dict(rows=[dict(id='b')], next=None, total=2, snapshot='stable')}
        self.assertEqual(len(self.m.collect_pages(lambda c: pages[c])), 2)
        pages['c']['rows'] = [dict(id='a')]
        with self.assertRaises(ValueError): self.m.collect_pages(lambda c: pages[c])
        pages['c']['rows'] = [dict(id='b')]; pages['c']['snapshot'] = 'changed'
        with self.assertRaises(ValueError): self.m.collect_pages(lambda c: pages[c])
        pages['c']['snapshot'] = 'stable'; pages['c']['next'] = 'c'
        with self.assertRaises(ValueError): self.m.collect_pages(lambda c: pages[c])
        pages['c']['next'] = None; pages['c']['total'] = 3
        with self.assertRaises(ValueError): self.m.collect_pages(lambda c: pages[c])

    def test_verified_provider_reads_complete_own_data(self):
        self.assertTrue(hasattr(self.m, 'collect_shop'), 'live provider contract missing')
        calls = []
        def get(path, query=()):
            calls.append((path, dict(query)))
            if path.endswith('/items/search'):
                return dict(seller_id='9', results=['MLM1'], paging=dict(total=1, limit=50, offset=0))
            if path == '/marketplace/items/MLM1': return self.product
            if path.endswith('/orders/search'):
                return dict(results=[dict(id='pack', orders=[dict(id='11')])], paging=dict(total=1, limit=50))
            if path.endswith('/orders/11'):
                return dict(id=11, seller=dict(id=9), context=dict(site='MLM'), date_created='2026-11-02T00:00:00-06:00', status='paid', order_items=[dict(item=dict(id='MLM1', variation_id='v1'), quantity=2)])
            raise AssertionError(path)
        products, orders, source = self.m.collect_shop(get, '9', self.criteria)
        self.assertEqual(self.evaluate(products, orders)['matches'][0]['sales_units'], 2)
        self.assertTrue(source['orders_complete'])
        self.assertEqual([q for p,q in calls if p.endswith('/orders/search')][0]['date_created.from'], self.criteria['start'])

    def test_live_provider_rejects_stock_detail_drift(self):
        detail_calls = []
        def get(path, query=()):
            if path.endswith('/items/search'):
                return dict(seller_id='9', results=['MLM1'], paging=dict(total=1, limit=50, offset=0))
            if path.endswith('/orders/search'):
                return dict(results=[], paging=dict(total=0, limit=50))
            if path == '/marketplace/items/MLM1':
                detail_calls.append(path)
                return dict(id='MLM1', seller_id='9', site_id='MLM', title='Dress', variations=[dict(id='v1', available_quantity=20 + len(detail_calls))])
            raise AssertionError(path)
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.m.collect_shop(get, '9', self.criteria)
        self.assertEqual(len(detail_calls), 2)

    def test_live_provider_refuses_unknown_and_unsupported_pagination(self):
        for paging in ({}, {'total': 1001, 'limit': 50}, {'total': 1, 'limit': 50}):
            def get(path, query=()):
                return dict(seller_id='9', results=[], paging=paging)
            with self.assertRaises(ValueError): self.m.collect_shop(get, '9', self.criteria)

    def evaluate(self, products=None, orders=None, complete=True):
        return self.m.screen_products(products if products is not None else [self.product], orders or [], self.criteria, '9', complete)

    def test_known_empty_orders_and_unknown_stock(self):
        result = self.evaluate()
        self.assertEqual(result['matches'][0]['sales_units'], 0)
        self.product['variations'][0].pop('available_quantity')
        self.assertEqual(self.evaluate()['unknown'][0]['item_id'], 'MLM1')
        self.assertEqual(self.evaluate()['matches'], [])

    def test_absent_sku_manifest_is_unknown(self):
        self.product.pop('variations')
        self.product['available_quantity'] = 20
        self.assertEqual(self.evaluate()['matches'], [])
        self.assertEqual(len(self.evaluate()['unknown']), 1)

    def test_malformed_sku_list_is_unknown(self):
        self.product['variations'] = {'id': 'v1', 'available_quantity': 20}
        self.product['available_quantity'] = 20
        self.assertEqual(self.evaluate()['matches'], [])
        self.assertEqual(len(self.evaluate()['unknown']), 1)

    def test_missing_order_time_is_explicitly_unknown(self):
        with self.assertRaises(ValueError): self.evaluate(orders=[self.order(date_created=None)])

    def test_incomplete_sales_fail_closed(self):
        with self.assertRaises(ValueError): self.evaluate(complete=False)

    def test_owned_products_and_order_identity(self):
        with self.assertRaises(ValueError): self.evaluate([dict(self.product, seller_id='other')])
        with self.assertRaises(ValueError): self.evaluate([self.product, self.product])
        order = self.order()
        with self.assertRaises(ValueError): self.evaluate(orders=[dict(order, seller_id='other')])
        with self.assertRaises(ValueError): self.evaluate(orders=[order, order])

    def order(self, **extra):
        return dict(dict(id='o1', seller_id='9', site_id='MLM', date_created='2026-11-02T00:00:00-06:00', status='paid', lines_complete=True, lines=[dict(item_id='MLM1', variation_id='v1', quantity=2)]), **extra)

    def test_paid_gross_units_explicit_status_and_sku(self):
        self.assertEqual(self.evaluate(orders=[self.order()])['matches'][0]['sales_units'], 2)
        self.assertEqual(self.evaluate(orders=[self.order(status='cancelled')])['matches'][0]['sales_units'], 0)
        self.assertEqual(self.evaluate(orders=[self.order(status='paid', returned=True)])['matches'][0]['sales_units'], 2)
        self.assertEqual(self.evaluate(orders=[self.order(lines=[dict(item_id='MLM1', variation_id='v1')])])['matches'], [])
        with self.assertRaises(ValueError): self.evaluate(orders=[self.order(status='unrecognized')])
        with self.assertRaises(ValueError): self.evaluate(orders=[self.order(lines_complete=False)])

if __name__ == '__main__': unittest.main()

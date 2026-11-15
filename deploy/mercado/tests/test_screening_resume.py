import importlib.util
import pathlib
import unittest

PATH = pathlib.Path(__file__).resolve().parents[1] / 'addons/meli_optimization/domain/screening_resume.py'

class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PATH.exists(), 'durable replay reader missing')
        spec = importlib.util.spec_from_file_location('resume', PATH)
        self.m = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.m)

    def test_each_call_bounds_io_and_resume_preserves_second_pass(self):
        checkpoint = {}
        calls = []
        def remote(path, query):
            calls.append(path)
            return dict(id='MLM1', seller_id=9, site_id='MLM', available_quantity=len(calls), variations=[])
        for _ in range(2):
            reader = self.m.ReplayReads(checkpoint, remote)
            with self.assertRaises(self.m.CollectionPending):
                reader.get('/marketplace/items/MLM1')
                reader.get('/marketplace/items/MLM1')
        reader = self.m.ReplayReads(checkpoint, remote)
        self.assertEqual(reader.get('/marketplace/items/MLM1')['available_quantity'], 1)
        self.assertEqual(reader.get('/marketplace/items/MLM1')['available_quantity'], 2)
        self.assertEqual(len(calls), 2)

    def test_checkpoint_excludes_credentials_and_buyer_data(self):
        checkpoint = {}
        raw = dict(id=1, seller=dict(id=9, nickname='private'), context=dict(site='MLM'), buyer=dict(email='private'), access_token='secret', order_items=[dict(item=dict(id='MLM1', title='safe'), quantity=2)])
        reader = self.m.ReplayReads(checkpoint, lambda *args: raw)
        with self.assertRaises(self.m.CollectionPending): reader.get('/marketplace/orders/1')
        serialized = repr(checkpoint)
        self.assertNotIn('private', serialized); self.assertNotIn('secret', serialized)
        self.assertEqual(checkpoint['responses'][0]['data']['order_items'][0]['quantity'], 2)

    def test_replay_rejects_divergent_request(self):
        checkpoint = {}
        with self.assertRaises(self.m.CollectionPending): self.m.ReplayReads(checkpoint, lambda *a: {'id': 8}).get('/users/me')
        with self.assertRaises(ValueError): self.m.ReplayReads(checkpoint, lambda *a: {}).get('/marketplace/items/MLM1')

    def test_full_provider_completes_over_repeated_bounded_steps(self):
        spec = importlib.util.spec_from_file_location('screening', PATH.with_name('screening.py'))
        domain = importlib.util.module_from_spec(spec); spec.loader.exec_module(domain)
        criteria = dict(site_id='MLM', timezone='America/Mexico_City', start='2026-11-01T00:00:00-06:00', end='2026-11-08T00:00:00-06:00', sales_max=1, stock_min=0, sales_policy='paid_gross_units', require_all_skus_in_stock=True)
        checkpoint, calls = {}, []
        def remote(path, query):
            calls.append(path)
            if path.endswith('/items/search'): return dict(seller_id='9', results=['MLM1'], paging=dict(total=1, limit=50, offset=0))
            if path.endswith('/orders/search'): return dict(results=[], paging=dict(total=0, limit=50))
            return dict(id='MLM1', seller_id=9, site_id='MLM', title='Dress', variations=[], attributes=[dict(id='SIZE', value_name='Unitalla')], available_quantity=2)
        for _ in range(10):
            try:
                products, orders, source = domain.collect_shop(self.m.ReplayReads(checkpoint, remote).get, '9', criteria)
                break
            except self.m.CollectionPending: pass
        else: self.fail('collection never completed')
        self.assertEqual(calls.count('/marketplace/items/MLM1'), 2)
        self.assertEqual(len(calls), 6)
        self.assertEqual(len(domain.screen_products(products, orders, criteria, '9', source['orders_complete'])['matches']), 1)

    def test_expired_final_checkpoint_does_not_replay_or_fetch(self):
        from datetime import datetime, timedelta, timezone
        clock = [datetime(2026, 11, 15, tzinfo=timezone.utc)]
        checkpoint, calls = {}, []
        reader = self.m.ReplayReads(checkpoint, lambda *a: calls.append(1) or {'id': 8}, now=lambda: clock[0])
        with self.assertRaises(self.m.CollectionPending): reader.get('/users/me')
        clock[0] += timedelta(hours=2, seconds=1)
        with self.assertRaises(self.m.CheckpointExpired):
            self.m.ReplayReads(checkpoint, lambda *a: calls.append(2), now=lambda: clock[0]).get('/users/me')
        self.assertEqual(calls, [1])

    def test_delayed_valid_restart_preserves_actual_observed_time(self):
        from datetime import datetime, timedelta, timezone
        clock = [datetime(2026, 11, 15, tzinfo=timezone.utc)]
        checkpoint = {}
        with self.assertRaises(self.m.CollectionPending):
            self.m.ReplayReads(checkpoint, lambda *a: {'id': 8}, now=lambda: clock[0]).get('/users/me')
        observed = clock[0].isoformat()
        clock[0] += timedelta(minutes=90)
        self.m.ReplayReads(checkpoint, lambda *a: self.fail('unexpected fetch'), now=lambda: clock[0]).get('/users/me')
        window = self.m.observation_window(checkpoint, now=clock[0])
        self.assertEqual(window['observed_at'], observed)

    def test_legacy_checkpoint_without_timestamps_fails_closed(self):
        checkpoint = {'responses': [{'path': '/users/me', 'query': [], 'data': {'id': 8}}]}
        with self.assertRaises(self.m.CheckpointExpired): self.m.ReplayReads(checkpoint, lambda *a: self.fail('unexpected fetch'))

    def test_wire_query_uses_tuple_pairs_while_checkpoint_roundtrips_json(self):
        import json
        from urllib.parse import urlencode
        checkpoint, encoded = {}, []
        def fetch(path, query):
            encoded.append(urlencode(query))
            return dict(seller_id='9', results=[], paging=dict(total=0, limit=50, offset=0))
        query = [('limit', '50'), ('offset', '0')]
        with self.assertRaises(self.m.CollectionPending):
            self.m.ReplayReads(checkpoint, fetch).get('/marketplace/users/9/items/search', query)
        self.assertEqual(encoded, ['limit=50&offset=0'])
        restored = json.loads(json.dumps(checkpoint))
        value = self.m.ReplayReads(restored, lambda *a: self.fail('replayed read must not fetch')).get('/marketplace/users/9/items/search', query)
        self.assertEqual(value['paging']['total'], 0)

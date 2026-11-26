import sqlite3
import tempfile
import unittest
from pathlib import Path

from optimization_worker.visual_v2_checkpoints import initialize, append_batch, load_query, candidate_pool, seal_queries, read_queries, finish_query, query_status, finish_plan, plan_finished


class VisualV2CheckpointTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        initialize(self.db)
        self.key = ('request', 'plan', 1, 'q1')

    def append(self, key, index, cards):
        return append_batch(self.db, key, index, cards, fence=lambda: True)

    def test_append_and_replay_are_idempotent(self):
        cards = [{'product_id': 'MLM1'}, {'product_id': 'MLM2'}]
        self.append(self.key, 0, cards)
        self.append(self.key, 0, cards)
        self.append(self.key, 1, [{'product_id': 'MLM2'}, {'product_id': 'MLM3'}])
        self.assertEqual([x['product_id'] for x in load_query(self.db, self.key)], ['MLM1', 'MLM2', 'MLM3'])

    def test_batch_cannot_be_replaced_or_skipped(self):
        self.append(self.key, 0, [{'product_id': 'MLM1'}])
        with self.assertRaises(ValueError):
            self.append(self.key, 0, [{'product_id': 'MLM2'}])
        with self.assertRaises(ValueError):
            self.append(self.key, 2, [{'product_id': 'MLM3'}])

    def test_total_limit_applies_to_unique_cards(self):
        self.append(self.key, 0, [{'product_id': str(n)} for n in range(100)])
        with self.assertRaises(ValueError):
            self.append(self.key, 1, [{'product_id': '101'}])

    def test_fence_is_checked_before_write_and_replay(self):
        self.append(self.key, 0, [{'product_id': 'MLM1'}])
        for index, cards in ((0, [{'product_id': 'MLM1'}]), (1, [{'product_id': 'MLM2'}])):
            with self.assertRaises(ValueError):
                append_batch(self.db, self.key, index, cards, fence=lambda: False)
        self.assertEqual(len(load_query(self.db, self.key)), 1)

    def test_candidate_pool_deduplicates_across_queries_preserving_sources(self):
        self.append(self.key, 0, [{'product_id': 'MLM1'}, {'product_id': 'MLM2'}])
        self.append(('request', 'plan', 1, 'q2'), 0, [{'product_id': 'MLM1'}, {'product_id': 'MLM3'}])
        pool = candidate_pool(self.db, self.key[:3], ['q1', 'q2'])
        self.assertEqual([entry['product_id'] for entry in pool], ['MLM1', 'MLM2', 'MLM3'])
        self.assertEqual(pool[0]['queries'], ['q1', 'q2'])

    def test_search_plan_can_append_but_not_change_previously_sealed_queries(self):
        scope = self.key[:3]
        initial = ['vestido largo', 'vestido plisado', 'vestido fiesta']
        seal_queries(self.db, scope, initial, fence=lambda: True)
        seal_queries(self.db, scope, initial + ['vestido elegante'], fence=lambda: True)
        self.assertEqual(read_queries(self.db, scope), initial + ['vestido elegante'])
        with self.assertRaises(ValueError):
            seal_queries(self.db, scope, ['vestido corto'] + initial[1:], fence=lambda: True)

    def test_query_completion_is_durable_and_prevents_further_batches(self):
        self.append(self.key, 0, [{'product_id': 'MLM1'}])
        finish_query(self.db, self.key, 'home_complete', fence=lambda: True)
        finish_query(self.db, self.key, 'home_complete', fence=lambda: True)
        self.assertEqual(query_status(self.db, self.key), 'home_complete')
        with self.assertRaises(ValueError):
            self.append(self.key, 1, [{'product_id': 'MLM2'}])
        with self.assertRaises(ValueError):
            finish_query(self.db, self.key, 'card_limit_reached', fence=lambda: True)

    def test_cap_status_requires_100_unique_cards(self):
        self.append(self.key, 0, [{'product_id': 'MLM1'}])
        with self.assertRaises(ValueError):
            finish_query(self.db, self.key, 'card_limit_reached', fence=lambda: True)

    def test_finished_plan_cannot_be_expanded(self):
        scope=self.key[:3]; initial=['vestido largo','vestido plisado','vestido fiesta']
        seal_queries(self.db,scope,initial,fence=lambda:True)
        finish_plan(self.db,scope,fence=lambda:True)
        self.assertTrue(plan_finished(self.db,scope))
        with self.assertRaises(ValueError):
            seal_queries(self.db,scope,initial+['vestido elegante'],fence=lambda:True)


class NativeReceiptTest(unittest.TestCase):
    def test_store_initial_plan_is_immutable_when_adding_expansions(self):
        from optimization_worker.tab_receipt_store import TabReceiptStore
        with tempfile.TemporaryDirectory() as directory:
            store=TabReceiptStore(str(Path(directory)/'receipts.sqlite'))
            scope=('r','d'*64,1)
            first=['vestido largo','vestido plisado','vestido fiesta']
            self.assertEqual(store.seal_visual_v2_queries(*scope,first,lambda:True),first)
            more=first+['vestido elegante']
            self.assertEqual(store.seal_visual_v2_queries(*scope,more,lambda:True),more)
            with self.assertRaises(ValueError):store.seal_visual_v2_queries(*scope,first,lambda:True)

    def test_visual_v2_resume_index_validates_each_receipt_in_order(self):
        import json
        from optimization_worker.tab_receipt_store import TabReceiptStore
        with tempfile.TemporaryDirectory() as directory:
            store=TabReceiptStore(str(Path(directory)/'receipts.sqlite'))
            binding={'session_id':'c'*32,'tab_id':1,'window_id':2,'plan_digest':'d'*64,'platform':'mercado-libre-mx'}
            with sqlite3.connect(store.path) as db:
                db.execute('INSERT INTO bindings VALUES (?,?)',('r',json.dumps(binding)))
                for i,q in enumerate(['vestido a','vestido b','vestido c']):
                    db.execute('INSERT INTO accepted_receipts(request_id,body) VALUES (?,?)',('r',json.dumps({**binding,
                        'lease_epoch':1,'query_index':i,'query':q,'version':3})))
            self.assertEqual(store.resume_index('r','d'*64,['vestido a','vestido b','vestido c'],1,visual_v2=True),3)
            self.assertEqual(store.next_query_index('r','d'*64,['vestido a','vestido b','vestido c'],1,visual_v2=True),0)
            with sqlite3.connect(store.path) as db:
                initialize(db)
                finish_query(db,('r','d'*64,1,'0'),'home_complete',fence=lambda:True)
            self.assertEqual(store.next_query_index('r','d'*64,['vestido a','vestido b','vestido c'],1,visual_v2=True),1)
            with self.assertRaises(ValueError):
                store.resume_index('r','d'*64,['vestido a','vestido altered','vestido c'],1,visual_v2=True)

    def test_version_three_challenge_requires_sealed_ninth_query(self):
        from optimization_worker.tab_receipt_store import TabReceiptStore
        with tempfile.TemporaryDirectory() as directory:
            store = TabReceiptStore(str(Path(directory) / 'receipts.sqlite'))
            scope = ('request', 'a' * 64, 1)
            queries = ['vestido modelo ' + str(n) for n in range(9)]
            challenge = {'version': 3, 'request_id': scope[0], 'nonce': 'b' * 32, 'lease_epoch': 1,
                         'plan_digest': scope[1], 'platform': 'mercado-libre-mx',
                         'query_index': 8, 'query': queries[8]}
            with self.assertRaises(ValueError):
                store.issue(challenge)
            with sqlite3.connect(store.path) as db:
                initialize(db)
                seal_queries(db, scope, queries, fence=lambda: True)
            store.issue(challenge)

    def test_batch_requires_matching_browser_receipt_and_valid_identity(self):
        import json
        from optimization_worker.tab_receipt_store import TabReceiptStore
        with tempfile.TemporaryDirectory() as directory:
            store = TabReceiptStore(str(Path(directory) / 'receipts.sqlite'))
            card = {'query': 'vestido largo', 'rank': 1, 'is_ad': False, 'title': 'Vestido',
                    'url': 'https://articulo.mercadolibre.com.mx/MLM-123456789-vestido-_JM',
                    'product_id': 'MLM123456789'}
            args = ('request', 'digest', 1, 0, 'vestido largo', 0, [card], lambda: True)
            with self.assertRaises(ValueError):
                store.record_visual_v2_batch(*args)
            with sqlite3.connect(store.path) as db:
                db.execute('INSERT INTO accepted_receipts (request_id,body) VALUES (?,?)', ('request', json.dumps({
                    'plan_digest': 'digest', 'lease_epoch': 1, 'query_index': 0,
                    'query': 'vestido largo', 'platform': 'mercado-libre-mx', 'version': 3})))
            store.record_visual_v2_batch(*args)
            with self.assertRaises(ValueError):
                store.record_visual_v2_batch(*args[:-2], [dict(card, product_id='MLM999')], args[-1])
            store.finish_visual_v2_query('request', 'digest', 1, 0, 'vestido largo', 'home_complete', lambda: True)
            with sqlite3.connect(store.path) as db:
                self.assertEqual(query_status(db, ('request', 'digest', 1, '0')), 'home_complete')

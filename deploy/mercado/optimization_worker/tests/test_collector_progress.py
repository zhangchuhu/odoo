import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from optimization_worker.collector_cli import collector_id, task_config
from optimization_worker.collector_import import convert_evidence
from optimization_worker.collector_progress import handle_progress, CollectorProgressError, checked_detail_identity
from optimization_worker.tab_receipt_broker import Broker
from optimization_worker.tests.test_collector_import import fixture


class CandidateCheckpointTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.ledger = Path(folder.name) / 'ledger'
        self.data, queries = fixture()
        rid = '01234567-89ab-4cde-8fab-0123456789ab'
        self.key = rid, 'a'*64, 2
        self.challenge = dict(request_id=rid, lease_epoch=2, plan_digest=self.key[1],
                              platform='shein-us', query=queries[0], query_index=0)
        self.plan = dict(platform='shein-us', category='dress', queries=queries, result_limit=5)
        self.event = dict(request_id=rid, session_id='1'*32, tab_id=3, window_id=4, native_origin='peer')
        self.broker = self.reopen()
        with closing(sqlite3.connect(self.ledger)) as db, db:
            for i, block in enumerate(self.data['queries']):
                db.execute('INSERT INTO query_cards VALUES (?,?,?,?,?)',
                           (*self.key, i, json.dumps(block['observations'])))

    def reopen(self):
        broker = Broker(self.ledger.with_suffix('.sock'), self.ledger, self.challenge, 3, lambda:True,
                        expected_window_id=4, expected_session='1'*32, watch_only=True,
                        anchor_origin='anchor', peer_origin='peer', progress_plan=(self.plan, task_config({}, self.plan)))
        broker.guard_alive = lambda session: True
        broker.anchor_guard_alive = lambda: True
        return broker

    def test_interrupted_reply_replays_detail_and_resumes_at_next_candidate(self):
        save = dict(self.event, operation='checkpoint_detail', detail_index=0, detail=self.data['details'][0])
        self.assertTrue(handle_progress(self.broker, save)['ok'])
        self.broker = self.reopen()
        self.assertTrue(handle_progress(self.broker, save)['ok'])
        status = handle_progress(self.broker, dict(self.event, operation='collector_progress', query_index=2))
        self.assertEqual(status['details'], [save['detail']])
        self.assertEqual(status['cards'], self.data['queries'][2]['observations'])
        for mutation in (dict(detail_index=2), dict(detail=dict(save['detail'],title='changed')),
                         dict(window_id=99), dict(native_origin='anchor')):
            with self.assertRaises(CollectorProgressError):
                handle_progress(self.broker, dict(save, **mutation))
        next_detail = self.data['details'][1]
        with self.assertRaisesRegex(CollectorProgressError, 'order'):
            handle_progress(self.broker, dict(save, detail_index=1, detail=self.data['details'][2]))
        self.assertEqual(checked_detail_identity(self.broker,next_detail['detail_url']), next_detail['identity'])
        self.assertIsNone(checked_detail_identity(self.broker,self.data['details'][2]['detail_url']))
        self.assertTrue(handle_progress(self.broker, dict(save, detail_index=1, detail=next_detail))['ok'])
        self.broker.fence = lambda: False
        with self.assertRaises(CollectorProgressError): handle_progress(self.broker, save)

    def test_mercado_sales_screen_checkpoints_only_a_verified_below_threshold_candidate(self):
        self.challenge.update(platform='mercado-libre-mx')
        self.plan.update(platform='mercado-libre-mx')
        self.broker = self.reopen()
        event = dict(self.event, native_origin='anchor', operation='checkpoint_detail',
                     detail_index=0, sales_only=True)
        with closing(sqlite3.connect(self.ledger)) as db, db:
            for i, block in enumerate(self.data['queries']):
                for card in block['observations']:
                    card['product_id'] = 'MLM' + card['product_id']
                    card['url'] = 'https://articulo.mercadolibre.com.mx/' + card['product_id']
                db.execute('UPDATE query_cards SET body=? WHERE query_index=?',
                           (json.dumps(block['observations']), i))
        detail = dict(self.data['details'][0], identity='mercado-libre-mx:MLM1', product_id='MLM1',
                      detail_url='https://articulo.mercadolibre.com.mx/MLM1',
                      status='rejected', reason='threshold_failure', visual_category_code=None,
                      match_level=None, visual_features=None)
        for value, outcome in [('+500 vendidos', 'visual_required'), (None, 'metric_missing')]:
            self.assertEqual(handle_progress(self.broker, dict(event, detail=dict(detail, sold_display=value)))['sales_screen'], outcome)
        wrong = dict(detail, product_id='MLM2', detail_url='https://articulo.mercadolibre.com.mx/MLM2', sold_display='+25 vendidos')
        with self.assertRaises(CollectorProgressError):
            handle_progress(self.broker, dict(event, detail=wrong))
        save = dict(event, detail=dict(detail, sold_display='+25 vendidos'))
        self.assertEqual(handle_progress(self.broker, save)['sales_screen'], 'rejected')
        self.assertEqual(handle_progress(self.broker, save)['sales_screen'], 'rejected')
        status = handle_progress(self.broker, dict(self.event, native_origin='anchor', operation='collector_progress', query_index=2))
        self.assertEqual(status['details'], [save['detail']])

    def test_confirmed_group_waits_only_for_the_original_guard_and_lease(self):
        event = dict(self.event, operation='collector_progress', query_index=2)
        binding = handle_progress(self.broker, event)['binding']
        event['expected_binding'] = binding
        self.broker.anchor_guard_alive = lambda: False
        with self.assertRaises(CollectorProgressError) as error:
            handle_progress(self.broker, event)
        self.assertEqual(error.exception.code, 'checkpoint_anchor_pending')
        for key, changed in [('lease_epoch', 3), ('request_id', 'other'), ('plan_digest', 'b' * 64),
                             ('tab_id', 7), ('window_id', 9), ('session_id', 'f' * 32)]:
            with self.subTest(key=key), self.assertRaises(CollectorProgressError) as error:
                handle_progress(self.broker, dict(event, expected_binding=dict(binding, **{key: changed})))
            self.assertEqual(error.exception.code, 'checkpoint_identity_changed')
        self.broker.fence = lambda: False
        with self.assertRaises(CollectorProgressError) as error:
            handle_progress(self.broker, event)
        self.assertEqual(error.exception.code, 'checkpoint_identity_changed')
        self.broker.fence = self.broker.anchor_guard_alive = lambda: True
        self.broker.guard_alive = lambda session: False
        with self.assertRaises(CollectorProgressError) as error:
            handle_progress(self.broker, event)
        self.assertEqual(error.exception.code, 'checkpoint_guard_pending')
        self.broker.guard_alive = lambda session: True
        reply = handle_progress(self.broker, event)
        self.assertTrue(reply['acknowledged'])
        self.assertEqual(reply['binding'], binding)

    def test_navigation_selects_only_next_candidate_for_the_bound_task(self):
        event = dict(self.event,operation='next_candidate')
        first = handle_progress(self.broker,event)
        self.assertEqual(first['candidate'],{'identity':'shein-us:1','url':'https://us.shein.com/item-p-1.html'})
        self.assertEqual(first['task'],collector_id(self.key[0]))
        for invalid in (dict(request_id='other-task'),dict(tab_id=4),dict(window_id=8),dict(native_origin='anchor'),
                        dict(session_id='b'*32),dict(url='https://us.shein.com/item-p-3.html'),dict(detail_index=2)):
            with self.subTest(invalid=invalid),self.assertRaises(CollectorProgressError):
                handle_progress(self.broker, dict(event,**invalid))
        handle_progress(self.broker,dict(self.event,operation='checkpoint_detail',detail_index=0,detail=self.data['details'][0]))
        self.assertEqual(handle_progress(self.broker,event)['candidate']['identity'],'shein-us:2')
        plan,config=self.broker.progress_plan
        self.broker.progress_plan=plan,dict(config,result_limit=1)
        self.assertIsNone(handle_progress(self.broker,event)['candidate'])
        self.broker.guard_alive = lambda session:False
        with self.assertRaises(CollectorProgressError): handle_progress(self.broker,event)

    def test_exhausted_pool_can_contain_zero_or_one_recurrent_product(self):
        for count in (0,1):
            data = copy.deepcopy(self.data)
            for i, block in enumerate(data['queries']):
                for card in block['observations'][count:]:
                    product_id = str(i*1000 + 100 + card['rank'])
                    card.update(product_id=product_id, url=f'https://us.shein.com/item-p-{product_id}.html')
            data['details'] = data['details'][:count]
            result = convert_evidence(data, data['task_record_id'], 'shein-us', self.plan['queries'],
                                      'dress', '2026-11-16T00:00:00Z')
            self.assertEqual(len(result['candidates']),count)

    def test_pre_upgrade_staged_review_survives_but_cannot_be_modified(self):
        store = self.broker.store
        with self.assertRaises(ValueError):
            store.require_checkpoint_evidence(*self.key, self.data)
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.execute('INSERT INTO staged_evidence VALUES (?,?,?)',
                       (self.key[0], self.key[2], json.dumps(self.data)))
        store.require_checkpoint_evidence(*self.key, self.data)
        changed = dict(self.data, details=self.data['details'][1:])
        with self.assertRaises(ValueError):
            store.require_checkpoint_evidence(*self.key, changed)

    def test_staging_an_incomplete_prefix_leaves_room_for_the_real_result(self):
        evidence = dict(self.data, task_record_id=collector_id(self.key[0]), details=[])
        stage = {k: v for k,v in self.event.items() if k != 'native_origin'}
        stage.update(operation='stage_evidence', evidence=evidence)
        self.assertFalse(self.broker.stage_evidence(stage))
        with closing(sqlite3.connect(self.ledger)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM staged_evidence').fetchone()[0], 0)

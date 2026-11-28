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
    def test_shein_home_guard_accepts_extended_query_indices(self):
        from optimization_worker.collector_progress import guarded_query_index_allowed
        self.assertTrue(guarded_query_index_allowed(6, 5))
        self.assertTrue(guarded_query_index_allowed(6, 8))
        self.assertFalse(guarded_query_index_allowed(6, 9))
        self.assertFalse(guarded_query_index_allowed(1, 5))
    def test_shein_home_freezes_when_suggestions_exhausted(self):
        from optimization_worker.card_sales_v3_checkpoints import initialize,seal_query
        from optimization_worker.card_sales_v3_pipeline import initialize_pipeline
        self.challenge.update(version=6,platform='shein-us');self.plan.update(platform='shein-us',strategy='shein-home-v1')
        self.broker=self.reopen();self.broker.query_verified=True
        self.broker.progress_plan=(self.plan,{'platform':'shein-us','min_sold':500,'min_reviews':20,'min_rating':4.1,'result_limit':5})
        self.broker.home_expansion_candidates=lambda used:[]
        with sqlite3.connect(self.ledger) as db:
            initialize(db);initialize_pipeline(db)
            for i,q in enumerate(self.plan['queries']):
                seal_query(db,self.key,i,q,fence=lambda:True)
                db.execute('INSERT INTO card_v3_pages VALUES (?,?,?,?,?,?)',(*self.key,i,1,'home_complete'))
        result=handle_progress(self.broker,dict(self.event,operation='seal_expansion',queries=[]))
        self.assertTrue(result['finished'])
    def test_shein_home_detail_uses_card_sales_and_task_review_threshold(self):
        from optimization_worker.card_sales_v3_checkpoints import initialize,seal_query,append_page_batch
        from optimization_worker.card_sales_v3_pipeline import initialize_pipeline,freeze_queue
        self.challenge.update(version=6,platform='shein-us')
        self.plan.update(platform='shein-us',strategy='shein-home-v1')
        self.broker=self.reopen();self.broker.query_verified=True
        config={'platform':'shein-us','min_sold':500,'min_reviews':20,'min_rating':4.1,'result_limit':5}
        self.broker.progress_plan=(self.plan,config)
        with sqlite3.connect(self.ledger) as db:
            initialize(db);initialize_pipeline(db)
            for i,q in enumerate(self.plan['queries']):
                seal_query(db,self.key,i,q,fence=lambda:True)
                if i==0:
                    card=dict(query=q,product_id='123',url='https://us.shein.com/example-p-123.html',rank=1,sales_text='500+ sold')
                    append_page_batch(db,self.key,i,1,0,[card],fence=lambda:True,platform='shein-us')
                db.execute('INSERT INTO card_v3_pages VALUES (?,?,?,?,?,?)',(*self.key,i,1,'home_complete'))
            freeze_queue(db,self.key,plan_exhausted=True,fence=lambda:True,homepage_only=True,platform='shein-us',threshold=500)
        self.assertEqual(checked_detail_identity(self.broker,'https://us.shein.com/example-p-123.html'),'shein-us:123')
        self.assertIsNone(checked_detail_identity(self.broker,'https://us.shein.com/example-p-124.html'))
        detail=dict(self.data['details'][0],identity='shein-us:123',product_id='123',detail_url='https://us.shein.com/example-p-123.html',sold_display=None,reviews_display='21')
        result=handle_progress(self.broker,dict(self.event,native_origin='peer',operation='checkpoint_detail',detail_index=0,detail=detail,sales_only=True))
        self.assertEqual(result['sales_screen'],'threshold_met')
    def test_home_expands_only_evidenced_suggestions_and_freezes_when_exhausted(self):
        from optimization_worker.card_sales_v3_checkpoints import initialize,seal_query
        from optimization_worker.card_sales_v3_pipeline import initialize_pipeline
        self.challenge.update(version=4,platform='mercado-libre-mx');self.plan.update(platform='mercado-libre-mx',strategy='card-visual-v3')
        self.broker=self.reopen();self.broker.query_verified=True;self.plan['strategy']='card-home-v4'
        self.broker.home_expansion_candidates=lambda used:['dress new'] if 'dress new' not in used else []
        with sqlite3.connect(self.ledger) as db:
            initialize(db);initialize_pipeline(db)
            for i,q in enumerate(self.plan['queries']):
                seal_query(db,self.key,i,q,fence=lambda:True)
                db.execute('INSERT INTO card_v3_pages VALUES (?,?,?,?,?,?)',(*self.key,i,1,'home_complete'))
        event=dict(self.event,native_origin='anchor',operation='seal_expansion',queries=['dress new'])
        with self.assertRaises(CollectorProgressError):handle_progress(self.broker,dict(event,queries=['invented']))
        with self.assertRaises(CollectorProgressError):handle_progress(self.broker,dict(event,queries=[]))
        self.assertEqual(handle_progress(self.broker,event)['queries'][-1],'dress new')
        with sqlite3.connect(self.ledger) as db:db.execute('INSERT INTO card_v3_pages VALUES (?,?,?,?,?,?)',(*self.key,3,1,'home_complete'))
        self.assertTrue(handle_progress(self.broker,dict(event,queries=[]))['finished'])
        with sqlite3.connect(self.ledger) as db:self.assertEqual(db.execute('SELECT reason FROM home_collection_end').fetchone()[0],'terms_exhausted')

    def test_home_candidates_use_frozen_queue_and_task_threshold(self):
        from optimization_worker.card_sales_v3_checkpoints import initialize,seal_query,append_page_batch
        from optimization_worker.card_sales_v3_pipeline import initialize_pipeline,freeze_queue
        self.challenge.update(version=4,platform='mercado-libre-mx');self.plan.update(platform='mercado-libre-mx',strategy='card-visual-v3')
        self.broker=self.reopen();self.broker.query_verified=True;self.plan['strategy']='card-home-v4'
        with sqlite3.connect(self.ledger) as db:
            initialize(db);initialize_pipeline(db)
            for i,q in enumerate(self.plan['queries']):
                seal_query(db,self.key,i,q,fence=lambda:True)
                card=dict(query=q,product_id='MLM'+str(i+1),url='https://articulo.mercadolibre.com.mx/MLM'+str(i+1),rank=1,first_seen=1,sales_text='+150 vendidos')
                append_page_batch(db,self.key,i,1,0,[card],fence=lambda:True)
                db.execute('INSERT INTO card_v3_pages VALUES (?,?,?,?,?,?)',(*self.key,i,1,'home_complete'))
            freeze_queue(db,self.key,plan_exhausted=True,fence=lambda:True,homepage_only=True)
        self.assertEqual(checked_detail_identity(self.broker,'https://articulo.mercadolibre.com.mx/MLM1'),'mercado-libre-mx:MLM1')
        result=handle_progress(self.broker,dict(self.event,native_origin='anchor',operation='next_candidate'))
        self.assertEqual((result['stage'],result['candidate']['identity']),('detail','mercado-libre-mx:MLM1'))
        detail=dict(self.data['details'][0],identity='mercado-libre-mx:MLM1',product_id='MLM1',detail_url='https://articulo.mercadolibre.com.mx/MLM1',sold_display='+150 vendidos')
        result=handle_progress(self.broker,dict(self.event,native_origin='anchor',operation='checkpoint_detail',detail_index=0,detail=detail,sales_only=True))
        self.assertEqual(result['sales_screen'],'supplemental')

    def test_home_resume_reads_saved_batch_only_after_current_query_verification(self):
        from optimization_worker.card_sales_v3_checkpoints import initialize,seal_query,append_page_batch
        self.challenge.update(version=4,platform='mercado-libre-mx');self.plan.update(platform='mercado-libre-mx',strategy='card-visual-v3')
        self.broker=self.reopen();self.broker.watch_only=False;self.broker.query_verified=True
        self.plan['strategy']='card-home-v4'
        with sqlite3.connect(self.ledger) as db:
            initialize(db)
            for i,q in enumerate(self.plan['queries']):seal_query(db,self.key,i,q,fence=lambda:True)
            card=dict(query=self.plan['queries'][0],product_id='MLM1',url='https://articulo.mercadolibre.com.mx/MLM1',rank=1,sales_text='+150 vendidos')
            append_page_batch(db,self.key,0,1,0,[card],fence=lambda:True)
        event=dict(self.event,native_origin='anchor',operation='collector_progress',query_index=0)
        result=handle_progress(self.broker,event)
        self.assertEqual((result['strategy'],result['next_page'],result['next_batch_index']),('card-home-v4',(0,1),1))
        self.assertEqual(result['cards'][0]['product_id'],'MLM1')
        self.broker.query_verified=False
        self.assertIsNone(handle_progress(self.broker,event)['cards'])

    def test_card_v3_expands_after_completed_pages_and_freezes_when_terms_exhausted(self):
        from optimization_worker.card_sales_v3_checkpoints import initialize,seal_query,finish_page
        from optimization_worker.card_sales_v3_pipeline import initialize_pipeline
        self.challenge.update(version=4,platform='mercado-libre-mx');self.plan.update(platform='mercado-libre-mx',strategy='card-visual-v3')
        self.broker=self.reopen();self.broker.query_verified=True
        with sqlite3.connect(self.ledger) as db:
            initialize(db);initialize_pipeline(db)
            for i,q in enumerate(self.plan['queries']):seal_query(db,self.key,i,q,fence=lambda:True)
            for i in range(3):finish_page(db,self.key,i,1,'end_of_results',fence=lambda:True)
        status=handle_progress(self.broker,dict(self.event,native_origin='anchor',operation='next_candidate'))
        self.assertEqual((status['action'],status['requires_expansion']),('search',True))
        event=dict(self.event,native_origin='anchor',operation='seal_expansion',queries=['vestido largo elegante'])
        result=handle_progress(self.broker,event);self.assertEqual(len(result['queries']),4)
        with self.assertRaises(CollectorProgressError):handle_progress(self.broker,dict(event,queries=[]))
        with sqlite3.connect(self.ledger) as db:finish_page(db,self.key,3,1,'end_of_results',fence=lambda:True)
        self.assertTrue(handle_progress(self.broker,dict(event,queries=[]))['finished'])
        with sqlite3.connect(self.ledger) as db:self.assertEqual(db.execute('SELECT count(*) FROM card_v3_meta').fetchone()[0],1)
    def test_card_v3_progress_exposes_page_and_durable_batches(self):
        from optimization_worker.card_sales_v3_checkpoints import initialize,seal_query,append_page_batch
        self.challenge.update(version=4,platform='mercado-libre-mx');self.plan.update(platform='mercado-libre-mx',strategy='card-visual-v3')
        self.broker=self.reopen();self.broker.query_verified=True;self.broker.watch_only=False
        with sqlite3.connect(self.ledger) as db:
            initialize(db)
            for i,q in enumerate(self.plan['queries']):seal_query(db,self.key,i,q,fence=lambda:True)
            card=dict(query=self.plan['queries'][0],product_id='MLM1',url='https://articulo.mercadolibre.com.mx/MLM1',rank=1,sales_text='+150 vendidos')
            append_page_batch(db,self.key,0,1,0,[card],fence=lambda:True)
            binding=dict(request_id=self.key[0],session_id=self.event['session_id'],tab_id=3,window_id=4)
            db.execute('INSERT INTO bindings VALUES (?,?)',(self.key[0],json.dumps(binding)))
            receipt=dict(binding,version=4,lease_epoch=self.key[2],plan_digest=self.key[1],query_index=0,query=self.plan['queries'][0])
            db.execute('INSERT INTO accepted_receipts(request_id,body) VALUES (?,?)',(self.key[0],json.dumps(receipt)))
        result=handle_progress(self.broker,dict(self.event,native_origin='anchor',operation='collector_progress',query_index=0))
        self.assertEqual(result['strategy'],'card-visual-v3')
        self.assertEqual(result['page'],1)
        self.assertEqual(result['next_batch_index'],1)
        self.assertEqual(result['cards'][0]['product_id'],'MLM1')
    def test_card_v3_can_only_open_reviewed_or_next_frozen_candidate(self):
        from optimization_worker.card_sales_v3_checkpoints import initialize,seal_query,append_page_batch,finish_page
        from optimization_worker.card_sales_v3_pipeline import initialize_pipeline,freeze_queue,record_detail
        self.challenge.update(version=4,platform='mercado-libre-mx');self.plan.update(platform='mercado-libre-mx',strategy='card-visual-v3')
        self.broker=self.reopen();self.broker.query_verified=True
        with sqlite3.connect(self.ledger) as db:
            initialize(db);initialize_pipeline(db)
            for i,q in enumerate(self.plan['queries']):
                seal_query(db,self.key,i,q,fence=lambda:True)
                card=dict(query=q,product_id='MLM'+str(i+1),url='https://articulo.mercadolibre.com.mx/MLM'+str(i+1),rank=1,first_seen=1,sales_text='+150 vendidos')
                append_page_batch(db,self.key,i,1,0,[card],fence=lambda:True);finish_page(db,self.key,i,1,'end_of_results',fence=lambda:True)
            freeze_queue(db,self.key,plan_exhausted=True,fence=lambda:True)
        self.assertEqual(checked_detail_identity(self.broker,'https://articulo.mercadolibre.com.mx/MLM1'),'mercado-libre-mx:MLM1')
        self.assertIsNone(checked_detail_identity(self.broker,'https://articulo.mercadolibre.com.mx/MLM2'))
        result=handle_progress(self.broker,dict(self.event,native_origin='anchor',operation='next_candidate'))
        self.assertEqual((result['action'],result['candidate']['identity']),('review','mercado-libre-mx:MLM1'))
        detail=dict(self.data['details'][0],identity='mercado-libre-mx:MLM1',product_id='MLM1',detail_url='https://articulo.mercadolibre.com.mx/MLM1',sold_display='+150 vendidos')
        result=handle_progress(self.broker,dict(self.event,native_origin='anchor',operation='checkpoint_detail',detail_index=0,detail=detail,sales_only=True))
        self.assertEqual(result['sales_screen'],'supplemental')
        with sqlite3.connect(self.ledger) as db:
            self.assertEqual(json.loads(db.execute('SELECT body FROM card_v3_detail_observations').fetchone()[0]),detail)
        with self.assertRaises(CollectorProgressError):
            handle_progress(self.broker,dict(self.event,native_origin='anchor',operation='checkpoint_detail',detail_index=0,detail=dict(detail,title='changed'),sales_only=True))
        self.assertEqual(checked_detail_identity(self.broker,'https://articulo.mercadolibre.com.mx/MLM2'),'mercado-libre-mx:MLM2')
    def test_visual_v2_orders_unique_candidates_by_first_observation(self):
        from optimization_worker.collector_progress import _candidate_order
        from optimization_worker.visual_v2_checkpoints import initialize, seal_queries, append_batch, finish_query
        plan=dict(self.plan,platform='mercado-libre-mx',strategy='visual-v2')
        with sqlite3.connect(self.ledger) as db:
            initialize(db);seal_queries(db,self.key,plan['queries'],fence=lambda:True)
            for index, ids in enumerate((['MLM1'],['MLM2'],['MLM2'])):
                for identity in ids:
                    card={'product_id':identity,'url':'https://articulo.mercadolibre.com.mx/'+identity}
                    append_batch(db,(*self.key,str(index)),0,[card],fence=lambda:True)
                finish_query(db,(*self.key,str(index)),'home_complete',fence=lambda:True)
            pool,order,reviewed,finished=_candidate_order(db,self.key,plan,{'result_limit':5})
            self.assertEqual(order,['MLM1','MLM2'])
            self.assertEqual((reviewed,finished),(0,False))
            self.assertIn('url',pool['MLM1'])
        self.challenge.update(version=3,platform='mercado-libre-mx')
        self.plan.update(platform='mercado-libre-mx',strategy='visual-v2')
        self.broker=self.reopen();self.broker.query_verified=True
        status=handle_progress(self.broker,dict(self.event,native_origin='anchor',operation='next_candidate'))
        self.assertEqual(status['action'],'review')
        self.assertEqual(status['candidate']['identity'],'mercado-libre-mx:MLM1')
        detail=dict(self.data['details'][0],identity='mercado-libre-mx:MLM1',product_id='MLM1',
                    detail_url='https://articulo.mercadolibre.com.mx/MLM1',status='rejected',reason='threshold_failure',
                    sold_display='+25 vendidos',visual_category_code=None,match_level=None,visual_features=None)
        result=handle_progress(self.broker,dict(self.event,native_origin='anchor',operation='checkpoint_detail',detail_index=0,detail=detail,sales_only=True))
        self.assertEqual(result['sales_screen'],'rejected')
        status=handle_progress(self.broker,dict(self.event,native_origin='anchor',operation='next_candidate'))
        self.assertEqual(status['candidate']['identity'],'mercado-libre-mx:MLM2')

    def test_visual_v2_restores_accepted_query_batches(self):
        from optimization_worker.visual_v2_checkpoints import initialize, seal_queries, append_batch, finish_query
        self.challenge.update(version=3,platform='mercado-libre-mx')
        self.plan.update(platform='mercado-libre-mx',strategy='visual-v2')
        self.broker=self.reopen()
        self.broker.query_verified=True
        with sqlite3.connect(self.ledger) as db:
            initialize(db)
            seal_queries(db,self.key,self.plan['queries'],fence=lambda:True)
            append_batch(db,(*self.key,'0'),0,[{'product_id':'MLM1'}],fence=lambda:True)
            finish_query(db,(*self.key,'0'),'home_complete',fence=lambda:True)
        before=handle_progress(self.broker,dict(self.event,native_origin='anchor',operation='collector_progress',query_index=0))
        self.assertIsNone(before['cards'])
        self.assertFalse(before['acknowledged'])
        with sqlite3.connect(self.ledger) as db:
            binding=dict(request_id=self.key[0],session_id=self.event['session_id'],tab_id=3,window_id=4)
            db.execute('INSERT INTO bindings VALUES (?,?)',(self.key[0],json.dumps(binding)))
            receipt=dict(binding,version=3,lease_epoch=self.key[2],plan_digest=self.key[1],query_index=0,query=self.plan['queries'][0])
            db.execute('INSERT INTO accepted_receipts(request_id,body) VALUES (?,?)',(self.key[0],json.dumps(receipt)))
        result=handle_progress(self.broker,dict(self.event,native_origin='anchor',operation='collector_progress',query_index=0))
        self.assertEqual(result['strategy'],'visual-v2')
        self.assertEqual(result['cards'],[{'product_id':'MLM1'}])
        self.assertTrue(result['acknowledged'])
        self.assertTrue(result['exhausted'])
        self.assertEqual(result['next_batch_index'],1)

    def test_visual_v2_expansion_requires_exhausted_candidates_and_can_end_plan(self):
        from optimization_worker.visual_v2_checkpoints import initialize,seal_queries,finish_query,read_queries,plan_finished
        self.challenge.update(version=3,platform='mercado-libre-mx')
        self.plan.update(platform='mercado-libre-mx',strategy='visual-v2')
        self.broker=self.reopen()
        with sqlite3.connect(self.ledger) as db:
            initialize(db);seal_queries(db,self.key,self.plan['queries'],fence=lambda:True)
            for i in range(3):finish_query(db,(*self.key,str(i)),'home_complete',fence=lambda:True)
        event=dict(self.event,native_origin='anchor',operation='seal_expansion',queries=['vestido elegante'])
        self.assertTrue(handle_progress(self.broker,event)['ok'])
        with sqlite3.connect(self.ledger) as db:self.assertEqual(read_queries(db,self.key)[-1],'vestido elegante')
        with self.assertRaises(CollectorProgressError):handle_progress(self.broker,dict(event,queries=[]))
        with sqlite3.connect(self.ledger) as db:finish_query(db,(*self.key,'3'),'home_complete',fence=lambda:True)
        self.assertTrue(handle_progress(self.broker,dict(event,queries=[]))['ok'])
        with sqlite3.connect(self.ledger) as db:self.assertTrue(plan_finished(db,self.key))

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

    def test_staged_plan_can_review_single_query_candidates(self):
        self.plan['strategy']='staged-v1'
        with closing(sqlite3.connect(self.ledger)) as db, db:
            for i, block in enumerate(self.data['queries']):
                for card in block['observations']:
                    product_id=str(i*1000+100+card['rank'])
                    card.update(product_id=product_id,url=f'https://us.shein.com/item-p-{product_id}.html')
                db.execute('UPDATE query_cards SET body=? WHERE query_index=?',(json.dumps(block['observations']),i))
        self.broker=self.reopen()
        result=handle_progress(self.broker,dict(self.event,operation='next_candidate'))
        self.assertEqual(result['candidate']['identity'],'shein-us:101')
        self.assertEqual(result['stage'],'single')
        detail=dict(self.data['details'][0],identity='shein-us:101',product_id='101',
                    detail_url='https://us.shein.com/item-p-101.html')
        save=dict(self.event,operation='checkpoint_detail',detail_index=0,detail=detail)
        self.assertTrue(handle_progress(self.broker,save)['ok'])
        self.broker=self.reopen()
        self.assertTrue(handle_progress(self.broker,save)['ok'])
        self.assertNotEqual(handle_progress(self.broker,dict(self.event,operation='next_candidate'))['candidate']['identity'],'shein-us:101')

    def test_expansion_is_sealed_only_after_initial_stages(self):
        self.plan['strategy']='staged-v1';self.broker=self.reopen()
        event=dict(self.event,operation='seal_expansion',queries=['dress four'])
        with self.assertRaises(CollectorProgressError):handle_progress(self.broker,event)
        for index,detail in enumerate(self.data['details'][:20]):
            self.assertTrue(handle_progress(self.broker,dict(self.event,operation='checkpoint_detail',
                                                            detail_index=index,detail=detail))['ok'])
        next_item=handle_progress(self.broker,dict(self.event,operation='next_candidate'))
        self.assertEqual(next_item['stage'],'expanded')
        self.assertEqual(next_item['action'],'search')
        first=handle_progress(self.broker,event)
        self.assertEqual(first,handle_progress(self.broker,event))
        with self.assertRaises(CollectorProgressError):
            handle_progress(self.broker,dict(event,queries=['another dress']))

    def test_sealed_expansion_resumes_new_candidates_and_rejects_unsealed_cards(self):
        self.plan['strategy']='staged-v1'; self.broker=self.reopen()
        for index, detail in enumerate(self.data['details'][:20]):
            handle_progress(self.broker, dict(self.event,operation='checkpoint_detail',detail_index=index,detail=detail))
        handle_progress(self.broker, dict(self.event,operation='seal_expansion',queries=['dress four']))
        cards = copy.deepcopy(self.data['queries'][0]['observations'])
        for card in cards:
            pid = str(1000 + card['rank'])
            card.update(query='dress four', product_id=pid, url=f'https://us.shein.com/item-p-{pid}.html')
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.execute('INSERT INTO query_cards VALUES (?,?,?,?,?)', (*self.key,3,json.dumps(cards)))
        progress=handle_progress(self.broker,dict(self.event,operation='collector_progress',query_index=3))
        self.assertEqual(progress['queries'],self.plan['queries']+['dress four'])
        self.assertEqual(progress['cards'],cards)
        self.assertEqual(len(progress['details']),20)
        next_item = handle_progress(self.broker,dict(self.event,operation='next_candidate'))
        self.assertEqual(next_item['stage'],'expanded')
        self.assertEqual(next_item['action'],'review')
        self.assertEqual(next_item['candidate']['identity'],'shein-us:1001')
        detail=dict(self.data['details'][0],identity='shein-us:1001',product_id='1001',
                    detail_url='https://us.shein.com/item-p-1001.html')
        handle_progress(self.broker,dict(self.event,operation='checkpoint_detail',detail_index=20,detail=detail))
        self.assertEqual(handle_progress(self.broker,dict(self.event,operation='next_candidate'))['candidate']['identity'],'shein-us:1002')
        self.broker=self.reopen()
        self.assertEqual(handle_progress(self.broker,dict(self.event,operation='next_candidate'))['candidate']['identity'],'shein-us:1002')
        evidence = dict(self.data, task_record_id=collector_id(self.key[0]),
                        details=self.data['details'][:20]+[detail],
                        queries=self.data['queries']+[dict(query='dress four',observations=cards)])
        self.broker.store.require_checkpoint_evidence(*self.key,evidence,staged=True)
        with self.assertRaises(ValueError):
            self.broker.store.require_checkpoint_evidence(*self.key,dict(evidence,queries=evidence['queries'][:3]),staged=True)

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

    def test_staged_target_can_be_saved_without_unnecessary_expansion(self):
        self.plan.update(strategy='staged-v1',result_limit=1)
        self.broker=self.reopen()
        handle_progress(self.broker,dict(self.event,operation='checkpoint_detail',detail_index=0,detail=self.data['details'][0]))
        evidence=dict(self.data,task_record_id=collector_id(self.key[0]),details=self.data['details'][:1])
        stage={k:v for k,v in self.event.items() if k!='native_origin'}
        stage.update(operation='stage_evidence',evidence=evidence)
        self.assertTrue(self.broker.stage_evidence(stage))

    def test_timeout_finishes_after_current_search_and_keeps_confirmed_results(self):
        from datetime import datetime,timedelta,timezone
        self.plan.update(strategy='staged-v1',result_limit=5); self.broker=self.reopen()
        for i,detail in enumerate(self.data['details'][:20]):
            handle_progress(self.broker,dict(self.event,operation='checkpoint_detail',detail_index=i,detail=detail))
        handle_progress(self.broker,dict(self.event,operation='seal_expansion',queries=['dress four','dress five']))
        cards=[dict(card,query='dress four') for card in self.data['queries'][0]['observations']]
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.execute('INSERT INTO query_cards VALUES (?,?,?,?,?)',(*self.key,3,json.dumps(cards)))
            receipt=dict(plan_digest=self.key[1],lease_epoch=self.key[2],query_index=0,
                         observed_at=(datetime.now(timezone.utc)-timedelta(minutes=61)).isoformat())
            db.execute('INSERT INTO accepted_receipts(request_id,body) VALUES (?,?)',(self.key[0],json.dumps(receipt)))
        status=handle_progress(self.broker,dict(self.event,operation='next_candidate'))
        self.assertEqual((status['action'],status['reason']),('finish','time_budget_exhausted'))
        self.assertIsNone(checked_detail_identity(self.broker,'https://us.shein.com/item-p-21.html'))
        evidence=dict(self.data,task_record_id=collector_id(self.key[0]),details=self.data['details'][:20],
                      queries=self.data['queries']+[dict(query='dress four',observations=cards)])
        event={k:v for k,v in self.event.items() if k!='native_origin'}
        event.update(operation='stage_evidence',evidence=evidence)
        self.assertTrue(self.broker.stage_evidence(event))

    def test_exhausted_empty_expansion_can_finish_without_invented_cards(self):
        self.plan.update(strategy='staged-v1',result_limit=5);self.broker=self.reopen()
        for i,detail in enumerate(self.data['details'][:20]):
            handle_progress(self.broker,dict(self.event,operation='checkpoint_detail',detail_index=i,detail=detail))
        handle_progress(self.broker,dict(self.event,operation='seal_expansion',queries=['dress four']))
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.execute('INSERT INTO query_cards VALUES (?,?,?,?,?)',(*self.key,3,'[]'))
            db.execute('INSERT INTO query_card_meta VALUES (?,?,?,?,?,?)',(*self.key,3,'dress four',1))
        status=handle_progress(self.broker,dict(self.event,operation='next_candidate'))
        self.assertEqual((status['action'],status['reason']),('finish','candidates_exhausted'))
        evidence=dict(self.data,task_record_id=collector_id(self.key[0]),details=self.data['details'][:20],
                      queries=self.data['queries']+[dict(query='dress four',observations=[])])
        event={k:v for k,v in self.event.items() if k!='native_origin'};event.update(operation='stage_evidence',evidence=evidence)
        self.assertTrue(self.broker.stage_evidence(event))

    def test_timeout_allows_only_a_detail_reserved_before_deadline(self):
        import time
        from datetime import datetime,timedelta,timezone
        self.plan.update(strategy='staged-v1');self.broker=self.reopen()
        first=handle_progress(self.broker,dict(self.event,operation='next_candidate'))
        self.assertEqual(first['candidate']['identity'],'shein-us:1')
        with closing(sqlite3.connect(self.ledger)) as db, db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='staged_detail_reservations'").fetchone())
            receipt=dict(plan_digest=self.key[1],lease_epoch=self.key[2],query_index=0,
                         observed_at=(datetime.now(timezone.utc)-timedelta(minutes=70)).isoformat())
            db.execute('INSERT INTO accepted_receipts(request_id,body) VALUES (?,?)',(self.key[0],json.dumps(receipt)))
            from optimization_worker.collector_progress import reserve_staged_detail
            reserve_staged_detail(db,self.key,'shein-us:1',time.time()-660)
        self.assertEqual(handle_progress(self.broker,dict(self.event,operation='next_candidate'))['action'],'review')
        self.assertEqual(checked_detail_identity(self.broker,self.data['details'][0]['detail_url']),'shein-us:1')
        stage={k:v for k,v in self.event.items() if k!='native_origin'}
        stage.update(operation='stage_evidence',evidence=dict(self.data,task_record_id=collector_id(self.key[0]),details=[]))
        self.assertFalse(self.broker.stage_evidence(stage))
        handle_progress(self.broker,dict(self.event,operation='checkpoint_detail',detail_index=0,detail=self.data['details'][0]))
        self.assertEqual(handle_progress(self.broker,dict(self.event,operation='next_candidate'))['reason'],'time_budget_exhausted')

    def test_detail_is_reserved_only_after_verified_browser_activation(self):
        from datetime import datetime,timezone
        self.plan.update(strategy='staged-v1');self.broker=self.reopen()
        base={k:v for k,v in self.event.items() if k!='native_origin'}
        issued=self.broker.issue_activation(dict(base,operation='activation_challenge',detail_url=self.data['details'][0]['detail_url']))
        with closing(sqlite3.connect(self.ledger)) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='staged_detail_reservations'").fetchone())
        confirm=dict(base,operation='activation_confirm',nonce=issued['nonce'],url=self.data['details'][0]['detail_url'],
                     query='shein-us:1',observed_at=datetime.now(timezone.utc).isoformat())
        self.assertTrue(self.broker.confirm_activation(confirm))
        with closing(sqlite3.connect(self.ledger)) as db:
            self.assertEqual(db.execute('SELECT identity FROM staged_detail_reservations').fetchone()[0],'shein-us:1')

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from optimization_worker.domain import canonical_hash
from optimization_worker.tab_receipt_factory import build_native_broker

class FactoryTest(unittest.TestCase):
    def test_home_initial_query_uses_page_receipt_protocol(self):
        import sqlite3
        claim={'id':1,'task_type':'research.bestseller','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'lease_epoch':4}
        plan={'platform':'mercado-libre-mx','queries':['vestido uno','vestido dos','vestido tres'],'strategy':'card-home-v4'}
        signed={'request_id':'req','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'plan':plan};signed['digest']=canonical_hash(signed)
        class Store:
            def inspect(self,request):return {'task_type':'research.bestseller','state':'prepared','collector_plan':signed}
        with tempfile.TemporaryDirectory() as tmp,patch('optimization_worker.tab_receipt_factory.worker_records',return_value=[{'request_id':'req','claim':claim}]),patch('optimization_worker.tab_receipt_factory.fence'):
            ledger=Path(tmp)/'db';broker=build_native_broker(Path(tmp)/'s',ledger,'req',0,Store(),object(),'journal')
            self.assertEqual(broker.challenge['version'],5)
            with sqlite3.connect(ledger) as db:self.assertEqual([r[0] for r in db.execute('SELECT query FROM card_v3_queries ORDER BY query_index')],plan['queries'])

    def test_home_watch_accepts_completed_homepages_without_replaying_tab_receipts(self):
        import sqlite3,json
        from optimization_worker.card_sales_v3_checkpoints import initialize,seal_query
        from optimization_worker.tab_receipt_store import TabReceiptStore
        claim={'id':1,'task_type':'research.bestseller','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'lease_epoch':4}
        plan={'platform':'mercado-libre-mx','queries':['one','two','three'],'strategy':'card-home-v4'}
        signed={'request_id':'req','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'plan':plan};signed['digest']=canonical_hash(signed)
        class Store:
            def inspect(self,request):return {'task_type':'research.bestseller','state':'prepared','collector_plan':signed}
        with tempfile.TemporaryDirectory() as tmp,patch('optimization_worker.tab_receipt_factory.worker_records',return_value=[{'request_id':'req','claim':claim}]),patch('optimization_worker.tab_receipt_factory.fence'):
            ledger=Path(tmp)/'db';TabReceiptStore(str(ledger));scope=('req',signed['digest'],4)
            binding={'tab_id':8,'window_id':9,'session_id':'a'*32,'platform':'mercado-libre-mx','plan_digest':signed['digest']}
            with sqlite3.connect(ledger) as db:
                initialize(db);db.execute('INSERT INTO bindings VALUES (?,?)',('req',json.dumps(binding)))
                for i,q in enumerate(plan['queries']):
                    seal_query(db,scope,i,q,fence=lambda:True)
                    db.execute('INSERT INTO card_v3_pages VALUES (?,?,?,?,?,?)',(*scope,i,1,'home_complete'))
                    db.execute('INSERT INTO card_v3_page_evidence VALUES (?,?,?,?,?,?,?,?,?)',(*scope,i,1,'https://listado.mercadolibre.com.mx/'+q,'d'*32,'{"no_results":true}',3))
            self.assertTrue(build_native_broker(Path(tmp)/'s',ledger,'req',0,Store(),object(),'journal',watch_only=True).watch_only)

    def test_home_resume_can_start_next_query_without_old_tab_binding(self):
        import sqlite3
        from optimization_worker.tab_receipt_store import TabReceiptStore
        claim={'id':1,'task_type':'research.bestseller','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'lease_epoch':4}
        plan={'platform':'mercado-libre-mx','queries':['one','two','three'],'strategy':'card-home-v4'}
        signed={'request_id':'req','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'plan':plan};signed['digest']=canonical_hash(signed)
        class Store:
            def inspect(self,request):return {'task_type':'research.bestseller','state':'prepared','collector_plan':signed}
        with tempfile.TemporaryDirectory() as tmp,patch('optimization_worker.tab_receipt_factory.worker_records',return_value=[{'request_id':'req','claim':claim}]),patch('optimization_worker.tab_receipt_factory.fence'):
            ledger=Path(tmp)/'db';scope=('req',signed['digest'],4);store=TabReceiptStore(str(ledger));store.card_home_state(*scope,plan['queries'],lambda:True)
            with sqlite3.connect(ledger) as db:db.execute('INSERT INTO card_v3_pages VALUES (?,?,?,?,?,?)',(*scope,0,1,'home_complete'))
            broker=build_native_broker(Path(tmp)/'s',ledger,'req',1,Store(),object(),'journal')
            self.assertEqual((broker.challenge['version'],broker.challenge['query']), (5,'two'))

    def test_card_v3_initial_query_is_sealed_and_uses_version_four(self):
        import sqlite3
        claim={'id':1,'task_type':'research.bestseller','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'lease_epoch':4}
        plan={'platform':'mercado-libre-mx','queries':['vestido uno','vestido dos','vestido tres'],'strategy':'card-visual-v3'}
        signed={'request_id':'req','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'plan':plan}; signed['digest']=canonical_hash(signed)
        class Store:
            def inspect(self,request): return {'task_type':'research.bestseller','state':'prepared','collector_plan':signed}
        with tempfile.TemporaryDirectory() as tmp, patch('optimization_worker.tab_receipt_factory.worker_records',return_value=[{'request_id':'req','claim':claim}]), patch('optimization_worker.tab_receipt_factory.fence'):
            ledger=Path(tmp)/'db'
            broker=build_native_broker(Path(tmp)/'s',ledger,'req',0,Store(),object(),'journal')
            self.assertEqual(broker.challenge['version'],4)
            with sqlite3.connect(ledger) as db:
                rows=db.execute('SELECT query FROM card_v3_queries ORDER BY query_index').fetchall()
                self.assertEqual([row[0] for row in rows],plan['queries'])

    def test_card_v3_watch_requires_all_sealed_pages_and_receipts(self):
        import sqlite3,json
        from optimization_worker.card_sales_v3_checkpoints import initialize,seal_query,finish_page
        from optimization_worker.tab_receipt_store import TabReceiptStore
        claim={'id':1,'task_type':'research.bestseller','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'lease_epoch':4}
        plan={'platform':'mercado-libre-mx','queries':['one','two','three'],'strategy':'card-visual-v3'}
        signed={'request_id':'req','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'plan':plan};signed['digest']=canonical_hash(signed)
        class Store:
            def inspect(self,request):return {'task_type':'research.bestseller','state':'prepared','collector_plan':signed}
        with tempfile.TemporaryDirectory() as tmp, patch('optimization_worker.tab_receipt_factory.worker_records',return_value=[{'request_id':'req','claim':claim}]), patch('optimization_worker.tab_receipt_factory.fence'):
            ledger=Path(tmp)/'db';TabReceiptStore(str(ledger));scope=('req',signed['digest'],4)
            binding={'tab_id':8,'window_id':9,'session_id':'a'*32,'platform':'mercado-libre-mx','plan_digest':signed['digest']}
            with sqlite3.connect(ledger) as db:
                initialize(db);db.execute('INSERT INTO bindings VALUES (?,?)',('req',json.dumps(binding)))
                for i,q in enumerate(plan['queries']):
                    seal_query(db,scope,i,q,fence=lambda:True)
                    receipt=dict(binding,version=4,lease_epoch=4,query_index=i,query=q)
                    db.execute('INSERT INTO accepted_receipts(request_id,body) VALUES (?,?)',('req',json.dumps(receipt)))
            with self.assertRaises(ValueError):build_native_broker(Path(tmp)/'s',ledger,'req',0,Store(),object(),'journal',watch_only=True)
            with sqlite3.connect(ledger) as db:
                for i in range(3):finish_page(db,scope,i,1,'end_of_results',fence=lambda:True)
            self.assertTrue(build_native_broker(Path(tmp)/'s',ledger,'req',0,Store(),object(),'journal',watch_only=True).watch_only)
    def test_visual_v2_initial_query_uses_sealed_version_three_challenge(self):
        from optimization_worker.visual_v2_checkpoints import read_queries
        import sqlite3
        claim={'id':1,'task_type':'research.bestseller','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'lease_epoch':4}
        plan={'platform':'mercado-libre-mx','queries':['vestido uno','vestido dos','vestido tres'],'strategy':'visual-v2'}
        signed={'request_id':'req','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'plan':plan}; signed['digest']=canonical_hash(signed)
        class Store:
            def inspect(self,request): return {'task_type':'research.bestseller','state':'prepared','collector_plan':signed}
        with tempfile.TemporaryDirectory() as tmp, patch('optimization_worker.tab_receipt_factory.worker_records',return_value=[{'request_id':'req','claim':claim}]), patch('optimization_worker.tab_receipt_factory.fence'):
            ledger=Path(tmp)/'db'
            broker=build_native_broker(Path(tmp)/'s',ledger,'req',0,Store(),object(),'journal')
            self.assertEqual(broker.challenge['version'],3)
            with sqlite3.connect(ledger) as db:
                self.assertEqual(read_queries(db,('req',signed['digest'],4)),plan['queries'])

    def test_staged_plan_uses_new_receipt_version(self):
        claim={'id':1,'task_type':'research.bestseller','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'lease_epoch':4}
        plan={'platform':'mercado-libre-mx','queries':['one','two','three'],'strategy':'staged-v1'}
        signed={'request_id':'req','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'plan':plan}; signed['digest']=canonical_hash(signed)
        class Store:
            def inspect(self,request): return {'task_type':'research.bestseller','state':'prepared','collector_plan':signed}
        with tempfile.TemporaryDirectory() as tmp, patch('optimization_worker.tab_receipt_factory.worker_records',return_value=[{'request_id':'req','claim':claim}]), patch('optimization_worker.tab_receipt_factory.fence'):
            broker=build_native_broker(Path(tmp)/'s',Path(tmp)/'db','req',0,Store(),object(),'journal')
            self.assertEqual(broker.challenge['version'],2)

    def test_expansion_challenge_requires_the_sealed_query_and_current_binding(self):
        import json, hashlib, sqlite3
        from optimization_worker.collector_staged_policy import initialize_expansions
        from optimization_worker.tab_receipt_store import TabReceiptStore
        claim={'id':1,'task_type':'research.bestseller','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'lease_epoch':4}
        plan={'platform':'mercado-libre-mx','queries':['one','two','three'],'strategy':'staged-v1'}
        signed={'request_id':'req','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'plan':plan}; signed['digest']=canonical_hash(signed)
        class Store:
            def inspect(self,request): return {'task_type':'research.bestseller','state':'prepared','collector_plan':signed}
        with tempfile.TemporaryDirectory() as tmp, patch('optimization_worker.tab_receipt_factory.worker_records',return_value=[{'request_id':'req','claim':claim}]), patch('optimization_worker.tab_receipt_factory.fence'):
            ledger=Path(tmp)/'db'; TabReceiptStore(str(ledger))
            binding={'tab_id':8,'window_id':9,'session_id':'a'*32,'platform':'mercado-libre-mx','plan_digest':signed['digest']}
            with sqlite3.connect(ledger) as db:
                initialize_expansions(db)
                db.execute('INSERT INTO bindings VALUES (?,?)',('req',json.dumps(binding)))
                body=json.dumps({'queries':['four']},sort_keys=True,separators=(',',':'))
                db.execute('INSERT INTO staged_expansions VALUES (?,?,?,?,?)',('req',signed['digest'],4,body,hashlib.sha256(body.encode()).hexdigest()))
            broker=build_native_broker(Path(tmp)/'s',ledger,'req',3,Store(),object(),'journal')
            self.assertEqual(broker.challenge['query'],'four')
            with self.assertRaises(ValueError):build_native_broker(Path(tmp)/'s',ledger,'req',4,Store(),object(),'journal')

    def test_dedicated_shein_requires_authorized_peer_origin(self):
        claim={'id':1,'task_type':'research.bestseller','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'lease_epoch':4}
        signed={'request_id':'req','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'plan':{'platform':'shein-us','queries':['one','two','three']}};signed['digest']=canonical_hash(signed)
        class Store:
            def inspect(self,request): return {'task_type':'research.bestseller','state':'prepared','collector_plan':signed}
        cfg={'anchor_extension_id':'a'*32,'extension_ids':['a'*32,'b'*32]}
        with tempfile.TemporaryDirectory() as tmp, patch('optimization_worker.native_collector_host.private_config',return_value=cfg), patch('optimization_worker.tab_receipt_factory.worker_records',return_value=[{'request_id':'req','claim':claim}]), patch('optimization_worker.tab_receipt_factory.fence'):
            with self.assertRaisesRegex(ValueError,'peer'):
                build_native_broker(Path(tmp)/'s',Path(tmp)/'db','req',0,Store(),object(),'journal',dedicated_required=True)
            cfg['peer_extension_id']='b'*32
            broker=build_native_broker(Path(tmp)/'s',Path(tmp)/'db','req',0,Store(),object(),'journal',dedicated_required=True)
            self.assertEqual(broker.peer_origin,'chrome-extension://'+'b'*32+'/')
    def test_dedicated_mode_refuses_missing_anchor_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'anchor'):
                build_native_broker(Path(tmp)/'s',Path(tmp)/'db','req',0,object(),object(),'journal',dedicated_required=True)
    def test_rebind_refuses_pending_external_write(self):
        from optimization_worker.tab_receipt_factory import can_rebind
        record={'state':'prepared','task_type':'research.bestseller','collector_pending':{'unknown':True}}
        self.assertFalse(can_rebind(record,{'phase':'host_pending'}))
        self.assertFalse(can_rebind({'state':'prepared','task_type':'research.bestseller'},{'phase':'host_pending','pending':{'operation':'complete'}}))
        self.assertTrue(can_rebind({'state':'prepared','task_type':'research.bestseller'},{'phase':'host_pending'}))
    def test_factory_fences_and_binds_original_signed_plan(self):
        claim={'id':1,'task_type':'research.bestseller','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'lease_epoch':4}
        plan={'platform':'mercado-libre-mx','queries':['one','two','three']}
        signed={'request_id':'req','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'plan':plan}; signed['digest']=canonical_hash(signed)
        class Store:
            def inspect(self, request): return {'task_type':'research.bestseller','state':'prepared','collector_plan':signed}
        with tempfile.TemporaryDirectory() as tmp, patch('optimization_worker.tab_receipt_factory.worker_records',return_value=[{'request_id':'req','claim':claim}]), patch('optimization_worker.tab_receipt_factory.fence') as fence:
            broker=build_native_broker(Path(tmp)/'s',Path(tmp)/'db','req',0,Store(),object(),'journal')
            self.assertEqual(broker.challenge['query'],'one')
            self.assertEqual(broker.challenge['plan_digest'],signed['digest'])
            self.assertEqual(broker.challenge['lease_epoch'],4)
            self.assertTrue(broker.fence())
            self.assertEqual(fence.call_count,2)
            signed['plan']['queries'][0]='changed'
            with self.assertRaises(ValueError): build_native_broker(Path(tmp)/'s',Path(tmp)/'db','req',0,Store(),object(),'journal')
    def test_candidate_image_stage_can_recreate_existing_session_guard_only(self):
        claim={'id':1,'task_type':'research.bestseller','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'lease_epoch':4}
        signed={'request_id':'req','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'plan':{'platform':'mercado-libre-mx','queries':['one','two','three']}};signed['digest']=canonical_hash(signed)
        class Store:
            def inspect(self, request): return {'task_type':'research.bestseller','state':'candidate_images_pending','collector_plan':signed,'collector_pending':{'result':{}}}
        binding={'tab_id':8,'window_id':9,'session_id':'a'*32,'platform':'mercado-libre-mx','plan_digest':signed['digest']}
        with tempfile.TemporaryDirectory() as tmp, patch('optimization_worker.tab_receipt_factory.worker_records',return_value=[{'request_id':'req','claim':claim}]), patch('optimization_worker.tab_receipt_factory.fence'), patch('optimization_worker.tab_receipt_factory.TabReceiptStore') as ledger:
            ledger.return_value.current_binding.return_value=binding
            ledger.return_value.next_query_index.return_value=3
            broker=build_native_broker(Path(tmp)/'s',Path(tmp)/'db','req',0,Store(),object(),'journal',watch_only=True)
            self.assertEqual(broker.initial_tab,8)
            with self.assertRaises(ValueError): build_native_broker(Path(tmp)/'s',Path(tmp)/'db','req',0,Store(),object(),'journal')
    def test_candidate_images_rebind_only_after_all_uploads_confirmed(self):
        from optimization_worker.tab_receipt_factory import can_rebind
        run={'phase':'host_pending'}
        record={'task_type':'research.bestseller','state':'candidate_images_pending','collector_artifact_id':'12','collector_evidence':{},'collector_pending':{'result':{},'candidate_uploads':{'one':{'state':'confirmed','artifact_id':'13'}}}}
        self.assertTrue(can_rebind(record,run))
        record['collector_pending']['candidate_uploads']['one']['state']='unknown'
        self.assertFalse(can_rebind(record,run))
        record['collector_pending']['candidate_uploads']['one']['state']='confirmed'
        self.assertFalse(can_rebind(record,dict(run,pending={'operation':'complete'})))
    def test_rebind_preserves_confirmed_candidate_images_and_requires_new_challenge(self):
        claim={'id':1,'task_type':'research.bestseller','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'lease_epoch':4}
        signed={'request_id':'req','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'plan':{'platform':'mercado-libre-mx','queries':['one','two','three']}};signed['digest']=canonical_hash(signed)
        record={'task_type':'research.bestseller','state':'candidate_images_pending','collector_plan':signed,'collector_artifact_id':'12','collector_evidence':{},'collector_pending':{'result':{},'candidate_uploads':{'one':{'state':'confirmed','artifact_id':'13'}}}}
        class Store:
            def inspect(self, request): return record
        binding={'tab_id':8,'window_id':9,'session_id':'a'*32,'platform':'mercado-libre-mx','plan_digest':signed['digest']}
        row={'request_id':'req','claim':claim,'phase':'host_pending'}
        with tempfile.TemporaryDirectory() as tmp, patch('optimization_worker.tab_receipt_factory.worker_records',return_value=[row]), patch('optimization_worker.tab_receipt_factory.fence'), patch('optimization_worker.tab_receipt_factory.TabReceiptStore') as ledger:
            ledger.return_value.current_binding.return_value=binding
            broker=build_native_broker(Path(tmp)/'s',Path(tmp)/'db','req',0,Store(),object(),'journal',rebind=True)
            self.assertIsNone(broker.initial_tab)
            self.assertTrue(broker.reconcile())
            self.assertEqual(record['collector_pending']['candidate_uploads']['one']['artifact_id'],'13')
            record['collector_pending']['candidate_uploads']['one']['state']='unknown'
            self.assertFalse(broker.reconcile())
            with self.assertRaises(ValueError): build_native_broker(Path(tmp)/'s',Path(tmp)/'db','req',0,Store(),object(),'journal',rebind=True)

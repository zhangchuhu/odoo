import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from optimization_worker.tab_receipt_store import TabReceiptStore


class TabReceiptStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TabReceiptStore(str(Path(self.tmp.name) / 'receipts.sqlite'))
        self.request = dict(version=1, request_id='r1', nonce='a'*32, lease_epoch=1,
                            plan_digest='b'*64, platform='mercado-libre-mx', query_index=0, query='vestido')
        self.receipt = dict(self.request, session_id='c'*32, tab_id=123, window_id=1,
                            collector_tab_id=123, observed_at=datetime.now(timezone.utc).isoformat(),
                            url='https://listado.mercadolibre.com.mx/vestido')

    def tearDown(self): self.tmp.cleanup()

    def test_single_use_receipt_and_stable_tab(self):
        self.store.issue(self.request)
        self.store.accept(self.receipt, lambda: True)
        with self.assertRaises(ValueError): self.store.accept(self.receipt, lambda: True)
        later = dict(self.request, nonce='d'*32)
        self.store.issue(later)
        with self.assertRaises(ValueError): self.store.accept(dict(self.receipt, nonce='d'*32, tab_id=124), lambda: True)

    def test_card_v3_receipts_are_bound_to_sealed_queries(self):
        import sqlite3
        from optimization_worker.card_sales_v3_checkpoints import initialize, seal_query
        scope = ('r1', 'b'*64, 1)
        req = dict(self.request, version=4)
        with self.assertRaises(ValueError): self.store.issue(req)
        with sqlite3.connect(self.store.path) as db:
            initialize(db)
            for i, query in enumerate(['vestido', 'otro', 'tercero']):
                seal_query(db, scope, i, query, fence=lambda: True)
        with self.assertRaises(ValueError): self.store.issue(dict(req, query='incorrecto'))
        self.store.issue(req)
        self.store.accept(dict(self.receipt, **req), lambda: True)
        self.assertEqual(self.store.resume_index(*scope[:2], ['vestido', 'otro', 'tercero'], 1, card_v3=True), 1)
        with self.assertRaises(ValueError): self.store.issue(dict(req, nonce='d'*32, platform='shein-us'))

    def test_card_v3_state_seals_initial_queries_and_returns_next_page(self):
        scope=('r1','b'*64,1);queries=['vestido','otro','tercero']
        state=self.store.card_v3_state(*scope,queries,lambda:True)
        self.assertEqual(state['queries'],queries)
        self.assertEqual(state['next_page'],(0,1))
        self.assertIsNone(state['next_url'])
        with self.assertRaises(ValueError):self.store.card_v3_state(*scope,['changed','otro','tercero'],lambda:True)
        with self.assertRaises(ValueError):self.store.card_v3_state(*scope,queries,lambda:False)

    def test_card_v3_state_freezes_when_all_nine_queries_are_exhausted(self):
        import sqlite3
        from optimization_worker.card_sales_v3_checkpoints import seal_query,finish_page
        scope=('r1','b'*64,1);queries=['vestido','otro','tercero']
        self.store.card_v3_state(*scope,queries,lambda:True)
        with sqlite3.connect(self.store.path) as db:
            for i in range(3,9):seal_query(db,scope,i,'extra '+str(i),fence=lambda:True)
            for i in range(9):finish_page(db,scope,i,1,'end_of_results',fence=lambda:True)
        state=self.store.card_v3_state(*scope,queries,lambda:True)
        self.assertIsNone(state['next_page'])
        with sqlite3.connect(self.store.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM card_v3_meta').fetchone()[0],1)

    def test_card_v3_resume_reconfirms_old_queries_before_continuing_pages(self):
        import sqlite3,json
        from optimization_worker.card_sales_v3_checkpoints import finish_page
        scope=('r1','b'*64,1);queries=['vestido','otro','tercero']
        self.store.card_v3_state(*scope,queries,lambda:True)
        with sqlite3.connect(self.store.path) as db:
            for i in range(3):finish_page(db,scope,i,1,'has_next',next_url='https://listado.mercadolibre.com.mx/page2',fence=lambda:True)
        self.assertEqual(self.store.card_v3_resume_step(*scope,queries),(0,0))
        binding=dict(session_id='c'*32,tab_id=123,window_id=1,plan_digest='b'*64,platform='mercado-libre-mx')
        with sqlite3.connect(self.store.path) as db:
            db.execute('INSERT INTO bindings VALUES (?,?)',('r1',json.dumps(binding)))
            for i,q in enumerate(queries):
                receipt=dict(binding,version=4,lease_epoch=1,query_index=i,query=q,url='https://listado.mercadolibre.com.mx/'+q)
                db.execute('INSERT INTO accepted_receipts(request_id,body) VALUES (?,?)',('r1',json.dumps(receipt)))
        self.assertEqual(self.store.card_v3_resume_step(*scope,queries),(0,-1))
        self.assertEqual(self.store.card_v3_resume_url(*scope,(0,-1)),'https://listado.mercadolibre.com.mx/vestido')
        with sqlite3.connect(self.store.path) as db:
            db.execute('CREATE TABLE card_v3_receipt_challenges (nonce TEXT,body TEXT,issued_at TEXT,used INTEGER)')
            proof=dict(request_id='r1',plan_digest='b'*64,lease_epoch=1,query_index=0,query='vestido',page=1,session_id='c'*32,tab_id=123,window_id=1)
            db.execute('INSERT INTO card_v3_receipt_challenges VALUES (?,?,?,1)',('e'*32,json.dumps(proof),'now'))
        self.assertEqual(self.store.card_v3_resume_step(*scope,queries),(0,2))

    def test_session_change_fails_closed_and_lease_rechecked(self):
        self.store.issue(self.request)
        with self.assertRaises(ValueError): self.store.accept(self.receipt, lambda: False)
        self.store.accept(self.receipt, lambda: True)
        later = dict(self.request, nonce='e'*32)
        self.store.issue(later)
        with self.assertRaises(ValueError): self.store.accept(dict(self.receipt, nonce='e'*32, session_id='f'*32), lambda: True)

    def test_rejects_wrong_collector_and_unrelated_query(self):
        self.store.issue(self.request)
        with self.assertRaises(ValueError): self.store.accept(dict(self.receipt, collector_tab_id=124), lambda: True)
        with self.assertRaises(ValueError): self.store.accept(dict(self.receipt, query='otro'), lambda: True)
        self.store.accept(self.receipt, lambda: True)

    def test_rebind_requires_verified_prior_request_and_keeps_history(self):
        self.store.issue(self.request)
        self.store.accept(self.receipt, lambda: True)
        next_request = dict(self.request, nonce='f'*32, lease_epoch=2)
        self.store.issue(next_request)
        new = dict(self.receipt, nonce='f'*32, lease_epoch=2, session_id='e'*32, tab_id=456, collector_tab_id=456)
        with self.assertRaises(ValueError): self.store.rebind(new, lambda: True, lambda: False)
        self.store.rebind(new, lambda: True, lambda: True)
        self.assertEqual(len(self.store.history('r1')), 2)
        self.assertEqual(self.store.resume_index('r1','b'*64,['vestido','otro','tercero'],2),1)
        self.assertFalse(self.store.verified_queries('r1','b'*64,['vestido','otro','tercero'],2))

    def test_all_three_queries_need_individual_receipts(self):
        self.store.issue(self.request)
        self.store.accept(self.receipt, lambda: True)
        queries=['vestido','otro','tercero']
        self.assertEqual(self.store.resume_index('r1','b'*64,queries,1),1)
        self.assertEqual(self.store.resume_index('r1','b'*64,queries,2),0)
        self.assertFalse(self.store.verified_queries('r1','b'*64,queries,1))
        for i in (1,2):
            req=dict(self.request,nonce=str(i)*32,query_index=i,query=queries[i])
            self.store.issue(req)
            self.store.accept(dict(self.receipt,**req),lambda:True)
        self.assertTrue(self.store.verified_queries('r1','b'*64,queries,1))
        self.assertEqual(self.store.resume_index('r1','b'*64,queries,1),3)
        self.assertFalse(self.store.verified_queries('r1','b'*64,queries,2))
        self.assertFalse(self.store.verified_queries('r1','b'*64,['vestido','wrong','tercero'],1))
        self.assertFalse(self.store.verified_queries('r1','b'*64,queries,1,staged=True))

    def test_shein_receipt_requires_shein_host(self):
        req=dict(self.request,platform='shein-us'); self.store.issue(req)
        with self.assertRaises(ValueError): self.store.accept(dict(self.receipt,**req),lambda:True)
        self.store.accept(dict(self.receipt,**req,url='https://us.shein.com/search'),lambda:True)

    def test_staged_receipts_require_sealed_expansion_and_resume_in_order(self):
        import sqlite3, json, hashlib
        queries = ['vestido', 'otro', 'tercero', 'conjunto largo']
        for i, query in enumerate(queries[:3]):
            req = dict(self.request, version=2, nonce=str(i)*32, query_index=i, query=query)
            self.store.issue(req)
            self.store.accept(dict(self.receipt, **req), lambda: True)
        extension = dict(self.request, version=2, nonce='d'*32, query_index=3, query=queries[3])
        with self.assertRaises(ValueError): self.store.issue(extension)
        from optimization_worker.collector_staged_policy import initialize_expansions
        with sqlite3.connect(self.store.path) as db:
            initialize_expansions(db)
            body = json.dumps({'queries': queries[3:]}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            db.execute('INSERT INTO staged_expansions VALUES (?,?,?,?,?)',
                       ('r1', 'b'*64, 1, body, hashlib.sha256(body.encode()).hexdigest()))
        with self.assertRaises(ValueError): self.store.issue(dict(extension, query='incorrecto'))
        self.store.issue(extension)
        self.store.accept(dict(self.receipt, **extension), lambda: True)
        cards=[dict(query=queries[3],rank=1,is_ad=False,title='Dress',url='https://articulo.mercadolibre.com.mx/MLM-123456-item',product_id='MLM123456')]
        with self.assertRaises(ValueError):self.store.record_cards('r1','b'*64,1,3,queries[3],cards,lambda:True)
        # The first three groups must already be durably confirmed.
        with sqlite3.connect(self.store.path) as db:
            for index in range(3):db.execute('INSERT INTO query_cards VALUES (?,?,?,?,?)',('r1','b'*64,1,index,'[]'))
        self.store.record_cards('r1','b'*64,1,3,queries[3],cards,lambda:True,exhausted=True)
        self.assertTrue(self.store.query_exhausted('r1','b'*64,1,3))
        self.assertEqual(self.store.resume_index('r1', 'b'*64, queries, 1, staged=True), 4)
        self.assertTrue(self.store.verified_queries('r1', 'b'*64, queries, 1, staged=True))
        self.assertEqual(self.store.next_query_index('r1','b'*64,queries,1,staged=True),4)
        self.assertEqual(self.store.sealed_queries('r1','b'*64,1,lambda:True),['conjunto largo'])
        with self.assertRaises(ValueError): self.store.sealed_queries('r1','b'*64,1,lambda:False)
        with self.assertRaises(ValueError): self.store.issue(dict(extension, version=1, nonce='e'*32))

    def test_restart_during_rebind_uses_new_tab_receipt_prefix(self):
        queries = ['vestido', 'vestido azul', 'vestido largo']
        for i, query in enumerate(queries):
            req = dict(self.request, nonce=str(i)*32, query_index=i, query=query)
            self.store.issue(req)
            self.store.accept(dict(self.receipt, **req), lambda: True)
            cards = [dict(query=query, rank=n+1, is_ad=False, title='vestido',
                          url=f'https://articulo.mercadolibre.com.mx/MLM-{100000+n}-item',
                          product_id=f'MLM{100000+n}') for n in range(30)]
            self.store.record_cards('r1', req['plan_digest'], 1, i, query, cards, lambda: True)
        rebind = dict(self.request, nonce='f'*32)
        self.store.issue(rebind)
        self.store.rebind(dict(self.receipt, **rebind, session_id='e'*32), lambda: True, lambda: True)
        reopened = TabReceiptStore(self.store.path)
        self.assertEqual(reopened.completed_card_index('r1', self.request['plan_digest'], 1), 3)
        self.assertEqual(reopened.next_query_index('r1', self.request['plan_digest'], queries, 1), 1)

class CardProgressTest(unittest.TestCase):
    def test_cards_without_receipt_never_advance(self):
        import tempfile, os
        from optimization_worker.tab_receipt_store import TabReceiptStore
        with tempfile.TemporaryDirectory() as tmp:
            store=TabReceiptStore(os.path.join(tmp,'x.sqlite'))
            self.assertEqual(store.completed_card_index('r','a'*64,1),0)
            with self.assertRaises(ValueError):
                store.record_cards('r','a'*64,1,0,'dress',[],lambda:True)
            self.assertEqual(store.completed_card_index('r','a'*64,1),0)
    def test_cards_persist_after_receipt_and_idempotently_resume(self):
        import tempfile,os
        from optimization_worker.tab_receipt_store import TabReceiptStore
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as tmp:
            store=TabReceiptStore(os.path.join(tmp,'x.sqlite'))
            req={'version':1,'request_id':'r','nonce':'a'*32,'lease_epoch':1,'plan_digest':'b'*64,'platform':'mercado-libre-mx','query_index':0,'query':'vestido'}
            receipt={**req,'session_id':'c'*32,'tab_id':1,'window_id':2,'collector_tab_id':1,'url':'https://listado.mercadolibre.com.mx/vestido','observed_at':datetime.now(timezone.utc).isoformat()}
            store.issue(req);store.accept(receipt,lambda:True)
            cards=[{'query':'vestido','rank':i+1,'is_ad':False,'title':'title','url':f'https://articulo.mercadolibre.com.mx/MLM-{100000+i}-item','product_id':f'MLM{100000+i}','thumbnail_url':None} for i in range(30)]
            self.assertEqual(store.completed_card_index('r','b'*64,1),0)
            store.record_cards('r','b'*64,1,0,'vestido',cards,lambda:True)
            store.record_cards('r','b'*64,1,0,'vestido',cards,lambda:True)
            self.assertEqual(store.completed_card_index('r','b'*64,1),1)
            bad=[dict(c) for c in cards];bad[0]['product_id']='MLM999'
            with self.assertRaises(ValueError):store.record_cards('r','b'*64,1,0,'vestido',bad,lambda:True)

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

    def test_shein_receipt_requires_shein_host(self):
        req=dict(self.request,platform='shein-us'); self.store.issue(req)
        with self.assertRaises(ValueError): self.store.accept(dict(self.receipt,**req),lambda:True)
        self.store.accept(dict(self.receipt,**req,url='https://us.shein.com/search'),lambda:True)

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

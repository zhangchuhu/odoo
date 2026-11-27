import sqlite3
import unittest
from optimization_worker.card_sales_v3_checkpoints import initialize,seal_query,append_page_batch,finish_page
from optimization_worker.card_sales_v3_pipeline import initialize_pipeline
from optimization_worker.card_sales_v3_recovery import restore_scope

class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close);initialize(self.db);initialize_pipeline(self.db)
        self.old=('old','a'*64,1);self.new=('new','b'*64,2)
        seal_query(self.db,self.old,0,'vestido',fence=lambda:True)
        card=dict(query='vestido',product_id='MLM1',url='https://articulo.mercadolibre.com.mx/MLM1',rank=1,first_seen=1,sales_text='+500 vendidos')
        append_page_batch(self.db,self.old,0,1,0,[card],fence=lambda:True)
        finish_page(self.db,self.old,0,1,'end_of_results',fence=lambda:True)
        self.db.execute('INSERT INTO card_v3_page_evidence VALUES (?,?,?,?,?,?,?,?,?)',(*self.old,0,1,'https://listado.mercadolibre.com.mx/vestido','c'*32,'disabled_next',3))
    def test_authorized_recovery_preserves_evidence_and_requires_fresh_browser_receipt(self):
        result=restore_scope(self.db,self.old,self.new,'hash','hash',authorize=lambda:True,fence=lambda:True)
        self.assertTrue(result['requires_browser_reconfirmation'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM card_v3_batches WHERE request_id=?',('new',)).fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT end_evidence FROM card_v3_page_evidence WHERE request_id=?',('new',)).fetchone(),('disabled_next',))
        self.assertEqual(restore_scope(self.db,self.old,self.new,'hash','hash',authorize=lambda:True,fence=lambda:True),result)
    def test_denied_or_incompatible_recovery_has_no_writes(self):
        with self.assertRaises(ValueError):restore_scope(self.db,self.old,self.new,'hash','other',authorize=lambda:True,fence=lambda:True)
        with self.assertRaises(ValueError):restore_scope(self.db,self.old,self.new,'hash','hash',authorize=lambda:False,fence=lambda:True)
        self.assertEqual(self.db.execute('SELECT count(*) FROM card_v3_batches WHERE request_id=?',('new',)).fetchone()[0],0)
    def test_started_target_cannot_import_previous_business_evidence(self):
        self.db.execute('CREATE TABLE accepted_receipts (request_id TEXT,body TEXT)')
        self.db.execute('INSERT INTO accepted_receipts VALUES (?,?)',('new','{}'))
        with self.assertRaises(ValueError):restore_scope(self.db,self.old,self.new,'hash','hash',authorize=lambda:True,fence=lambda:True)

    def test_recovery_rejects_unverified_page_evidence(self):
        from optimization_worker.card_sales_v3_recovery import validate_source_receipts
        from datetime import datetime,timezone,timedelta
        import json
        self.db.execute('CREATE TABLE accepted_receipts (request_id TEXT,body TEXT)')
        self.db.execute('CREATE TABLE card_v3_receipt_challenges (nonce TEXT,body TEXT,issued_at TEXT,used INTEGER)')
        with self.assertRaises(ValueError):validate_source_receipts(self.db,self.old)
        stamp=datetime.now(timezone.utc).isoformat()
        receipt=dict(version=4,request_id='old',plan_digest='a'*64,lease_epoch=1,query_index=0,query='vestido',platform='mercado-libre-mx',session_id='d'*32,tab_id=1,window_id=2,collector_tab_id=1,observed_at=stamp)
        proof=dict(request_id='old',plan_digest='a'*64,lease_epoch=1,query_index=0,query='vestido',page=1,page_url='https://listado.mercadolibre.com.mx/vestido',session_id='d'*32,tab_id=1,window_id=2,nonce='c'*32)
        self.db.execute('INSERT INTO accepted_receipts VALUES (?,?)',('old',json.dumps(receipt)))
        self.db.execute('INSERT INTO card_v3_receipt_challenges VALUES (?,?,?,1)',('c'*32,json.dumps(proof),stamp))
        with self.assertRaises(ValueError):validate_source_receipts(self.db,self.old)
        self.db.execute('INSERT INTO card_v3_batch_evidence VALUES (?,?,?,?,?,?,?,?,?,?)',(*self.old,0,1,0,'d'*64,'shot.jpg',120,120))
        self.assertTrue(validate_source_receipts(self.db,self.old,verify_photo=lambda *_:True))
        old=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
        self.db.execute('UPDATE card_v3_receipt_challenges SET issued_at=?',(old,))
        with self.assertRaises(ValueError):validate_source_receipts(self.db,self.old)

    def test_home_recovery_requires_homepage_receipts_and_rejects_later_pages(self):
        from optimization_worker.card_sales_v3_recovery import validate_source_receipts
        from datetime import datetime,timezone
        import json
        self.db.execute('CREATE TABLE accepted_receipts (request_id TEXT,body TEXT)')
        self.db.execute('CREATE TABLE card_v3_receipt_challenges (nonce TEXT,body TEXT,issued_at TEXT,used INTEGER)')
        stamp=datetime.now(timezone.utc).isoformat()
        receipt=dict(version=5,request_id='old',plan_digest='a'*64,lease_epoch=1,query_index=0,query='vestido',platform='mercado-libre-mx',session_id='d'*32,tab_id=1,window_id=2,observed_at=stamp)
        proof=dict(request_id='old',plan_digest='a'*64,lease_epoch=1,query_index=0,query='vestido',page=1,page_url='https://listado.mercadolibre.com.mx/vestido',session_id='d'*32,tab_id=1,window_id=2,nonce='c'*32)
        self.db.execute('INSERT INTO accepted_receipts VALUES (?,?)',('old',json.dumps(receipt)))
        self.db.execute('INSERT INTO card_v3_receipt_challenges VALUES (?,?,?,1)',('c'*32,json.dumps(proof),stamp))
        self.db.execute('INSERT INTO card_v3_batch_evidence VALUES (?,?,?,?,?,?,?,?,?,?)',(*self.old,0,1,0,'d'*64,'shot.jpg',120,120))
        self.db.execute("UPDATE card_v3_pages SET status='home_complete' WHERE request_id='old'")
        self.assertTrue(validate_source_receipts(self.db,self.old,verify_photo=lambda *_:True,homepage_only=True))
        self.db.execute('INSERT INTO card_v3_pages VALUES (?,?,?,?,?,?)',(*self.old,0,2,'end_of_results'))
        with self.assertRaises(ValueError):validate_source_receipts(self.db,self.old,verify_photo=lambda *_:True,homepage_only=True)

    def test_original_scoped_authorization_is_required_for_task_restore(self, strategy='card-visual-v3'):
        import tempfile,json
        from pathlib import Path
        from unittest.mock import patch
        from datetime import datetime,timezone
        from optimization_worker.domain import canonical_hash
        from optimization_worker.tab_receipt_store import TabReceiptStore
        from optimization_worker.prepared_search_recovery import restore_card_v3_checkpoint
        plan={'strategy':strategy,'platform':'mercado-libre-mx','queries':['vestido','otro','tercero']}
        oldclaim={'id':1,'company_id':2,'snapshot_id':3,'input_hash':'x','task_type':'research.bestseller','lease_epoch':1,'payload':{}}
        claim=dict(oldclaim,lease_epoch=2,lease_token='token')
        binding={'request_id':'old','company_id':2,'snapshot_id':3,'input_hash':'x','plan':plan};binding['digest']=canonical_hash(binding)
        old={'request_id':'old','task_type':'research.bestseller','state':'prepared','payload':{},'collector_plan':binding}
        new={'request_id':'new','task_type':'research.bestseller','state':'prepared','payload':{}}
        store=type('Store',(),{'inspect':lambda self,r:old if r=='old' else new})()
        runs=[{'request_id':'old','claim':oldclaim,'done':True}]
        with tempfile.TemporaryDirectory() as tmp:
            ledger=TabReceiptStore(str(Path(tmp)/'db'));scope=('old',binding['digest'],1);target=('new','d'*64,2)
            with sqlite3.connect(ledger.path) as db:
                initialize(db);initialize_pipeline(db)
                for i,q in enumerate(plan['queries']):seal_query(db,scope,i,q,fence=lambda:True)
                if strategy=='card-home-v4':db.execute('INSERT INTO card_v3_pages VALUES (?,?,?,?,?,?)',(*scope,0,1,'home_complete'))
                else:finish_page(db,scope,0,1,'end_of_results',fence=lambda:True)
                db.execute('CREATE TABLE card_v3_receipt_challenges (nonce TEXT,body TEXT,issued_at TEXT,used INTEGER)')
                stamp=datetime.now(timezone.utc).isoformat();url='https://listado.mercadolibre.com.mx/vestido'
                receipt=dict(version=5 if strategy=='card-home-v4' else 4,request_id='old',plan_digest=binding['digest'],lease_epoch=1,query_index=0,query='vestido',platform='mercado-libre-mx',session_id='e'*32,tab_id=1,window_id=2,collector_tab_id=1,observed_at=stamp)
                proof=dict(request_id='old',plan_digest=binding['digest'],lease_epoch=1,query_index=0,query='vestido',page=1,page_url=url,session_id='e'*32,tab_id=1,window_id=2,nonce='c'*32)
                db.execute('INSERT INTO accepted_receipts(request_id,body) VALUES (?,?)',('old',json.dumps(receipt)))
                db.execute('INSERT INTO card_v3_receipt_challenges VALUES (?,?,?,1)',('c'*32,json.dumps(proof),stamp))
                db.execute('INSERT INTO card_v3_page_evidence VALUES (?,?,?,?,?,?,?,?,?)',(*scope,0,1,url,'c'*32,'empty_results',3))
            class Client:
                def request(self,method,path,data):return dict(task_id=1,lease_epoch=2,request_id='new',source_request_id='old',source_epoch=1,allowed=True,snapshot_id=3,input_hash='x')
            with patch('optimization_worker.collector_cli.bind_plan',return_value='d'*64),patch('optimization_worker.home_suggestion_bootstrap.restore_home_suggestions') as suggestions:
                self.assertTrue(restore_card_v3_checkpoint(ledger,store,runs,Client(),'new',claim,lambda:None))
                if strategy=='card-home-v4':suggestions.assert_called_once()
            with sqlite3.connect(ledger.path) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM card_v3_pages WHERE request_id=?',('new',)).fetchone()[0],1)
                self.assertEqual(db.execute('SELECT count(*) FROM accepted_receipts WHERE request_id=?',('new',)).fetchone()[0],0)

    def test_home_scoped_authorization_recovers_business_checkpoints_without_old_tabs(self):
        self.test_original_scoped_authorization_is_required_for_task_restore('card-home-v4')

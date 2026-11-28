import unittest
from datetime import datetime,timezone,timedelta
from optimization_worker.card_sales_v3_receipts import verify_page_receipt, PageReceiptGuard


class PageReceiptTest(unittest.TestCase):
    def test_shein_receipt_requires_exact_homepage_and_platform(self):
        expected=dict(request_id='r',plan_digest='a'*64,lease_epoch=1,query_index=0,page=1,query='women skirt set',nonce='c'*32,
                      tab_id=3,window_id=4,session_id='b'*32,platform='shein-us',page_url='https://us.shein.com/pdsearch/women%20skirt%20set/')
        self.assertTrue(verify_page_receipt(expected,dict(expected,observed_at=datetime.now(timezone.utc).isoformat())))
        slug=dict(expected,page_url='https://us.shein.com/pdsearch/women-skirt-set/')
        self.assertTrue(verify_page_receipt(slug,dict(slug,observed_at=datetime.now(timezone.utc).isoformat())))
        for change in ({'page':2},{'page_url':'https://us.shein.com/pdsearch/other/'},{'page_url':'https://us.shein.com/pdsearch/women%20skirt%20set/?page=2'}):
            invalid=dict(expected,**change)
            with self.assertRaises(ValueError):verify_page_receipt(invalid,dict(invalid,observed_at=datetime.now(timezone.utc).isoformat()))

    def test_shein_page_challenge_requires_accepted_shein_binding(self):
        import sqlite3,json
        from optimization_worker.card_sales_v3_checkpoints import initialize
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);initialize(db);guard=PageReceiptGuard(db)
        db.execute('CREATE TABLE accepted_receipts (request_id TEXT, body TEXT)')
        binding=dict(version=6,platform='shein-us',request_id='r',plan_digest='a'*64,lease_epoch=1,query_index=0,query='dress',tab_id=3,window_id=4,session_id='b'*32,url='https://us.shein.com/pdsearch/dress/')
        db.execute('insert into card_v3_queries values (?,?,?,?,?)',('r','a'*64,1,0,'dress'))
        db.execute('insert into accepted_receipts values (?,?)',('r',json.dumps(binding)))
        challenge=guard.issue_for_page(binding,1,fence=lambda:True)
        self.assertEqual(challenge['platform'],'shein-us')
        self.assertTrue(guard.confirm(dict(challenge,observed_at=datetime.now(timezone.utc).isoformat()),fence=lambda:True))
        with self.assertRaises(ValueError):guard.issue_for_page(binding,2,fence=lambda:True)
    def test_page_receipt_checks_exact_page_and_binding(self):
        now=datetime.now(timezone.utc).isoformat()
        expected=dict(request_id='r',plan_digest='a'*64,lease_epoch=1,query_index=0,page=2,query='vestido',nonce='c'*32,
                      tab_id=3,window_id=4,session_id='b'*32,page_url='https://listado.mercadolibre.com.mx/vestido_Desde_51')
        receipt=dict(expected,observed_at=now)
        self.assertTrue(verify_page_receipt(expected,receipt))
        fragment=dict(expected,page_url=expected['page_url']+'#D[A:vestido]')
        self.assertTrue(verify_page_receipt(fragment,dict(fragment,observed_at=now)))
        for key,value in (('page',3),('query','wrong'),('tab_id',8),('page_url','https://evil.test/vestido')):
            with self.assertRaises(ValueError):verify_page_receipt(expected,dict(receipt,**{key:value}))
    def test_rejects_non_marketplace_urls_and_old_receipts(self):
        expected=dict(request_id='r',plan_digest='a'*64,lease_epoch=1,query_index=0,page=1,query='vestido',nonce='c'*32,
                      tab_id=3,window_id=4,session_id='b'*32,page_url='https://evil.test/vestido')
        with self.assertRaises(ValueError):verify_page_receipt(expected,dict(expected,observed_at=datetime.now(timezone.utc).isoformat()))
        expected['page_url']='https://listado.mercadolibre.com.mx/vestido'
        with self.assertRaises(ValueError):verify_page_receipt(expected,dict(expected,observed_at=(datetime.now(timezone.utc)-timedelta(seconds=20)).isoformat()))
    def test_page_challenge_is_one_use_and_fenced(self):
        import sqlite3
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        guard=PageReceiptGuard(db)
        request=dict(request_id='r',plan_digest='a'*64,lease_epoch=1,query_index=0,page=1,query='vestido',
                     tab_id=3,window_id=4,session_id='b'*32,page_url='https://listado.mercadolibre.com.mx/vestido')
        challenge=guard.issue(request,fence=lambda:True)
        receipt=dict(challenge,observed_at=datetime.now(timezone.utc).isoformat())
        self.assertTrue(guard.confirm(receipt,fence=lambda:True))
        with self.assertRaises(ValueError):guard.confirm(receipt,fence=lambda:True)
        challenge=guard.issue(request,fence=lambda:True)
        with self.assertRaises(ValueError):guard.confirm(dict(challenge,observed_at=datetime.now(timezone.utc).isoformat()),fence=lambda:False)
    def test_next_page_url_is_derived_from_confirmed_prior_page(self):
        import sqlite3,json
        from optimization_worker.card_sales_v3_checkpoints import initialize,seal_query,finish_page
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);initialize(db);guard=PageReceiptGuard(db)
        scope=('r','a'*64,1);seal_query(db,scope,0,'vestido',fence=lambda:True)
        db.execute('CREATE TABLE accepted_receipts (request_id TEXT,body TEXT)')
        binding=dict(version=4,platform='mercado-libre-mx',request_id='r',plan_digest='a'*64,lease_epoch=1,query_index=0,query='vestido',tab_id=3,window_id=4,session_id='b'*32,url='https://listado.mercadolibre.com.mx/vestido')
        db.execute('INSERT INTO accepted_receipts VALUES (?,?)',('r',json.dumps(dict(binding,version=3))))
        with self.assertRaises(ValueError):guard.issue_for_page(binding,1,fence=lambda:True)
        db.execute('INSERT INTO accepted_receipts VALUES (?,?)',('r',json.dumps(binding)))
        first=guard.issue_for_page(binding,1,fence=lambda:True)
        guard.confirm(dict(first,observed_at=datetime.now(timezone.utc).isoformat()),fence=lambda:True)
        next_url='https://listado.mercadolibre.com.mx/vestido_Desde_51'
        finish_page(db,scope,0,1,'has_next',next_url=next_url,fence=lambda:True)
        second=guard.issue_for_page(binding,2,fence=lambda:True)
        self.assertEqual(second['page_url'],next_url)
        with self.assertRaises(ValueError):guard.issue_for_page(dict(binding,session_id='d'*32),2,fence=lambda:True)

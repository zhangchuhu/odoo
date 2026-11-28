import sqlite3
import unittest

from optimization_worker.home_collection_checkpoint import finish_homepage, append_home_batch
from optimization_worker.card_sales_v3_checkpoints import initialize


class HomeCompletionTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close);initialize(self.db)
        self.scope=('request','digest',1);self.db.execute('insert into card_v3_queries values (?,?,?,?,?)',(*self.scope,0,'query'))
        self.data={'url':'https://listado.mercadolibre.com.mx/query','receipt_nonce':'a'*32,'end_checks':3,'no_results':True}

    def test_complete_is_idempotent_and_has_no_next_page(self):
        finish_homepage(self.db,self.scope,0,self.data,fence=lambda:True,verify=lambda _:True)
        finish_homepage(self.db,self.scope,0,self.data,fence=lambda:True,verify=lambda _:True)
        self.assertEqual(self.db.execute('select page,status from card_v3_pages').fetchall(),[(1,'home_complete')])
        self.assertEqual(self.db.execute('select count(*) from card_v3_navigation').fetchone()[0],0)

    def test_shein_homepage_is_platform_scoped(self):
        data=dict(self.data,url='https://us.shein.com/pdsearch/query/')
        with self.assertRaises(ValueError):finish_homepage(self.db,self.scope,0,data,fence=lambda:True,verify=lambda _:True)
        finish_homepage(self.db,self.scope,0,data,fence=lambda:True,verify=lambda _:True,platform='shein-us')

    def test_missing_proof_wrong_site_or_no_empty_evidence_is_rejected(self):
        with self.assertRaises(ValueError):finish_homepage(self.db,self.scope,0,self.data,fence=lambda:False,verify=lambda _:True)
        with self.assertRaises(ValueError):finish_homepage(self.db,self.scope,0,dict(self.data,url='https://example.org'),fence=lambda:True,verify=lambda _:True)
        with self.assertRaises(ValueError):finish_homepage(self.db,self.scope,0,dict(self.data,no_results=False),fence=lambda:True,verify=lambda _:True)
        with self.assertRaises(ValueError):finish_homepage(self.db,self.scope,0,self.data,fence=lambda:True,verify=lambda _:False)

    def test_batch_retry_is_idempotent_and_cannot_append_after_finish(self):
        card={'query':'query','product_id':'MLM123','rank':1,'url':'https://articulo.mercadolibre.com.mx/MLM-123-item'}
        append_home_batch(self.db,self.scope,0,0,[card],fence=lambda:True)
        append_home_batch(self.db,self.scope,0,0,[card],fence=lambda:True)
        finish_homepage(self.db,self.scope,0,dict(self.data,no_results=False),fence=lambda:True,verify=lambda _:True)
        with self.assertRaises(ValueError):append_home_batch(self.db,self.scope,0,1,[card],fence=lambda:True)
        self.assertEqual(self.db.execute('select page,batch_index from card_v3_batches').fetchall(),[(1,0)])

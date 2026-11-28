import sqlite3
import unittest


class VerificationWatchTests(unittest.TestCase):
    def setUp(self):
        from optimization_worker.home_verification_watch import initialize
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close);initialize(self.db)
        self.task=(2,42,3,1);self.binding={'tab_id':7,'window_id':8,'session_id':'a'*32}

    def test_search_only_becomes_ready_after_same_page_is_verified(self):
        from optimization_worker.home_verification_watch import arm,observe,ready,armed_info
        arm(self.db,'request',self.task,'search',{'query':'conjunto mujer falda'},self.binding,fence=lambda:True)
        self.assertEqual(armed_info(self.db,'request',self.binding)['task'],self.task)
        with self.assertRaises(ValueError):armed_info(self.db,'request',dict(self.binding,tab_id=4))
        wrong={'url':'https://listado.mercadolibre.com.mx/conjunto','query':'otro','blocked':False}
        with self.assertRaises(ValueError):observe(self.db,'request',self.task,self.binding,wrong,authorize=lambda:True)
        right=dict(wrong,query='conjunto mujer falda')
        with self.assertRaises(ValueError):observe(self.db,'request',self.task,dict(self.binding,tab_id=9),right,authorize=lambda:True)
        with self.assertRaises(ValueError):observe(self.db,'request',self.task,self.binding,dict(right,blocked=True),authorize=lambda:True)
        self.assertTrue(observe(self.db,'request',self.task,self.binding,right,authorize=lambda:True))
        self.assertTrue(observe(self.db,'request',self.task,self.binding,right,authorize=lambda:True))
        self.assertEqual(ready(self.db,'request',self.task)['stage'],'search')

    def test_stale_or_unauthorized_watcher_cannot_resume(self):
        from optimization_worker.home_verification_watch import arm,observe
        arm(self.db,'request',self.task,'search',{'query':'vestido'},self.binding,fence=lambda:True)
        page={'url':'https://listado.mercadolibre.com.mx/vestido','query':'vestido','blocked':False}
        with self.assertRaises(ValueError):observe(self.db,'request',self.task,self.binding,page,authorize=lambda:False)
        with self.assertRaises(ValueError):observe(self.db,'request',(2,42,3,2),self.binding,page,authorize=lambda:True)

    def test_old_readable_page_evidence_expires(self):
        from optimization_worker.home_verification_watch import arm,observe,ready
        arm(self.db,'request',self.task,'search',{'query':'vestido'},self.binding,fence=lambda:True)
        observe(self.db,'request',self.task,self.binding,{'url':'https://listado.mercadolibre.com.mx/vestido','query':'vestido','blocked':False},authorize=lambda:True)
        self.db.execute('UPDATE home_verification_watch SET verified_at=verified_at-601')
        self.assertIsNone(ready(self.db,'request',self.task))

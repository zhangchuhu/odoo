import json,sqlite3,unittest
from datetime import datetime,timezone
from optimization_worker.collector_progress import initialize_details
from optimization_worker.visual_v2_checkpoints import initialize,seal_queries,append_batch,finish_query,finish_plan
from optimization_worker.visual_v2_outcomes import save_decision
from optimization_worker.visual_v2_finalize import build_result, snapshot_evidence, prepare_stage
from optimization_worker.domain import canonical_hash


class FinalizeTest(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close);initialize(self.db);initialize_details(self.db)
        self.key=('r','d'*64,1);self.queries=['vestido largo','vestido plisado','vestido fiesta']
        self.primary={'category':'dress','components':{'status':'not_applicable','value':None},'length':'long','shoulders':'regular','structure':'one_piece',
                      'silhouette':'a_line','fit':None,'sleeve_length':None}
        self.design={'primary':self.primary,'secondary':['lace'],'styles':['boho'],'occasions':[]}
        self.plan={'platform':'mercado-libre-mx','category':'dress','queries':self.queries,'design_profile':self.design}
        self.config={'result_limit':1,'min_sold':800,'min_reviews':50,'min_rating':4.1}
        seal_queries(self.db,self.key,self.queries,fence=lambda:True)
        for i,q in enumerate(self.queries):
            card={'query':q,'rank':1,'is_ad':False,'title':'Vestido','url':'https://articulo.mercadolibre.com.mx/MLM1',
                  'product_id':'MLM1','thumbnail_url':None}
            append_batch(self.db,(*self.key,str(i)),0,[card],fence=lambda:True)
            finish_query(self.db,(*self.key,str(i)),'home_complete',fence=lambda:True)
        save_decision(self.db,self.key,['0','1','2'],'MLM1',self.primary,self.primary,['lace'],['lace'],['boho'],[],[],[],fence=lambda:True)
        self.detail={'identity':'mercado-libre-mx:MLM1','status':'qualified','reason':None,
                     'detail_url':'https://articulo.mercadolibre.com.mx/MLM1','product_id':'MLM1','title':'Vestido','category':'dress',
                     'visual_category_code':'dress','sold_display':'+1000 vendidos','reviews_display':None,'rating_display':None,
                     'match_level':'高度相似','visual_features':['lace']}
        self.db.execute('INSERT INTO collector_details VALUES (?,?,?,?,?)',(*self.key,0,json.dumps(self.detail)))
        self.db.execute('CREATE TABLE visual_verdicts (scope TEXT,ordinal INTEGER,submission TEXT,digest TEXT,receipt TEXT)')
        receipt={'status':'qualified','detail_digest':canonical_hash(self.detail),'images':[{'receipt':'f'*32,'sha256':'a'*64,'source':'https://http2.mlstatic.com/a.jpg'}]}
        self.db.execute('INSERT INTO visual_verdicts VALUES (?,?,?,?,?)',('scope',0,'s','d',json.dumps(receipt)))

    def test_result_uses_task_sales_threshold_and_visual_decision(self):
        result=build_result(self.db,self.key,'scope',self.plan,self.config,'2026-11-26T00:00:00+00:00')
        self.assertEqual(result['status'],'complete');self.assertEqual(len(result['candidates']),1)
        self.assertEqual(result['candidates'][0]['sold_lower_bound'],1000)
        with self.assertRaises(ValueError):build_result(self.db,self.key,'scope',self.plan,dict(self.config,min_sold=1100),'2026-11-26T00:00:00+00:00')

    def test_evidence_contains_durable_queries_details_decisions_and_photo_proof(self):
        evidence=snapshot_evidence(self.db,self.key,'scope',self.plan,'rec123')
        self.assertEqual(len(evidence['queries']),3)
        self.assertEqual(evidence['details'],[self.detail])
        self.assertEqual(evidence['decisions'][0]['identity'],'MLM1')
        self.assertEqual(evidence['photo_receipts'][0]['status'],'qualified')
        staged=prepare_stage(self.db,self.key,'scope',self.plan,self.config,'rec123','2026-11-26T00:00:00+00:00')
        self.assertEqual(staged,evidence)
        from optimization_worker.archived_recovery import _revalidate_visual_v2
        result=build_result(self.db,self.key,'scope',self.plan,self.config,'2026-11-26T00:00:00+00:00')
        self.assertEqual(_revalidate_visual_v2(self.db,self.key,'scope',self.plan,self.config,'rec123',evidence,
                                              '2026-11-26T00:00:00+00:00')['candidates'],result['candidates'])
        changed=dict(evidence,details=[])
        with self.assertRaises(ValueError):
            _revalidate_visual_v2(self.db,self.key,'scope',self.plan,self.config,'rec123',changed,'2026-11-26T00:00:00+00:00')

    def test_incomplete_plan_cannot_finish_below_target(self):
        with self.assertRaises(ValueError):build_result(self.db,self.key,'scope',self.plan,dict(self.config,result_limit=2),'2026-11-26T00:00:00+00:00')
        finish_plan(self.db,self.key,fence=lambda:True)
        result=build_result(self.db,self.key,'scope',self.plan,dict(self.config,result_limit=2),'2026-11-26T00:00:00+00:00')
        self.assertEqual(result['status'],'partial');self.assertEqual(result['reason'],'search_plan_exhausted')

    def test_empty_plan_can_finish_without_visual_photo_tables(self):
        self.db.execute('DELETE FROM visual_v2_batches')
        self.db.execute('DELETE FROM collector_details')
        self.db.execute('DROP TABLE visual_verdicts')
        finish_plan(self.db,self.key,fence=lambda:True)
        evidence=prepare_stage(self.db,self.key,'scope',self.plan,self.config,'rec123','2026-11-26T00:00:00+00:00')
        self.assertEqual(evidence['photo_receipts'],[])
        self.assertEqual(build_result(self.db,self.key,'scope',self.plan,self.config,'2026-11-26T00:00:00+00:00')['status'],'empty')

import sqlite3
import unittest
from optimization_worker.card_sales_v3_checkpoints import initialize,seal_query,append_page_batch,finish_page
from optimization_worker.card_sales_v3_pipeline import initialize_pipeline,freeze_queue,record_detail,record_visual,record_visual_observation,selection


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close);initialize(self.db);initialize_pipeline(self.db)
        self.scope=('request','digest',1)
        for i in range(3):
            q='query '+str(i);seal_query(self.db,self.scope,i,q,fence=lambda:True)
            c=dict(product_id='MLM'+str(i),url='https://articulo.mercadolibre.com.mx/MLM'+str(i),query=q,rank=1,first_seen=i,sales_text='+%s vendidos'%(500-i*150))
            append_page_batch(self.db,self.scope,i,1,0,[c],fence=lambda:True)
            finish_page(self.db,self.scope,i,1,'end_of_results',fence=lambda:True)
    def test_requires_plan_exhaustion_when_under_target_and_preserves_sorted_results(self):
        with self.assertRaises(ValueError):freeze_queue(self.db,self.scope,plan_exhausted=False,fence=lambda:True)
        self.assertEqual(freeze_queue(self.db,self.scope,plan_exhausted=True,fence=lambda:True),['MLM0','MLM1','MLM2'])
        self.assertEqual(record_detail(self.db,self.scope,0,'MLM0','+500 vendidos',500,fence=lambda:True),'threshold_met')
        self.assertEqual(record_detail(self.db,self.scope,1,'MLM1','+350 vendidos',500,fence=lambda:True),'supplemental')
        self.assertEqual(record_detail(self.db,self.scope,2,'MLM2','+100 vendidos',500,fence=lambda:True),'exclude')
        with self.assertRaises(ValueError):selection(self.db,self.scope,10)
        source=dict(category='skirt_set',components=['top','skirt'],length='long',shoulders='regular',structure='two_piece',silhouette='a_line',fit='loose',sleeve_length='sleeveless')
        record_visual_observation(self.db,self.scope,0,'MLM0',source,source,['lace'],['lace'],[],[],fence=lambda:True)
        record_visual_observation(self.db,self.scope,1,'MLM1',source,source,['lace'],['lace'],[],[],fence=lambda:True)
        result=selection(self.db,self.scope,10)
        self.assertEqual([x['identity'] for x in result['threshold_met']],['MLM0'])
        self.assertEqual([x['identity'] for x in result['supplemental']],['MLM1'])
    def test_retry_and_changed_results(self):
        freeze_queue(self.db,self.scope,plan_exhausted=True,fence=lambda:True)
        record_detail(self.db,self.scope,0,'MLM0','+500 vendidos',500,fence=lambda:True)
        self.assertEqual(record_detail(self.db,self.scope,0,'MLM0','+500 vendidos',500,fence=lambda:True),'threshold_met')
        with self.assertRaises(ValueError):record_detail(self.db,self.scope,0,'MLM0','+150 vendidos',500,fence=lambda:True)
        with self.assertRaises(ValueError):record_detail(self.db,self.scope,1,'MLM1','+350 vendidos',500,fence=lambda:False)
    def test_frozen_empty_queue_cannot_gain_new_queries(self):
        self.db.execute('DELETE FROM card_v3_batches')
        self.assertEqual(freeze_queue(self.db,self.scope,plan_exhausted=True,fence=lambda:True),[])
        with self.assertRaises(ValueError):seal_query(self.db,self.scope,3,'new query',fence=lambda:True)
        self.assertEqual(freeze_queue(self.db,self.scope,plan_exhausted=True,fence=lambda:True),[])
    def test_visual_observations_are_assessed_by_backend(self):
        freeze_queue(self.db,self.scope,plan_exhausted=True,fence=lambda:True)
        record_detail(self.db,self.scope,0,'MLM0','+500 vendidos',500,fence=lambda:True)
        source=dict(category='skirt_set',components=['top','skirt'],length='long',shoulders='regular',structure='two_piece',silhouette='a_line',fit='loose',sleeve_length='sleeveless')
        candidate=dict(source,category='pants_set')
        result=record_visual_observation(self.db,self.scope,0,'MLM0',source,candidate,['lace'],['lace'],[],[],fence=lambda:True)
        self.assertEqual(result['level'],'reject')
        row=self.db.execute('SELECT body FROM card_v3_visual_evidence WHERE request_id=? AND ordinal=0',(self.scope[0],)).fetchone()
        self.assertIn('pants_set',row[0])
        with self.assertRaises(ValueError):
            record_visual_observation(self.db,self.scope,0,'MLM0',dict(source,length='short'),candidate,['lace'],['lace'],[],[],fence=lambda:True)
    def test_visual_selection_keeps_matching_and_difference_explanations(self):
        freeze_queue(self.db,self.scope,plan_exhausted=True,fence=lambda:True)
        for i,text in enumerate(('+500 vendidos','+100 vendidos','+100 vendidos')):
            record_detail(self.db,self.scope,i,'MLM'+str(i),text,500,fence=lambda:True)
        source=dict(category='skirt_set',components=['top','skirt'],length='long',shoulders='regular',structure='two_piece',silhouette='a_line',fit='loose',sleeve_length='sleeveless')
        record_visual_observation(self.db,self.scope,0,'MLM0',source,dict(source,sleeve_length='short'),['lace'],['lace'],[],[],fence=lambda:True)
        result=selection(self.db,self.scope,10)['threshold_met'][0]
        self.assertIn('sleeve_length',result['visual_comparison']['differences'])
        import json
        body=self.db.execute('SELECT body FROM card_v3_visual_evidence WHERE request_id=? AND ordinal=0',(self.scope[0],)).fetchone()[0]
        changed=json.loads(body);changed['result']['primary_matches']=99
        self.db.execute('UPDATE card_v3_visual_evidence SET body=? WHERE request_id=? AND ordinal=0',(json.dumps(changed),self.scope[0]))
        with self.assertRaises(ValueError):selection(self.db,self.scope,10)
    def test_raw_visual_rank_without_observation_cannot_be_published(self):
        freeze_queue(self.db,self.scope,plan_exhausted=True,fence=lambda:True)
        for i,text in enumerate(('+500 vendidos','+100 vendidos','+100 vendidos')):
            record_detail(self.db,self.scope,i,'MLM'+str(i),text,500,fence=lambda:True)
        record_visual(self.db,self.scope,0,'MLM0',dict(level='highly_similar',primary_matches=3,secondary_matches=1,style_matches=0,conflicts=0),fence=lambda:True)
        with self.assertRaises(ValueError):selection(self.db,self.scope,10)
    def test_thirty_visual_candidates_must_all_finish_before_selection(self):
        scope=('other','digest',1)
        for i in range(3):
            q='word '+str(i);seal_query(self.db,scope,i,q,fence=lambda:True)
            cards=[dict(product_id=f'MLM{i*12+n}',url=f'https://articulo.mercadolibre.com.mx/MLM{i*12+n}',query=q,rank=n+1,first_seen=n+1,sales_text='+500 vendidos') for n in range(12)]
            append_page_batch(self.db,scope,i,1,0,cards,fence=lambda:True);finish_page(self.db,scope,i,1,'end_of_results',fence=lambda:True)
        queue=freeze_queue(self.db,scope,plan_exhausted=True,fence=lambda:True)
        for i in range(30):
            record_detail(self.db,scope,i,queue[i],'+500 vendidos',500,fence=lambda:True)
        with self.assertRaises(ValueError):record_detail(self.db,scope,30,queue[30],'+500 vendidos',500,fence=lambda:True)
        source=dict(category='skirt_set',components=['top','skirt'],length='long',shoulders='regular',structure='two_piece',silhouette='a_line',fit='loose',sleeve_length='sleeveless')
        for i in range(29):record_visual_observation(self.db,scope,i,queue[i],source,source,['lace'],['lace'],[],[],fence=lambda:True)
        with self.assertRaises(ValueError):selection(self.db,scope,10)
        record_visual_observation(self.db,scope,29,queue[29],source,source,['lace'],['lace'],[],[],fence=lambda:True)
        self.assertEqual(len(selection(self.db,scope,10)['threshold_met']),10)
    def test_visual_observation_rolls_back_when_lease_is_lost_during_write(self):
        freeze_queue(self.db,self.scope,plan_exhausted=True,fence=lambda:True)
        record_detail(self.db,self.scope,0,'MLM0','+500 vendidos',500,fence=lambda:True)
        source=dict(category='skirt_set',components=['top','skirt'],length='long',shoulders='regular',structure='two_piece',silhouette='a_line',fit='loose',sleeve_length='sleeveless')
        calls=iter([True,True,True,False])
        with self.assertRaises(ValueError):record_visual_observation(self.db,self.scope,0,'MLM0',source,source,['lace'],['lace'],[],[],fence=lambda:next(calls))
        self.assertEqual(self.db.execute('SELECT count(*) FROM card_v3_visual').fetchone()[0],0)

    def test_queue_freeze_rolls_back_if_lease_expires_during_write(self):
        calls=iter([True,True,False])
        with self.assertRaises(ValueError):freeze_queue(self.db,self.scope,plan_exhausted=True,fence=lambda:next(calls))
        self.assertEqual(self.db.execute('SELECT count(*) FROM card_v3_queue').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM card_v3_meta').fetchone()[0],0)

    def test_detail_write_rolls_back_if_lease_expires_during_write(self):
        freeze_queue(self.db,self.scope,plan_exhausted=True,fence=lambda:True)
        calls=iter([True,True,False])
        with self.assertRaises(ValueError):record_detail(self.db,self.scope,0,'MLM0','+500 vendidos',500,fence=lambda:next(calls))
        self.assertEqual(self.db.execute('SELECT count(*) FROM card_v3_details').fetchone()[0],0)
        self.assertIsNone(self.db.execute('SELECT threshold FROM card_v3_meta').fetchone()[0])

    def test_progress_distinguishes_known_card_count_and_selection_tiers(self):
        from optimization_worker.card_sales_v3_pipeline import progress
        before=progress(self.db,self.scope)
        self.assertEqual(before['known_products'],3)
        self.assertEqual(before['selected_per_query'],[1,1,1])
        self.assertEqual(before['detail_count'],0)
        freeze_queue(self.db,self.scope,plan_exhausted=True,fence=lambda:True)
        record_detail(self.db,self.scope,0,'MLM0','+500 vendidos',500,fence=lambda:True)
        record_detail(self.db,self.scope,1,'MLM1','+350 vendidos',500,fence=lambda:True)
        record_detail(self.db,self.scope,2,'MLM2','+100 vendidos',500,fence=lambda:True)
        after=progress(self.db,self.scope)
        self.assertEqual((after['detail_count'],after['threshold_met'],after['supplemental'],after['excluded']), (3,1,1,1))
        self.assertEqual((after['visual_required'],after['visual_done']), (2,0))
        source=dict(category='skirt_set',components=['top','skirt'],length='long',shoulders='regular',structure='two_piece',silhouette='a_line',fit='loose',sleeve_length='sleeveless')
        record_visual_observation(self.db,self.scope,0,'MLM0',source,source,['lace'],['lace'],[],[],fence=lambda:True)
        self.assertEqual(progress(self.db,self.scope)['visual_threshold_passed'],1)

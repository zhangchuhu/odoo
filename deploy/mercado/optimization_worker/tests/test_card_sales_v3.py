import unittest

from optimization_worker.card_sales_v3 import parse_sales, rank_per_query, merge_candidates, classify_detail, rank_visual, assess_visual, resolve_sales, next_step


class CardSalesTests(unittest.TestCase):
    def test_sales_text_strict_lower_bounds(self):
        self.assertEqual(parse_sales('+100 vendidos'), 100)
        self.assertEqual(parse_sales('+150 vendidos'), 150)
        self.assertEqual(parse_sales('+1.000 vendidos'), 1000)
        self.assertEqual(parse_sales('+1,5 mil vendidos'), 1500)
        self.assertEqual(parse_sales('+1.5 mil vendidos'), 1500)
        self.assertIsNone(parse_sales('+1,5 vendidos'))
        self.assertIsNone(parse_sales('Vendedor: +500 ventas'))
        self.assertIsNone(parse_sales('+500 opiniones'))
        self.assertIsNone(parse_sales(''))
        self.assertIsNone(parse_sales('9'*5000+' vendidos'))

    def test_top_30_uses_all_pages_and_excludes_unknown_and_100(self):
        rows = [dict(product_id='MLM'+str(i), sales_lower_bound=i, sales_status='known', first_seen=i) for i in range(90, 140)]
        rows += [dict(product_id='unknown', sales_lower_bound=None, sales_status='missing', first_seen=0)]
        result = rank_per_query(rows)
        self.assertEqual(len(result), 30)
        self.assertEqual(result[0]['sales_lower_bound'], 139)
        self.assertEqual(result[-1]['sales_lower_bound'], 110)
        self.assertNotIn('MLM100', [x['product_id'] for x in result])

    def test_merge_deduplicates_without_losing_sources(self):
        one=dict(product_id='MLM1',sales_lower_bound=150,first_seen=1)
        two=dict(product_id='MLM2',sales_lower_bound=500,first_seen=2)
        merged=merge_candidates([('a',[one,two]),('b',[one])])
        self.assertEqual([x['product_id'] for x in merged],['MLM2','MLM1'])
        self.assertEqual(merged[1]['search_sources'],['a','b'])

    def test_latest_known_sales_can_remove_prior_eligible_observation(self):
        old=dict(product_id='MLM1',sales_lower_bound=500,sales_status='known',first_seen=1,observed_at='2026-01-01T00:00:00Z')
        new=dict(old,sales_lower_bound=100,first_seen=2,observed_at='2026-01-02T00:00:00Z')
        self.assertEqual(rank_per_query([old,new]),[])

    def test_merge_preserves_original_discovery_when_newer_observation_arrives(self):
        first=dict(product_id='MLM9',sales_lower_bound=500,first_seen=1,observed_at='2026-01-01T00:00:00Z')
        newer=dict(first,first_seen=5,observed_at='2026-01-02T00:00:00Z')
        other=dict(product_id='MLM1',sales_lower_bound=500,first_seen=2,observed_at='2026-01-01T00:00:00Z')
        rows=merge_candidates([('one',[first,other]),('two',[newer])])
        self.assertEqual([row['product_id'] for row in rows],['MLM9','MLM1'])

    def test_sales_conflicts_require_disjoint_intervals(self):
        self.assertEqual(resolve_sales(['+100 vendidos','+150 vendidos'])['status'],'known')
        self.assertEqual(resolve_sales(['+500 vendidos','100 vendidos'])['status'],'conflict')
        self.assertEqual(resolve_sales([None,'+150 vendidos'])['lower_bound'],150)

    def test_detail_sales_is_separate_from_task_threshold(self):
        self.assertEqual(classify_detail(100,500),'exclude')
        self.assertEqual(classify_detail(150,500),'supplemental')
        self.assertEqual(classify_detail(500,500),'threshold_met')
        self.assertEqual(classify_detail(101,0),'threshold_met')
        self.assertEqual(classify_detail(None,500),'exclude')

    def test_visual_order_requires_all_decisions_and_prioritizes_similarity(self):
        rows=[dict(identity='a',level='competitor',primary_matches=3,secondary_matches=2,style_matches=0,conflicts=1,sales_lower_bound=1000),
              dict(identity='b',level='highly_similar',primary_matches=4,secondary_matches=1,style_matches=0,conflicts=0,sales_lower_bound=150)]
        self.assertEqual([x['identity'] for x in rank_visual(rows, expected=2)],['b','a'])
        with self.assertRaises(ValueError): rank_visual(rows, expected=3)

    def test_visual_requires_category_and_components_but_relaxes_sleeves(self):
        source=dict(category='skirt_set',components=['top','skirt'],length='long',shoulders='regular',structure='two_piece',silhouette='a_line',fit='loose',sleeve_length='sleeveless')
        candidate=dict(source,sleeve_length='short')
        result=assess_visual(source,candidate,['lace'],['lace'],['boho'],['boho'])
        self.assertEqual(result['level'],'highly_similar')
        self.assertIn('sleeve_length',result['differences'])
        self.assertEqual(assess_visual(source,dict(candidate,category='dress'),[],[],[],[])['level'],'reject')
        self.assertEqual(assess_visual(source,dict(candidate,components=None),[],[],[],[])['level'],'review')

    def test_no_features_cannot_be_invented_as_confirmed_similarity(self):
        source=dict(category='dress',components={'status':'not_applicable','value':None},length='long',shoulders=None,structure='one_piece',silhouette=None,fit=None,sleeve_length=None)
        candidate=dict(source,length=None)
        result=assess_visual(source,candidate,None,None,None,None)
        self.assertEqual(result['level'],'review')
        with self.assertRaises(ValueError):assess_visual(source,dict(candidate,length=True),None,None,None,None)

    def test_discovery_then_thirty_visual_decisions_before_finish(self):
        self.assertEqual(next_step(queries_complete=False,known=120,query_count=3,plan_exhausted=False,candidates_frozen=False,detail_remaining=1,visual_reserved=0,visual_decisions=0)['action'],'search')
        self.assertEqual(next_step(queries_complete=True,known=99,query_count=3,plan_exhausted=False,candidates_frozen=False,detail_remaining=0,visual_reserved=0,visual_decisions=0)['action'],'expand')
        self.assertEqual(next_step(queries_complete=True,known=100,query_count=3,plan_exhausted=False,candidates_frozen=False,detail_remaining=0,visual_reserved=0,visual_decisions=0)['action'],'freeze')
        self.assertEqual(next_step(queries_complete=True,known=100,query_count=3,plan_exhausted=False,candidates_frozen=True,detail_remaining=20,visual_reserved=30,visual_decisions=29)['action'],'visual')
        self.assertEqual(next_step(queries_complete=True,known=100,query_count=3,plan_exhausted=False,candidates_frozen=True,detail_remaining=20,visual_reserved=30,visual_decisions=30)['action'],'finish')

    def test_v3_task_config_is_strict_and_preserves_user_threshold(self):
        from optimization_worker.collector_cli import task_config
        cfg=dict(platform='mercado-libre-mx',result_limit=10,min_sold=500,min_reviews=50,min_rating=4.1,
                 strategy='card-visual-v3',initial_query_count=3,max_expansion_queries=6,max_pages_per_query=3,
                 sales_known_target=100,top_per_query=30,visual_limit=30)
        plan=dict(platform='mercado-libre-mx',result_limit=10,strategy='card-visual-v3')
        self.assertEqual(task_config({'payload':{'collection_config':cfg}},plan),cfg)
        with self.assertRaises(ValueError):task_config({'payload':{'collection_config':dict(cfg,max_pages_per_query=4)}},plan)


if __name__ == '__main__': unittest.main()

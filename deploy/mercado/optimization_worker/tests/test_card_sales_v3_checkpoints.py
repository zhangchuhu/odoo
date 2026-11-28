import sqlite3
import unittest
from optimization_worker.card_sales_v3_checkpoints import initialize, seal_query, append_page_batch, finish_page, query_candidates, known_count


class CardSalesCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:'); self.addCleanup(self.db.close); initialize(self.db)
        self.scope=('request','digest',1); seal_query(self.db,self.scope,0,'vestido',fence=lambda:True)
    def card(self,pid,text,rank=1):
        return dict(product_id=pid,url='https://articulo.mercadolibre.com.mx/'+pid,sales_text=text,query='vestido',rank=rank,first_seen=rank)
    def test_batches_are_fenced_immutable_and_idempotent(self):
        card=self.card('MLM1','+150 vendidos')
        append_page_batch(self.db,self.scope,0,1,0,[card],fence=lambda:True)
        append_page_batch(self.db,self.scope,0,1,0,[card],fence=lambda:True)
        with self.assertRaises(ValueError):append_page_batch(self.db,self.scope,0,1,0,[self.card('MLM2','+200 vendidos')],fence=lambda:True)
        with self.assertRaises(ValueError):append_page_batch(self.db,self.scope,0,2,0,[card],fence=lambda:False)
        with self.assertRaises(ValueError):query_candidates(self.db,self.scope,0)
        finish_page(self.db,self.scope,0,1,'end_of_results',fence=lambda:True)
        self.assertEqual(query_candidates(self.db,self.scope,0)[0]['product_id'],'MLM1')
        self.assertEqual(known_count(self.db,self.scope),1)

    def test_browser_card_gets_first_seen_from_durable_order(self):
        card=self.card('MLM1','+150 vendidos');del card['first_seen']
        append_page_batch(self.db,self.scope,0,1,0,[card],fence=lambda:True)
        finish_page(self.db,self.scope,0,1,'end_of_results',fence=lambda:True)
        self.assertEqual(query_candidates(self.db,self.scope,0)[0]['first_seen'],1)
    def test_three_pages_must_be_in_order_and_known_counts_include_100(self):
        append_page_batch(self.db,self.scope,0,1,0,[self.card('MLM1','+100 vendidos')],fence=lambda:True)
        with self.assertRaises(ValueError):finish_page(self.db,self.scope,0,2,'end_of_results',fence=lambda:True)
        finish_page(self.db,self.scope,0,1,'has_next',next_url='https://listado.mercadolibre.com.mx/vestido_Desde_51',fence=lambda:True)
        append_page_batch(self.db,self.scope,0,2,0,[self.card('MLM2','+150 vendidos')],fence=lambda:True)
        finish_page(self.db,self.scope,0,2,'end_of_results',fence=lambda:True)
        self.assertEqual(known_count(self.db,self.scope),2)
        self.assertEqual([x['product_id'] for x in query_candidates(self.db,self.scope,0)],['MLM2'])
    def test_incompatible_observations_never_count_or_enter_queue(self):
        append_page_batch(self.db,self.scope,0,1,0,[self.card('MLM1','+500 vendidos')],fence=lambda:True)
        finish_page(self.db,self.scope,0,1,'has_next',next_url='https://listado.mercadolibre.com.mx/vestido_Desde_51',fence=lambda:True)
        append_page_batch(self.db,self.scope,0,2,0,[self.card('MLM1','100 vendidos')],fence=lambda:True)
        finish_page(self.db,self.scope,0,2,'end_of_results',fence=lambda:True)
        self.assertEqual(known_count(self.db,self.scope),0)
        self.assertEqual(query_candidates(self.db,self.scope,0),[])
    def test_equal_sales_keep_first_seen_page_order(self):
        append_page_batch(self.db,self.scope,0,1,0,[self.card('MLM9','+500 vendidos',9)],fence=lambda:True)
        finish_page(self.db,self.scope,0,1,'has_next',next_url='https://listado.mercadolibre.com.mx/vestido_Desde_51',fence=lambda:True)
        append_page_batch(self.db,self.scope,0,2,0,[self.card('MLM1','+500 vendidos',1)],fence=lambda:True)
        finish_page(self.db,self.scope,0,2,'end_of_results',fence=lambda:True)
        self.assertEqual([x['product_id'] for x in query_candidates(self.db,self.scope,0)],['MLM9','MLM1'])

    def test_latest_compatible_sales_observation_controls_ranking_without_browser_timestamps(self):
        append_page_batch(self.db,self.scope,0,1,0,[self.card('MLM1','+150 vendidos')],fence=lambda:True)
        finish_page(self.db,self.scope,0,1,'has_next',next_url='https://listado.mercadolibre.com.mx/p2',fence=lambda:True)
        append_page_batch(self.db,self.scope,0,2,0,[self.card('MLM1','+500 vendidos')],fence=lambda:True)
        finish_page(self.db,self.scope,0,2,'end_of_results',fence=lambda:True)
        self.assertEqual(query_candidates(self.db,self.scope,0)[0]['sales_lower_bound'],500)
    def test_ambiguous_card_label_cannot_be_upgraded_by_numeric_text(self):
        card=dict(self.card('MLM1','+500 vendidos'),sales_status='ambiguous')
        append_page_batch(self.db,self.scope,0,1,0,[card],fence=lambda:True)
        finish_page(self.db,self.scope,0,1,'end_of_results',fence=lambda:True)
        self.assertEqual(known_count(self.db,self.scope),0)
        self.assertEqual(query_candidates(self.db,self.scope,0),[])
    def test_product_identity_must_match_the_observed_platform_url(self):
        card=dict(self.card('MLM1','+500 vendidos'),url='https://evil.test/MLM1')
        with self.assertRaises(ValueError):append_page_batch(self.db,self.scope,0,1,0,[card],fence=lambda:True)

    def test_shein_card_batch_is_explicitly_platform_scoped(self):
        card=dict(product_id='123',url='https://us.shein.com/example-p-123.html',sales_text='500+ sold',query='vestido',rank=1)
        with self.assertRaises(ValueError):
            append_page_batch(self.db,self.scope,0,1,0,[card],fence=lambda:True)
        append_page_batch(self.db,self.scope,0,1,0,[card],fence=lambda:True,platform='shein-us')
        with self.assertRaises(ValueError):
            append_page_batch(self.db,self.scope,0,1,1,[dict(card,product_id='456')],fence=lambda:True,platform='shein-us')
    def test_next_page_requires_verified_marketplace_url(self):
        with self.assertRaises(ValueError):finish_page(self.db,self.scope,0,1,'has_next',fence=lambda:True)
        with self.assertRaises(ValueError):finish_page(self.db,self.scope,0,1,'has_next',next_url='https://evil.test/',fence=lambda:True)
    def test_next_page_and_completion_are_atomic(self):
        self.db.execute("CREATE TRIGGER fail_navigation BEFORE INSERT ON card_v3_navigation BEGIN SELECT RAISE(ABORT, 'failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            finish_page(self.db,self.scope,0,1,'has_next',next_url='https://listado.mercadolibre.com.mx/page2',fence=lambda:True)
        self.assertEqual(self.db.execute('SELECT count(*) FROM card_v3_pages').fetchone()[0],0)

    def test_scheduler_resumes_homepages_then_round_robin(self):
        from optimization_worker.card_sales_v3_checkpoints import next_page
        seal_query(self.db,self.scope,1,'otro',fence=lambda:True)
        seal_query(self.db,self.scope,2,'tercero',fence=lambda:True)
        self.assertEqual(next_page(self.db,self.scope), (0,1))
        finish_page(self.db,self.scope,0,1,'has_next',next_url='https://listado.mercadolibre.com.mx/p2',fence=lambda:True)
        self.assertEqual(next_page(self.db,self.scope), (1,1))
        finish_page(self.db,self.scope,1,1,'end_of_results',fence=lambda:True)
        finish_page(self.db,self.scope,2,1,'has_next',next_url='https://listado.mercadolibre.com.mx/p2',fence=lambda:True)
        self.assertEqual(next_page(self.db,self.scope), (0,2))
        finish_page(self.db,self.scope,0,2,'has_next',next_url='https://listado.mercadolibre.com.mx/p3',fence=lambda:True)
        self.assertEqual(next_page(self.db,self.scope), (2,2))
        finish_page(self.db,self.scope,2,2,'end_of_results',fence=lambda:True)
        self.assertEqual(next_page(self.db,self.scope), (0,3))
        finish_page(self.db,self.scope,0,3,'page_limit',fence=lambda:True)
        self.assertIsNone(next_page(self.db,self.scope))

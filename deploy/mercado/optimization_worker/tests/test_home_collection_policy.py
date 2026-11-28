import unittest

from optimization_worker.home_collection_policy import next_home_action


class HomeCollectionPolicyTests(unittest.TestCase):
    def test_store_resumes_incomplete_homepage_without_replaying_previous_pages(self):
        import sqlite3,tempfile
        from contextlib import closing
        from pathlib import Path
        from optimization_worker.tab_receipt_store import TabReceiptStore
        with tempfile.TemporaryDirectory() as tmp:
            store=TabReceiptStore(str(Path(tmp)/'ledger.db'))
            scope=('req','digest',1);queries=['uno','dos','tres']
            state=store.card_home_state(*scope,queries,lambda:True)
            self.assertEqual(state['next_page'],(0,1))
            with closing(sqlite3.connect(store.path)) as db,db:
                db.execute('insert into card_v3_pages values (?,?,?,?,?,?)',(*scope,0,1,'home_complete'))
            self.assertEqual(store.card_home_state(*scope,queries,lambda:True)['next_page'],(1,1))
            with self.assertRaises(ValueError):store.card_home_state(*scope,queries,lambda:False)

    def test_store_freezes_when_nine_homepages_are_complete(self):
        import sqlite3,tempfile
        from contextlib import closing
        from pathlib import Path
        from optimization_worker.tab_receipt_store import TabReceiptStore
        with tempfile.TemporaryDirectory() as tmp:
            store=TabReceiptStore(str(Path(tmp)/'ledger.db'));scope=('req','digest',1);queries=['uno','dos','tres']
            store.card_home_state(*scope,queries,lambda:True)
            with closing(sqlite3.connect(store.path)) as db,db:
                for i in range(3,9):db.execute('INSERT INTO card_v3_queries VALUES (?,?,?,?,?)',(*scope,i,'q'+str(i)))
                for i in range(9):db.execute('INSERT INTO card_v3_pages VALUES (?,?,?,?,?,?)',(*scope,i,1,'home_complete'))
            state=store.card_home_state(*scope,queries,lambda:True)
            self.assertIsNone(state['next_page'])
            self.assertEqual(state['end_reason'],'query_limit')
            with closing(sqlite3.connect(store.path)) as db:
                self.assertIsNotNone(db.execute('SELECT 1 FROM card_v3_meta').fetchone())

    def test_versioned_config_requires_one_homepage(self):
        from optimization_worker.collector_cli import task_config,protocol_matches
        config={'platform':'mercado-libre-mx','result_limit':10,'min_sold':500,'min_reviews':50,'min_rating':4.1,
                'strategy':'card-home-v4','initial_query_count':3,'max_expansion_queries':6,
                'max_pages_per_query':1,'sales_known_target':100,'top_per_query':30,'visual_limit':30}
        plan={'platform':'mercado-libre-mx','result_limit':10,'strategy':'card-home-v4'}
        self.assertEqual(task_config({'payload':{'collection_config':config}},plan),config)
        self.assertTrue(protocol_matches(6,plan))
        self.assertFalse(protocol_matches(5,plan))
        with self.assertRaises(ValueError):task_config({'payload':{'collection_config':dict(config,max_pages_per_query=3)}},plan)
    def test_each_query_only_once_before_expansion(self):
        self.assertEqual(next_home_action([True,False,False],4,False),{'action':'collect','query_index':1,'page':1})
        self.assertEqual(next_home_action([True,True,True],4,False),{'action':'expand','remaining':6})

    def test_target_or_exhaustion_freezes(self):
        self.assertEqual(next_home_action([True]*3,100,False),{'action':'freeze','reason':'sales_target_reached'})
        self.assertEqual(next_home_action([True]*9,2,False),{'action':'freeze','reason':'query_limit'})
        self.assertEqual(next_home_action([True]*3,2,True),{'action':'freeze','reason':'terms_exhausted'})

    def test_invalid_state_fails_closed(self):
        for state in ([True,1,True],[],[True]*10):
            with self.subTest(state=state),self.assertRaises(ValueError):next_home_action(state,3,False)

    def test_checkpoint_only_schedules_first_page(self):
        import sqlite3
        from optimization_worker.card_sales_v3_checkpoints import initialize
        from optimization_worker.home_collection_policy import next_home_query
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);initialize(db);scope=('req','digest',1)
        db.executemany('insert into card_v3_queries values (?,?,?,?,?)',[(*scope,i,'query '+str(i)) for i in range(3)])
        db.execute('insert into card_v3_pages values (?,?,?,?,?,?)',(*scope,0,1,'home_complete'))
        self.assertEqual(next_home_query(db,scope),(1,1))
        db.execute('insert into card_v3_pages values (?,?,?,?,?,?)',(*scope,1,2,'end'))
        with self.assertRaises(ValueError):next_home_query(db,scope)

    def test_bad_homepage_completion_is_not_treated_as_finished(self):
        import sqlite3
        from optimization_worker.card_sales_v3_checkpoints import initialize
        from optimization_worker.home_collection_policy import next_home_query
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);initialize(db);scope=('req','digest',1)
        db.execute('insert into card_v3_queries values (?,?,?,?,?)',(*scope,0,'query'))
        db.execute('insert into card_v3_pages values (?,?,?,?,?,?)',(*scope,0,1,'unknown'))
        with self.assertRaises(ValueError):next_home_query(db,scope)

    def test_home_candidates_do_not_require_later_pages(self):
        import sqlite3,json
        from optimization_worker.card_sales_v3_checkpoints import initialize
        from optimization_worker.home_collection_policy import home_query_candidates
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);initialize(db);scope=('req','digest',1)
        db.execute('insert into card_v3_queries values (?,?,?,?,?)',(*scope,0,'query'))
        cards=[{'product_id':'A','rank':1,'sales_status':'known','sales_text':'+100 vendidos'},
               {'product_id':'B','rank':2,'sales_status':'known','sales_text':'+500 vendidos'}]
        db.execute('insert into card_v3_batches values (?,?,?,?,?,?,?)',(*scope,0,1,0,json.dumps(cards)))
        with self.assertRaises(ValueError):home_query_candidates(db,scope,0)
        db.execute('insert into card_v3_pages values (?,?,?,?,?,?)',(*scope,0,1,'home_complete'))
        self.assertEqual([x['product_id'] for x in home_query_candidates(db,scope,0)],['B','A'])

    def test_old_page_completion_is_not_reused_for_home_strategy(self):
        import sqlite3
        from optimization_worker.card_sales_v3_checkpoints import initialize
        from optimization_worker.home_collection_policy import next_home_query
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);initialize(db);scope=('req','digest',1)
        db.executemany('insert into card_v3_queries values (?,?,?,?,?)',[(*scope,i,'query '+str(i)) for i in range(3)])
        db.execute('insert into card_v3_pages values (?,?,?,?,?,?)',(*scope,0,1,'has_next'))
        with self.assertRaises(ValueError):next_home_query(db,scope)

    def test_home_queue_freeze_deduplicates_queries(self):
        import sqlite3,json
        from optimization_worker.card_sales_v3_checkpoints import initialize
        from optimization_worker.card_sales_v3_pipeline import initialize_pipeline,freeze_queue
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);initialize(db);initialize_pipeline(db);scope=('req','digest',1)
        for i in range(3):
            db.execute('insert into card_v3_queries values (?,?,?,?,?)',(*scope,i,'query '+str(i)))
            cards=[{'product_id':'B','rank':1,'sales_status':'known','sales_text':'+500 vendidos','query':'query '+str(i)}]
            db.execute('insert into card_v3_batches values (?,?,?,?,?,?,?)',(*scope,i,1,0,json.dumps(cards)))
            db.execute('insert into card_v3_pages values (?,?,?,?,?,?)',(*scope,i,1,'home_complete'))
        self.assertEqual(freeze_queue(db,scope,plan_exhausted=True,fence=lambda:True,homepage_only=True),['B'])
        body=json.loads(db.execute('select body from card_v3_queue').fetchone()[0])
        self.assertEqual(body['search_sources'],['query 0','query 1','query 2'])

    def test_shein_homepage_candidates_use_card_sales_and_reject_conflicts(self):
        import sqlite3,json
        from optimization_worker.card_sales_v3_checkpoints import initialize
        from optimization_worker.home_collection_policy import shein_home_query_candidates
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);initialize(db);scope=('req','digest',1)
        for i in range(2):
            db.execute('insert into card_v3_queries values (?,?,?,?,?)',(*scope,i,'q'+str(i)))
            db.execute('insert into card_v3_pages values (?,?,?,?,?,?)',(*scope,i,1,'home_complete'))
        rows=[[{'product_id':'1','rank':1,'sales_text':'100+ sold'}, {'product_id':'2','rank':2,'sales_text':'500+ sold'}],
              [{'product_id':'2','rank':1,'sales_text':'100 sold'}, {'product_id':'3','rank':2,'sales_text':'200+ sold'}]]
        for i,cards in enumerate(rows):
            db.execute('insert into card_v3_batches values (?,?,?,?,?,?,?)',(*scope,i,1,0,json.dumps(cards)))
        self.assertEqual([c['product_id'] for c in shein_home_query_candidates(db,scope,0,500)], ['1'])
        self.assertEqual([c['product_id'] for c in shein_home_query_candidates(db,scope,1,500)], ['3'])

    def test_shein_frozen_queue_merges_searches_and_preserves_sources(self):
        import sqlite3,json
        from optimization_worker.card_sales_v3_checkpoints import initialize
        from optimization_worker.card_sales_v3_pipeline import initialize_pipeline,freeze_queue
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);initialize(db);initialize_pipeline(db);scope=('req','digest',1)
        for i in range(3):
            db.execute('insert into card_v3_queries values (?,?,?,?,?)',(*scope,i,'q'+str(i)))
            cards=[{'product_id':'123','rank':1,'sales_text':'300+ sold' if i else '500+ sold','query':'q'+str(i)}]
            db.execute('insert into card_v3_batches values (?,?,?,?,?,?,?)',(*scope,i,1,0,json.dumps(cards)))
            db.execute('insert into card_v3_pages values (?,?,?,?,?,?)',(*scope,i,1,'home_complete'))
        self.assertEqual(freeze_queue(db,scope,plan_exhausted=True,fence=lambda:True,homepage_only=True,platform='shein-us',threshold=500),['123'])
        row=json.loads(db.execute('select body from card_v3_queue').fetchone()[0])
        self.assertEqual(row['search_sources'],['q0','q1','q2'])
        self.assertEqual(row['sales_text'],'500+ sold')

    def test_shein_state_recovers_homepage_with_english_sales(self):
        import sqlite3,tempfile,json
        from pathlib import Path
        from optimization_worker.tab_receipt_store import TabReceiptStore
        with tempfile.TemporaryDirectory() as tmp:
            store=TabReceiptStore(str(Path(tmp)/'ledger.db'));scope=('req','digest',1);queries=['one','two','three']
            state=store.card_home_state(*scope,queries,lambda:True,platform='shein-us',threshold=500)
            self.assertEqual(state['next_page'],(0,1))
            with sqlite3.connect(store.path) as db:
                db.execute('insert into card_v3_batches values (?,?,?,?,?,?,?)',(*scope,0,1,0,json.dumps([{'product_id':'123','rank':1,'sales_text':'500+ sold'}])))
                db.execute('insert into card_v3_pages values (?,?,?,?,?,?)',(*scope,0,1,'home_complete'))
            state=store.card_home_state(*scope,queries,lambda:True,platform='shein-us',threshold=500)
            self.assertEqual(state['next_page'],(1,1))
            self.assertEqual(state['known_products'],1)

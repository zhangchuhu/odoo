import unittest

from optimization_worker.shein_home_sales import parse_sales, classify_sales, rank_cards, classify_candidate


class SheinHomeSalesTest(unittest.TestCase):
    def test_explicit_english_product_sales(self):
        for text, expected in [('100+ sold',100), ('+100 sold',100), ('1,200+ sold',1200), ('1.2k+ sold',1200), ('500 sold',500)]:
            with self.subTest(text=text): self.assertEqual(parse_sales(text), expected)
        for text in ('100 reviews', 'store 100+ sold', '1,2k+ sold', '+100+ sold', 'unknown', None):
            with self.subTest(text=text): self.assertIsNone(parse_sales(text))

    def test_task_threshold_and_explicit_plus_hundred(self):
        self.assertEqual(classify_sales('100+ sold',500),'supplemental')
        self.assertEqual(classify_sales('100 sold',500),'exclude')
        self.assertEqual(classify_sales('500+ sold',500),'threshold_met')
        self.assertEqual(classify_sales(None,500),'exclude')
        self.assertEqual(classify_sales('100+ sold',101),'threshold_met')
        self.assertEqual(classify_sales('100+ sold',102),'supplemental')

    def test_rank_unique_explicit_sales_before_truncation(self):
        cards = [
            {'product_id': 'a', 'sales_text': '100+ sold', 'rank': 1},
            {'product_id': 'b', 'sales_text': '300+ sold', 'rank': 2},
            {'product_id': 'c', 'sales_text': None, 'rank': 3},
            {'product_id': 'd', 'sales_text': '100 sold', 'rank': 4},
            {'product_id': 'a', 'sales_text': '100+ sold', 'rank': 5},
        ]
        selected = rank_cards(cards, threshold=500, limit=30)
        self.assertEqual([row['product_id'] for row in selected], ['b', 'a'])
        self.assertEqual([row['tier'] for row in selected], ['supplemental', 'supplemental'])
        self.assertEqual(selected[1]['sales_lower_bound'], 100)

    def test_conflicting_sales_observations_do_not_guess(self):
        cards = [{'product_id':'a','sales_text':'300 sold','rank':1},
                 {'product_id':'a','sales_text':'500 sold','rank':2}]
        self.assertEqual(rank_cards(cards, threshold=500, limit=30), [])

    def test_compatible_lower_bound_observations_are_not_conflicts(self):
        cards = [{'product_id':'a','sales_text':'300+ sold','rank':1},
                 {'product_id':'a','sales_text':'500+ sold','rank':2}]
        rows = rank_cards(cards, threshold=500, limit=30)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['sales_lower_bound'], 500)
        self.assertEqual(rows[0]['tier'], 'threshold_met')

    def test_candidate_uses_card_sales_and_requires_product_reviews(self):
        card = {'product_id':'123', 'url':'https://us.shein.com/example-p-123.html', 'sales_text':'500+ sold'}
        detail = {'product_id':'123', 'detail_url':'https://us.shein.com/example-p-123.html', 'reviews_display':'51', 'rating_display':None}
        self.assertEqual(classify_candidate(card, detail, min_sold=500, min_reviews=50), 'threshold_met')
        self.assertEqual(classify_candidate(card, dict(detail, reviews_display='49'), min_sold=500, min_reviews=50), 'review_threshold_failure')
        self.assertEqual(classify_candidate(card, dict(detail, reviews_display=None), min_sold=500, min_reviews=50), 'review_missing')
        self.assertEqual(classify_candidate(card, dict(detail, product_id='456'), min_sold=500, min_reviews=50), 'identity_mismatch')
        self.assertEqual(classify_candidate(dict(card, sales_text='100+ sold'), detail, min_sold=500, min_reviews=50), 'supplemental')

    def test_frozen_queue_detail_uses_card_sales_and_requires_reviews(self):
        import json,sqlite3
        from optimization_worker.card_sales_v3_pipeline import initialize_pipeline,record_shein_detail
        from optimization_worker.card_sales_v3_checkpoints import initialize
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);initialize(db);initialize_pipeline(db);scope=('req','digest',1)
        card={'product_id':'123','url':'https://us.shein.com/example-p-123.html','sales_text':'500+ sold'}
        detail={'product_id':'123','detail_url':card['url'],'reviews_display':'51'}
        db.execute('insert into card_v3_meta values (?,?,?,NULL)',scope)
        db.execute('insert into card_v3_queue values (?,?,?,?,?,?)',(*scope,0,'123',json.dumps(card)))
        self.assertEqual(record_shein_detail(db,scope,0,detail,500,50,fence=lambda:True),'threshold_met')
        self.assertEqual(record_shein_detail(db,scope,0,detail,500,50,fence=lambda:True),'threshold_met')
        with self.assertRaises(ValueError):record_shein_detail(db,scope,0,dict(detail,reviews_display='49'),500,50,fence=lambda:True)
        card2={'product_id':'124','url':'https://us.shein.com/example-p-124.html','sales_text':'500+ sold'}
        db.execute('insert into card_v3_queue values (?,?,?,?,?,?)',(*scope,1,'124',json.dumps(card2)))
        detail2={'product_id':'124','detail_url':card2['url'],'reviews_display':'49'}
        with self.assertRaises(ValueError):record_shein_detail(db,scope,1,detail2,500,40,fence=lambda:True)
        self.assertEqual(record_shein_detail(db,scope,1,detail2,500,50,fence=lambda:True),'exclude')
        from optimization_worker.card_sales_v3_pipeline import progress
        totals=progress(db,scope,homepage_only=True,platform='shein-us',threshold=500)
        self.assertEqual(totals['reviews_met'],1)
        self.assertEqual(totals['reviews_below'],1)

    def test_result_revalidates_card_sales_and_detail_review_evidence(self):
        from optimization_worker.visual_v3_finalize import validate_shein_result_evidence
        card={'product_id':'123','url':'https://us.shein.com/example-p-123.html','sales_text':'500+ sold'}
        observed={'identity':'shein-us:123','product_id':'123','detail_url':card['url'],'reviews_display':'51'}
        decision={'identity':'123','sales_text':'500+ sold','sales_lower_bound':500,'reviews_display':'51','min_reviews':50,'status':'threshold_met'}
        config={'min_sold':500,'min_reviews':50}
        validate_shein_result_evidence(card,decision,observed,config)
        for altered in (dict(observed,reviews_display='49'),dict(observed,product_id='456'),dict(observed,identity='shein-us:456')):
            with self.assertRaises(ValueError):validate_shein_result_evidence(card,decision,altered,config)
        with self.assertRaises(ValueError):validate_shein_result_evidence(dict(card,sales_text='100+ sold'),decision,observed,config)

    def test_english_platform_suggestions_prioritize_the_actual_set_component(self):
        from optimization_worker.home_suggestions import rank_shein_suggestions
        rows=[{'text':'women skirt set lace','position':3},{'text':'women pants set','position':1},
              {'text':'women skirt set','position':2},{'text':'women black skirt set','position':4},
              {'text':'women skirt set','position':5}]
        result=rank_shein_suggestions('womens_skirt_set',rows,['lace'])
        self.assertEqual([r['text'] for r in result],['women skirt set lace','women skirt set'])

    def test_visual_review_is_bound_to_shein_card_and_review_decision(self):
        import json,sqlite3,tempfile
        from optimization_worker.card_sales_v3_pipeline import initialize_pipeline,record_shein_detail
        from optimization_worker.visual_candidate import VisualCandidate
        with tempfile.NamedTemporaryFile() as f:
            scope=('req','digest',1);plan={'platform':'shein-us','strategy':'shein-home-v1','category':'dress'}
            config={'min_sold':500,'min_reviews':50,'result_limit':5};claim={'id':1,'snapshot_id':2,'company_id':3,'input_hash':'h'}
            card={'product_id':'123','url':'https://us.shein.com/example-p-123.html','sales_text':'500+ sold','query':'dress','rank':1,'bbox':[1,1,30,40],'is_ad':False,'thumbnail_url':'https://img.sheincdn.com/photo.jpg'}
            detail={'identity':'shein-us:123','product_id':'123','detail_url':card['url'],'reviews_display':'51','title':'Example'}
            with sqlite3.connect(f.name) as db:
                initialize_pipeline(db);db.execute('insert into card_v3_meta values (?,?,?,NULL)',scope)
                db.execute('insert into card_v3_queue values (?,?,?,?,?,?)',(*scope,0,'123',json.dumps(card)))
                record_shein_detail(db,scope,0,detail,500,50,fence=lambda:True)
            reviewer=VisualCandidate(f.name,scope,plan,config,claim)
            self.assertEqual(reviewer.current(),(0,'shein-us:123'))
            self.assertTrue(reviewer.valid_detail(detail,'shein-us:123'))
            self.assertFalse(reviewer.valid_detail(dict(detail,reviews_display='49'),'shein-us:123'))
            from types import SimpleNamespace
            from optimization_worker.collector_import import _DETAIL
            from pathlib import Path
            full={key:None for key in _DETAIL};full.update(detail)
            full.update(rating_display='4.9',price_observation={'amount':'55.28','currency':'USD','source':'product_detail'},listed_at=None)
            broker=SimpleNamespace(watch_only=True,gallery_binding=None,conflict=False,rebind_requested=False,
                fence=lambda:True,anchor_guard_alive=lambda:True,guard_alive=lambda _:True,anchor_origin='anchor',peer_origin='peer',tab=3,window=4,session='s')
            event={'request_id':'req','tab_id':3,'window_id':4,'session_id':'s','native_origin':'peer',
                'operation':'visual_photo_start','detail':full,'photo':1,'source':'https://img.sheincdn.com/photo.jpg','variant':'default'}
            ticket=reviewer.native(broker,event,SimpleNamespace(),Path('/tmp'))
            self.assertTrue(ticket['ok'])
            import io,hashlib
            from PIL import Image
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder);downloads=root/'downloads';downloads.mkdir();files=root/'files';files.mkdir()
                out=io.BytesIO();Image.new('RGB',(120,120),'pink').save(out,format='PNG');raw=out.getvalue()
                target=downloads/ticket['filename'];target.parent.mkdir(parents=True);target.write_bytes(raw)
                def save(raw):
                    path=files/(hashlib.sha256(raw).hexdigest()+'.png');path.write_bytes(raw);return str(path)
                store=SimpleNamespace(files=files,_file=save)
                reviewer.native(broker,dict(request_id='req',tab_id=3,window_id=4,session_id='s',native_origin='peer',operation='visual_photo_saved',receipt=ticket['receipt'],download_id=1,final_url=event['source'],size=len(raw)),store,downloads)
                with sqlite3.connect(f.name) as db:
                    db.execute('insert into card_v3_detail_observations values (?,?,?,?,?)',(*scope,0,json.dumps(full)))
                primary={'category':'dress','components':'one_piece','length':'long','shoulders':'regular','structure':'one_piece','silhouette':'a_line','fit':'loose','sleeve_length':'short'}
                plan['design_profile']={'primary':primary,'secondary':[],'styles':[],'occasions':[]}
                from unittest.mock import patch
                store.inspect=lambda _:{}
                with patch('optimization_worker.collector_cli.validate_source'):
                    result=reviewer.submit({'submission_id':'shein-visual-test-1','receipts':[ticket['receipt']],'candidate_primary':primary,'secondary':[],'styles':[],'occasions':[],'features':['lace']},store,lambda:True)
                self.assertEqual(result['identity'],'shein-us:123')
                from optimization_worker.card_sales_v3_checkpoints import initialize
                from optimization_worker.visual_v3_finalize import build_result
                screenshot=io.BytesIO();Image.new('RGB',(120,120),'white').save(screenshot,format='JPEG');shot=screenshot.getvalue();shotsha=hashlib.sha256(shot).hexdigest();shotfile=Path(save(shot)).name
                with sqlite3.connect(f.name) as db:
                    initialize(db)
                    db.execute('insert into card_v3_batches values (?,?,?,?,?,?,?)',(*scope,0,1,0,json.dumps([card])))
                    db.execute('insert into card_v3_batch_evidence values (?,?,?,?,?,?,?,?,?,?)',(*scope,0,1,0,shotsha,shotfile,120,120))
                    final=build_result(db,scope,reviewer.scope,plan,config,'2026-11-28T00:00:00+00:00',lambda name,sha:reviewer.photo(files,name,sha))
                self.assertEqual(len(final['candidates']),1)
                self.assertEqual(final['candidates'][0]['sold_display'],'500+ sold')
                self.assertEqual(final['candidates'][0]['review_count'],51)
                self.assertEqual(final['candidates'][0]['price_display'],'USD 55.28')

    def test_new_detail_contract_accepts_usd_only_when_explicitly_enabled(self):
        from optimization_worker.collector_import import _DETAIL,valid_detail_fields
        detail={k:None for k in _DETAIL}
        detail['price_observation']={'amount':'55.28','currency':'USD','source':'product_detail'}
        detail['listed_at']=None
        self.assertFalse(valid_detail_fields(detail))
        self.assertTrue(valid_detail_fields(detail,shein_home=True))
        self.assertFalse(valid_detail_fields(dict(detail,listed_at='2026-01-01'),shein_home=True))
        self.assertFalse(valid_detail_fields(dict(detail,price_observation=dict(detail['price_observation'],currency='MXN')),shein_home=True))

    def test_shein_home_has_a_separate_fenced_protocol(self):
        from optimization_worker.collector_cli import task_config,protocol_matches
        config={'platform':'shein-us','result_limit':5,'min_sold':500,'min_reviews':20,'min_rating':4.1,
                'strategy':'shein-home-v1','initial_query_count':3,'max_expansion_queries':6,
                'max_pages_per_query':1,'sales_known_target':100,'top_per_query':30,'visual_limit':30}
        plan={'platform':'shein-us','result_limit':5,'strategy':'shein-home-v1'}
        self.assertEqual(task_config({'payload':{'collection_config':config}},plan),config)
        self.assertTrue(protocol_matches(7,plan))
        self.assertFalse(protocol_matches(6,plan))
        with self.assertRaises(ValueError):task_config({'payload':{'collection_config':dict(config,max_pages_per_query=3)}},plan)

    def test_shein_suggestion_bootstrap_does_not_apply_mexico_anchor(self):
        from optimization_worker.home_suggestion_bootstrap import make_bootstrap,validate_bootstrap
        claim={'id':1,'company_id':2,'snapshot_id':3,'lease_epoch':1,'input_hash':'h','task_type':'research.bestseller'}
        request='01234567-89ab-4cde-8fab-0123456789ab'
        draft=make_bootstrap(claim,request,'a'*64,'womens_skirt_set','women skirt set',platform='shein-us')
        self.assertEqual(draft['version'],'shein-home-v1')
        self.assertTrue(validate_bootstrap(draft,claim,request))
        with self.assertRaises(ValueError):make_bootstrap(claim,request,'a'*64,'womens_skirt_set','women skirt set')

    def test_shein_suggestion_page_is_platform_scoped(self):
        import sqlite3
        from optimization_worker.home_suggestion_checkpoint import initialize
        from optimization_worker.home_suggestion_ingest import ingest
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);initialize(db)
        body={'page_url':'https://us.shein.com/pdsearch/women%20skirt%20set/',
              'prefix':'women skirt set','visible_input':'women skirt set',
              'state':'observed','suggestions':[{'text':'women skirt set lace','position':1}]}
        args=(db,('req','digest',1),body,'a'*64,'2026-11-28T00:00:00+00:00')
        with self.assertRaises(ValueError):ingest(*args,fence=lambda:True,verify=lambda *a:True)
        ingest(*args,fence=lambda:True,verify=lambda *a:True,platform='shein-us')

    def test_shein_suggestion_and_plan_binding_with_snapshot(self):
        import base64,hashlib,io,sqlite3,tempfile
        from pathlib import Path
        from PIL import Image
        from optimization_worker.tests.test_visual_profile_local import profile
        from optimization_worker.host_session import SessionStore
        from optimization_worker.home_suggestion_bootstrap import bind_bootstrap,seal_suggestion_plan
        from optimization_worker.home_suggestion_checkpoint import initialize,save_read,save_observation
        from optimization_worker.collector_cli import bind_plan
        request='01234567-89ab-4cde-8fab-0123456789ab';p=profile()
        claim={'id':1,'company_id':2,'snapshot_id':3,'lease_epoch':1,'input_hash':'h','task_type':'research.bestseller','payload':{'site_id':'SHEIN_US'}}
        config={'platform':'shein-us','result_limit':5,'min_sold':500,'min_reviews':20,'min_rating':4.1,'strategy':'shein-home-v1',
                'initial_query_count':3,'max_expansion_queries':6,'max_pages_per_query':1,'sales_known_target':100,'top_per_query':30,'visual_limit':30}
        with tempfile.TemporaryDirectory() as tmp:
            out=io.BytesIO();Image.new('RGB',(2,2),'red').save(out,format='PNG');raw=out.getvalue();sha=hashlib.sha256(raw).hexdigest()
            snapshot={'case_id':4,'snapshot_id':3,'payload':{'source_image_artifact_ids':['1'],'collection_config':config}}
            store=SessionStore(Path(tmp)/'session.db','test')
            store.enqueue(request,'research.bestseller',claim['payload'],{'snapshot':snapshot,'artifacts':{'1':{'case_id':4,'company_id':2,'data':base64.b64encode(raw).decode(),'digest':sha}}})
            draft=bind_bootstrap(store,request,claim,'1',sha,'dress','dress',fence=lambda:True)
            db=sqlite3.connect(':memory:');self.addCleanup(db.close);initialize(db);scope=(request,draft['digest'],1)
            base={'prefix':'dress','observed_at':'2026-11-28T00:00:00+00:00','evidence_sha256':sha}
            save_read(db,scope,dict(base,state='observed'),fence=lambda:True,verify_evidence=lambda _:True)
            save_observation(db,scope,dict(base,text='dress casual',position=1),fence=lambda:True,verify_evidence=lambda _:True)
            selected=seal_suggestion_plan(store,request,claim,db,p,fence=lambda:True)
            self.assertEqual(selected[1]['text'],'dress casual')
            primary={'category':'dress','components':'one_piece','length':'long','shoulders':'regular','structure':'one_piece','silhouette':'a_line','fit':'loose','sleeve_length':'short'}
            plan={'platform':'shein-us','queries':[x['text'] for x in selected],'category':'dress','result_limit':5,'source_artifact_id':'1','viewed_source_sha256':sha,'visual_profile':p,'strategy':'shein-home-v1','design_profile':{'primary':primary,'secondary':[],'styles':[],'occasions':[]}}
            from optimization_worker.research_bridge_protocol import _valid_plan_input
            self.assertTrue(_valid_plan_input(plan))
            digest=bind_plan(store,request,claim,plan)
            self.assertEqual(bind_plan(store,request,claim,plan),digest)
            self.assertEqual(store.inspect(request)['collector_plan']['protocol_version'],7)

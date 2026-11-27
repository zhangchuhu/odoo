import unittest

from optimization_worker.home_suggestion_bootstrap import make_bootstrap, validate_bootstrap


class BootstrapTests(unittest.TestCase):
    def test_unbound_browser_suggestion_bridge_end_to_end(self):
        import base64,hashlib,io,json,re,tempfile
        from pathlib import Path
        from unittest.mock import patch
        from PIL import Image
        from optimization_worker.host_session import SessionStore
        from optimization_worker.research_return_bridge import build_bridge
        request='a1a1a1a1-1234-4234-8234-123456789abc';claim={'id':42,'company_id':2,'snapshot_id':3,'lease_epoch':1,'input_hash':'abc','task_type':'research.bestseller','payload':{'site_id':'MLM'}}
        doc=(Path(__file__).resolve().parents[1]/'RESEARCH_BRIDGE.md').read_text();profile=json.loads(re.findall(r'```json\n(.*?)\n```',doc,re.S)[-1])['visual_profile']
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=io.BytesIO();Image.new('RGB',(20,20),'red').save(out,format='PNG');raw=out.getvalue();sha=hashlib.sha256(raw).hexdigest()
            config={'strategy':'card-home-v4'};snapshot={'case_id':4,'snapshot_id':3,'payload':{'source_image_artifact_ids':['1'],'collection_config':config}}
            store=SessionStore(root/'session.db','test');store.enqueue(request,'research.bestseller',claim['payload'],{'snapshot':snapshot,'artifacts':{'1':{'case_id':4,'company_id':2,'data':base64.b64encode(raw).decode(),'digest':sha}}})
            with patch('optimization_worker.host_session.worker_records',return_value=[{'request_id':request,'claim':claim}]),patch('optimization_worker.host_session.fence'):
                bridge=build_bridge(request,object(),store,'unused',ledger_path_override=root/'ledger.db')
                self.assertEqual(bridge.call(request,'prepare_suggestions',{'source_artifact_id':'1','viewed_source_sha256':sha,'category':profile['category_code'],'anchor':profile['query_seeds'][0]}),{'accepted':True})
                body={'page_url':'https://www.mercadolibre.com.mx/','prefix':'vestido','visible_input':'vestido','state':'observed',
                      'suggestions':[{'text':'vestido mujer boda','position':1}],'screenshot':raw}
                self.assertEqual(bridge.call(request,'record_suggestions',body),{'accepted':True})
                self.assertEqual(bridge.call(request,'record_suggestions',body),{'accepted':True})
                result=bridge.call(request,'seal_suggestions',{'profile':profile})['selection']['queries']
                self.assertEqual(result[1]['source'],'platform_suggestion')
                self.assertEqual(result[2]['source'],'image_fallback')
                bridge.close()
    def test_final_selection_is_bound_to_verified_suggestion_read(self):
        import base64, hashlib, io, json, re, sqlite3, tempfile
        from datetime import datetime,timezone
        from pathlib import Path
        from PIL import Image
        from optimization_worker.host_session import SessionStore
        from optimization_worker.home_suggestion_bootstrap import bind_bootstrap,seal_suggestion_plan
        from optimization_worker.home_suggestion_checkpoint import initialize,save_read,save_observation
        request='a1a1a1a1-1234-4234-8234-123456789abc';claim={'id':42,'company_id':2,'snapshot_id':3,'lease_epoch':1,'input_hash':'abc','task_type':'research.bestseller','payload':{'site_id':'MLM'}}
        doc=(Path(__file__).resolve().parents[1]/'RESEARCH_BRIDGE.md').read_text();plan=json.loads(re.findall(r'```json\n(.*?)\n```',doc,re.S)[-1]);profile=plan['visual_profile']
        with tempfile.TemporaryDirectory() as tmp:
            out=io.BytesIO();Image.new('RGB',(2,2),'red').save(out,format='PNG');raw=out.getvalue();sha=hashlib.sha256(raw).hexdigest()
            config={'platform':'mercado-libre-mx','result_limit':10,'min_sold':500,'min_reviews':50,'min_rating':4.1,
                    'strategy':'card-home-v4','initial_query_count':3,'max_expansion_queries':6,
                    'max_pages_per_query':1,'sales_known_target':100,'top_per_query':30,'visual_limit':30}
            snapshot={'case_id':4,'snapshot_id':3,'payload':{'source_image_artifact_ids':['1'],'collection_config':config}}
            store=SessionStore(Path(tmp)/'session.db','test');store.enqueue(request,'research.bestseller',claim['payload'],{'snapshot':snapshot,'artifacts':{'1':{'case_id':4,'company_id':2,'data':base64.b64encode(raw).decode(),'digest':sha}}})
            draft=bind_bootstrap(store,request,claim,'1',sha,profile['category_code'],profile['query_seeds'][0],fence=lambda:True)
            db=sqlite3.connect(':memory:');self.addCleanup(db.close);initialize(db);scope=(request,draft['digest'],1)
            base={'prefix':profile['query_seeds'][0],'observed_at':datetime.now(timezone.utc).isoformat(),'evidence_sha256':sha}
            with self.assertRaisesRegex(ValueError,'Suggestion read required'):
                seal_suggestion_plan(store,request,claim,db,profile,fence=lambda:True)
            save_read(db,scope,dict(base,state='observed'),fence=lambda:True,verify_evidence=lambda _:True)
            save_observation(db,scope,dict(base,text='vestido mujer boda',position=1),fence=lambda:True,verify_evidence=lambda _:True)
            save_observation(db,scope,dict(base,text='vestido mujer fiesta',position=2),fence=lambda:True,verify_evidence=lambda _:True)
            save_observation(db,scope,dict(base,text='vestido mujer casual',position=3),fence=lambda:True,verify_evidence=lambda _:True)
            selected=seal_suggestion_plan(store,request,claim,db,profile,fence=lambda:True)
            self.assertEqual(selected[1]['source'],'platform_suggestion')
            self.assertEqual(selected[2]['source'],'platform_suggestion')
            self.assertEqual(seal_suggestion_plan(store,request,claim,db,profile,fence=lambda:True),selected)
            from optimization_worker.home_suggestion_bootstrap import available_expansion
            expansion=available_expansion(store,request,claim,db,[v['text'] for v in selected],fence=lambda:True)
            self.assertEqual([r['text'] for r in expansion],['vestido mujer casual'])
            from optimization_worker.collector_cli import bind_plan
            primary={'category':profile['category_code'],'components':'one_piece','length':'long','shoulders':'regular','structure':'one_piece','silhouette':'a_line','fit':'loose','sleeve_length':'short'}
            final={'platform':'mercado-libre-mx','queries':[v['text'] for v in selected],'category':profile['category_code'],'result_limit':10,
                   'source_artifact_id':'1','viewed_source_sha256':sha,'visual_profile':profile,'strategy':'card-home-v4',
                   'design_profile':{'primary':primary,'secondary':[],'styles':[],'occasions':[]}}
            digest=bind_plan(store,request,claim,final)
            self.assertEqual(bind_plan(store,request,claim,final),digest)
            with self.assertRaises(ValueError):bind_plan(store,request,claim,dict(final,queries=['vestido','vestido mujer x','vestido mujer y']))
            from optimization_worker.home_suggestion_bootstrap import restore_home_suggestions
            new_request='b1a1a1a1-1234-4234-8234-123456789abc';new_claim=dict(claim,lease_epoch=2)
            store.enqueue(new_request,'research.bestseller',claim['payload'],{'snapshot':snapshot,'artifacts':{'1':{'case_id':4,'company_id':2,'data':base64.b64encode(raw).decode(),'digest':sha}}})
            with self.assertRaises(ValueError):restore_home_suggestions(store,request,new_request,claim,new_claim,db,final,authorize=lambda:False,fence=lambda:True)
            with self.assertRaises(ValueError):restore_home_suggestions(store,request,new_request,claim,dict(new_claim,company_id=99),db,final,authorize=lambda:True,fence=lambda:True)
            restore_home_suggestions(store,request,new_request,claim,new_claim,db,final,authorize=lambda:True,fence=lambda:True)
            self.assertEqual(store.inspect(new_request)['suggestion_selection']['queries'],selected)
            self.assertEqual(len(available_expansion(store,new_request,new_claim,db,[v['text'] for v in selected],fence=lambda:True)),1)
            from optimization_worker.collector_cli import import_evidence
            from optimization_worker.tab_receipt_store import TabReceiptStore
            from unittest.mock import patch
            ledger=TabReceiptStore(str(Path(tmp)/'collection.db'));search_scope=(request,digest,1)
            ledger.card_home_state(*search_scope,final['queries'],lambda:True)
            with sqlite3.connect(ledger.path) as saved:
                for i in range(3):
                    saved.execute('INSERT INTO card_v3_pages VALUES (?,?,?,?,?,?)',(*search_scope,i,1,'home_complete'))
                    saved.execute('INSERT INTO card_v3_page_evidence VALUES (?,?,?,?,?,?,?,?,?)',(*search_scope,i,1,'https://listado.mercadolibre.com.mx/example','d'*32,'{"no_results":true}',3))
                from optimization_worker.card_sales_v3_pipeline import freeze_queue
                freeze_queue(saved,search_scope,plan_exhausted=True,fence=lambda:True,homepage_only=True)
            evidence={'task_record_id':'rec'+request.replace('-',''),'v3_observations':{}}
            class Client:research_ledger_enabled=True
            with patch('optimization_worker.visual_v3_finalize.snapshot_evidence',return_value=evidence), \
                 patch('optimization_worker.visual_v3_finalize.build_result',return_value={'candidates':[]}), \
                 patch('optimization_worker.research_upload.upload',side_effect=lambda *a,**k:{'artifact_id':9,'digest':hashlib.sha256(a[6]).hexdigest()}), \
                 patch('optimization_worker.collector_cli.reconcile_evidence',return_value=0):
                with sqlite3.connect(ledger.path) as saved:
                    deleted=saved.execute('SELECT * FROM card_v3_page_evidence WHERE query_index=2').fetchone()
                    saved.execute('DELETE FROM card_v3_page_evidence WHERE query_index=2')
                with self.assertRaisesRegex(ValueError,'Verified homepage evidence required'):
                    import_evidence(store,request,claim,evidence,'2026-11-26T00:00:00Z',Client(),lambda:None,tab_receipts=ledger)
                with sqlite3.connect(ledger.path) as saved:saved.execute('INSERT INTO card_v3_page_evidence VALUES (?,?,?,?,?,?,?,?,?)',deleted)
                self.assertEqual(import_evidence(store,request,claim,evidence,'2026-11-26T00:00:00Z',Client(),lambda:None,tab_receipts=ledger),0)
    def test_explicit_unavailable_does_not_claim_observed_suggestions(self):
        import base64,hashlib,io,json,re,sqlite3,tempfile
        from pathlib import Path
        from PIL import Image
        from optimization_worker.host_session import SessionStore
        from optimization_worker.home_suggestion_bootstrap import bind_bootstrap,mark_suggestions_unavailable,seal_suggestion_plan,restore_home_suggestions
        from optimization_worker.home_suggestion_checkpoint import initialize
        request='a1a1a1a1-1234-4234-8234-123456789abc';claim={'id':42,'company_id':2,'snapshot_id':3,'lease_epoch':1,'input_hash':'abc','task_type':'research.bestseller','payload':{'site_id':'MLM'}}
        doc=(Path(__file__).resolve().parents[1]/'RESEARCH_BRIDGE.md').read_text();profile=json.loads(re.findall(r'```json\n(.*?)\n```',doc,re.S)[-1])['visual_profile']
        with tempfile.TemporaryDirectory() as tmp:
            out=io.BytesIO();Image.new('RGB',(2,2),'red').save(out,format='PNG');raw=out.getvalue();sha=hashlib.sha256(raw).hexdigest()
            snap={'case_id':4,'snapshot_id':3,'payload':{'source_image_artifact_ids':['1']}}
            store=SessionStore(Path(tmp)/'host','test');store.enqueue(request,'research.bestseller',claim['payload'],{'snapshot':snap,'artifacts':{'1':{'case_id':4,'company_id':2,'data':base64.b64encode(raw).decode(),'digest':sha}}})
            bind_bootstrap(store,request,claim,'1',sha,profile['category_code'],profile['query_seeds'][0],fence=lambda:True)
            with self.assertRaises(ValueError):mark_suggestions_unavailable(store,request,claim,'captcha',fence=lambda:True)
            mark_suggestions_unavailable(store,request,claim,'page_unavailable',fence=lambda:True)
            db=sqlite3.connect(':memory:');self.addCleanup(db.close);initialize(db)
            selected=seal_suggestion_plan(store,request,claim,db,profile,fence=lambda:True)
            self.assertEqual([row['source'] for row in selected],['category_anchor','image_fallback','image_fallback'])
            self.assertEqual(store.inspect(request)['suggestion_unavailable']['source'],'host_report')
            mark_suggestions_unavailable(store,request,claim,'page_unavailable',fence=lambda:True)
            plan={'visual_profile':profile,'queries':[row['text'] for row in selected],'source_artifact_id':'1','viewed_source_sha256':sha}
            with store._db() as host_db:
                record=store._get(host_db,request);record['collector_plan']={'plan':plan};store._put(host_db,record)
            new_request='b1a1a1a1-1234-4234-8234-123456789abc';new_claim=dict(claim,lease_epoch=2)
            store.enqueue(new_request,'research.bestseller',claim['payload'],{'snapshot':snap,'artifacts':{'1':{'case_id':4,'company_id':2,'data':base64.b64encode(raw).decode(),'digest':sha}}})
            restore_home_suggestions(store,request,new_request,claim,new_claim,db,plan,authorize=lambda:True,fence=lambda:True)
            self.assertEqual(store.inspect(new_request)['suggestion_unavailable']['reason'],'page_unavailable')
    def test_bootstrap_checks_source_and_persists_idempotently(self):
        import base64, hashlib, io, tempfile
        from pathlib import Path
        from PIL import Image
        from optimization_worker.host_session import SessionStore
        from optimization_worker.home_suggestion_bootstrap import bind_bootstrap
        request='a1a1a1a1-1234-4234-8234-123456789abc'
        claim={'id':42,'company_id':2,'snapshot_id':3,'lease_epoch':1,'input_hash':'abc','task_type':'research.bestseller','payload':{'site_id':'MLM'}}
        with tempfile.TemporaryDirectory() as tmp:
            out=io.BytesIO();Image.new('RGB',(2,2),'red').save(out,format='PNG');raw=out.getvalue();sha=hashlib.sha256(raw).hexdigest()
            snap={'case_id':4,'snapshot_id':3,'payload':{'source_image_artifact_ids':['1']}}
            artifact={'case_id':4,'company_id':2,'data':base64.b64encode(raw).decode(),'digest':sha}
            store=SessionStore(Path(tmp)/'session.db','test');store.enqueue(request,'research.bestseller',claim['payload'],{'snapshot':snap,'artifacts':{'1':artifact}})
            first=bind_bootstrap(store,request,claim,'1',sha,'womens_skirt_set','conjunto mujer falda',fence=lambda:True)
            self.assertEqual(bind_bootstrap(store,request,claim,'1',sha,'womens_skirt_set','conjunto mujer falda',fence=lambda:True),first)
            with self.assertRaises(ValueError):bind_bootstrap(store,request,claim,'1','b'*64,'womens_skirt_set','conjunto mujer falda',fence=lambda:True)
            with self.assertRaises(ValueError):bind_bootstrap(store,request,claim,'1',sha,'womens_skirt_set','conjunto mujer falda',fence=lambda:False)
    def test_scope_is_bound_to_task_source_and_lease(self):
        claim={'id':42,'company_id':2,'snapshot_id':3,'lease_epoch':1,'input_hash':'abc','task_type':'research.bestseller'}
        request='a1a1a1a1-1234-4234-8234-123456789abc'
        draft=make_bootstrap(claim,request,'a'*64,'womens_skirt_set','conjunto mujer falda')
        self.assertTrue(validate_bootstrap(draft,claim,request))
        self.assertFalse(validate_bootstrap(draft,dict(claim,lease_epoch=2),request))
        self.assertFalse(validate_bootstrap(draft,claim,'b1a1a1a1-1234-4234-8234-123456789abc'))
        self.assertFalse(validate_bootstrap(dict(draft,anchor='conjunto mujer pantalon'),claim,request))

    def test_rejects_unsupported_category_and_invalid_image(self):
        claim={'id':42,'company_id':2,'snapshot_id':3,'lease_epoch':1,'input_hash':'abc','task_type':'research.bestseller'}
        request='a1a1a1a1-1234-4234-8234-123456789abc'
        with self.assertRaises(ValueError):make_bootstrap(claim,request,'bad','womens_skirt_set','conjunto mujer falda')
        with self.assertRaises(ValueError):make_bootstrap(claim,request,'a'*64,'unknown','conjunto mujer falda')

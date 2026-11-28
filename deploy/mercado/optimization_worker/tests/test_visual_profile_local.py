import copy
import unittest
from optimization_worker.visual_profile import validate_profile


def profile():
    return {'category_code':'dress','category':'dress','subtype':'dress','silhouette':'flowy hem','fit':'loose','color':'red','style_codes':['casual'],'style':['casual'],'selling_point_codes':['round_neck','sleeveless'],'selling_points':['round neck','sleeveless'],'construction':['flowy hem'],'defining_features':['round neck','sleeveless'],'exclusions':['long sleeve'],'scene_codes':['everyday'],'use_scene':['everyday'],'query_language':'en-US','query_seeds':['dress','dress flowy hem','casual dress']}


class ProfileTests(unittest.TestCase):
    def test_mexico_skirt_set_uses_category_anchor_for_new_v3_plan(self):
        from optimization_worker.collector_cli import validate_new_query_anchor
        validate_new_query_anchor({'platform':'mercado-libre-mx','strategy':'card-visual-v3',
                                   'category':'womens_skirt_set','queries':['conjunto mujer falda','conjunto mujer falda calado','conjunto mujer falda boho']})
        with self.assertRaises(ValueError):
            validate_new_query_anchor({'platform':'mercado-libre-mx','strategy':'card-visual-v3',
                                       'category':'womens_skirt_set','queries':['conjunto de top y falda larga','b','c']})
        validate_new_query_anchor({'platform':'mercado-libre-mx','strategy':'visual-v2',
                                   'category':'womens_skirt_set','queries':['conjunto de top y falda larga','b','c']})
    def test_profile_rejects_wrong_language_mapping_and_category(self):
        p=profile(); self.assertEqual(validate_profile(p,'shein-us')['query_seeds'],p['query_seeds'])
        for changes in ({'query_language':'es-MX'},{'category_code':'pants'},{'style':['formal']},{'query_seeds':['dress','red dress','casual dress']},{'query_seeds':['dress','dress flowy hem','ropa mujer']}):
            with self.subTest(changes=changes),self.assertRaises((ValueError,TypeError)):
                validate_profile(dict(p,**changes),'shein-us')
    def test_spanish_profile_and_no_hidden_or_duplicate_queries(self):
        p={'category_code':'dress','category':'vestido','subtype':'vestido','silhouette':'vuelo fluido','fit':'holgado','color':'rojo','style_codes':['elegant'],'style':['elegante'],'selling_point_codes':['round_neck','sleeveless'],'selling_points':['cuello redondo','sin mangas'],'construction':['vuelo fluido'],'defining_features':['cuello redondo','sin mangas'],'exclusions':['manga larga'],'scene_codes':['wedding'],'use_scene':['boda'],'query_language':'es-MX','query_seeds':['vestido','vestido vuelo fluido','vestido elegante']}
        self.assertEqual(validate_profile(p,'mercado-libre-mx')['category_code'],'dress')
        for queries in (['vestido','vestido vuelo fluido','vestido elegante azul'],['vestido','vestido vuelo fluido','vestido'],['vestido','vestido vuelo fluido','vestido \u200belegante']):
            with self.assertRaises((ValueError,TypeError)):
                validate_profile(dict(p,query_seeds=queries),'mercado-libre-mx')


class ProfileBindingTests(unittest.TestCase):
    def test_protocol_matches_only_its_bound_strategy(self):
        from optimization_worker.collector_cli import protocol_matches
        self.assertTrue(protocol_matches(5,{'strategy':'card-visual-v3'}))
        self.assertFalse(protocol_matches(4,{'strategy':'card-visual-v3'}))
        self.assertTrue(protocol_matches(4,{'strategy':'visual-v2'}))
        self.assertFalse(protocol_matches(5,{'strategy':'visual-v2'}))
    def test_card_v3_plan_is_bound_to_immutable_config_and_design(self):
        import base64,hashlib,io,tempfile
        from pathlib import Path
        from PIL import Image
        from optimization_worker.host_session import SessionStore
        from optimization_worker.collector_cli import bind_plan
        p={'category_code':'dress','category':'vestido','subtype':'vestido','silhouette':'vuelo fluido','fit':'holgado','color':'rojo','style_codes':['elegant'],'style':['elegante'],'selling_point_codes':['round_neck','sleeveless'],'selling_points':['cuello redondo','sin mangas'],'construction':['vuelo fluido'],'defining_features':['cuello redondo','sin mangas'],'exclusions':['manga larga'],'scene_codes':['wedding'],'use_scene':['boda'],'query_language':'es-MX','query_seeds':['vestido','vestido vuelo fluido','vestido elegante']}
        primary={'category':'dress','components':'one_piece','length':'long','shoulders':'regular','structure':'one_piece','silhouette':'a_line','fit':'loose','sleeve_length':'short'}
        config={'platform':'mercado-libre-mx','result_limit':5,'min_sold':500,'min_reviews':50,'min_rating':4.1,'strategy':'card-visual-v3','initial_query_count':3,'max_expansion_queries':6,'max_pages_per_query':3,'sales_known_target':100,'top_per_query':30,'visual_limit':30}
        with tempfile.TemporaryDirectory() as root:
            out=io.BytesIO();Image.new('RGB',(2,2),'red').save(out,format='PNG');raw=out.getvalue();sha=hashlib.sha256(raw).hexdigest()
            request='01234567-89ab-4cde-8fab-0123456789ab';store=SessionStore(Path(root)/'db','test');payload={'site_id':'MLM'}
            claim={'company_id':6,'snapshot_id':5,'task_type':'research.bestseller','payload':payload,'input_hash':'hash'}
            snap={'case_id':4,'snapshot_id':5,'payload':{'source_image_artifact_ids':['1'],'collection_config':config}}
            store.enqueue(request,'research.bestseller',payload,{'snapshot':snap,'artifacts':{'1':{'case_id':4,'company_id':6,'data':base64.b64encode(raw).decode(),'digest':sha}}})
            plan={'platform':'mercado-libre-mx','queries':p['query_seeds'],'category':'dress','result_limit':5,'source_artifact_id':'1','viewed_source_sha256':sha,'visual_profile':p,'strategy':'card-visual-v3','design_profile':{'primary':primary,'secondary':[],'styles':[],'occasions':[]}}
            digest=bind_plan(store,request,claim,plan)
            self.assertEqual(store.inspect(request)['collector_plan']['protocol_version'],5)
            self.assertEqual(bind_plan(store,request,claim,plan),digest)
            with self.assertRaises(ValueError):bind_plan(store,request,claim,dict(plan,result_limit=6))
    def test_new_task_needs_bound_profile_and_exact_plan(self):
        import base64, hashlib, io, tempfile
        from pathlib import Path
        from PIL import Image
        from optimization_worker.host_session import SessionStore
        from optimization_worker.collector_cli import bind_plan
        with tempfile.TemporaryDirectory() as root:
            out=io.BytesIO(); Image.new('RGB',(2,2),'red').save(out,format='PNG'); raw=out.getvalue(); digest=hashlib.sha256(raw).hexdigest()
            request='01234567-89ab-4cde-8fab-0123456789ab'; store=SessionStore(Path(root)/'db','test')
            config={'platform':'shein-us','min_sold':0,'min_reviews':0,'min_rating':0,'result_limit':5}
            snapshot={'case_id':4,'snapshot_id':5,'payload':{'source_image_artifact_ids':['1'],'collection_config':config}}
            context={'snapshot':snapshot,'artifacts':{'1':{'case_id':4,'company_id':6,'data':base64.b64encode(raw).decode(),'digest':digest}}}
            payload={'site_id':'SHEIN_US'}; claim={'company_id':6,'snapshot_id':5,'task_type':'research.bestseller','payload':payload,'input_hash':'x'}
            store.enqueue(request,'research.bestseller',payload,context)
            p=profile(); plan={'platform':'shein-us','queries':p['query_seeds'],'category':'dress','result_limit':5,'source_artifact_id':'1','viewed_source_sha256':digest}
            with self.assertRaises(ValueError): bind_plan(store,request,claim,plan)
            plan['visual_profile']=p; bind_plan(store,request,claim,plan); store=SessionStore(Path(root)/'db','test'); bind_plan(store,request,claim,plan)
            altered=copy.deepcopy(plan); altered['visual_profile']['fit']='tight'
            with self.assertRaises(ValueError): bind_plan(store,request,claim,altered)
            altered=copy.deepcopy(plan); altered['queries'][0]='other dress'
            with self.assertRaises(ValueError): bind_plan(store,request,claim,altered)

class HistoricalBindingTests(unittest.TestCase):
    def test_existing_config_plan_survives_restart_and_unknown_upload(self):
        import base64, hashlib, io, tempfile
        from pathlib import Path
        from PIL import Image
        from optimization_worker.host_session import SessionStore
        from optimization_worker.collector_cli import import_evidence, reconcile_evidence, bind_plan, collector_id
        from optimization_worker.domain import canonical_hash
        from test_collector_import import fixture
        request='01234567-89ab-4cde-8fab-0123456789ab'
        with tempfile.TemporaryDirectory() as root:
            store=SessionStore(Path(root)/'db','test'); out=io.BytesIO();Image.new('RGB',(2,2),'red').save(out,format='PNG'); raw=out.getvalue();sha=hashlib.sha256(raw).hexdigest(); data,queries=fixture()
            config={'platform':'shein-us','min_sold':500,'min_reviews':50,'min_rating':4.1,'result_limit':15}
            snap={'case_id':4,'snapshot_id':5,'payload':{'source_image_artifact_ids':['1','2'],'collection_config':config}}
            payload={'site_id':'SHEIN_US'};claim={'company_id':6,'snapshot_id':5,'task_type':'research.bestseller','payload':payload,'input_hash':'hash'}
            store.enqueue(request,'research.bestseller',payload,{'snapshot':snap,'artifacts':{x:{'case_id':4,'company_id':6,'data':base64.b64encode(raw).decode(),'digest':sha} for x in ('1','2')}})
            plan={'platform':'shein-us','queries':queries,'category':'dress','result_limit':15,'source_artifact_id':'2','viewed_source_sha256':sha}
            with self.assertRaises(ValueError): bind_plan(store,request,claim,plan)
            binding={'collection_config':config,'company_id':6,'case_id':4,'snapshot_id':5,'request_id':request,'collector_id':collector_id(request),'input_hash':'hash','plan':plan};binding['digest']=canonical_hash(binding)
            with store._db() as db:
                r=store._get(db,request);r['collector_plan']=binding;store._put(db,r)
            store=SessionStore(Path(root)/'db','test');self.assertEqual(bind_plan(store,request,claim,plan),binding['digest'])
            with self.assertRaises(ValueError): bind_plan(store,request,claim,dict(plan,category='pants'))
            class Client:
                calls=0
                def create_artifact(self,case_id,**kw): self.calls+=1;self.raw=kw['data'];raise OSError('lost receipt')
                def artifact(self,id):return {'case_id':4,'company_id':6,'mime_type':'application/json','digest':hashlib.sha256(self.raw).hexdigest(),'data':base64.b64encode(self.raw).decode()}
            client=Client()
            with self.assertRaises(OSError): import_evidence(store,request,claim,data,'2026-11-16T00:00:00Z',client,lambda:None,tab_receipts=type('Verified',(),{'verified_queries':lambda *args:True})())
            store=SessionStore(Path(root)/'db','test'); self.assertEqual(reconcile_evidence(store,request,claim,'9',client),1);self.assertEqual(client.calls,1)

    def test_only_bound_legacy_plan_can_use_second_source(self):
        import base64, hashlib, io, tempfile
        from pathlib import Path
        from PIL import Image
        from optimization_worker.host_session import SessionStore
        from optimization_worker.collector_cli import bind_plan, collector_id
        from optimization_worker.domain import canonical_hash
        from test_collector_import import fixture
        request='01234567-89ab-4cde-8fab-0123456789ab'
        with tempfile.TemporaryDirectory() as root:
            store=SessionStore(Path(root)/'db','test');out=io.BytesIO();Image.new('RGB',(2,2),'red').save(out,format='PNG');raw=out.getvalue();sha=hashlib.sha256(raw).hexdigest();_,queries=fixture();payload={'site_id':'MLM'};claim={'company_id':6,'snapshot_id':5,'task_type':'research.bestseller','payload':payload,'input_hash':'hash'}
            store.enqueue(request,'research.bestseller',payload,{'snapshot':{'case_id':4,'snapshot_id':5,'payload':{'source_image_artifact_ids':['1','2']}},'artifacts':{x:{'case_id':4,'company_id':6,'data':base64.b64encode(raw).decode(),'digest':sha} for x in ('1','2')}})
            plan={'platform':'shein-us','queries':queries,'category':'dress','result_limit':15,'source_artifact_id':'2','viewed_source_sha256':sha}
            with self.assertRaises(ValueError):bind_plan(store,request,claim,plan)
            binding={'company_id':6,'case_id':4,'snapshot_id':5,'request_id':request,'collector_id':collector_id(request),'input_hash':'hash','plan':plan};binding['digest']=canonical_hash(binding)
            with store._db() as db:r=store._get(db,request);r['collector_plan']=binding;store._put(db,r)
            self.assertEqual(bind_plan(store,request,claim,plan),binding['digest'])

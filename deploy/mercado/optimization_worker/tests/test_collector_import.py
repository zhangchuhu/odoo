import unittest, sys, copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))


def fixture():
    queries=['dress one','dress two','dress three']
    blocks=[]
    for q in queries:
        blocks.append({'query':q,'observations':[{'query':q,'rank':i,'is_ad':False,'title':'Dress', 'url':f'https://us.shein.com/item-p-{i}.html', 'product_id':str(i),'thumbnail_url':None} for i in range(1,31)]})
    details=[{'identity':f'shein-us:{i}','status':'qualified' if i==1 else 'rejected','reason':None if i==1 else 'threshold_failure','detail_url':f'https://us.shein.com/item-p-{i}.html','product_id':str(i),'title':'Dress','category':'dress','visual_category_code':'dress','sold_display':None,'reviews_display':'500+' if i==1 else '3','rating_display':'4.8','match_level':'同款' if i==1 else None,'visual_features':['round neckline'] if i==1 else None} for i in range(1,31)]
    return {'task_record_id':'rec0123456789ab4cde8fab0123456789ab','platform':'shein-us','queries':blocks,'details':details},queries

class CollectorConfigTest(unittest.TestCase):
    def test_visual_v2_preserves_user_threshold_and_fixed_query_bounds(self):
        from optimization_worker.collector_cli import task_config
        plan = {'platform':'mercado-libre-mx','result_limit':3,'strategy':'visual-v2'}
        config = {'platform':'mercado-libre-mx','result_limit':3,'min_sold':800,'min_reviews':50,'min_rating':4.1,
                  'strategy':'visual-v2','initial_query_count':3,'max_expansion_queries':6,'max_cards_per_query':100}
        self.assertEqual(task_config({'payload':{'collection_config':config}}, plan),config)
        for changed in ({'min_sold':True}, {'max_expansion_queries':7}, {'strategy':'staged-v1'}):
            with self.assertRaises(ValueError):
                task_config({'payload':{'collection_config':dict(config,**changed)}},plan)

    def test_task_snapshot_thresholds_and_platform_cannot_be_overridden(self):
        from optimization_worker.collector_cli import task_config
        plan = {'platform':'shein-us','result_limit':3}
        config = {'platform':'shein-us','result_limit':3,'min_sold':0,'min_reviews':600,'min_rating':4.6}
        self.assertEqual(task_config({'payload':{'collection_config':config}}, plan),config)
        for change in ({'platform':'mercado-libre-mx'}, {'result_limit':4}):
            with self.assertRaises(ValueError): task_config({'payload':{'collection_config':config}}, dict(plan,**change))
        for field,value in [('min_reviews',-1),('min_rating',float('nan')),('min_sold',True)]:
            with self.assertRaises(ValueError): task_config({'payload':{'collection_config':dict(config,**{field:value})}},plan)

    def test_positive_target_above_fifteen_and_fifty_is_accepted(self):
        from optimization_worker.collector_cli import task_config
        from optimization_worker.collector_import import convert_evidence
        plan={'platform':'shein-us','result_limit':100}
        config={'platform':'shein-us','result_limit':100,'min_sold':0,'min_reviews':0,'min_rating':0}
        self.assertEqual(task_config({'payload':{'collection_config':config}},plan)['result_limit'],100)
        data,queries=fixture()
        result=convert_evidence(data,data['task_record_id'],'shein-us',queries,'dress','2026-11-16T00:00:00Z',result_limit=100)
        self.assertEqual(len(result['candidates']),1)

    def test_configured_platform_is_bound_to_site(self):
        from optimization_worker.collector_cli import validate_platform_site
        for platform, site in [('shein-us','SHEIN_US'),('mercado-libre-mx','MLM')]:
            validate_platform_site({'payload':{'collection_config':{}}},platform,site)
            with self.assertRaises(ValueError): validate_platform_site({'payload':{'collection_config':{}}},platform,'MLM' if site == 'SHEIN_US' else 'SHEIN_US')
        validate_platform_site({'payload':{}},'shein-us','MLM')

    def test_source_must_be_first_snapshot_attachment(self):
        from optimization_worker.collector_cli import validate_source
        record={'context':{'snapshot':{'payload':{'source_image_artifact_ids':['1','2']}}, 'artifacts':{}}, 'images':{}}
        with self.assertRaisesRegex(ValueError, 'First source image'): validate_source(record, {'company_id':1}, {'source_artifact_id':'2'})

    def test_configured_shein_threshold_is_enforced(self):
        from optimization_worker.collector_import import convert_evidence
        data,queries=fixture()
        with self.assertRaises(ValueError):
            convert_evidence(data,data['task_record_id'],'shein-us',queries,'dress','2026-11-16T00:00:00Z',min_reviews=600)

class CollectorImportTest(unittest.TestCase):
    def test_missing_mercado_listing_sales_can_be_rejected_without_substitution(self):
        from optimization_worker.collector_import import convert_evidence
        data,queries=fixture();data['platform']='mercado-libre-mx'
        for block in data['queries']:
            for card in block['observations']:
                card['product_id']='MLM'+card['product_id']
                card['url']='https://articulo.mercadolibre.com.mx/'+card['product_id']
        for index,detail in enumerate(data['details'],1):
            detail.update(identity='mercado-libre-mx:MLM'+str(index),product_id='MLM'+str(index),
                detail_url='https://articulo.mercadolibre.com.mx/MLM'+str(index),status='rejected',
                reason='metric_missing_or_ambiguous',sold_display=None,match_level=None,visual_features=None)
        result=convert_evidence(data,data['task_record_id'],'mercado-libre-mx',queries,'dress','2026-11-16T00:00:00Z')
        self.assertEqual(result['candidates'],[])
        data['details'][0]['sold_display']='+500 vendidos'
        with self.assertRaisesRegex(ValueError,'Sales are not missing'):
            convert_evidence(data,data['task_record_id'],'mercado-libre-mx',queries,'dress','2026-11-16T00:00:00Z')
    def test_duplicate_cards_keep_raw_rank_and_one_query_hit(self):
        from optimization_worker.collector_import import convert_evidence
        data,queries=fixture()
        original=copy.deepcopy(data['queries'][0]['observations'][0])
        duplicate=dict(original,rank=30,is_ad=True)
        data['queries'][0]['observations'][-1]=duplicate
        saved=copy.deepcopy(data)
        result=convert_evidence(data,data['task_record_id'],'shein-us',queries,'dress','2026-11-16T00:00:00Z')
        self.assertEqual(result['candidates'][0]['item_id'],'1')
        self.assertEqual(result['candidates'][0]['position'],1)
        self.assertFalse(result['candidates'][0]['is_ad'])
        self.assertEqual(data,saved)

    def test_same_query_ad_and_organic_use_best_organic_position(self):
        from optimization_worker.collector_import import convert_evidence
        data,queries=fixture()
        first=data['queries'][0]['observations'][0]; first['is_ad']=True
        data['queries'][0]['observations'][-1]=dict(first, rank=30, is_ad=False)
        # Other queries have an organic rank 1 for the same product.
        result=convert_evidence(data,data['task_record_id'],'shein-us',queries,'dress','2026-11-16T00:00:00Z')
        self.assertEqual(result['candidates'][0]['position'],1)
        self.assertFalse(result['candidates'][0]['is_ad'])

    def test_duplicate_within_one_query_is_not_cross_query_recurrence(self):
        from optimization_worker.collector_import import convert_evidence
        data,queries=fixture()
        duplicate=dict(data['queries'][0]['observations'][0],rank=30,is_ad=True)
        data['queries'][0]['observations'][-1]=duplicate
        for j in (1,2):
            card=data['queries'][j]['observations'][0]; pid=str(1000+j)
            card.update(product_id=pid,url=f'https://us.shein.com/item-p-{pid}.html')
        data['details']=data['details'][1:]
        result=convert_evidence(data,data['task_record_id'],'shein-us',queries,'dress','2026-11-16T00:00:00Z')
        self.assertEqual(result['status'],'empty')
        self.assertEqual(result['candidates'],[])

    def test_valid_data_and_rejections(self):
        from optimization_worker.collector_import import convert_evidence
        data,queries=fixture(); result=convert_evidence(data,'rec0123456789ab4cde8fab0123456789ab','shein-us',queries,'dress','2026-11-16T00:00:00Z')
        self.assertEqual(result['status'],'complete');self.assertEqual(len(result['candidates']),1)
        self.assertIsNone(result['candidates'][0]['sold_lower_bound'])
        for mutation in ('binding','query','cardid','metric','visual','order'):
            d=copy.deepcopy(data)
            if mutation=='binding':d['task_record_id']='other'
            elif mutation=='query':d['queries'][0]['query']='rewritten'
            elif mutation=='cardid':d['queries'][0]['observations'][0]['product_id']='999'
            elif mutation=='metric':d['details'][0]['reviews_display']='8'
            elif mutation=='visual':d['details'][0]['visual_category_code']='pants'
            elif mutation=='order':d['details'][0],d['details'][1]=d['details'][1],d['details'][0]
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):convert_evidence(d,'rec0123456789ab4cde8fab0123456789ab','shein-us',queries,'dress','2026-11-16T00:00:00Z')

class CollectorBindingTest(unittest.TestCase):
    def test_visual_v2_plan_binds_source_profile_and_immutable_threshold(self):
        import tempfile,base64,hashlib,io,json,re
        from PIL import Image
        from optimization_worker.host_session import SessionStore
        from optimization_worker.collector_cli import bind_plan
        doc=(Path(__file__).resolve().parents[1]/'RESEARCH_BRIDGE.md').read_text()
        plan=json.loads(re.findall(r'```json\n(.*?)\n```',doc,re.S)[-1])
        plan.update(strategy='visual-v2',result_limit=3)
        plan['design_profile']={'primary':{'category':plan['category'],'components':None,'length':'long','shoulders':'regular',
                                'structure':'two_piece','silhouette':'a_line','fit':None,'sleeve_length':None},
                                'secondary':['lace'],'styles':['boho'],'occasions':[]}
        rid='01234567-89ab-4cde-8fab-0123456789ab'
        b=io.BytesIO();Image.new('RGB',(2,2),'red').save(b,format='PNG');raw=b.getvalue();digest=hashlib.sha256(raw).hexdigest()
        plan.update(source_artifact_id='1',viewed_source_sha256=digest)
        payload={'site_id':'MLM','facts_artifact_id':'1','keywords_artifact_id':'2'}
        config=dict(platform='mercado-libre-mx',result_limit=3,min_sold=800,min_reviews=50,min_rating=4.1,
                    strategy='visual-v2',initial_query_count=3,max_expansion_queries=6,max_cards_per_query=100)
        context={'snapshot':{'case_id':4,'snapshot_id':5,'payload':{'source_image_artifact_ids':['1'],'collection_config':config}},
                 'artifacts':{'1':{'case_id':4,'company_id':6,'data':base64.b64encode(raw).decode(),'digest':digest}}}
        claim=dict(company_id=6,snapshot_id=5,task_type='research.bestseller',payload=payload,input_hash='digest',lease_epoch=1)
        with tempfile.TemporaryDirectory() as d:
            store=SessionStore(Path(d)/'host.sqlite','identity');store.enqueue(rid,'research.bestseller',payload,context)
            bind_plan(store,rid,claim,plan)
            self.assertEqual(store.inspect(rid)['collector_plan']['protocol_version'],4)
            self.assertEqual(store.inspect(rid)['collector_plan']['collection_config']['min_sold'],800)
            changed=copy.deepcopy(plan);changed['design_profile']['primary']['length']='short'
            with self.assertRaises(ValueError):bind_plan(store,rid,claim,changed)
            from unittest.mock import patch
            from optimization_worker.collector_cli import import_evidence
            claim['id']=55
            evidence={'queries':[{}, {}, {}], 'task_record_id':'rec'+rid.replace('-','')}
            class Receipts:
                path=str(Path(d)/'ledger.sqlite')
                def visual_v2_queries(self,*args):return plan['queries']
                def next_query_index(self,*args,**kwargs):return 3
            class Client:research_ledger_enabled=True
            with patch('optimization_worker.visual_v2_finalize.snapshot_evidence',return_value=evidence), \
                 patch('optimization_worker.visual_v2_finalize.build_result',return_value={'candidates':[]}) as build, \
                 patch('optimization_worker.research_upload.upload',side_effect=lambda *a,**k:{'artifact_id':9,'digest':hashlib.sha256(a[6]).hexdigest()}), \
                 patch('optimization_worker.collector_cli.reconcile_evidence',return_value=0):
                self.assertEqual(import_evidence(store,rid,claim,evidence,'2026-11-26T00:00:00+00:00',Client(),lambda:None,tab_receipts=Receipts()),0)
                self.assertEqual(build.call_args.args[4]['min_sold'],800)

    def test_staged_binding_and_import_keep_original_sales_threshold(self):
        import tempfile,base64,hashlib,io,json,re
        from PIL import Image
        from optimization_worker.host_session import SessionStore
        from optimization_worker.collector_cli import bind_plan,import_evidence
        doc=(Path(__file__).resolve().parents[1]/'RESEARCH_BRIDGE.md').read_text()
        plan=json.loads(re.findall(r'```json\n(.*?)\n```',doc,re.S)[-1]);plan['strategy']='staged-v1';plan['result_limit']=1
        rid='01234567-89ab-4cde-8fab-0123456789ab'
        b=io.BytesIO();Image.new('RGB',(2,2),'red').save(b,format='PNG');raw=b.getvalue();digest=hashlib.sha256(raw).hexdigest()
        plan.update(source_artifact_id='1',viewed_source_sha256=digest)
        payload={'site_id':'MLM','facts_artifact_id':'1','keywords_artifact_id':'2'}
        config=dict(platform='mercado-libre-mx',result_limit=1,min_sold=500,min_reviews=50,min_rating=4.1)
        context={'snapshot':{'case_id':4,'snapshot_id':5,'payload':{'source_image_artifact_ids':['1'],'collection_config':config}},
                 'artifacts':{'1':{'case_id':4,'company_id':6,'data':base64.b64encode(raw).decode(),'digest':digest}}}
        claim=dict(company_id=6,snapshot_id=5,task_type='research.bestseller',payload=payload,input_hash='digest',lease_epoch=1)
        with tempfile.TemporaryDirectory() as d:
            store=SessionStore(Path(d)/'host.sqlite','identity');store.enqueue(rid,'research.bestseller',payload,context)
            bind_plan(store,rid,claim,plan)
            self.assertEqual(store.inspect(rid)['collector_plan']['protocol_version'],3)
            data,old=fixture(); data.update(platform='mercado-libre-mx',details=data['details'][:1])
            for block,q in zip(data['queries'],plan['queries']):
                block['query']=q
                for card in block['observations']:
                    card.update(query=q,product_id='MLM'+card['product_id'],url='https://articulo.mercadolibre.com.mx/MLM'+card['product_id'])
            data['details'][0].update(identity='mercado-libre-mx:MLM1',product_id='MLM1',detail_url='https://articulo.mercadolibre.com.mx/MLM1',sold_display='+500 vendidos')
            class Receipts:
                def verified_queries(self,*args,**kw): return kw=={'staged':True}
                def budget_elapsed(self,*args):return 0
            class Client:
                def create_artifact(self,case_id,**kwargs):self.raw=kwargs['data'];return {'artifact_id':9,'digest':hashlib.sha256(self.raw).hexdigest()}
                def artifact(self,artifact_id):return {'case_id':4,'company_id':6,'mime_type':'application/json','digest':hashlib.sha256(self.raw).hexdigest(),'data':base64.b64encode(self.raw).decode()}
            client=Client()
            self.assertEqual(import_evidence(store,rid,claim,data,'2026-11-16T00:00:00Z',client,lambda:None,tab_receipts=Receipts()),1)
    def test_plan_and_result_are_bound_to_original_request_and_snapshot(self):
        import tempfile, base64, hashlib, io
        from PIL import Image
        from optimization_worker.host_session import SessionStore
        from optimization_worker.collector_cli import bind_plan, import_evidence
        with tempfile.TemporaryDirectory() as d:
            store=SessionStore(Path(d)/'host.sqlite','identity')
            payload={'site_id':'MLM','facts_artifact_id':'1','keywords_artifact_id':'2'}
            buffer=io.BytesIO();Image.new('RGB',(2,2),'red').save(buffer,format='PNG');raw=buffer.getvalue(); digest=hashlib.sha256(raw).hexdigest()
            context={'snapshot':{'case_id':4,'snapshot_id':5,'payload':{'source_image_artifact_ids':['1']}},'artifacts':{'1':{'case_id':4,'company_id':6,'data':base64.b64encode(raw).decode(),'digest':digest}}}
            store.enqueue('01234567-89ab-4cde-8fab-0123456789ab','research.bestseller',payload,context)
            data,queries=fixture(); claim={'company_id':6,'snapshot_id':5,'task_type':'research.bestseller','payload':payload,'input_hash':'digest','lease_epoch':1}
            plan={'platform':'shein-us','queries':queries,'category':'dress','result_limit':15,'source_artifact_id':'1','viewed_source_sha256':digest}
            for field, wrong in [('case_id', 9), ('company_id', 9)]:
                with store._db() as db:
                    row=store._get(db,'01234567-89ab-4cde-8fab-0123456789ab'); original=row['context']['artifacts']['1'][field]
                    row['context']['artifacts']['1'][field]=wrong; store._put(db,row)
                with self.assertRaises(ValueError):bind_plan(store,'01234567-89ab-4cde-8fab-0123456789ab',claim,plan)
                with store._db() as db:
                    row=store._get(db,'01234567-89ab-4cde-8fab-0123456789ab'); row['context']['artifacts']['1'][field]=original; store._put(db,row)
            self.assertTrue(bind_plan(store,'01234567-89ab-4cde-8fab-0123456789ab',claim,plan))
            self.assertEqual(store.inspect('01234567-89ab-4cde-8fab-0123456789ab')['collector_plan']['collector_id'], 'rec0123456789ab4cde8fab0123456789ab')
            wrong=copy.deepcopy(data); wrong['task_record_id']='rec0123456789ab4cde8fab0123456789ac'
            class NoUpload:
                def create_artifact(self,*args,**kwargs): raise AssertionError('must not upload')
            with self.assertRaises(ValueError):import_evidence(store,'01234567-89ab-4cde-8fab-0123456789ab',claim,wrong,'2026-11-16T00:00:00Z',NoUpload(),lambda:None)
            with store._db() as db:
                record=store._get(db,'01234567-89ab-4cde-8fab-0123456789ab'); record['context']['artifacts']['1'].pop('company_id'); store._put(db,record)
            class Uncalled:
                def create_artifact(self,*args,**kwargs): raise AssertionError('must not upload')
            with self.assertRaises(ValueError):import_evidence(store,'01234567-89ab-4cde-8fab-0123456789ab',claim,data,'2026-11-16T00:00:00Z',Uncalled(),lambda:None)
            self.assertEqual(store.inspect('01234567-89ab-4cde-8fab-0123456789ab')['state'],'prepared')
            with store._db() as db:
                record=store._get(db,'01234567-89ab-4cde-8fab-0123456789ab'); record['context']['artifacts']['1']['company_id']=6; store._put(db,record)
            with self.assertRaises(ValueError):bind_plan(store,'01234567-89ab-4cde-8fab-0123456789ab',claim,dict(plan,category='pants'))
            with self.assertRaises(ValueError):import_evidence(store,'01234567-89ab-4cde-8fab-0123456789ab',dict(claim,company_id=7),data,'2026-11-16T00:00:00Z')

            class Client:
                def create_artifact(self,case_id,**kwargs):
                    self.raw=kwargs['data']; return {'artifact_id':9,'digest':hashlib.sha256(self.raw).hexdigest()}
                def artifact(self,artifact_id):
                    return {'case_id':4,'company_id':6,'mime_type':'application/json','digest':hashlib.sha256(self.raw).hexdigest(),'data':base64.b64encode(self.raw).decode()}
            client=Client()
            with self.assertRaisesRegex(ValueError,'query receipts'):
                import_evidence(store,'01234567-89ab-4cde-8fab-0123456789ab',claim,data,'2026-11-16T00:00:00Z',client,lambda:None)
            self.assertFalse(hasattr(client,'raw'))
            class Receipts:
                def __init__(self, ok): self.ok=ok; self.calls=[]
                def verified_queries(self, request_id, plan_digest, queries, lease_epoch):
                    self.calls.append((request_id,plan_digest,queries,lease_epoch)); return self.ok
            receipt=Receipts(False)
            with self.assertRaisesRegex(ValueError,'query receipts'):
                import_evidence(store,'01234567-89ab-4cde-8fab-0123456789ab',claim,data,'2026-11-16T00:00:00Z',client,lambda:None,tab_receipts=receipt)
            self.assertFalse(hasattr(client,'raw'))
            self.assertEqual(store.inspect('01234567-89ab-4cde-8fab-0123456789ab')['state'],'prepared')
            receipt.ok=True
            self.assertEqual(import_evidence(store,'01234567-89ab-4cde-8fab-0123456789ab',claim,data,'2026-11-16T00:00:00Z',client,lambda:None,tab_receipts=receipt),1)
            self.assertEqual(receipt.calls[-1],('01234567-89ab-4cde-8fab-0123456789ab',store.inspect('01234567-89ab-4cde-8fab-0123456789ab')['collector_plan']['digest'],queries,1))
            self.assertEqual(store.result('01234567-89ab-4cde-8fab-0123456789ab')['candidates'][0]['item_id'],'1')
            with self.assertRaises(ValueError):import_evidence(store,'01234567-89ab-4cde-8fab-0123456789ab',claim,data,'2026-11-16T00:00:00Z')

class CollectorBypassTest(unittest.TestCase):
    def test_generic_submit_cannot_bypass_collector_validation(self):
        import tempfile
        from optimization_worker.host_session import SessionStore
        with tempfile.TemporaryDirectory() as d:
            store=SessionStore(Path(d)/'host.sqlite','identity')
            store.enqueue('01234567-89ab-4cde-8fab-0123456789ab','research.bestseller',{'site_id':'MLM'}, {'snapshot':{'case_id':4,'snapshot_id':5,'payload':{}},'artifacts':{}})
            with self.assertRaises(ValueError):store.submit('01234567-89ab-4cde-8fab-0123456789ab',{}, {'runtime':'codex','browser':'test','vision':'view_image','provenance':'test'})

class CollectorParsingTest(unittest.TestCase):
    def test_count_and_catalog_identity(self):
        from optimization_worker.collector_import import _number, _identity
        self.assertIsNone(_number('50.0'))
        self.assertEqual(_number('+1 mil vendidos'),(1000,True))
        self.assertEqual(_number('500+'),(500,True))
        self.assertEqual(_identity('https://www.mercadolibre.com.mx/p/MLMU12345','mercado-libre-mx')[0], 'MLMU12345')

class CollectorRejectionTest(unittest.TestCase):
    def test_inaccessible_and_changed_identity(self):
        from optimization_worker.collector_import import convert_evidence
        data,queries=fixture()
        inaccessible=data['details'][1]
        inaccessible.update(reason='detail_inaccessible',detail_url=None,product_id=None,title=None,category=None,visual_category_code=None,sold_display=None,reviews_display=None,rating_display=None,match_level=None,visual_features=None)
        changed=data['details'][2]
        changed.update(reason='identity_changed',detail_url='https://us.shein.com/item-p-333.html',product_id='333',match_level=None,visual_features=None)
        self.assertEqual(len(convert_evidence(data,'rec0123456789ab4cde8fab0123456789ab','shein-us',queries,'dress','2026-11-16T00:00:00Z')['candidates']),1)
        changed['product_id']='3'
        with self.assertRaises(ValueError):convert_evidence(data,'rec0123456789ab4cde8fab0123456789ab','shein-us',queries,'dress','2026-11-16T00:00:00Z')

class CollectorForbiddenTermsTest(unittest.TestCase):
    def test_chinese_color_and_size_in_visual_features_are_rejected(self):
        from optimization_worker.collector_import import disallowed_term
        for phrase in ('黑白撞色', '粉白撞色', '红色马甲', '尺码L', '大码连衣裙'):
            with self.subTest(phrase=phrase): self.assertTrue(disallowed_term(phrase))
        for phrase in ('无袖马甲', '宽腿长裤', '白领通勤风'):
            with self.subTest(phrase=phrase): self.assertFalse(disallowed_term(phrase))

    def test_hyphenated_colors_and_spanish_violeta_are_rejected(self):
        from optimization_worker.collector_import import disallowed_term
        for phrase in ('red-dress', 'blue-green', 'vestido violeta'):
            with self.subTest(phrase=phrase):self.assertTrue(disallowed_term(phrase))

class CollectorUploadRecoveryTest(unittest.TestCase):
    def test_lost_receipt_requires_verified_original_artifact_no_reupload(self):
        import tempfile, base64, hashlib, io
        from PIL import Image
        from optimization_worker.host_session import SessionStore
        from optimization_worker.collector_cli import bind_plan, import_evidence, reconcile_evidence
        with tempfile.TemporaryDirectory() as d:
            store=SessionStore(Path(d)/'host.sqlite','identity'); b=io.BytesIO();Image.new('RGB',(2,2),'red').save(b,format='PNG');raw=b.getvalue(); sha=hashlib.sha256(raw).hexdigest()
            payload={'site_id':'MLM','facts_artifact_id':'1','keywords_artifact_id':'2'}
            context={'snapshot':{'case_id':4,'snapshot_id':5,'payload':{'source_image_artifact_ids':['1']}},'artifacts':{'1':{'case_id':4,'company_id':6,'data':base64.b64encode(raw).decode(),'digest':sha}}}
            store.enqueue('01234567-89ab-4cde-8fab-0123456789ab','research.bestseller',payload,context); data,queries=fixture()
            claim={'company_id':6,'snapshot_id':5,'task_type':'research.bestseller','payload':payload,'input_hash':'digest'}
            bind_plan(store,'01234567-89ab-4cde-8fab-0123456789ab',claim,{'platform':'shein-us','queries':queries,'category':'dress','result_limit':15,'source_artifact_id':'1','viewed_source_sha256':sha})
            class Client:
                calls=0
                def create_artifact(self,case_id,**kwargs):
                    self.calls+=1; self.raw=kwargs['data'];raise OSError('lost acknowledgement')
                def artifact(self,artifact_id):
                    return {'case_id':4,'company_id':6,'mime_type':'application/json','digest':hashlib.sha256(self.raw).hexdigest(),'data':base64.b64encode(self.raw).decode()}
            client=Client()
            with self.assertRaises(OSError):import_evidence(store,'01234567-89ab-4cde-8fab-0123456789ab',claim,data,'2026-11-16T00:00:00Z',client,lambda:None,tab_receipts=type('Verified',(),{'verified_queries':lambda *args:True})())
            self.assertEqual(store.inspect('01234567-89ab-4cde-8fab-0123456789ab')['state'],'collector_upload_unknown')
            self.assertEqual(store.next()['action'],'reconcile_upload')
            with self.assertRaises(ValueError):import_evidence(store,'01234567-89ab-4cde-8fab-0123456789ab',claim,data,'2026-11-16T00:00:00Z',client,lambda:None)
            with self.assertRaises(ValueError):reconcile_evidence(store,'01234567-89ab-4cde-8fab-0123456789ab',dict(claim,company_id=7),'9',client)
            with store._db() as db:
                record=store._get(db,'01234567-89ab-4cde-8fab-0123456789ab'); record['context']['artifacts']['1'].pop('case_id'); store._put(db,record)
            with self.assertRaises(ValueError):reconcile_evidence(store,'01234567-89ab-4cde-8fab-0123456789ab',claim,'9',client)
            self.assertEqual(store.inspect('01234567-89ab-4cde-8fab-0123456789ab')['state'],'collector_upload_unknown')
            with store._db() as db:
                record=store._get(db,'01234567-89ab-4cde-8fab-0123456789ab'); record['context']['artifacts']['1']['case_id']=4; store._put(db,record)
            self.assertEqual(reconcile_evidence(store,'01234567-89ab-4cde-8fab-0123456789ab',claim,'9',client),1)
            self.assertEqual(client.calls,1)
            self.assertIn('odoo-artifact:9:sha256:',store.result('01234567-89ab-4cde-8fab-0123456789ab')['evidence'][0]['source'])

class CollectorReceiptTest(unittest.TestCase):
    def test_empty_scoped_result_seals_before_resolution_and_preserves_pending_on_failure(self):
        import tempfile, base64, hashlib, io
        from unittest.mock import patch
        from PIL import Image
        from optimization_worker.host_session import SessionStore
        from optimization_worker.collector_cli import bind_plan, import_evidence, reconcile_evidence
        with tempfile.TemporaryDirectory() as d:
            store=SessionStore(Path(d)/'host.sqlite','identity'); b=io.BytesIO(); Image.new('RGB',(2,2)).save(b,format='PNG'); raw=b.getvalue(); sha=hashlib.sha256(raw).hexdigest()
            request='01234567-89ab-4cde-8fab-0123456789ab'; payload={'site_id':'MLM','facts_artifact_id':'1','keywords_artifact_id':'2'}
            claim={'id':9,'lease_token':'t','lease_epoch':1,'company_id':6,'snapshot_id':5,'task_type':'research.bestseller','payload':payload,'input_hash':'digest'}
            store.enqueue(request,'research.bestseller',payload,{'snapshot':{'case_id':4,'snapshot_id':5,'payload':{'source_image_artifact_ids':['1']}},'artifacts':{'1':{'case_id':4,'company_id':6,'data':base64.b64encode(raw).decode(),'digest':sha}}})
            data,queries=fixture(); bind_plan(store,request,claim,{'platform':'shein-us','queries':queries,'category':'dress','result_limit':15,'source_artifact_id':'1','viewed_source_sha256':sha})
            class C:
                research_ledger_enabled=True; fail=True
                def create_research_artifact(self,*args,**kwargs): self.raw=kwargs['data']; return {'artifact_id':9,'digest':hashlib.sha256(self.raw).hexdigest()}
                def artifact(self,_): return {'case_id':4,'company_id':6,'mime_type':'application/json','digest':hashlib.sha256(self.raw).hexdigest(),'data':base64.b64encode(self.raw).decode()}
                def seal_research_manifest(self,claim,r,intents):
                    if self.fail: raise OSError('seal reply lost')
                    return {'request_id':r,'intent_count':len(intents),'coverage':'unknown'}
            c=C(); result={'evidence':[{'source':'x'}],'candidates':[]}
            with patch('optimization_worker.collector_cli.convert_evidence',return_value=result):
                self.assertEqual(import_evidence(store,request,claim,data,'2026-11-16T00:00:00Z',c,lambda:None,tab_receipts=type('Verified',(),{'verified_queries':lambda *args:True})()),0)
            self.assertEqual(store.inspect(request)['state'],'candidate_images_pending')
            from optimization_worker.candidate_image_upload import finish
            with self.assertRaises(OSError): finish(store,request,claim,lambda:None,client=c)
            self.assertEqual(store.inspect(request)['state'],'candidate_images_pending')
            c.fail=False
            self.assertEqual(finish(store,request,claim,lambda:None,client=c),0)
            self.assertEqual(store.inspect(request)['state'],'resolved')
    def test_returned_receipt_survives_post_upload_fence_failure(self):
        import tempfile, base64, hashlib, io
        from PIL import Image
        from optimization_worker.host_session import SessionStore
        from optimization_worker.collector_cli import bind_plan, import_evidence, reconcile_evidence
        with tempfile.TemporaryDirectory() as d:
            store=SessionStore(Path(d)/'host.sqlite','identity'); b=io.BytesIO();Image.new('RGB',(2,2)).save(b,format='PNG');raw=b.getvalue();sha=hashlib.sha256(raw).hexdigest()
            payload={'site_id':'MLM','facts_artifact_id':'1','keywords_artifact_id':'2'};claim={'company_id':6,'snapshot_id':5,'task_type':'research.bestseller','payload':payload,'input_hash':'digest'}
            store.enqueue('01234567-89ab-4cde-8fab-0123456789ab','research.bestseller',payload,{'snapshot':{'case_id':4,'snapshot_id':5,'payload':{'source_image_artifact_ids':['1']}},'artifacts':{'1':{'case_id':4,'company_id':6,'data':base64.b64encode(raw).decode(),'digest':sha}}})
            data,queries=fixture();bind_plan(store,'01234567-89ab-4cde-8fab-0123456789ab',claim,{'platform':'shein-us','queries':queries,'category':'dress','result_limit':15,'source_artifact_id':'1','viewed_source_sha256':sha})
            class Client:
                calls=0
                def create_artifact(self,case_id,**kwargs):self.calls+=1;self.raw=kwargs['data'];return {'artifact_id':9,'digest':hashlib.sha256(self.raw).hexdigest()}
                def artifact(self,artifact_id):return {'case_id':4,'company_id':6,'mime_type':'application/json','digest':hashlib.sha256(self.raw).hexdigest(),'data':base64.b64encode(self.raw).decode()}
            client=Client();fence_count=[]
            from optimization_worker.client import APIError
            def failed_fence():raise APIError(503)
            with self.assertRaises(APIError):import_evidence(store,'01234567-89ab-4cde-8fab-0123456789ab',claim,data,'2026-11-16T00:00:00Z',client,failed_fence)
            self.assertEqual(store.inspect('01234567-89ab-4cde-8fab-0123456789ab')['state'],'prepared')
            self.assertEqual(client.calls,0)
            def fence():
                fence_count.append(1)
                if len(fence_count)==2:raise OSError('network down')
            with self.assertRaises(OSError):import_evidence(store,'01234567-89ab-4cde-8fab-0123456789ab',claim,data,'2026-11-16T00:00:00Z',client,fence,tab_receipts=type('Verified',(),{'verified_queries':lambda *args:True})())
            self.assertEqual(store.inspect('01234567-89ab-4cde-8fab-0123456789ab')['collector_pending']['artifact_id'],'9')
            self.assertEqual(reconcile_evidence(store,'01234567-89ab-4cde-8fab-0123456789ab',claim,'9',client),1)
            self.assertEqual(client.calls,1)

class CollectorIdMappingTest(unittest.TestCase):
    def test_canonical_uuid_mapping_is_injective_and_rejects_aliases(self):
        from optimization_worker.collector_cli import collector_id
        a='01234567-89ab-4cde-8fab-0123456789ab'
        b='01234567-89ab-4cde-8fab-0123456789ac'
        self.assertEqual(collector_id(a),'rec0123456789ab4cde8fab0123456789ab')
        self.assertNotEqual(collector_id(a),collector_id(b))
        for invalid in (a.upper(),a.replace('-',''),'{'+a+'}','req-1','rec0123456789ab4cde8fab0123456789ab'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): collector_id(invalid)

class DetailObservationTimeTest(unittest.TestCase):
    def test_detail_time_is_distinct_and_normalized_to_utc(self):
        from optimization_worker.collector_import import convert_evidence
        data,queries=fixture();data['details'][0]['observed_at']='2026-11-15T10:22:13+08:00'
        candidate=convert_evidence(data,data['task_record_id'],'shein-us',queries,'dress','2026-11-16T00:00:00Z')['candidates'][0]
        self.assertEqual(candidate['observed_at'],'2026-11-15T02:22:13+00:00')
        self.assertEqual(candidate['observation_time_scope'],'detail')
    def test_legacy_batch_time_is_explicitly_marked(self):
        from optimization_worker.collector_import import convert_evidence
        data,queries=fixture()
        candidate=convert_evidence(data,data['task_record_id'],'shein-us',queries,'dress','2026-11-16T00:00:00Z')['candidates'][0]
        self.assertEqual(candidate['observation_time_scope'],'batch')
    def test_invalid_naive_or_after_batch_timestamps_are_rejected(self):
        from optimization_worker.collector_import import convert_evidence
        for value in ('2026-11-15T01:00:00',None,'invalid','2026-11-16T00:00:01Z'):
            data,queries=fixture();data['details'][0]['observed_at']=value
            with self.subTest(value=value),self.assertRaises(ValueError):
                convert_evidence(data,data['task_record_id'],'shein-us',queries,'dress','2026-11-16T00:00:00Z')

    def test_scope_contract_rejects_misleading_values_and_accepts_legacy(self):
        from optimization_worker.collector_import import convert_evidence
        from optimization_worker.domain import validate_result, DomainError
        data,queries=fixture();result=convert_evidence(data,data['task_record_id'],'shein-us',queries,'dress','2026-11-16T00:00:00Z')
        del result['candidates'][0]['observation_time_scope']
        validate_result('research.bestseller',result)
        result['candidates'][0]['observation_time_scope']='precise'
        with self.assertRaises(DomainError): validate_result('research.bestseller',result)

    def test_multiple_details_keep_their_own_times(self):
        from optimization_worker.collector_import import convert_evidence
        data,queries=fixture()
        data['details'][0]['observed_at']='2026-11-15T01:00:00Z'
        data['details'][1].update(status='qualified',reason=None,reviews_display='500+',match_level='类似竞品',visual_features=['sleeveless'],observed_at='2026-11-15T03:00:00Z')
        candidates=convert_evidence(data,data['task_record_id'],'shein-us',queries,'dress','2026-11-16T00:00:00Z')['candidates']
        self.assertEqual([c['observed_at'] for c in candidates],['2026-11-15T01:00:00+00:00','2026-11-15T03:00:00+00:00'])
        self.assertEqual([c['observation_time_scope'] for c in candidates],['detail','detail'])

    def test_rejected_detail_time_is_also_validated(self):
        from optimization_worker.collector_import import convert_evidence
        data,queries=fixture();data['details'][1]['observed_at']='2026-11-15T01:00:00'
        with self.assertRaises(ValueError):convert_evidence(data,data['task_record_id'],'shein-us',queries,'dress','2026-11-16T00:00:00Z')

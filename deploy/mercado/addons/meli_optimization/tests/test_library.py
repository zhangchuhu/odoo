"""Disposable, explicitly synthetic archived image fixtures; no external traffic."""
import base64
import hashlib
import uuid
from unittest.mock import patch
import odoo
from psycopg2.errors import DeadlockDetected, SerializationFailure, UniqueViolation
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import AccessError, ValidationError
from ..domain import canonical_hash


@tagged('post_install', '-at_install')
class LibraryCase(TransactionCase):
    def setUp(self):
        super().setUp()
        self.ui = self.env['meli.optimization.case']

    def collection(self):
        return self.ui.workbench_collection_create({'name': 'Test only dress research',
            'source_url': 'https://example.test/source', 'garment': 'test dress', 'site_id': 'MLM'})

    def create_started_collection(self, ui=None):
        if ui is None:
            ui = self.ui
        detail = ui.workbench_collection_create({
            'garment': 'test dress', 'source_sku': 'TEST-SKU', 'platform': 'mercado-libre-mx',
            'image_data': 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC',
            'image_mime': 'image/png', 'min_sold': 0, 'min_reviews': 0, 'min_rating': 0, 'result_limit': 3,
        })
        self.start_collection(detail, ui)
        return detail

    def start_collection(self, detail, ui=None):
        if ui is None:
            ui = self.ui
        tasks = ui.env['meli.optimization.task']
        self.assertFalse(tasks.search([('case_id', '=', detail['id'])]))
        started = ui.workbench_collection_start(detail['id'], detail['snapshot']['id'], detail['revision'])
        self.assertEqual((started['stage'], started['state']), ('product.facts', 'queued'))

    def test_collection_config_is_bound_to_snapshot(self):
        image = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        config = {'platform':'shein-us', 'min_sold':500, 'min_reviews':50, 'min_rating':4.1, 'result_limit':15}
        detail = self.ui.workbench_collection_create({'garment':'test dress', 'source_sku':'MLM123', 'image_data':image, 'image_mime':'image/png', **config})
        self.start_collection(detail)
        snapshot = detail['snapshot']['payload']
        self.assertEqual(snapshot['collection_config'], config)
        self.assertEqual(snapshot['source_sku'], 'MLM123')
        self.assertEqual(len(snapshot['source_image_artifact_ids']), 1)
        tasks = self.env['meli.optimization.task'].search([('case_id','=',detail['id'])])
        self.assertEqual(tasks.mapped('task_type'), ['product.facts'])
        self.assertEqual(tasks.snapshot_id.id, detail['snapshot']['id'])
        row = next(x for x in self.ui.workbench_collections() if x['id'] == detail['id'])
        self.assertEqual(row['company_id'], self.env.company.id)
        self.assertEqual(row['company_name'], self.env.company.name)

    def test_collection_config_requires_valid_source_and_thresholds(self):
        image = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        values = {'garment':'dress', 'source_sku':'SKU', 'image_data':image, 'image_mime':'image/png', 'platform':'mercado-libre-mx', 'min_sold':500, 'min_reviews':50, 'min_rating':4.1, 'result_limit':15}
        for bad in ({'source_sku':''}, {'image_data':''}, {'platform':'other'}, {'min_sold':-1}, {'min_reviews':True}, {'min_rating':float('nan')}, {'min_rating':5.1}, {'result_limit':0}, {'min_sold':'500'}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.ui.workbench_collection_create(dict(values, **bad))

    def test_collection_accepts_platform_links_and_large_limit(self):
        image = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        values = {'garment':'dress', 'source_sku':'SKU', 'image_data':image, 'image_mime':'image/png', 'platform':'[SHEIN](https://us.shein.com/item)', 'min_sold':0, 'min_reviews':0, 'min_rating':0, 'result_limit':1000001}
        detail = self.ui.workbench_collection_create(values)
        self.assertEqual(detail['snapshot']['payload']['collection_config']['platform'], 'shein-us')
        self.assertEqual(detail['snapshot']['payload']['collection_config']['result_limit'],1000001)
        with self.assertRaises(ValidationError):
            self.ui.workbench_collection_create(dict(values,platform='https://shein.com.example.test/'))

    def test_collection_auto_research_is_idempotent_and_waits_for_empty_keywords(self):
        image = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        detail=self.ui.workbench_collection_create({'garment':'dress','source_sku':'SKU','image_data':image,'image_mime':'image/png','platform':'mercado-libre-mx','min_sold':0,'min_reviews':0,'min_rating':0,'result_limit':3})
        self.start_collection(detail)
        case=self.ui.browse(detail['id']); tasks=self.env['meli.optimization.task']; facts=tasks.search([('case_id','=',case.id),('task_type','=','product.facts')])
        evidence=[{'id':'e','source':'snapshot','observed_at':'2026-11-15T00:00:00Z'}]
        claim=facts.claim(['product.facts']); result={'schema_version':'1','evidence':evidence,'facts':[{'name':'garment','value':'dress','evidence_ids':['e']}],'uncertainties':[]}
        facts.complete(claim['lease_token'],claim['lease_epoch'],'facts',result)
        keywords=tasks.search([('case_id','=',case.id),('task_type','=','research.keywords')]); self.assertEqual(len(keywords),1)
        claim=keywords.claim(['research.keywords']); result={'schema_version':'1','evidence':evidence,'status':'complete','keywords':[{'text':'vestido','evidence_ids':['e']}]}
        keywords.complete(claim['lease_token'],claim['lease_epoch'],'keywords',result)
        research=tasks.search([('case_id','=',case.id),('task_type','=','research.bestseller')]); self.assertEqual(len(research),1)
        self.assertEqual(research.snapshot_id.id,detail['snapshot']['id'])
        self.assertEqual(research.payload['site_id'],'MLM')
        keywords._attempt_derived(); self.assertEqual(len(tasks.search([('case_id','=',case.id),('task_type','=','research.bestseller')])),1)

    def test_collection_empty_keywords_does_not_create_research(self):
        image = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        d=self.ui.workbench_collection_create({'garment':'dress','source_sku':'SKU','image_data':image,'image_mime':'image/png','platform':'mercado-libre-mx','min_sold':0,'min_reviews':0,'min_rating':0,'result_limit':3})
        self.start_collection(d)
        tasks=self.env['meli.optimization.task']; facts=tasks.search([('case_id','=',d['id']),('task_type','=','product.facts')]); evidence=[{'id':'e','source':'snapshot','observed_at':'2026-11-15T00:00:00Z'}]
        claim=facts.claim(['product.facts']); facts.complete(claim['lease_token'],claim['lease_epoch'],'facts',{'schema_version':'1','evidence':evidence,'facts':[{'name':'garment','value':'dress','evidence_ids':['e']}],'uncertainties':[]})
        keywords=tasks.search([('case_id','=',d['id']),('task_type','=','research.keywords')]); claim=keywords.claim(['research.keywords'])
        keywords.complete(claim['lease_token'],claim['lease_epoch'],'keywords',{'schema_version':'1','evidence':evidence,'status':'empty','keywords':[],'reason':'no evidence'})
        self.assertFalse(tasks.search([('case_id','=',d['id']),('task_type','=','research.bestseller')]))

    def test_research_candidates_import_verified_images_and_idempotency(self):
        image='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        d=self.ui.workbench_collection_create({'garment':'dress','source_sku':'SKU','image_data':image,'image_mime':'image/png','platform':'mercado-libre-mx','min_sold':2,'min_reviews':1,'min_rating':4,'result_limit':3})
        self.start_collection(d)
        tasks=self.env['meli.optimization.task']; evidence=[{'id':'e','source':'snapshot','observed_at':'2026-11-15T00:00:00Z'}]
        facts=tasks.search([('case_id','=',d['id']),('task_type','=','product.facts')]); c=facts.claim(['product.facts'])
        facts.complete(c['lease_token'],c['lease_epoch'],'facts',{'schema_version':'1','evidence':evidence,'facts':[{'name':'garment','value':'dress','evidence_ids':['e']}],'uncertainties':[]})
        keywords=tasks.search([('case_id','=',d['id']),('task_type','=','research.keywords')]); c=keywords.claim(['research.keywords'])
        keywords.complete(c['lease_token'],c['lease_epoch'],'keywords',{'schema_version':'1','evidence':evidence,'status':'complete','keywords':[{'text':'dress','evidence_ids':['e']}]})
        task=tasks.search([('case_id','=',d['id']),('task_type','=','research.bestseller')]); aid=self.ui.workbench_upload_garment(d['id'],image,'image/png'); art=self.env['meli.optimization.artifact'].browse(aid)
        candidate={'platform':'mercado-libre-mx','item_id':'MLM1','url':'https://mercadolibre.com.mx/item','raw_url':'https://mercadolibre.com.mx/item','query':'dress','selection_reason':'similar','observed_at':'2026-11-15T00:00:00Z','sold_lower_bound':2,'sold_upper_bound':None,'review_count':None,'position':1,'sold_display':'2','image_url':'https://example.test/image.png','visual_category':'dress','is_ad':False,'rating':None,'evidence_ids':['e']}
        img={'artifact_id':str(aid),'image_id':'candidate','order':0,'role':'reference','source':'https://example.test/image.png','mime_type':'image/png','sha256':art.digest,'width':1,'height':1,'evidence_ids':['e']}
        candidate['gallery_image_urls']=['https://example.test/image.png','https://example.test/second.png']
        result={'schema_version':'1','evidence':evidence,'status':'complete','candidates':[candidate]}; c=task.claim(['research.bestseller']); task.complete(c['lease_token'],c['lease_epoch'],'research',result)
        self.assertFalse(self.env['meli.optimization.library'].search([('task_id','=',task.id)]))
        self.assertEqual(task.status_detail['code'],'research_images_pending')
        key='mercado-libre-mx:MLM1'
        bad=dict(img,source='http://example.test/image.png')
        self.assertEqual(self.ui.workbench_library_research_import(d['id'],task.id,{key:bad})['pending'],[key])
        bad=dict(img,source='https://example.test/unrelated.png')
        self.assertEqual(self.ui.workbench_library_research_import(d['id'],task.id,{key:bad})['pending'],[key])
        self.assertFalse(self.env['meli.optimization.library'].search([('task_id','=',task.id)]))
        # Historical collector assigned the product id to every image. Import
        # must preserve the original evidence while assigning unique gallery ids.
        legacy_first=dict(img,image_id='MLM1')
        second=dict(legacy_first,order=1,source='https://example.test/second.png')
        self.ui.workbench_library_research_import(d['id'],task.id,{key:[legacy_first,second]})
        self.ui.workbench_library_research_import(d['id'],task.id,{key:[legacy_first,second]})
        libs=self.env['meli.optimization.library'].search([('task_id','=',task.id)]); self.assertEqual(len(libs),1)
        self.assertEqual([i['source'] for i in libs.payload['images']],candidate['gallery_image_urls'])
        self.assertEqual(len({i['image_id'] for i in libs.payload['images']}), 2)
        first=self.ui.workbench_library_recollect(libs.id)
        again=self.ui.workbench_library_recollect(libs.id)
        self.assertEqual(first['task_id'],again['task_id'])
        self.assertFalse(first['reused']); self.assertTrue(again['reused'])
        followup=tasks.browse(first['task_id'])
        self.assertEqual(followup.payload['selected_variant']['product_url'],candidate['url'])
        self.assertEqual(task.result['candidates'][0],candidate)
        claim=followup.claim(['gallery.collect'])
        gallery={'schema_version':'1','evidence':evidence,'reference_item_id':candidate['item_id'],
                 'selected_variant':followup.payload['selected_variant'],'observed_at':candidate['observed_at'],
                 'gallery_manifest_hash':'a'*64,'completeness_status':'complete','images':[img,second]}
        followup.complete(claim['lease_token'],claim['lease_epoch'],'gallery-version',gallery)
        visible=self.ui.workbench_library()['rows']
        versions=[r for r in visible if r['payload'].get('selected_variant',{}).get('library_id')==str(libs.id)]
        self.assertEqual(len(versions),1)
        self.assertNotIn(libs.id,[r['id'] for r in visible])
        self.assertEqual(len(versions[0]['payload']['images']),2)
        self.assertTrue(libs.exists())

    def test_missing_sales_cannot_pass_zero_threshold(self):
        image='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        d=self.ui.workbench_collection_create({'garment':'dress','source_sku':'SKU','image_data':image,'image_mime':'image/png','platform':'mercado-libre-mx','min_sold':0,'min_reviews':1,'min_rating':4,'result_limit':3})
        self.start_collection(d)
        tasks=self.env['meli.optimization.task']; evidence=[{'id':'e','source':'snapshot','observed_at':'2026-11-15T00:00:00Z'}]
        facts=tasks.search([('case_id','=',d['id']),('task_type','=','product.facts')]); c=facts.claim(['product.facts'])
        facts.complete(c['lease_token'],c['lease_epoch'],'facts',{'schema_version':'1','evidence':evidence,'facts':[{'name':'garment','value':'dress','evidence_ids':['e']}],'uncertainties':[]})
        keywords=tasks.search([('case_id','=',d['id']),('task_type','=','research.keywords')]); c=keywords.claim(['research.keywords'])
        keywords.complete(c['lease_token'],c['lease_epoch'],'keywords',{'schema_version':'1','evidence':evidence,'status':'complete','keywords':[{'text':'dress','evidence_ids':['e']}]})
        task=tasks.search([('case_id','=',d['id']),('task_type','=','research.bestseller')]); aid=self.ui.workbench_upload_garment(d['id'],image,'image/png'); art=self.env['meli.optimization.artifact'].browse(aid)
        candidate={'platform':'mercado-libre-mx','item_id':'MLM1','url':'https://mercadolibre.com.mx/item','raw_url':'https://mercadolibre.com.mx/item','query':'dress','selection_reason':'similar','observed_at':'2026-11-15T00:00:00Z','sold_lower_bound':None,'sold_upper_bound':None,'review_count':None,'position':1,'sold_display':'2','image_url':'https://example.test/image.png','visual_category':'dress','is_ad':False,'rating':None,'evidence_ids':['e']}
        img={'artifact_id':str(aid),'image_id':'candidate','order':0,'role':'reference','source':'https://example.test/image.png','mime_type':'image/png','sha256':art.digest,'width':1,'height':1,'evidence_ids':['e']}
        result={'schema_version':'1','evidence':evidence,'status':'complete','candidates':[candidate]}; c=task.claim(['research.bestseller']); task.complete(c['lease_token'],c['lease_epoch'],'research',result)
        key='mercado-libre-mx:MLM1'
        self.assertEqual(self.ui.workbench_library_research_import(d['id'],task.id,{key:img})['pending'],[key])
        self.assertFalse(self.env['meli.optimization.library'].search([('task_id','=',task.id)]))

    def test_shein_import_accepts_review_lower_bound(self):
        image='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        d=self.ui.workbench_collection_create({'garment':'dress','source_sku':'SKU','image_data':image,'image_mime':'image/png','platform':'shein-us','min_sold':2,'min_reviews':50,'min_rating':4,'result_limit':3})
        self.start_collection(d)
        tasks=self.env['meli.optimization.task']; evidence=[{'id':'e','source':'snapshot','observed_at':'2026-11-15T00:00:00Z'}]
        facts=tasks.search([('case_id','=',d['id']),('task_type','=','product.facts')]); c=facts.claim(['product.facts'])
        facts.complete(c['lease_token'],c['lease_epoch'],'facts',{'schema_version':'1','evidence':evidence,'facts':[{'name':'garment','value':'dress','evidence_ids':['e']}],'uncertainties':[]})
        keywords=tasks.search([('case_id','=',d['id']),('task_type','=','research.keywords')]); c=keywords.claim(['research.keywords'])
        keywords.complete(c['lease_token'],c['lease_epoch'],'keywords',{'schema_version':'1','evidence':evidence,'status':'complete','keywords':[{'text':'dress','evidence_ids':['e']}]})
        task=tasks.search([('case_id','=',d['id']),('task_type','=','research.bestseller')]); aid=self.ui.workbench_upload_garment(d['id'],image,'image/png'); art=self.env['meli.optimization.artifact'].browse(aid)
        candidate={'platform':'shein-us','item_id':'MLM1','url':'https://mercadolibre.com.mx/item','raw_url':'https://mercadolibre.com.mx/item','query':'dress','selection_reason':'similar','observed_at':'2026-11-15T00:00:00Z','sold_lower_bound':None,'sold_upper_bound':None,'review_count':None,'reviews_display':'50+','position':1,'sold_display':'2','image_url':'https://example.test/image.png','visual_category':'dress','is_ad':False,'rating':4.5,'evidence_ids':['e']}
        img={'artifact_id':str(aid),'image_id':'candidate','order':0,'role':'reference','source':'https://example.test/image.png','mime_type':'image/png','sha256':art.digest,'width':1,'height':1,'evidence_ids':['e']}
        result={'schema_version':'1','evidence':evidence,'status':'complete','candidates':[candidate]}; c=task.claim(['research.bestseller']); task.complete(c['lease_token'],c['lease_epoch'],'research',result)
        self.assertFalse(self.env['meli.optimization.library'].search([('task_id','=',task.id)]))
        self.assertEqual(task.status_detail['code'],'research_images_pending')
        key='shein-us:MLM1'
        bad=dict(img,sha256='0'*64)
        self.assertEqual(self.ui.workbench_library_research_import(d['id'],task.id,{key:bad})['pending'],[key])
        self.assertFalse(self.env['meli.optimization.library'].search([('task_id','=',task.id)]))
        self.ui.workbench_library_research_import(d['id'],task.id,{key:img})
        self.ui.workbench_library_research_import(d['id'],task.id,{key:img})
        libs=self.env['meli.optimization.library'].search([('task_id','=',task.id)]); self.assertEqual(len(libs),1)
        self.assertEqual(libs.payload['images'][0]['sha256'],art.digest)

    def test_missing_shein_metrics_cannot_pass_zero_threshold(self):
        image='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        d=self.ui.workbench_collection_create({'garment':'dress','source_sku':'SKU','image_data':image,'image_mime':'image/png','platform':'shein-us','min_sold':2,'min_reviews':0,'min_rating':0,'result_limit':3})
        self.start_collection(d)
        tasks=self.env['meli.optimization.task']; evidence=[{'id':'e','source':'snapshot','observed_at':'2026-11-15T00:00:00Z'}]
        facts=tasks.search([('case_id','=',d['id']),('task_type','=','product.facts')]); c=facts.claim(['product.facts'])
        facts.complete(c['lease_token'],c['lease_epoch'],'facts',{'schema_version':'1','evidence':evidence,'facts':[{'name':'garment','value':'dress','evidence_ids':['e']}],'uncertainties':[]})
        keywords=tasks.search([('case_id','=',d['id']),('task_type','=','research.keywords')]); c=keywords.claim(['research.keywords'])
        keywords.complete(c['lease_token'],c['lease_epoch'],'keywords',{'schema_version':'1','evidence':evidence,'status':'complete','keywords':[{'text':'dress','evidence_ids':['e']}]})
        task=tasks.search([('case_id','=',d['id']),('task_type','=','research.bestseller')]); aid=self.ui.workbench_upload_garment(d['id'],image,'image/png'); art=self.env['meli.optimization.artifact'].browse(aid)
        candidate={'platform':'shein-us','item_id':'MLM1','url':'https://mercadolibre.com.mx/item','raw_url':'https://mercadolibre.com.mx/item','query':'dress','selection_reason':'similar','observed_at':'2026-11-15T00:00:00Z','sold_lower_bound':None,'sold_upper_bound':None,'review_count':None,'reviews_display':None,'position':1,'sold_display':'2','image_url':'https://example.test/image.png','visual_category':'dress','is_ad':False,'rating':None,'evidence_ids':['e']}
        img={'artifact_id':str(aid),'image_id':'candidate','order':0,'role':'reference','source':'https://example.test/image.png','mime_type':'image/png','sha256':art.digest,'width':1,'height':1,'evidence_ids':['e']}
        result={'schema_version':'1','evidence':evidence,'status':'complete','candidates':[candidate]}; c=task.claim(['research.bestseller']); task.complete(c['lease_token'],c['lease_epoch'],'research',result)
        key='shein-us:MLM1'
        self.assertEqual(self.ui.workbench_library_research_import(d['id'],task.id,{key:img})['pending'],[key])
        self.assertFalse(self.env['meli.optimization.library'].search([('task_id','=',task.id)]))

    def test_collection_archive_hides_only_its_library(self):
        old=self.create_started_collection(); case=self.ui.browse(old['id']); task=self.env['meli.optimization.task'].search([('case_id','=',case.id)],limit=1); task.cancel()
        library=self.env['meli.optimization.library']._append({'company_id':case.company_id.id,'case_id':case.id,'snapshot_id':old['snapshot']['id'],'name':'fixture','site_id':'MLM','result_hash':'fixture','payload':{'images':[]}})
        self.ui.workbench_collection_archive(case.id,True,case.revision)
        self.assertTrue(library.collection_archived)
        self.assertNotIn(library.id,[r['id'] for r in self.ui.workbench_library()['rows']])
        self.ui.workbench_collection_archive(case.id,False,case.revision)
        self.assertIn(library.id,[r['id'] for r in self.ui.workbench_library()['rows']])

    def test_hide_collection_list_preserves_library(self):
        old=self.create_started_collection(); case=self.ui.browse(old['id'])
        task=self.env['meli.optimization.task'].search([('case_id','=',case.id)],limit=1); task.cancel()
        library=self.env['meli.optimization.library']._append({'company_id':case.company_id.id,'case_id':case.id,'snapshot_id':old['snapshot']['id'],'name':'fixture','site_id':'MLM','result_hash':'hidden-fixture','payload':{'images':[]}})
        self.ui.workbench_collection_hide_from_list(case.id,case.revision)
        self.assertNotIn(case.id,[r['id'] for r in self.ui.workbench_collections()])
        self.assertFalse(case.collection_archived)
        self.assertIn(library.id,[r['id'] for r in self.ui.workbench_library()['rows']])

    def test_replace_does_not_archive_other_company(self):
        other=self.env['res.company'].create({'name':'Synthetic other company'})
        other_ui=self.ui.with_company(other).with_context(allowed_company_ids=[other.id])
        detail=self.create_started_collection(other_ui)
        other_task=self.env['meli.optimization.task'].sudo().search([('case_id','=',detail['id'])],limit=1)
        self.assertEqual(other_task.state,'queued')
        image='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        row={'record':'1','status':'done','garment':'dress','source_sku':'SKU','image_data':image,'image_mime':'image/png','platform':'mercado-libre-mx','min_sold':0,'min_reviews':0,'min_rating':0,'result_limit':1}
        self.ui.workbench_collection_replace_from_records('other-company-test','base','table',[row])
        self.assertFalse(self.ui.sudo().browse(detail['id']).collection_archived)
        self.assertEqual(other_task.state,'queued')

    def test_collection_ir_defaults_cannot_select_foreign_batch(self):
        other=self.env['res.company'].create({'name':'synthetic default batch ' + uuid.uuid4().hex})
        batch=self.env['meli.optimization.batch'].with_company(other).create({'name':'other'})
        defaults=self.env['ir.default'].sudo()
        defaults.set('meli.optimization.case','collection',True)
        defaults.set('meli.optimization.case','batch_id',batch.id)
        with self.assertRaises(AccessError): self.ui.create({'name':'foreign defaults'})

    def test_collection_ir_defaults_cannot_set_archive_flag(self):
        defaults=self.env['ir.default'].sudo(); defaults.set('meli.optimization.case','collection_archived',True)
        with self.assertRaises(AccessError): self.collection()

    def test_collection_create_rejects_other_company_batch(self):
        other=self.env['res.company'].create({'name':'synthetic batch company'})
        batch=self.env['meli.optimization.batch'].with_company(other).create({'name':'other'})
        with self.assertRaises(AccessError): self.ui.create({'name':'foreign','batch_id':batch.id,'collection':True})

    def test_derived_serialization_errors_are_not_swallowed(self):
        d=self.create_started_collection(); task=self.env['meli.optimization.task'].search([('case_id','=',d['id'])],limit=1)
        for error in (SerializationFailure, DeadlockDetected):
            with patch.object(type(task),'_run_derived',side_effect=error('retry whole transaction')):
                with self.assertRaises(error): task._attempt_derived()

    def _cancel_race_fixtures(self, company_id):
        with self.env.registry.cursor() as cleanup:
            env=odoo.api.Environment(cleanup,odoo.SUPERUSER_ID,{'allowed_company_ids':[company_id]})
            for task in env['meli.optimization.task'].search([('company_id','=',company_id),('state','=','queued')]):
                task.cancel()
            cleanup.commit()

    def test_stale_snapshot_archive_conflicts_with_concurrent_task_create(self):
        with self.env.registry.cursor() as setup:
            env=odoo.api.Environment(setup,odoo.SUPERUSER_ID,{})
            company=env['res.company'].create({'name':'synthetic task race ' + uuid.uuid4().hex});company_id=company.id
            env=odoo.api.Environment(setup,odoo.SUPERUSER_ID,{'allowed_company_ids':[company_id]})
            d=self.create_started_collection(env['meli.optimization.case'])
            task=env['meli.optimization.task'].search([('case_id','=',d['id'])],limit=1);task.cancel(); cid=d['id']; setup.commit()
        with self.env.registry.cursor() as stale, self.env.registry.cursor() as fresh:
            old=odoo.api.Environment(stale,odoo.SUPERUSER_ID,{'allowed_company_ids':[company_id]})
            new=odoo.api.Environment(fresh,odoo.SUPERUSER_ID,{'allowed_company_ids':[company_id]})
            old_case=old['meli.optimization.case'].browse(cid); rev=old_case.revision
            new['meli.optimization.case'].workbench_task(cid,'product.facts',{}); fresh.commit()
            with self.assertRaises(SerializationFailure): old['meli.optimization.case'].workbench_collection_archive(cid,True,rev)
            stale.rollback()
        self._cancel_race_fixtures(company_id)

    def test_stale_replace_conflicts_with_concurrent_replace(self):
        with self.env.registry.cursor() as setup:
            env=odoo.api.Environment(setup,odoo.SUPERUSER_ID,{})
            company=env['res.company'].create({'name':'synthetic replace race ' + uuid.uuid4().hex}); company_id=company.id;setup.commit()
        image='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        row={'record':'1','status':'done','garment':'dress','source_sku':'SKU','image_data':image,'image_mime':'image/png','platform':'mercado-libre-mx','min_sold':0,'min_reviews':0,'min_rating':0,'result_limit':1}
        with self.env.registry.cursor() as stale, self.env.registry.cursor() as fresh:
            old=odoo.api.Environment(stale,odoo.SUPERUSER_ID,{'allowed_company_ids':[company_id]})
            new=odoo.api.Environment(fresh,odoo.SUPERUSER_ID,{'allowed_company_ids':[company_id]})
            old['meli.optimization.case'].search_count([('collection','=',True)])
            ids=new['meli.optimization.case'].workbench_collection_replace_from_records('first','base','table',[row]);fresh.commit()
            with self.assertRaises(SerializationFailure): old['meli.optimization.case'].workbench_collection_replace_from_records('second','base','table',[row])
            stale.rollback()
            self.assertEqual(len(ids),1)
        self._cancel_race_fixtures(company_id)

    def test_stale_company_snapshot_conflicts_with_new_collection(self):
        with self.env.registry.cursor() as setup:
            env=odoo.api.Environment(setup,odoo.SUPERUSER_ID,{})
            company=env['res.company'].create({'name':'synthetic creation race ' + uuid.uuid4().hex});company_id=company.id;setup.commit()
        with self.env.registry.cursor() as stale, self.env.registry.cursor() as fresh:
            old=odoo.api.Environment(stale,odoo.SUPERUSER_ID,{'allowed_company_ids':[company_id]})
            new=odoo.api.Environment(fresh,odoo.SUPERUSER_ID,{'allowed_company_ids':[company_id]})
            old['meli.optimization.case'].search_count([('collection','=',True)])
            new['meli.optimization.case'].workbench_collection_create({'garment':'synthetic race'}); fresh.commit()
            image='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
            row={'record':'1','status':'done','garment':'dress','source_sku':'SKU','image_data':image,'image_mime':'image/png','platform':'mercado-libre-mx','min_sold':0,'min_reviews':0,'min_rating':0,'result_limit':1}
            with self.assertRaises(SerializationFailure): old['meli.optimization.case'].workbench_collection_replace_from_records('race','base','table',[row])
            stale.rollback()
        self._cancel_race_fixtures(company_id)

    def test_collection_company_lock_blocks_competing_transaction(self):
        with self.env.registry.cursor() as first, self.env.registry.cursor() as second:
            first.execute('SELECT pg_advisory_xact_lock(hashtext(%s), %s)', ('collection-company',self.env.company.id))
            second.execute('SELECT pg_try_advisory_xact_lock(hashtext(%s), %s)', ('collection-company',self.env.company.id))
            self.assertFalse(second.fetchone()[0])
            second.rollback(); first.rollback()

    def test_archive_rejects_unknown_external_request(self):
        d=self.create_started_collection(); case=self.ui.browse(d['id']); task=self.env['meli.optimization.task'].search([('case_id','=',case.id)],limit=1);task.cancel()
        self.env['meli.optimization.external.request']._append({'task_id':task.id,'company_id':case.company_id.id,'request_id':'synthetic-pending','state':'unknown'})
        with self.assertRaises(ValidationError): self.ui.workbench_collection_archive(case.id,True,case.revision)
        self.assertFalse(case.collection_archived)

    def test_replace_rejects_activity_without_side_effects(self):
        old=self.create_started_collection(); image='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        row={'record':'1','status':'done','garment':'dress','source_sku':'SKU','image_data':image,'image_mime':'image/png','platform':'mercado-libre-mx','min_sold':0,'min_reviews':0,'min_rating':0,'result_limit':1}
        with self.assertRaises(ValidationError): self.ui.workbench_collection_replace_from_records('busy','base','table',[row])
        self.assertFalse(self.ui.browse(old['id']).collection_archived)
        self.assertEqual(self.env['meli.optimization.task'].search_count([('case_id','=',old['id']),('state','=','queued')]),1)

    def test_replace_failure_rolls_back_archive(self):
        old=self.create_started_collection(); task=self.env['meli.optimization.task'].search([('case_id','=',old['id'])],limit=1); task.cancel()
        image='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        row={'record':'1','status':'done','garment':'','source_sku':'SKU','image_data':image,'image_mime':'image/png','platform':'mercado-libre-mx','min_sold':0,'min_reviews':0,'min_rating':0,'result_limit':1}
        with self.assertRaises(ValidationError): self.ui.workbench_collection_replace_from_records('bad','base','table',[row])
        case=self.ui.browse(old['id']);case.invalidate_recordset();self.assertFalse(case.collection_archived)

    def test_replace_synthetic_large_images_roundtrip(self):
        import io
        from PIL import Image
        rows=[]
        for i, fmt in enumerate(('WEBP','JPEG','PNG')*4):
            out=io.BytesIO()
            if fmt == 'WEBP':
                image_data='UklGRiQAAABXRUJQVlA4IBgAAAAwAQCdASoCAAIAAUAmJaQAA3AA/v0gUAA='
            else:
                Image.new('RGB',(800,1200),(i*10,0,80)).save(out,format=fmt)
                image_data=base64.b64encode(out.getvalue()).decode()
            rows.append({'record':str(i),'status':'done','garment':'dress','source_sku':'SKU','image_data':image_data,'image_mime':{'WEBP':'image/webp','JPEG':'image/jpeg','PNG':'image/png'}[fmt],'platform':'mercado-libre-mx','min_sold':0,'min_reviews':0,'min_rating':0,'result_limit':5})
        ids=self.ui.workbench_collection_replace_from_records('synthetic-large','base','table',rows)
        for cid,row in zip(ids,rows):
            snap=self.ui.workbench_detail(cid)['snapshot']['payload'];artifact=self.env['meli.optimization.artifact'].browse(int(snap['source_image_artifact_ids'][0]))
            self.assertEqual(hashlib.sha256(base64.b64decode(artifact.data)).hexdigest(),hashlib.sha256(base64.b64decode(row['image_data'])).hexdigest())

    def test_replace_import_is_atomic_and_idempotent(self):
        old=self.create_started_collection(); old_case=self.ui.browse(old['id']); old_task=self.env['meli.optimization.task'].search([('case_id','=',old_case.id)],limit=1); old_task.cancel()
        image='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        records=[{'record':str(i),'status':'已完成','garment':'dress','source_sku':'SKU','image_data':image,'image_mime':'image/png','platform':'mercado-libre-mx','min_sold':0,'min_reviews':0,'min_rating':0,'result_limit':5} for i in range(12)]
        ids=self.ui.workbench_collection_replace_from_records('batch-1','base','table',records)
        self.assertEqual(len(ids),12);self.assertTrue(old_case.collection_archived)
        self.assertEqual(ids,self.ui.workbench_collection_replace_from_records('batch-1','base','table',records))
        tasks = self.env['meli.optimization.task']
        self.assertFalse(tasks.search([('case_id', 'in', ids)]))
        for case_id in ids:
            self.start_collection(self.ui.workbench_detail(case_id))
        self.assertEqual(self.env['meli.optimization.task'].search_count([('case_id','in',ids),('task_type','=','product.facts')]),12)
        self.assertEqual(self.ui.workbench_detail(ids[0])['snapshot']['payload']['external_source']['status'],'已完成')
        with self.assertRaises(ValidationError): self.ui.workbench_collection_replace_from_records('batch-1','base','table',records[:-1])

    def test_archive_collection_requires_idle_and_is_reversible(self):
        detail=self.create_started_collection(); case=self.ui.browse(detail['id'])
        task=self.env['meli.optimization.task'].search([('case_id','=',case.id)], limit=1)
        with self.assertRaises(ValidationError): self.ui.workbench_collection_archive(case.id,True,case.revision)
        task.cancel(); self.ui.workbench_collection_archive(case.id,True,case.revision)
        self.assertNotIn(case.id,[r['id'] for r in self.ui.workbench_collections()])
        self.assertTrue(self.ui.workbench_detail(case.id)['collection'])
        with self.assertRaises(ValidationError): self.ui.workbench_task(case.id,'product.facts',{})
        self.ui.workbench_collection_archive(case.id,False,case.revision)
        self.assertIn(case.id,[r['id'] for r in self.ui.workbench_collections()])

    def test_upload_source_image_creates_new_snapshot_without_losing_fields(self):
        detail = self.ui.workbench_create({'shop':'Test', 'item_id':'SRC-IMG', 'title':'Dress', 'site_id':'MLM'})
        image = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        before = dict(detail['snapshot']['payload'])
        proposal = self.env['meli.optimization.proposal'].create({'case_id':detail['id'], 'payload':{'action':'title'}})
        confirmation = proposal.confirm(proposal.digest)
        updated = self.ui.workbench_snapshot_source_image(detail['id'], image, 'image/png', detail['revision'])
        self.assertNotEqual(updated['snapshot']['id'], detail['snapshot']['id'])
        payload = dict(updated['snapshot']['payload'])
        refs = payload.pop('source_image_artifact_ids')
        self.assertEqual(payload, before)
        artifact = self.env['meli.optimization.artifact'].browse(int(refs[0]))
        self.assertEqual(artifact.case_id.id, detail['id'])
        self.assertEqual(artifact.digest, hashlib.sha256(base64.b64decode(image)).hexdigest())
        self.assertFalse(confirmation.is_valid())
        with self.assertRaises(ValidationError):
            self.ui.workbench_snapshot_source_image(detail['id'], image, 'image/png', detail['revision'])
        with self.assertRaises(ValidationError):
            self.ui.workbench_snapshot_source_image(detail['id'], image, 'text/plain', updated['revision'])
        other = self.ui.workbench_create({'shop':'Test', 'item_id':'OTHER-SRC', 'title':'Other', 'site_id':'MLM'})
        foreign = self.ui.workbench_upload_garment(other['id'], image, 'image/png')
        corrupt_payload = dict(updated['snapshot']['payload'], source_image_artifact_ids=[str(foreign)])
        with self.assertRaises(ValidationError):
            self.env['meli.optimization.snapshot'].create({'case_id':detail['id'], 'payload':corrupt_payload})
        current = self.ui.workbench_detail(detail['id'])
        count = self.env['meli.optimization.artifact'].search_count([('case_id', '=', detail['id'])])
        self.assertEqual(self.env['meli.optimization.artifact'].search_count([('case_id', '=', detail['id'])]), count)
        self.assertEqual(self.ui.workbench_detail(detail['id'])['snapshot']['id'], current['snapshot']['id'])

    def test_concurrent_qc_creation_fences_stale_image_confirmation(self):
        registry = self.env.registry
        with registry.cursor() as setup:
            env = odoo.api.Environment(setup, odoo.SUPERUSER_ID, {})
            d = env['meli.optimization.case'].workbench_create({'shop':'test','item_id':'concurrent-qc','title':'test'})
            p = env['meli.optimization.proposal'].create({'case_id':d['id'], 'payload':{'action':'images'}})
            case_id, snap_id, proposal_id, digest = d['id'], d['snapshot']['id'], p.id, p.digest
            setup.commit()
        with registry.cursor() as stale, registry.cursor() as writer:
            old = odoo.api.Environment(stale, odoo.SUPERUSER_ID, {})
            fresh = odoo.api.Environment(writer, odoo.SUPERUSER_ID, {})
            old['meli.optimization.case'].browse(case_id).read(['revision'])
            fresh['meli.optimization.task'].create({'case_id':case_id, 'snapshot_id':snap_id, 'task_type':'image.qc',
                'payload':{'schema_version':'1','image_artifact_id':'1','generation_plan_id':'1'}})
            writer.commit()
            with self.assertRaises(SerializationFailure):
                old['meli.optimization.proposal'].browse(proposal_id).confirm(digest)

    def test_qc_key_uniqueness_across_independent_transactions(self):
        registry = self.env.registry
        with registry.cursor() as setup:
            env = odoo.api.Environment(setup, odoo.SUPERUSER_ID, {})
            d = env['meli.optimization.case'].workbench_create({'shop':'test','item_id':'qc-key-race','title':'test'})
            values = {'case_id':d['id'], 'snapshot_id':d['snapshot']['id'], 'task_type':'image.qc',
                      'payload':{'schema_version':'1','image_artifact_id':'1','generation_plan_id':'1'}}
            one = env['meli.optimization.task'].create(values).id
            two = env['meli.optimization.task'].create(values).id
            setup.commit()
        with registry.cursor() as first, registry.cursor() as second:
            env1 = odoo.api.Environment(first, odoo.SUPERUSER_ID, {})
            env2 = odoo.api.Environment(second, odoo.SUPERUSER_ID, {})
            env2['meli.optimization.task'].browse(two).read(['id'])
            env1['meli.optimization.task'].browse(one)._set({'image_qc_key':'same-qc-identity'})
            env1['meli.optimization.task'].flush_model(['image_qc_key'])
            first.commit()
            with self.assertRaises(UniqueViolation):
                env2['meli.optimization.task'].browse(two)._set({'image_qc_key':'same-qc-identity'})
                env2['meli.optimization.task'].flush_model(['image_qc_key'])

    def test_concurrent_qc_transition_fences_stale_image_confirmation(self):
        registry = self.env.registry
        with registry.cursor() as setup:
            env = odoo.api.Environment(setup, odoo.SUPERUSER_ID, {})
            d = env['meli.optimization.case'].workbench_create({'shop':'test','item_id':'qc-transition-race','title':'test'})
            p = env['meli.optimization.proposal'].create({'case_id':d['id'], 'payload':{'action':'images'}})
            qc = env['meli.optimization.task'].create({'case_id':d['id'], 'snapshot_id':d['snapshot']['id'], 'task_type':'image.qc',
                'payload':{'schema_version':'1','image_artifact_id':'1','generation_plan_id':'1'}})
            case_id, proposal_id, digest, qc_id = d['id'], p.id, p.digest, qc.id
            setup.commit()
        with registry.cursor() as stale, registry.cursor() as writer:
            old = odoo.api.Environment(stale, odoo.SUPERUSER_ID, {})
            fresh = odoo.api.Environment(writer, odoo.SUPERUSER_ID, {})
            old['meli.optimization.case'].browse(case_id).read(['qc_revision'])
            fresh['meli.optimization.task'].browse(qc_id)._transition('running')
            writer.commit()
            with self.assertRaises(SerializationFailure):
                old['meli.optimization.proposal'].browse(proposal_id).confirm(digest)

    def test_import_error_does_not_rollback_completed_gallery(self):
        detail = self.collection()
        with patch.object(type(self.ui), 'workbench_library_import', side_effect=ValidationError('fixture')):
            task, _ = self.gallery(detail)
        self.assertEqual(task.state, 'succeeded')
        self.assertEqual(task.status_detail['code'], 'derived_action_failed')
        self.assertFalse(self.env['meli.optimization.library'].search([('task_id', '=', task.id)]))
        self.ui.workbench_retry_derived(detail['id'], task.id)
        self.assertEqual(self.env['meli.optimization.library'].search_count([('task_id', '=', task.id)]), 1)

    def test_oversized_combined_gallery_preserves_completed_result(self):
        detail = self.collection()
        previous, _ = self.gallery(detail)
        snapshot = self.env['meli.optimization.snapshot'].create({'case_id':detail['id'], 'payload':{'filler':'x' * 140000}})
        task = self.env['meli.optimization.task'].create({'case_id':detail['id'], 'snapshot_id':snapshot.id,
            'task_type':'gallery.collect', 'payload':previous.payload})
        result = dict(previous.result, evidence=[{'id':'fixture', 'source':'x' * 140000,
                                                  'observed_at':'2026-11-15T00:00:00Z'}])
        claim = task.claim(['gallery.collect'])
        task.complete(claim['lease_token'], claim['lease_epoch'], 'large-gallery', result)
        self.assertEqual(task.state, 'succeeded')
        self.assertEqual(task.status_detail['code'], 'derived_action_failed')
        self.assertFalse(self.env['meli.optimization.library'].search([('task_id', '=', task.id)]))
        self.assertFalse(self.ui.workbench_retry_derived(detail['id'], task.id)['ok'])
        self.assertEqual(task.state, 'succeeded')

    def gallery(self, detail):
        raw = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC')
        artifact = self.env['meli.optimization.artifact'].create({'case_id': detail['id'],
            'data': base64.b64encode(raw), 'digest': hashlib.sha256(raw).hexdigest(), 'mime_type': 'image/png'})
        task_id = self.ui.workbench_task(detail['id'], 'gallery.collect',
            {'reference_item_id': 'TEST-REF', 'selected_variant': {'seller_sku': 'TEST-SKU'}})
        task = self.env['meli.optimization.task'].browse(task_id)
        result = {'schema_version': '1', 'evidence': [{'id': 'fixture', 'source': 'https://example.test/item', 'observed_at': '2026-11-15T00:00:00Z'}],
            'reference_item_id': 'TEST-REF', 'selected_variant': {'seller_sku': 'TEST-SKU'},
            'observed_at': '2026-11-15T00:00:00Z', 'gallery_manifest_hash': 'a' * 64,
            'completeness_status': 'complete', 'images': [{'image_id': 'TEST-IMAGE', 'order': 0,
                'role': 'front', 'artifact_id': str(artifact.id), 'source': 'https://example.test/item',
                'evidence_ids': ['fixture'], 'mime_type': 'image/png', 'width': 1, 'height': 1, 'sha256': artifact.digest}]}
        claim = task.claim(['gallery.collect'])
        task.complete(claim['lease_token'], claim['lease_epoch'], 'test-gallery', result)
        return task, artifact

    def test_external_result_import_is_idempotent_and_has_no_fake_gallery(self):
        image = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        value = {'base': 'base1', 'table': 'tbl1', 'record': 'rec1', 'source_sku': 'SKU1',
                 'image_data': image, 'image_mime': 'image/png', 'url': 'https://example.test/item',
                 'platform': 'SHEIN US', 'title': 'Test title', 'rating': '5.0'}
        first = self.ui.workbench_library_external_import(value)
        self.assertEqual(first, self.ui.workbench_library_external_import(value))
        row = next(r for r in self.ui.workbench_library()['rows'] if r['id'] == first)
        self.assertEqual(row['source_sku'], 'SKU1')
        self.assertEqual(row['title'], 'Test title')
        self.assertEqual(row['payload']['images'], [])
        self.assertTrue(row['source_image_artifact_ids'])
        self.assertFalse(self.env['meli.optimization.library'].browse(first).task_id)
        second = self.ui.workbench_library_external_import(dict(value, record='rec2'))
        records = self.env['meli.optimization.library'].browse([first, second])
        self.assertEqual(len(records.mapped('case_id.batch_id')), 1)
        self.assertTrue(records[0].case_id.batch_id.external_import_key)
        with self.assertRaises(ValidationError):
            self.ui.workbench_library_generate(row['payload']['case_id'], first, [], [], 0)

    def test_standalone_collection_and_idempotent_library(self):
        detail = self.collection()
        overview = self.ui.workbench_overview()
        self.assertFalse(any(r['id'] == detail['id'] for r in overview['rows']))
        collection_batch = self.ui.browse(detail['id']).batch_id.id
        self.assertFalse(any(b['id'] == collection_batch for b in overview['batches']))
        self.assertIn(detail['id'], [r['id'] for r in self.ui.workbench_collections()])
        task, artifact = self.gallery(detail)
        self.assertEqual(self.env['meli.optimization.library'].search_count([('task_id', '=', task.id)]), 1)
        entry = self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(task.result))
        self.assertEqual(entry, self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(task.result)))
        row = self.ui.workbench_library('TEST-REF')['rows'][0]
        self.assertEqual(row['id'], entry)
        self.assertFalse(row['source_sku'])
        self.assertEqual(row['source_image_artifact_ids'], [])
        self.assertFalse(row['price'])
        self.assertFalse(row['title'])
        page = self.ui.workbench_library('', '', {'query': 'absent'}, 0)
        self.assertEqual(page['total'], 0)
        self.assertEqual(page['rows'], [])
        self.assertEqual(page['limit'], 50)
        library = self.env['meli.optimization.library'].browse(entry)
        library._set({'payload': dict(library.payload, research_candidate={'platform': 'MLM'})})
        self.assertEqual(self.ui.workbench_library('', '', {'platform': 'Mercado Libre México'}, 0)['total'], 1)
        self.assertEqual(self.ui.workbench_library('', '', {'platform': 'SHEIN US'}, 0)['total'], 0)
        library._set({'payload': dict(library.payload, research_candidate={'platform': 'SHEIN US'})})
        self.assertEqual(self.ui.workbench_library('', '', {'platform': 'SHEIN US'}, 0)['total'], 1)
        self.assertEqual(row['payload']['images'][0]['sha256'], artifact.digest)
        with self.assertRaises(AccessError):
            self.env['meli.optimization.library'].browse(entry).write({'name': 'tampered'})

    def test_import_rejects_wrong_hash_stale_missing_and_wrong_sku(self):
        detail = self.collection()
        task, artifact = self.gallery(detail)
        with self.assertRaises(ValidationError):
            self.ui.workbench_library_import(detail['id'], task.id, 'wrong')
        original = task.result
        task._set({'result': dict(original, selected_variant={'seller_sku': 'FORGED'})})
        with self.assertRaises(ValidationError):
            self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(task.result))
        task._set({'result': original})
        artifact._set({'data': False})
        with self.assertRaises(ValidationError):
            self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(task.result))
        self.env['meli.optimization.snapshot'].create({'case_id': detail['id'], 'payload': {'changed': True}})
        with self.assertRaises(ValidationError):
            self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(task.result))

    def test_ambiguous_research_and_legacy_metadata_origin(self):
        detail = self.create_started_collection()
        gallery, _ = self.gallery(detail)
        tasks = self.env['meli.optimization.task']
        research = []
        for platform in ('MLM', 'SHEIN US'):
            t = tasks.create({'case_id': detail['id'], 'snapshot_id': gallery.snapshot_id.id,
                'task_type': 'product.facts', 'payload': {'schema_version': '1',
                    'product_snapshot_id': str(gallery.snapshot_id.id)}})
            t._set({'task_type': 'research.bestseller', 'state': 'succeeded', 'result': {'candidates': [{'item_id': 'TEST-REF', 'platform': platform}]}})
            research.append(t)
        entry = self.ui.workbench_library_import(detail['id'], gallery.id, canonical_hash(gallery.result))
        row = self.ui.workbench_library('TEST-REF')['rows'][0]
        self.assertFalse(row['platform'])
        self.assertEqual(row['research_metadata_origin'], 'unknown')
        research[1]._set({'state': 'cancelled'})
        row = self.ui.workbench_library('TEST-REF')['rows'][0]
        self.assertFalse(row['platform'])  # frozen absence must not change later
        library = self.env['meli.optimization.library'].browse(entry)
        old_payload = dict(library.payload)
        old_payload.pop('research_candidate')
        library._set({'payload': old_payload})
        row = self.ui.workbench_library('TEST-REF')['rows'][0]
        self.assertEqual(row['platform'], 'MLM')
        self.assertEqual(row['research_metadata_origin'], 'legacy_backfill')

    def test_completed_old_snapshot_is_not_automatically_archived(self):
        detail = self.collection()
        prior, _ = self.gallery(detail)
        old = self.env['meli.optimization.task'].create({'case_id': detail['id'], 'snapshot_id': prior.snapshot_id.id,
            'task_type': 'gallery.collect', 'payload': prior.payload})
        claim = old.claim(['gallery.collect'])
        self.env['meli.optimization.snapshot'].create({'case_id': detail['id'], 'payload': {'changed': True}})
        old.complete(claim['lease_token'], claim['lease_epoch'], 'old-snapshot', prior.result)
        self.assertFalse(self.env['meli.optimization.library'].search([('task_id', '=', old.id)]))

    def test_cross_case_selection_freezes_exact_scope_and_origin(self):
        detail = self.collection()
        task, artifact = self.gallery(detail)
        library_id = self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(task.result))
        destination = self.ui.workbench_create({'shop': 'Test', 'item_id': 'DEST', 'title': 'Destination'})
        source = self.env['meli.optimization.artifact'].create({'case_id': destination['id'], 'data': artifact.data,
            'digest': artifact.digest, 'mime_type': artifact.mime_type})
        self.env['meli.optimization.snapshot'].create({'case_id': destination['id'], 'payload': {'source_image_artifact_ids': [str(source.id)]}})
        destination = self.ui.workbench_detail(destination['id'])
        selected = self.ui.workbench_library_generate(destination['id'], library_id, [0], [source.id], destination['revision'])
        self.assertEqual(len(selected['task_ids']), 1)
        generated = self.env['meli.optimization.task'].browse(selected['task_ids'])
        clone = self.env['meli.optimization.artifact'].browse(int(generated.payload['target_artifact_id']))
        self.assertEqual(clone.case_id.id, destination['id'])
        self.assertEqual(clone.library_origin_id.id, library_id)
        self.assertEqual(clone.library_artifact_id, artifact)
        plan = self.env['meli.optimization.artifact'].browse(int(generated.payload['generation_plan_id']))
        output = self.env['meli.optimization.artifact'].create({'case_id': destination['id'], 'data': artifact.data,
            'digest': artifact.digest, 'mime_type': artifact.mime_type})
        evidence = [{'id':'e', 'source':'fixture', 'observed_at':'2026-11-15T00:00:00Z'}]
        image = {'image_id':'generated', 'order':0, 'role':'front', 'artifact_id':str(output.id),
                 'source':'fixture', 'evidence_ids':['e'], 'mime_type':'image/png', 'width':1, 'height':1, 'sha256':output.digest}
        result = {'schema_version':'1', 'request_id':'fixture', 'evidence':evidence, 'image':image}
        claim = generated.claim(['image.generate'])
        generated.complete(claim['lease_token'], claim['lease_epoch'], 'generated', result)
        qcs = self.env['meli.optimization.task'].search([('case_id','=',destination['id']), ('task_type','=','image.qc')])
        self.assertEqual(len(qcs), 1)
        self.assertEqual(qcs.payload['image_artifact_id'], str(output.id))
        generated.complete(claim['lease_token'], claim['lease_epoch'], 'generated', result)
        self.assertEqual(self.env['meli.optimization.task'].search_count([('case_id','=',destination['id']), ('task_type','=','image.qc')]), 1)
        with self.assertRaises(ValidationError):
            self.ui.workbench_propose(destination['id'], generated.id, canonical_hash(result), 'images', [0])
        forged = self.env['meli.optimization.proposal'].create({'case_id':destination['id'], 'payload': {
            'action':'images', 'task_id':generated.id, 'result_hash':canonical_hash(result), 'desired_value':[image]}})
        with self.assertRaises(ValidationError):
            forged.confirm(forged.digest)
        qc_claim = qcs.claim(['image.qc'])
        self.assertEqual(qc_claim['generation_task_id'], generated.id)
        self.assertEqual(qc_claim['generation_target_artifact_id'], generated.payload['target_artifact_id'])
        duplicate = self.env['meli.optimization.task'].create({'case_id':destination['id'], 'snapshot_id':generated.snapshot_id.id,
            'task_type':'image.generate', 'payload':generated.payload})
        duplicate._set({'state':'succeeded', 'result':result})
        self.assertNotIn('generation_target_artifact_id', qcs._envelope())
        duplicate._set({'state':'cancelled'})
        failed_result = {'schema_version':'1', 'evidence':evidence, 'image_artifact_id':str(output.id), 'status':'failed',
                         'checks':[{'name':'garment_construction','status':'failed','evidence_ids':['e']}]}
        qcs.complete(qc_claim['lease_token'], qc_claim['lease_epoch'], 'qc-failed', failed_result)
        with self.assertRaises(ValidationError):
            self.ui.workbench_propose(destination['id'], generated.id, canonical_hash(result), 'images', [0])
        passed_id = self.ui.workbench_task(destination['id'], 'image.qc', {'image_artifact_id':str(output.id),
            'generation_plan_id':str(plan.id)})
        passed = self.env['meli.optimization.task'].browse(passed_id)
        qc_claim = passed.claim(['image.qc'])
        qc_result = {'schema_version':'1', 'evidence':evidence, 'image_artifact_id':str(output.id), 'status':'passed',
                     'checks':[{'name':name,'status':'passed','evidence_ids':['e']} for name in (
                         'garment_construction','color_material','garment_details','identity_pose','accessories',
                         'scene_lighting_framing','text_layout')]}
        passed.complete(qc_claim['lease_token'], qc_claim['lease_epoch'], 'qc-passed', qc_result)
        proposal = self.ui.workbench_propose(destination['id'], generated.id, canonical_hash(result), 'images', [0])
        self.assertTrue(proposal['id'])
        self.assertTrue(self.ui.workbench_confirm(destination['id'], proposal['id'], proposal['digest']))
        newest_id = self.ui.workbench_task(destination['id'], 'image.qc', {'image_artifact_id':str(output.id),
            'generation_plan_id':str(plan.id)})
        newest = self.env['meli.optimization.task'].browse(newest_id)
        newest_claim = newest.claim(['image.qc'])
        newest.complete(newest_claim['lease_token'], newest_claim['lease_epoch'], 'newer-failed', failed_result)
        with self.assertRaises(ValidationError):
            self.ui.workbench_propose(destination['id'], generated.id, canonical_hash(result), 'images', [0])
        with self.assertRaises(ValidationError):
            self.env['meli.optimization.proposal'].browse(proposal['id']).confirm(proposal['digest'])
        self.assertEqual(plan.payload['target_count'], 1)
        self.assertEqual(plan.payload['source_artifact_ids'], [str(source.id)])
        self.assertEqual(plan.payload['library_result_hash'], canonical_hash(task.result))
        with self.assertRaises(ValidationError):
            self.ui.workbench_library_generate(destination['id'], library_id, [0, 0], [source.id], destination['revision'])
        company = self.env['res.company'].create({'name': 'Other library company'})
        cross = self.ui.with_context(allowed_company_ids=[company.id, self.env.company.id])
        other = cross.workbench_create({'shop': 'Other', 'item_id': 'OTHER', 'title': 'Other company'})
        with self.assertRaises(AccessError):
            cross.workbench_library_generate(other['id'], library_id, [0], [source.id], other['revision'])

    def test_binary_content_and_dimensions_must_be_real_images(self):
        detail = self.collection()
        task, artifact = self.gallery(detail)
        bad = b'not an image'
        digest = hashlib.sha256(bad).hexdigest()
        artifact._set({'data': base64.b64encode(bad), 'digest': digest})
        result = dict(task.result)
        result['images'] = [dict(result['images'][0], sha256=digest)]
        task._set({'result': result})
        with self.assertRaises(ValidationError):
            self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(result))

    def test_library_rejects_non_web_source_and_other_company_reads(self):
        detail = self.collection()
        task, artifact = self.gallery(detail)
        original = task.result
        forged = dict(original, images=[dict(original['images'][0], source='file:///etc/passwd')])
        task._set({'result': forged})
        with self.assertRaises(ValidationError):
            self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(forged))
        task._set({'result': original})
        entry = self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(original))
        company = self.env['res.company'].create({'name': 'Excluded library company'})
        worker = self.env['res.users'].create({'name': 'Library readonly', 'login': 'library-excluded',
            'company_id': company.id, 'company_ids': [(6, 0, company.ids)],
            'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        scoped = self.ui.with_user(worker).with_context(allowed_company_ids=company.ids)
        self.assertFalse(scoped.workbench_library()['rows'])
        with self.assertRaises(AccessError):
            self.env['meli.optimization.library'].with_user(worker).with_context(allowed_company_ids=company.ids).browse(entry).read(['payload'])
        with self.assertRaises(AccessError):
            scoped.workbench_library_import(detail['id'], task.id, canonical_hash(original))

    def test_upload_source_reference_preserves_actual_bytes_in_collection_snapshot(self):
        import io
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (3, 4), 'navy').save(buffer, format='PNG')
        data = base64.b64encode(buffer.getvalue()).decode()
        detail = self.ui.workbench_collection_create({'garment': 'Test source image', 'image_data': data, 'image_mime': 'image/png'})
        references = detail['snapshot']['payload']['source_image_artifact_ids']
        self.assertEqual(len(references), 1)
        self.assertEqual(self.env['meli.optimization.artifact'].browse(references).data.decode(), data)
        with self.assertRaises(ValidationError):
            self.ui.workbench_upload_garment(detail['id'], base64.b64encode(b'not an image').decode(), 'image/png')

    def test_invalid_generic_garment_artifact_creates_no_generation_side_effects(self):
        detail = self.collection()
        task, image = self.gallery(detail)
        library_id = self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(task.result))
        destination = self.ui.workbench_create({'shop': 'Test', 'item_id': 'BAD-SOURCE', 'title': 'Source validation'})
        raw = b'not an image'
        artifacts = self.env['meli.optimization.artifact']
        source = artifacts.create({'case_id': destination['id'], 'data': base64.b64encode(raw),
            'digest': hashlib.sha256(raw).hexdigest(), 'mime_type': 'image/png'})
        domain = [('case_id', '=', destination['id'])]
        before_artifacts = artifacts.search_count(domain)
        before_tasks = self.env['meli.optimization.task'].search_count(domain)
        with self.assertRaisesRegex(ValidationError, '缺少可用原图'):
            self.ui.workbench_library_generate(destination['id'], library_id, [0], [source.id], destination['revision'])
        self.assertEqual(artifacts.search_count(domain), before_artifacts)
        self.assertEqual(self.env['meli.optimization.task'].search_count(domain), before_tasks)

    def test_stale_destination_revision_creates_no_generation_side_effects(self):
        detail = self.collection()
        task, image = self.gallery(detail)
        library_id = self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(task.result))
        destination = self.ui.workbench_create({'shop': 'Test', 'item_id': 'STALE', 'title': 'Before'})
        artifacts = self.env['meli.optimization.artifact']
        source = artifacts.create({'case_id': destination['id'], 'data': image.data,
            'digest': image.digest, 'mime_type': image.mime_type})
        self.ui.workbench_snapshot(destination['id'], {'shop': 'Test', 'item_id': 'STALE', 'title': 'After'})
        domain = [('case_id', '=', destination['id'])]
        before = artifacts.search_count(domain)
        with self.assertRaisesRegex(ValidationError, '快照'):
            self.ui.workbench_library_generate(destination['id'], library_id, [0], [source.id], destination['revision'])
        self.assertEqual(artifacts.search_count(domain), before)
        self.assertFalse(self.env['meli.optimization.task'].search(domain))

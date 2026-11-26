from unittest.mock import patch
from datetime import datetime, timezone
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import AccessError, ValidationError


@tagged('post_install', '-at_install')
class ScreeningCase(TransactionCase):
    def test_screening_applies_inventory_threshold_to_each_variant_group(self):
        preview=self.preview()
        products,orders,source=self.collected()
        products[0]['variations']=[{'id':101,'available_quantity':3},{'id':102,'available_quantity':17}]
        products[0]['_verified_parent_id']='CBT1'
        products[0]['_verified_variant_groups']=[
            {'key':'v','label':'Violeta','variant_ids':['11'],'site_variant_ids':['101'],'stock':3,'picture_ids':['p1'],'picture_urls':['https://http2.mlstatic.com/a.jpg'],'sizes':['S']},
            {'key':'g','label':'Gray','variant_ids':['12'],'site_variant_ids':['102'],'stock':17,'picture_ids':['p2'],'picture_urls':['https://http2.mlstatic.com/b.jpg'],'sizes':['S']}]
        source['variant_groups_checked']=True
        with patch.object(type(self.ui),'_screen_collect',return_value=(products,orders,source)):
            result=self.ui.screening_execute(preview['id'],preview['digest'])
        self.assertEqual(len(result['case_ids']),1)
        self.assertEqual(self.ui._wb_snapshot(self.ui.browse(result['case_ids'][0])).payload['variant_scope']['label'],'Gray')
    def test_screen_collection_joins_site_and_parent_variations_by_pictures(self):
        def v(i,c,p):return {'id':i,'attribute_combinations':[{'id':'COLOR','value_name':c},{'id':'SIZE','value_name':'S'}], 'picture_ids':[p],'available_quantity':10}
        item={'id':'MLM1','site_id':'MLM','seller_id':'9','title':'Test','cbt_item_id':'CBT1','tags':[],
              'variations':[v(101,'Violeta','p1'),v(102,'Gray','p2')]}
        parent={'id':'CBT1','site_id':'CBT','seller_id':'8','pictures':[{'id':'p1','secure_url':'https://http2.mlstatic.com/a.jpg'}, {'id':'p2','secure_url':'https://http2.mlstatic.com/b.jpg'}],
                'variations':[v(11,'Violeta','p1'),v(12,'Gray','p2')],
                'marketplace_items':[{'item_id':'MLM1','site_id':'MLM','user_id':'9','parent_id':'CBT1','parent_user_id':'8','logistic_type':self.shop.logistic_type}]}
        class Response:
            status=200
            def __init__(self,data):self.data=data
            def json(self):return self.data
            def close(self):pass
        values={'/users/me':{'id':'8'},'/marketplace/users/8':{'user_id':'8','marketplaces':[{'user_id':'9','site_id':'MLM','logistic_type':self.shop.logistic_type}]},'/marketplace/items/CBT1':parent}
        mod='odoo.addons.meli_optimization.models.screening'
        with patch.object(type(self.shop),'_token',return_value='fake'),patch(mod+'.transport.send',side_effect=lambda method,path,**kw:Response(values[path])),patch(mod+'.collect_shop',return_value=([item],[],{'orders_complete':True})):
            products,_,source=self.ui._screen_collect(self.shop,self.criteria)
        self.assertTrue(source['variant_groups_checked'])
        self.assertEqual([g['variant_ids'] for g in products[0]['_verified_variant_groups']],[['11'],['12']])
        self.assertEqual([g['site_variant_ids'] for g in products[0]['_verified_variant_groups']],[['101'],['102']])
    def test_screening_creates_separate_verified_variant_cases(self):
        preview=self.preview()
        products,orders,source=self.collected()
        products[0]['variations']=[{'id':101,'available_quantity':10},{'id':102,'available_quantity':10}]
        products[0]['_verified_parent_id']='CBT1'
        products[0]['_verified_variant_groups']=[
            {'key':'v','label':'Violeta','variant_ids':['11'],'site_variant_ids':['101'],'stock':10,'picture_ids':['p1'],'picture_urls':['https://http2.mlstatic.com/a.jpg'],'sizes':['S'],'attribute_signature':'a'},
            {'key':'g','label':'Gray','variant_ids':['12'],'site_variant_ids':['102'],'stock':10,'picture_ids':['p2'],'picture_urls':['https://http2.mlstatic.com/b.jpg'],'sizes':['S'],'attribute_signature':'b'}]
        source['variant_groups_checked']=True
        with patch.object(type(self.ui),'_screen_collect',return_value=(products,orders,source)):
            result=self.ui.screening_execute(preview['id'],preview['digest'])
        self.assertEqual(len(result['case_ids']),2)
        cases=self.ui.browse(result['case_ids'])
        snapshots=[self.ui._wb_snapshot(c).payload for c in cases]
        self.assertEqual([p['variant_scope']['label'] for p in snapshots],['Violeta','Gray'])
        self.assertEqual([p['variant_scope']['variant_ids'] for p in snapshots],[['11'],['12']])
        self.assertEqual([p['available_quantity'] for p in snapshots],[10,10])
        self.assertTrue(all(p['sales_scope']=='product' for p in snapshots))
    def test_variant_creation_is_scoped_and_prevents_overlap(self):
        from ..domain.variant_groups import traditional_groups, bind_site_variations
        def v(i,color,size,pic):
            return {'id':i,'attribute_combinations':[{'id':'COLOR','value_id':'','value_name':color},{'id':'SIZE','value_name':size}], 'picture_ids':[pic], 'available_quantity':2}
        parent={'pictures':[{'id':'p1','secure_url':'https://http2.mlstatic.com/a.jpg'},{'id':'p2','secure_url':'https://http2.mlstatic.com/b.jpg'}],
                'variations':[v(11,'Violeta','S','p1'),v(12,'Violeta','M','p1'),v(13,'Gray','S','p2')]}
        def product(shop,item):
            return {'item_id':item,'parent_id':'CBT1','site':'MLM','title':'Test','model':'traditional','groups':traditional_groups(parent)}
        with patch.object(type(self.ui),'_screen_shop',return_value=self.shop), patch.object(type(self.ui),'_variant_read_product',side_effect=product):
            p=self.ui.workbench_variant_preview(self.shop.id,'MLM1')
            created=self.ui.workbench_variant_create(self.shop.id,'MLM1',p['digest'],[g['key'] for g in p['item']['groups']])
            self.assertEqual(len(created),2)
            cases=self.ui.browse([x['case_id'] for x in created])
            scopes=[self.ui._wb_snapshot(c).payload['variant_scope'] for c in cases]
            self.assertEqual([s['picture_ids'] for s in scopes],[['p1'],['p2']])
            self.assertEqual([s['variant_ids'] for s in scopes],[['11','12'],['13']])
            again=self.ui.workbench_variant_preview(self.shop.id,'MLM1')
            self.assertTrue(all(not g['selectable'] and g['existing_id'] for g in again['item']['groups']))
            with self.assertRaises(ValidationError):self.ui.workbench_variant_create(self.shop.id,'MLM1',p['digest'],[p['item']['groups'][0]['key']])
            with self.assertRaises(ValidationError):self.ui.workbench_snapshot(cases[0].id,{'shop':'test','item_id':'MLM1','title':'overwrite'})
            with self.assertRaises(AccessError):self.env['meli.optimization.snapshot'].with_user(self.ui.env.user).create({'case_id':cases[0].id,'payload':self.ui._wb_snapshot(cases[0]).payload})
        with self.assertRaises(AccessError):
            self.ui.create({'name':'forged','batch_id':cases[0].batch_id.id,'variant_identity':'forged'})

    def test_variant_gallery_sync_only_downloads_selected_color(self):
        import base64
        from ..domain.variant_groups import traditional_groups, bind_site_variations
        def v(i,c,p):return {'id':i,'attribute_combinations':[{'id':'COLOR','value_name':c},{'id':'SIZE','value_name':'S'}],'picture_ids':[p],'available_quantity':1}
        pics=[{'id':'p1','secure_url':'https://http2.mlstatic.com/a.jpg'},{'id':'p2','secure_url':'https://http2.mlstatic.com/b.jpg'}]
        parent={'id':'CBT1','site_id':'CBT','seller_id':'8','pictures':pics,'variations':[v(11,'Violeta','p1'),v(12,'Gray','p2')],
                'marketplace_items':[{'item_id':'MLM1','site_id':'MLM','user_id':'9','logistic_type':self.shop.logistic_type,'parent_id':'CBT1','parent_user_id':'8'}]}
        site_vars=[v(101,'Violeta','p1'),v(102,'Gray','p2')]
        product=lambda shop,item:{'item_id':item,'parent_id':'CBT1','site':'MLM','title':'Test','model':'traditional','groups':bind_site_variations(traditional_groups(parent),site_vars)}
        with patch.object(type(self.ui),'_screen_shop',return_value=self.shop),patch.object(type(self.ui),'_variant_read_product',side_effect=product):
            preview=self.ui.workbench_variant_preview(self.shop.id,'MLM1')
            row=self.ui.workbench_variant_create(self.shop.id,'MLM1',preview['digest'],[preview['item']['groups'][0]['key']])[0]
        item={'id':'MLM1','site_id':'MLM','seller_id':'9','cbt_item_id':'CBT1','tags':[],'variations':site_vars}
        values={'/users/me':{'id':'8'},'/marketplace/users/8':{'user_id':'8','marketplaces':[{'user_id':'9','site_id':'MLM','logistic_type':self.shop.logistic_type}]},'/marketplace/items/MLM1':item,'/marketplace/items/CBT1':parent}
        class Response:
            status=200
            def __init__(self,data):self.data=data
            def json(self):return self.data
            def close(self):pass
        raw=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC')
        mod='odoo.addons.meli_optimization.models.snapshot_gallery'
        with patch.object(type(self.ui),'_screen_shop',return_value=self.shop),patch.object(type(self.shop),'_token',return_value='fake'),patch(mod+'.transport.send',side_effect=lambda method,path,**kw:Response(values[path])),patch(mod+'.fetch',return_value=(raw,'image/png')) as download:
            result=self.ui.workbench_archive_snapshot_gallery(row['case_id'],row['revision'])
            unchanged=self.ui.workbench_archive_snapshot_gallery(row['case_id'],result['revision'])
            self.assertEqual(unchanged['snapshot']['id'],result['snapshot']['id'])
            parent['pictures'].append({'id':'p3','secure_url':'https://http2.mlstatic.com/c.jpg'})
            parent['variations'][0]['picture_ids']=['p3']
            site_vars[0]['picture_ids']=['p3']
            changed=self.ui.workbench_archive_snapshot_gallery(row['case_id'],result['revision'])
            self.assertNotEqual(changed['snapshot']['id'],result['snapshot']['id'])
            self.assertEqual(changed['snapshot']['payload']['variant_scope']['picture_ids'],['p3'])
            parent['variations'][0]['attribute_combinations'][0]['value_name']='Other color'
            with self.assertRaises(ValidationError):self.ui.workbench_archive_snapshot_gallery(row['case_id'],changed['revision'])
        self.assertEqual([c.args[0] for c in download.call_args_list],['https://http2.mlstatic.com/a.jpg','https://http2.mlstatic.com/a.jpg','https://http2.mlstatic.com/c.jpg'])
        self.assertEqual([x['id'] for x in result['snapshot']['payload']['pictures']],['p1'])
    def test_bestseller_import_rejects_invalid_batch(self):
        config = {'platform':'mercado-libre-mx','result_limit':5,'min_sold':100,'min_reviews':0,'min_rating':0}
        with self.assertRaises(ValidationError):
            self.ui.import_bestseller_batch(999999999, 'import-test', config)

    def test_bestseller_import_existing_image_is_idempotent(self):
        preview = self.preview()
        with patch.object(type(self.ui), '_screen_collect', self.collected):
            result = self.ui.screening_execute(preview['id'], preview['digest'])
        case = self.ui.browse(result['case_ids'][0])
        image = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        aid = self.ui.workbench_upload_garment(case.id, image, 'image/png')
        original = self.ui._wb_snapshot(case)
        payload = dict(original.payload, source_image_artifact_ids=[str(aid)])
        self.env['meli.optimization.snapshot'].with_user(self.ui.env.user).create({'case_id':case.id,'payload':payload})
        config = {'platform':'mercado-libre-mx','result_limit':5,'min_sold':100,'min_reviews':0,'min_rating':0}
        first = self.ui.import_bestseller_batch(result['batch_id'], 'import-test', config)
        self.assertEqual(first['job_state'],'completed')
        self.assertEqual(first['items'][0]['item_id'],'MLM1')
        self.assertEqual(first['items'][0]['status'],'success')
        again = self.ui.import_bestseller_batch(result['batch_id'], 'import-test', config)
        self.assertEqual(first, again)
        target = self.ui.workbench_detail(first['items'][0]['collection_id'])
        self.assertEqual(target['snapshot']['payload']['collection_config'], dict(
            config, strategy='card-visual-v3', initial_query_count=3,
            max_expansion_queries=6, max_pages_per_query=3, sales_known_target=100, top_per_query=30, visual_limit=30))
        self.assertEqual(target['snapshot']['payload']['source_sku'],'MLM1')
        with self.assertRaises(ValidationError):
            self.ui.import_bestseller_batch(result['batch_id'], 'import-test', dict(config, min_sold=500))

    def test_bestseller_import_failed_item_explicit_retry_and_batch_details(self):
        preview = self.preview()
        with patch.object(type(self.ui), '_screen_collect', self.collected):
            result = self.ui.screening_execute(preview['id'], preview['digest'])
        case = self.ui.browse(result['case_ids'][0])
        config = {'platform':'mercado-libre-mx','result_limit':5,'min_sold':100,'min_reviews':0,'min_rating':0}
        details = self.ui.bestseller_batch_details(result['batch_id'])
        self.assertEqual(details['items'][0]['item_id'], 'MLM1')
        with patch.object(type(self.ui), '_bestseller_first_image', side_effect=ValidationError('test missing image')):
            failed = self.ui.import_bestseller_batch(result['batch_id'], 'retry-test', config)
        self.assertEqual(failed['job_state'], 'partial')
        self.assertEqual(failed['items'][0]['status'], 'failed')
        self.assertEqual(self.ui.import_bestseller_batch(result['batch_id'], 'retry-test', config), failed)
        image = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        aid = self.ui.workbench_upload_garment(case.id, image, 'image/png')
        original = self.ui._wb_snapshot(case)
        self.env['meli.optimization.snapshot'].with_user(self.ui.env.user).create({'case_id':case.id,'payload':dict(original.payload,source_image_artifact_ids=[str(aid)])})
        done = self.ui.import_bestseller_batch(result['batch_id'], 'retry-test', config, True)
        self.assertEqual(done['job_state'], 'completed')
        self.assertEqual(done['items'][0]['item_id'], 'MLM1')
        outsider = self.env['res.users'].create({'name':'Import outsider','login':'import-outsider'})
        with self.assertRaises(AccessError):
            self.ui.with_user(outsider).bestseller_batch_details(result['batch_id'])
        with self.assertRaises(AccessError):
            self.ui.with_user(outsider).import_bestseller_batch(result['batch_id'], 'unauthorized', config)
        with self.assertRaises(AccessError):
            self.ui.create({'name':'forged', 'batch_id':result['batch_id'], 'collection_import_key':'retry-test','collection_import_source_id':case.id})

    def test_bestseller_import_advances_past_failed_item(self):
        preview=self.preview()
        products, orders, source=self.collected()
        products.append(dict(products[0],id='MLM2',title='Second'))
        with patch.object(type(self.ui), '_screen_collect', return_value=(products, orders, source)):
            result=self.ui.screening_execute(preview['id'],preview['digest'])
        second=self.ui.browse(result['case_ids'][1])
        image='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
        aid=self.ui.workbench_upload_garment(second.id,image,'image/png')
        snap=self.ui._wb_snapshot(second)
        self.env['meli.optimization.snapshot'].with_user(self.ui.env.user).create({'case_id':second.id,'payload':dict(snap.payload,source_image_artifact_ids=[str(aid)])})
        config={'platform':'mercado-libre-mx','result_limit':5,'min_sold':100,'min_reviews':0,'min_rating':0}
        with patch.object(type(self.ui),'_bestseller_first_image',side_effect=ValidationError('image unavailable')):
            first=self.ui.import_bestseller_batch(result['batch_id'],'two-items',config)
        self.assertEqual(first['job_state'],'running')
        self.assertEqual(first['items'][0]['status'],'failed')
        second_step=self.ui.import_bestseller_batch(result['batch_id'],'two-items',config)
        self.assertEqual(second_step['job_state'],'partial')
        self.assertEqual([r['status'] for r in second_step['items']],['failed','success'])
        self.assertEqual(second_step['items'][1]['item_id'],'MLM2')

    def test_bestseller_import_network_error_is_sanitized_and_retryable(self):
        import requests
        preview=self.preview()
        with patch.object(type(self.ui),'_screen_collect',self.collected):
            result=self.ui.screening_execute(preview['id'],preview['digest'])
        config={'platform':'mercado-libre-mx','result_limit':5,'min_sold':100,'min_reviews':0,'min_rating':0}
        with patch.object(type(self.ui),'_bestseller_first_image',side_effect=requests.ConnectionError('private-token')):
            failed=self.ui.import_bestseller_batch(result['batch_id'],'network-error',config)
        self.assertEqual(failed['job_state'],'partial')
        self.assertEqual(failed['items'][0]['status'],'failed')
        self.assertNotIn('private-token',str(failed))
        self.assertEqual(self.ui.import_bestseller_batch(result['batch_id'],'network-error',config),failed)

    def test_bestseller_import_fetches_only_first_image_without_new_snapshot(self):
        import base64
        preview=self.preview()
        with patch.object(type(self.ui),'_screen_collect',self.collected):
            result=self.ui.screening_execute(preview['id'],preview['digest'])
        case=self.ui.browse(result['case_ids'][0]); before=self.ui._wb_snapshot(case).id
        image=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC')
        class Response:
            status=200
            def __init__(self,data): self.data=data
            def json(self): return self.data
            def close(self): pass
        urls=['https://http2.mlstatic.com/a.jpg','https://http2.mlstatic.com/b.jpg']
        def send(method,path,**kwargs):
            return Response({'/users/me':{'id':'8'},'/marketplace/users/8':{'user_id':'8','marketplaces':[{'user_id':'9','site_id':'MLM','logistic_type':self.shop.logistic_type}]},'/marketplace/items/MLM1':{'id':'MLM1','seller_id':'9','site_id':'MLM','pictures':[{'secure_url':u} for u in urls]}}[path])
        mod='odoo.addons.meli_optimization.models.bestseller_import'
        config={'platform':'mercado-libre-mx','result_limit':5,'min_sold':100,'min_reviews':0,'min_rating':0}
        with patch.object(type(self.shop),'_token',return_value='synthetic'),patch(mod+'.transport.send',side_effect=send),patch(mod+'.fetch',return_value=(image,'image/png')) as download:
            completed=self.ui.import_bestseller_batch(result['batch_id'],'first-image',config)
        self.assertEqual(completed['job_state'],'completed')
        self.assertEqual(download.call_count,1)
        self.assertEqual(download.call_args.args[0],urls[0])
        self.assertEqual(self.ui._wb_snapshot(case).id,before)

    def test_bestseller_first_image_uses_explicit_proxy(self):
        import base64
        preview=self.preview()
        with patch.object(type(self.ui),'_screen_collect',self.collected):
            result=self.ui.screening_execute(preview['id'],preview['digest'])
        case=self.ui.browse(result['case_ids'][0]); image=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC')
        class Response:
            status=200
            def __init__(self,data): self.data=data
            def json(self): return self.data
            def close(self): pass
        def send(method,path,**kwargs):
            return Response({'/users/me':{'id':'8'},'/marketplace/users/8':{'user_id':'8','marketplaces':[{'user_id':'9','site_id':'MLM','logistic_type':self.shop.logistic_type}]},'/marketplace/items/MLM1':{'id':'MLM1','seller_id':'9','site_id':'MLM','pictures':[{'secure_url':'https://http2.mlstatic.com/a.jpg'}]}}[path])
        def fetch(url,get):
            get(url,timeout=1)
            return image,'image/png'
        mod='odoo.addons.meli_optimization.models.bestseller_import'
        with patch.object(type(self.shop),'_token',return_value='synthetic'),patch(mod+'.transport.send',side_effect=send),patch(mod+'.fetch',side_effect=fetch),patch(mod+'.requests.get') as get,patch.dict('os.environ',{'MERCADO_API_HTTPS_PROXY':'http://127.0.0.1:7890'}):
            self.ui._bestseller_first_image(case,self.shop,'MLM1')
        self.assertEqual(get.call_args.kwargs['proxies'],{'https':'http://127.0.0.1:7890'})
    def setUp(self):
        super().setUp()
        operator = self.env['res.users'].create({'name': 'Screen operator', 'login': 'screen-operator', 'group_ids': [(6, 0, [self.env.ref(g).id for g in ('meli_bff.group_reader', 'meli_optimization.group_editor', 'meli_oerp.group_mercadolibre_manager')])]})
        self.ui = self.env['meli.optimization.case'].with_user(operator)
        self.shop = self.env['meli.independent.store'].create({'name': 'Screen shop', 'seller_id': '8', 'marketplace_seller_id': '9', 'site': 'MLM'})
        self.criteria = dict(site_id='MLM', timezone='America/Mexico_City', start='2026-11-01T00:00:00-06:00', end='2026-11-08T00:00:00-06:00', sales_max=2, stock_min=10, sales_policy='paid_gross_units')

    def preview(self):
        self.assertTrue(hasattr(self.ui, 'screening_preview'), 'preview operation missing')
        return self.ui.screening_preview(self.shop.id, 'stable-request', self.criteria)

    def collected(self, *args):
        return ([dict(id='MLM1', seller_id='9', site_id='MLM', title='Dress', available_quantity=20, variations=[])], [], dict(orders_complete=True, provider='isolated-test'))

    def test_historical_gallery_shop_resolution_is_exact_and_authorized(self):
        detail = self.ui.workbench_create({'shop': self.shop.name, 'site_id': 'MLM', 'item_id': 'MLM1', 'title': 'Dress'})
        case = self.ui.browse(detail['id'])
        with patch.object(type(self.ui), '_screen_shop', return_value=self.shop) as authorize:
            self.assertEqual(self.ui._gallery_shop(case, detail['snapshot']['payload']), self.shop)
            authorize.assert_called_once_with(self.shop.id)
        with patch.object(type(self.ui), '_screen_shop', side_effect=AccessError('denied')):
            with self.assertRaises(AccessError): self.ui._gallery_shop(case, detail['snapshot']['payload'])
        with self.assertRaises(ValidationError): self.ui._gallery_shop(case, dict(detail['snapshot']['payload'], site_id='MLB'))
        other = self.env['res.company'].create({'name': 'Other gallery company'})
        self.env['meli.independent.store'].create({'name': self.shop.name, 'seller_id': '8', 'marketplace_seller_id': '9', 'site': 'MLM', 'company_id': other.id})
        with patch.object(type(self.ui), '_screen_shop', return_value=self.shop):
            self.assertEqual(self.ui._gallery_shop(case, detail['snapshot']['payload']), self.shop)
        self.env['meli.independent.store'].create({'name': self.shop.name, 'seller_id': '8', 'marketplace_seller_id': '9', 'site': 'MLM'})
        with self.assertRaises(ValidationError): self.ui._gallery_shop(case, detail['snapshot']['payload'])

    def test_snapshot_gallery_sync_fences_platform_and_preserves_order(self):
        import base64
        preview = self.preview()
        with patch.object(type(self.ui), '_screen_collect', self.collected):
            result = self.ui.screening_execute(preview['id'], preview['digest'])
        case = self.ui.browse(result['case_ids'][0])
        current = self.ui.workbench_detail(case.id)
        urls = ['https://http2.mlstatic.com/a.jpg', 'https://http2.mlstatic.com/b.jpg']
        product = {'id':'MLM1', 'seller_id':'9', 'site_id':'MLM', 'pictures':[{'secure_url':u} for u in urls]}
        image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC')
        class Response:
            status = 200
            def __init__(self, data): self.data = data
            def json(self): return self.data
            def close(self): pass
        def send(method, path, **kwargs):
            return Response({'/users/me':{'id':'8'}, '/marketplace/users/8':{'user_id':'8','marketplaces':[{'user_id':'9','site_id':'MLM','logistic_type':self.shop.logistic_type}]}, '/marketplace/items/MLM1':product}[path])
        downloads=[]
        def fetch(url, get): downloads.append(url); return image, 'image/png'
        mod='odoo.addons.meli_optimization.models.snapshot_gallery'
        before=self.env['meli.optimization.artifact'].search_count([('case_id','=',case.id)])
        with patch.object(type(self.shop), '_token', return_value='synthetic'), patch(mod+'.transport.send', side_effect=send), patch(mod+'.fetch', side_effect=[(image,'image/png'), ValueError('download failed')]):
            with self.assertRaises(ValidationError): self.ui.workbench_archive_snapshot_gallery(case.id, current['revision'])
        self.assertEqual(self.env['meli.optimization.artifact'].search_count([('case_id','=',case.id)]),before)
        self.assertEqual(self.ui.workbench_detail(case.id)['snapshot']['id'], current['snapshot']['id'])
        with self.assertRaises(ValidationError): self.ui.workbench_archive_snapshot_gallery(case.id, current['revision']-1)
        with patch.object(type(self.shop), '_token', return_value='synthetic'), patch(mod+'.transport.send', side_effect=send), patch(mod+'.fetch', side_effect=fetch):
            updated=self.ui.workbench_archive_snapshot_gallery(case.id, current['revision'])
        self.assertEqual(downloads, urls)
        self.assertEqual(updated['snapshot']['payload']['pictures'], product['pictures'])
        self.assertEqual(len(updated['snapshot']['payload']['source_image_artifact_ids']), 2)
        self.assertNotEqual(updated['snapshot']['id'], current['snapshot']['id'])

    def test_snapshot_gallery_rejects_wrong_seller_without_artifacts(self):
        preview = self.preview()
        with patch.object(type(self.ui), '_screen_collect', self.collected):
            result = self.ui.screening_execute(preview['id'], preview['digest'])
        case=self.ui.browse(result['case_ids'][0]); current=self.ui.workbench_detail(case.id)
        class Response:
            status=200
            def __init__(self, data): self.data=data
            def json(self): return self.data
            def close(self): pass
        def send(method,path,**kwargs):
            return Response({'/users/me':{'id':'8'}, '/marketplace/users/8':{'user_id':'8','marketplaces':[{'user_id':'9','site_id':'MLM','logistic_type':self.shop.logistic_type}]}, '/marketplace/items/MLM1':{'id':'MLM1','seller_id':'99','site_id':'MLM','pictures':[{'secure_url':'https://http2.mlstatic.com/a.jpg'}]}}[path])
        mod='odoo.addons.meli_optimization.models.snapshot_gallery'
        before=self.env['meli.optimization.artifact'].search_count([('case_id','=',case.id)])
        with patch.object(type(self.shop), '_token', return_value='synthetic'), patch(mod+'.transport.send', side_effect=send), patch(mod+'.fetch') as fetch:
            with self.assertRaises(ValidationError): self.ui.workbench_archive_snapshot_gallery(case.id, current['revision'])
            fetch.assert_not_called()
        self.assertEqual(self.env['meli.optimization.artifact'].search_count([('case_id','=',case.id)]),before)
        self.assertEqual(self.ui.workbench_detail(case.id)['snapshot']['id'], current['snapshot']['id'])

    def test_preview_execution_idempotency_and_visibility(self):
        preview = self.preview()
        self.assertEqual(preview['criteria'], self.criteria)
        with patch.object(type(self.ui), '_screen_collect', self.collected):
            result = self.ui.screening_execute(preview['id'], preview['digest'])
            again = self.ui.screening_execute(preview['id'], preview['digest'])
        self.assertEqual(result, again)
        case = self.ui.browse(result['case_ids'])
        self.assertEqual(case.shop_id, self.shop)
        self.assertEqual(self.ui.workbench_overview(batch_id=result['batch_id'])['total'], 1)
        with self.assertRaises(ValidationError):
            self.ui.screening_preview(self.shop.id, 'stable-request', dict(self.criteria, sales_max=3))

    def test_screening_arranges_and_archives_facts_for_every_product(self):
        from ..domain import canonical_hash, validate_result
        preview = self.preview()
        def collect(*args):
            products, orders, source = self.collected()
            products[0]['seller_custom_field'] = None
            products.append(dict(products[0], id='MLM2', title='Second dress'))
            return products, orders, source
        with patch.object(type(self.ui), '_screen_collect', collect):
            result = self.ui.screening_execute(preview['id'], preview['digest'])
            self.assertEqual(self.ui.screening_execute(preview['id'], preview['digest']), result)
        self.assertEqual(result['matched'], 2)
        for case_id in result['case_ids']:
            detail = self.ui.workbench_detail(case_id)
            self.assertEqual(len(detail['tasks']), 2)
            task = next(t for t in detail['tasks'] if t['state'] == 'succeeded')
            enhanced = next(t for t in detail['tasks'] if t['state'] == 'queued')
            self.assertEqual(enhanced['task_type'], 'product.facts')
            self.assertEqual(enhanced['snapshot_id'], detail['snapshot']['id'])
            self.assertEqual(self.env['meli.optimization.task'].browse(enhanced['id']).payload['screening_visual_enrichment'], preview['id'])
            self.assertEqual((task['task_type'], task['state']), ('product.facts', 'succeeded'))
            self.assertEqual(task['snapshot_id'], detail['snapshot']['id'])
            validate_result('product.facts', task['result'])
            facts = {row['name']: row['value'] for row in task['result']['facts']}
            self.assertEqual(facts, detail['snapshot']['payload'])
            self.assertIsNone(facts['seller_custom_field'], 'Missing SKU must remain unknown')
            self.assertNotIn('seller_sku', facts)
            self.assertEqual(task['result']['evidence'][0]['source'], 'snapshot:' + str(task['snapshot_id']))
            self.assertIn('external truth has not been verified', task['result']['uncertainties'][0])
            self.assertEqual(len(detail['artifacts']), 1)
            artifact = detail['artifacts'][0]
            self.assertEqual(artifact['payload'], task['result'])
            self.assertEqual(artifact['result_task_id'][0], task['id'])
            self.assertEqual(artifact['result_snapshot_id'][0], task['snapshot_id'])
            self.assertEqual(artifact['digest'], canonical_hash(task['result']))
            self.assertEqual(artifact['result_hash'], task['result_hash'])
            self.assertEqual(self.ui.workbench_archive(case_id, task['id'], task['result_hash']), artifact['id'])
            self.assertEqual(len(detail['events']), 1)
            self.assertEqual(detail['events'][0]['kind'], 'complete')
            self.assertEqual(detail['events'][0]['receipt']['execution'], 'local_snapshot')
            self.assertEqual(detail['events'][0]['receipt']['screening_id'], preview['id'])
            stored_task = self.env['meli.optimization.task'].browse(task['id'])
            self.assertFalse(stored_task.lease_token)
            self.assertEqual(stored_task.lease_epoch, 0)
            self.assertFalse(stored_task.claim(['product.facts']), 'Local facts must not enter the worker queue')
            self.assertFalse(self.env['meli.optimization.attempt'].search([('task_id', '=', task['id'])]))
            keyword_id = self.ui.workbench_task(case_id, 'research.keywords', {'facts_artifact_id': artifact['id'], 'site_id': 'MLM'})
            self.assertEqual(self.env['meli.optimization.task'].browse(keyword_id).payload['facts_artifact_id'], str(artifact['id']))

    def test_screening_visual_facts_auto_archive_is_idempotent(self):
        preview = self.preview()
        with patch.object(type(self.ui), '_screen_collect', self.collected):
            result = self.ui.screening_execute(preview['id'], preview['digest'])
        case = self.ui.browse(result['case_ids'])
        tasks = self.env['meli.optimization.task'].search([('case_id', '=', case.id), ('state', '=', 'queued')])
        self.assertEqual(len(tasks), 1)
        claim = tasks.claim(['product.facts'])
        from ..domain.facts import product_facts_from_snapshot
        body = product_facts_from_snapshot({'snapshot_id': tasks.snapshot_id.id, 'payload': tasks.snapshot_id.payload})
        tasks.complete(claim['lease_token'], claim['lease_epoch'], 'visual-complete', body)
        self.assertEqual(self.env['meli.optimization.artifact'].search_count([('result_task_id', '=', tasks.id)]), 1,
                         'Completing the worker task must automatically archive its result')
        tasks._run_derived()
        artifacts = self.env['meli.optimization.artifact'].search([('result_task_id', '=', tasks.id)])
        self.assertEqual(len(artifacts), 1)
        self.assertFalse(self.env['meli.optimization.task'].search([('case_id', '=', case.id), ('task_type', '=', 'research.keywords')]))

    def test_facts_archive_failure_rolls_back_entire_screening_and_retry(self):
        preview = self.preview()
        names = ('batch', 'case', 'snapshot', 'task', 'artifact', 'event')
        counts = {name: self.env['meli.optimization.' + name].search_count([]) for name in names}
        with patch.object(type(self.ui), '_screen_collect', self.collected):
            with patch.object(type(self.ui), 'workbench_archive', side_effect=ValidationError('forced archive failure')):
                with self.assertRaises(ValidationError):
                    self.ui.screening_execute(preview['id'], preview['digest'])
            for name in names:
                self.assertEqual(self.env['meli.optimization.' + name].search_count([]), counts[name], name)
            self.assertFalse(self.env['meli.optimization.screening'].browse(preview['id']).batch_id)
            result = self.ui.screening_execute(preview['id'], preview['digest'])
        self.assertEqual(len(self.ui.workbench_detail(result['case_ids'][0])['artifacts']), 1)

    def test_all_sizes_criteria_and_evidence_persisted(self):
        self.criteria.update(require_all_skus_in_stock=True, sales_max=1, stock_min=0)
        preview = self.preview()
        self.assertIn('every listed SKU', preview['semantics'])
        def collect(*args):
            return ([dict(id='MLM1', seller_id='9', site_id='MLM', title='Dress', variations=[dict(id='small', available_quantity=2, attribute_combinations=[dict(id='SIZE', value_name='S')]), dict(id='medium', available_quantity=1, attribute_combinations=[dict(id='SIZE', value_name='M')])])], [], dict(orders_complete=True, provider='isolated-test'))
        with patch.object(type(self.ui), '_screen_collect', collect):
            outcome = self.ui.screening_execute(preview['id'], preview['digest'])
        payload = self.ui.workbench_detail(outcome['case_ids'][0])['snapshot']['payload']
        self.assertTrue(payload['screening_criteria']['require_all_skus_in_stock'])
        self.assertEqual([row['size']['value_name'] for row in payload['sku_size_evidence']], ['S', 'M'])
        self.assertEqual(payload['sales_units'], 0)
        facts = self.ui.workbench_detail(outcome['case_ids'][0])['artifacts'][0]['payload']['facts']
        self.assertEqual(next(row['value'] for row in facts if row['name'] == 'sku_size_evidence'), payload['sku_size_evidence'])

    def test_execute_checkpoints_progress_without_exposing_private_cache(self):
        from ..domain.screening_resume import CollectionPending
        preview = self.preview()
        def collect(model, shop, criteria, checkpoint=None):
            if not checkpoint.get('responses'):
                checkpoint['responses'] = [{'path': '/users/me', 'query': [], 'data': {'id': 8}, 'read_started_at': datetime.now(timezone.utc).isoformat(), 'observed_at': datetime.now(timezone.utc).isoformat()}]
                raise CollectionPending()
            return self.collected()
        with patch.object(type(self.ui), '_screen_collect', collect):
            running = self.ui.screening_execute(preview['id'], preview['digest'])
            self.assertEqual(running['job_state'], 'running')
            self.assertNotIn('job_checkpoint', running)
            self.assertNotIn('job_checkpoint', self.ui.screening_history()[0]['outcome'])
            result = self.ui.screening_execute(preview['id'], preview['digest'])
            self.assertEqual(result['matched'], 1)
            self.assertEqual(self.ui.screening_execute(preview['id'], preview['digest']), result)

    def test_concurrent_step_returns_progress_without_remote_call(self):
        from odoo import sql_db
        preview = self.preview()
        with sql_db.db_connect(self.env.cr.dbname).cursor() as other:
            other.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', [f"screen-step:{preview['id']}"])
            with patch.object(type(self.ui), '_screen_collect', side_effect=AssertionError('duplicate remote fetch')):
                state = self.ui.screening_execute(preview['id'], preview['digest'])
            self.assertEqual(state['code'], 'step_in_progress')
        with patch.object(type(self.ui), '_screen_collect', self.collected):
            self.assertEqual(self.ui.screening_execute(preview['id'], preview['digest'])['matched'], 1)

    def test_failed_collection_retries_fresh_without_duplicate_batch(self):
        preview = self.preview()
        checkpoints = []
        def collect(model, shop, criteria, checkpoint):
            checkpoints.append(dict(checkpoint))
            if len(checkpoints) == 1:
                checkpoint['responses'] = [{'unverified': True}]
                raise ValueError('source changed')
            return self.collected()
        with patch.object(type(self.ui), '_screen_collect', collect):
            failed = self.ui.screening_execute(preview['id'], preview['digest'])
            self.assertEqual(failed['job_state'], 'failed')
            result = self.ui.screening_execute(preview['id'], preview['digest'])
        self.assertEqual(checkpoints[1], {'variant_schema':1})
        self.assertEqual(result['matched'], 1)

    def test_expired_checkpoint_fails_without_recollection_or_batch(self):
        from datetime import datetime, timedelta, timezone
        preview = self.preview()
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        row = self.env['meli.optimization.screening'].browse(preview['id'])
        row._set({'outcome': {'job_state': 'running', 'job_checkpoint': {'responses': [{'path': '/users/me', 'query': [], 'data': {'id': 8}, 'read_started_at': old, 'observed_at': old}]}}})
        with patch.object(type(self.ui), '_screen_collect', side_effect=AssertionError('expired checkpoint must not fetch')):
            failed = self.ui.screening_execute(preview['id'], preview['digest'])
        self.assertEqual(failed['code'], 'checkpoint_expired')
        self.assertFalse(row.batch_id)
        with patch.object(type(self.ui), '_screen_collect', self.collected):
            self.assertEqual(self.ui.screening_execute(preview['id'], preview['digest'])['matched'], 1)

    def test_final_restart_uses_last_actual_read_time(self):
        from datetime import datetime, timedelta, timezone
        preview = self.preview()
        observed = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
        row = self.env['meli.optimization.screening'].browse(preview['id'])
        row._set({'outcome': {'job_state': 'running', 'job_checkpoint': {'variant_schema':1,'responses': [{'path': '/users/me', 'query': [], 'data': {'id': 8}, 'read_started_at': observed, 'observed_at': observed}]}}})
        with patch.object(type(self.ui), '_screen_collect', self.collected):
            result = self.ui.screening_execute(preview['id'], preview['digest'])
        self.assertEqual(result['observed_at'], observed)
        self.assertGreater(result['committed_at'], observed)

    def test_atomic_batch_rollback(self):
        preview = self.preview()
        count = self.env['meli.optimization.batch'].search_count([])
        with patch.object(type(self.ui), '_screen_collect', self.collected), patch.object(type(self.env['meli.optimization.snapshot']), 'create', side_effect=ValidationError('forced snapshot failure')):
            with self.assertRaises(ValidationError): self.ui.screening_execute(preview['id'], preview['digest'])
        self.assertEqual(self.env['meli.optimization.batch'].search_count([]), count)
        self.assertFalse(self.env['meli.optimization.screening'].browse(preview['id']).batch_id)

    def test_digest_and_company_boundary(self):
        preview = self.preview()
        with self.assertRaises(ValidationError): self.ui.screening_execute(preview['id'], 'wrong')
        company = self.env['res.company'].create({'name': 'Other screen company'})
        worker = self.env['res.users'].create({'name': 'Worker', 'login': 'screen-worker', 'company_id': company.id, 'company_ids': [(6, 0, company.ids)], 'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_editor').id, self.env.ref('meli_bff.group_reader').id])]})
        with self.assertRaises(AccessError): self.ui.with_user(worker).with_context(allowed_company_ids=company.ids).screening_execute(preview['id'], preview['digest'])

    def test_live_provider_rejects_wrong_parent_token_before_search(self):
        from types import SimpleNamespace
        from ..models import screening
        with patch.object(type(self.shop), '_token', return_value='isolated-token'), patch.object(screening.transport, 'send', return_value=SimpleNamespace(status=200, json=lambda: {'id': 'wrong'})) as send:
            with self.assertRaises(ValidationError):
                self.ui._screen_collect(self.shop, self.criteria)
            self.assertEqual(send.call_args.args[1], '/users/me')

    def test_case_shop_link_cannot_cross_company(self):
        company = self.env['res.company'].create({'name': 'Foreign screening shop'})
        shop = self.env['meli.independent.store'].create({'name': 'Foreign', 'company_id': company.id})
        batch = self.env['meli.optimization.batch'].create({'name': 'Current company'})
        with self.assertRaises(AccessError):
            self.ui.create({'name': 'Forged ownership', 'batch_id': batch.id, 'shop_id': shop.id})


from datetime import timedelta
from odoo import fields
from odoo.tests import HttpCase


@tagged('post_install', '-at_install', 'optimization_http')
class ScreeningHTTPCase(HttpCase):
    def test_preview_execute_history_authenticated_transport(self):
        user = self.env['res.users'].create({'name': 'Screen HTTP', 'login': 'screen-http', 'group_ids': [(6, 0, [self.env.ref(g).id for g in ('meli_bff.group_reader', 'meli_optimization.group_editor', 'meli_oerp.group_mercadolibre_manager')])]})
        key = self.env['res.users.apikeys'].with_user(user).sudo()._generate('rpc', 'test only', fields.Datetime.now() + timedelta(hours=1))
        headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
        shop = self.env['meli.independent.store'].create({'name': 'Screen HTTP shop', 'seller_id': '8', 'marketplace_seller_id': '9', 'site': 'MLM'})
        criteria = dict(site_id='MLM', timezone='America/Mexico_City', start='2026-11-01T00:00:00-06:00', end='2026-11-08T00:00:00-06:00', sales_max=2, stock_min=10, sales_policy='paid_gross_units')
        preview = self.url_open('/api/meli/optimization/screening/preview', json=dict(shop_id=shop.id, request_key='http-stable', criteria=criteria), headers=headers)
        self.assertEqual(preview.status_code, 200, preview.text)
        row = preview.json()['data']
        path = f"/api/meli/optimization/screening/{row['id']}/execute"
        wrong = self.url_open(path, json={'digest': 'changed'}, headers=headers)
        self.assertEqual(wrong.status_code, 409, wrong.text)
        def collect(*args):
            from ..domain.screening_resume import CollectionPending
            if not args[-1].get('responses'):
                args[-1]['responses'] = [{'path': '/users/me', 'query': [], 'data': {'id': 8}, 'read_started_at': datetime.now(timezone.utc).isoformat(), 'observed_at': datetime.now(timezone.utc).isoformat()}]
                raise CollectionPending()
            return ([dict(id='MLM1', seller_id='9', site_id='MLM', title='Dress', available_quantity=20, variations=[])], [], dict(orders_complete=True, provider='isolated-test'))
        with patch.object(type(self.env['meli.optimization.case']), '_screen_collect', collect):
            pending = self.url_open(path, json={'digest': row['digest']}, headers=headers)
            self.assertEqual(pending.json()['data']['job_state'], 'running')
            self.assertNotIn('job_checkpoint', pending.text)
            executed = self.url_open(path, json={'digest': row['digest']}, headers=headers)
        self.assertEqual(executed.status_code, 200, executed.text)
        self.assertEqual(executed.json()['data']['matched'], 1)
        history = self.url_open('/api/meli/optimization/screening/history', data='{}', headers=headers)
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()['data'][0]['batch_id'], executed.json()['data']['batch_id'])

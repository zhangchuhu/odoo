"""Actual native action smoke test against a disposable database, no platform traffic."""
from unittest.mock import patch
from pathlib import Path
from odoo.tests import HttpCase, tagged
from odoo.tests.common import ChromeBrowser
from ..domain import canonical_hash


@tagged('post_install', '-at_install', 'optimization_browser')
class WorkbenchBrowserCase(HttpCase):
    browser_size = '1440x1000'

    def test_variant_create_dialog_separates_two_colors(self):
        from odoo import api
        from ..domain.variant_groups import traditional_groups
        self.env.ref('base.user_admin').write({'group_ids':[(4,self.env.ref('meli_optimization.group_editor').id),(4,self.env.ref('meli_bff.group_reader').id)]})
        shop=self.env['meli.independent.store'].create({'name':'Variant browser shop','seller_id':'8','marketplace_seller_id':'9','site':'MLM','state':'connected'})
        parent={'pictures':[{'id':'p1','secure_url':'https://http2.mlstatic.com/a.jpg'},{'id':'p2','secure_url':'https://http2.mlstatic.com/b.jpg'}],
                'variations':[{'id':11,'attribute_combinations':[{'id':'COLOR','value_name':'Violeta'},{'id':'SIZE','value_name':'S'}],'picture_ids':['p1'],'available_quantity':2},
                              {'id':12,'attribute_combinations':[{'id':'COLOR','value_name':'Gray'},{'id':'SIZE','value_name':'M'}],'picture_ids':['p2'],'available_quantity':3}]}
        def product(shop,item):return {'item_id':item,'parent_id':'CBT1','site':'MLM','title':'Variant fixture','model':'traditional','groups':traditional_groups(parent)}
        action=self.env.ref('meli_optimization.workbench_action').id
        spawn=ChromeBrowser._spawn_chrome
        def fixed(browser,cmd):
            Path(browser.user_data_dir,'DevToolsActivePort').write_text('55441\n/devtools/browser\n')
            return spawn(browser,cmd)
        ui=self.env['meli.optimization.case']
        @api.model
        def fake_sync(self,case_id,revision):return True
        with patch.object(type(ui),'_screen_shop',return_value=shop),patch.object(type(ui),'_variant_read_product',side_effect=product),patch.object(type(ui),'workbench_archive_snapshot_gallery',new=fake_sync),patch.object(ChromeBrowser,'remote_debugging_port',55441),patch.object(ChromeBrowser,'_spawn_chrome',fixed):
            self.browser_js('/odoo/action-%s'%action, """
              (async()=>{const wait=async f=>{for(let i=0;i<150;i++){let x=f();if(x)return x;await new Promise(r=>setTimeout(r,100))}throw Error('variant timeout')};
              const button=t=>[...document.querySelectorAll('.meli-workbench button')].find(b=>b.textContent.includes(t));
              (await wait(()=>button('新建优化'))).click();const sel=await wait(()=>[...document.querySelectorAll('.mw-modal select')].find(e=>[...e.options].some(o=>o.textContent.includes('Variant browser shop'))));
              sel.value=[...sel.options].find(o=>o.textContent.includes('Variant browser shop')).value;sel.dispatchEvent(new Event('change',{bubbles:true}));
              const input=document.querySelector('.mw-modal input[placeholder="MLM1234567890"]');input.value='MLM1';input.dispatchEvent(new Event('input',{bubbles:true}));
              (await wait(()=>{let b=button('读取商品');return b&&!b.disabled&&b})).click();await wait(()=>document.querySelector('.mw-modal')?.textContent.includes('Violeta'));
              if(!document.querySelector('.mw-modal').textContent.includes('Gray'))throw Error('第二个变体缺失');
              (await wait(()=>{let b=button('创建优化记录');return b&&!b.disabled&&b})).click();await wait(()=>document.querySelector('.mw-modal')?.textContent.includes('已创建，原图同步完成'));
              if([...document.querySelectorAll('.mw-modal p')].filter(e=>e.textContent.includes('已创建，原图同步完成')).length!==2)throw Error('变体未分开创建');
              console.log('test successful')})().catch(e=>console.error(e));
            """,login='admin',timeout=50)

    def test_confirm_and_publish_user_product_through_workbench(self):
        import base64, hashlib
        from types import SimpleNamespace
        admin=self.env.ref('base.user_admin')
        admin.write({'group_ids':[(4,self.env.ref('meli_optimization.group_editor').id),(4,self.env.ref('meli_bff.group_writer').id)]})
        ui=self.env['meli.optimization.case']
        detail=ui.workbench_create({'shop':'Browser test','item_id':'MLM123','title':'Publish browser fixture'})
        raw=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC')
        digest=hashlib.sha256(raw).hexdigest()
        artifact=self.env['meli.optimization.artifact'].create({'case_id':detail['id'],'data':base64.b64encode(raw),'digest':digest,'mime_type':'image/png'})
        payload={'operation':'garment_swap','case_id':detail['id'],'snapshot_id':detail['snapshot']['id'],
                 'selected_indices':[0],'source_artifact_ids':[str(artifact.id)],'target_artifact_ids':[str(artifact.id)]}
        plan=self.env['meli.optimization.artifact'].create({'case_id':detail['id'],'payload':payload,'digest':canonical_hash(payload)})
        task=self.env['meli.optimization.task'].create({'case_id':detail['id'],'snapshot_id':detail['snapshot']['id'],'task_type':'image.generate',
            'payload':{'schema_version':'1','source_artifact_ids':[str(artifact.id)],'target_artifact_id':str(artifact.id),'generation_plan_id':str(plan.id)}})
        claim=task.claim(['image.generate']);image={'image_id':'generated','order':0,'role':'front','artifact_id':str(artifact.id),'source':'fixture',
            'evidence_ids':['e'],'mime_type':'image/png','width':1,'height':1,'sha256':digest}
        task.complete(claim['lease_token'],claim['lease_epoch'],'publish-browser',{'schema_version':'1','request_id':'browser',
            'evidence':[{'id':'e','source':'fixture','observed_at':'2026-11-15T00:00:00Z'}],'image':image,
            'visual_qc':{'status':'passed','candidate_sha256':digest,'report':{'decision':'accept'}}})
        from datetime import timedelta
        from odoo import fields
        other_plan=self.env['meli.optimization.artifact'].create({'case_id':detail['id'],'payload':payload,'digest':canonical_hash(payload)})
        expired=self.env['meli.optimization.task'].create({'case_id':detail['id'],'snapshot_id':detail['snapshot']['id'],'task_type':'image.generate',
            'payload':{'schema_version':'1','source_artifact_ids':[str(artifact.id)],'target_artifact_id':str(artifact.id),'generation_plan_id':str(other_plan.id)}})
        c2=expired.claim(['image.generate']);expired.checkpoint(c2['lease_token'],c2['lease_epoch'],'old',{'request_id':'expired-plan','state':'unknown'})
        expired._set({'lease_expires_at':fields.Datetime.now()-timedelta(seconds=1)})
        shop=SimpleNamespace(id=1,name='Browser test',site='MLM',seller_id='11',marketplace_seller_id='22',logistic_type='remote',company_id=self.env.company,_token=lambda:'fixture')
        responses={'/users/me':{'id':11},'/marketplace/users/11':{'user_id':11,'marketplaces':[{'user_id':22,'site_id':'MLM','logistic_type':'remote'}]},
            '/marketplace/items/MLM123':{'id':'MLM123','site_id':'MLM','seller_id':22,'cbt_item_id':'CBT456','user_product_id':'MLMU999','tags':['user_product_listing']},
            '/marketplace/items/CBT456':{'id':'CBT456','site_id':'CBT','seller_id':11,'category_id':'CBT321','marketplace_items':[{'item_id':'MLM123','site_id':'MLM','user_id':22,'logistic_type':'remote','parent_id':'CBT456','parent_user_id':11,'user_product_id':'MLMU999','siteless_user_product_id':'U999'}]},
            '/categories/CBT321':{'id':'CBT321','settings':{'max_pictures_per_item':12}},'/user-products/U999':{'id':'U999','user_id':11,'pictures':[{'id':'111-CBT123_012026'}]}}
        calls=[]
        switched=[False]
        def read(shop,path):
            if path == '/users/me' and ('PUT','/global/user-products/U999') in calls and not switched[0]:
                switched[0]=True
                responses['/marketplace/items/MLM123']['tags']=[]
                parent=responses['/marketplace/items/CBT456']
                parent['pictures']=[{'id':'111-CBT123_012026'},{'id':'222-CBT123_012026'}]
                parent['variations']=[{'id':12,'seller_custom_field':'BLACK-S','picture_ids':['111-CBT123_012026']},
                                      {'id':13,'seller_custom_field':'BLACK-M','picture_ids':['222-CBT123_012026']}]
                responses['/categories/CBT321']['settings']['max_pictures_per_item_var']=6
            return responses[path]
        def send(method,path,**kwargs):
            calls.append((method,path))
            if path == '/pictures/items/upload': data={'id':'333-CBT123_012026' if switched[0] else '222-CBT123_012026'}
            elif path == '/items/CBT456/pictures':
                responses['/marketplace/items/CBT456']['pictures'].append({'id':'333-CBT123_012026'});data={'id':'333-CBT123_012026'}
            elif path == '/global/items/CBT456':
                import json
                sent=json.loads(kwargs['body']);parent=responses['/marketplace/items/CBT456'];parent['pictures']=sent['pictures']
                parent['variations'][0]['picture_ids']=['333-CBT123_012026'];data={'success':True}
            else: responses['/user-products/U999']['pictures']=[{'id':'222-CBT123_012026'}];data={'success':True}
            return SimpleNamespace(status=200,json=lambda:data,close=lambda:None)
        spawn=ChromeBrowser._spawn_chrome
        def fixed(browser,cmd):
            Path(browser.user_data_dir,'DevToolsActivePort').write_text('55441\n/devtools/browser\n')
            return spawn(browser,cmd)
        action=self.env.ref('meli_optimization.workbench_action').id
        with patch.object(type(ui),'_gallery_shop',return_value=shop), patch.object(type(ui),'_image_publish_read',side_effect=read), patch('odoo.addons.meli_optimization.models.image_publish.transport.send',side_effect=send), patch.object(ChromeBrowser,'remote_debugging_port',55441), patch.object(ChromeBrowser,'_spawn_chrome',fixed):
            self.browser_js('/odoo/action-%s' % action, """
                (async()=>{const wait=async f=>{for(let i=0;i<150;i++){let x=f();if(x)return x;await new Promise(r=>setTimeout(r,100));}throw Error('publish UI timeout')};
                const button=t=>[...document.querySelectorAll('.meli-workbench button')].find(b=>b.textContent.includes(t));
                const row=await wait(()=>[...document.querySelectorAll('.mw-table tbody tr')].find(e=>e.textContent.includes('Publish browser fixture')));row.querySelector('.mw-link').click();
                (await wait(()=>button('图片与质检'))).click();
                const expired=await wait(()=>[...document.querySelectorAll('.mw-image-plan .mw-candidate')].find(e=>e.textContent.includes('租约已过期')));
                if(!expired.closest('.mw-image-plan')?.textContent.includes('计划 #'))throw Error('任务未归入计划');
                expired.querySelector('button').click();(await wait(()=>button('确认重新生成'))).click();
                await wait(()=>!document.querySelector('.mw-modal'));
                const complete=await wait(()=>[...document.querySelectorAll('.mw-image-plan')].find(e=>e.textContent.includes('已完成') && e.textContent.includes('成功 1 张')));
                complete.querySelector('.mw-selection button.primary').click();
                (await wait(()=>button('确认以上确切方案'))).click();(await wait(()=>button('核验平台发布范围'))).click();
                (await wait(()=>button('确认替换并发布'))).click();await wait(()=>document.body.textContent.includes('已发布并核验'));
                (await wait(()=>button('核验平台发布范围'))).click();
                const sku=await wait(()=>document.querySelector('.mw-preview select'));sku.value='12';sku.dispatchEvent(new Event('change',{bubbles:true}));
                (await wait(()=>{const b=button('核验所选 SKU');return b && !b.disabled && b})).click();
                (await wait(()=>document.querySelector('.mw-preview input[type=checkbox]'))).click();
                (await wait(()=>{const b=button('确认替换并发布');return b && !b.disabled && b})).click();
                await wait(()=>[...document.querySelectorAll('.mw-task')].filter(e=>e.textContent.includes('已发布并核验')).length===2);
                console.log('test successful');})().catch(e=>console.error(e));
            """,login='admin',timeout=100)
        self.assertEqual(calls,[('POST','/pictures/items/upload'),('PUT','/global/user-products/U999'),
            ('POST','/pictures/items/upload'),('POST','/items/CBT456/pictures'),('PUT','/global/items/CBT456')])

    def test_best_candidate_review_renders_in_images_tab(self):
        import hashlib
        import base64
        self.env.ref('base.user_admin').write({'group_ids': [(4, self.env.ref('meli_optimization.group_editor').id)]})
        ui = self.env['meli.optimization.case']
        detail = ui.workbench_create({'shop': 'Browser fixture', 'item_id': 'BEST-REVIEW-UI', 'title': 'Review fixture'})
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC')
        artifact = self.env['meli.optimization.artifact'].create({'case_id': detail['id'], 'data': base64.b64encode(png),
            'digest': hashlib.sha256(png).hexdigest(), 'mime_type': 'image/png'})
        plan_payload = {'case_id':detail['id'],'snapshot_id':detail['snapshot']['id'],
                        'source_artifact_ids':[str(artifact.id)],'target_artifact_ids':[str(artifact.id)]}
        plan = self.env['meli.optimization.artifact'].create({'case_id':detail['id'],'mime_type':'application/json',
                         'payload':plan_payload,'digest':canonical_hash(plan_payload)})
        task = self.env['meli.optimization.task'].create({'case_id': detail['id'], 'snapshot_id': detail['snapshot']['id'],
            'task_type': 'image.generate', 'payload': {'schema_version': '1', 'source_artifact_ids': [str(artifact.id)],
            'target_artifact_id': str(artifact.id), 'generation_plan_id': str(plan.id)}})
        claim=task.claim(['image.generate']); token,epoch=claim['lease_token'],claim['lease_epoch']
        task.checkpoint(token,epoch,'intent',{'request_id':'browser-review','state':'unknown'})
        task.host_progress(token,epoch,'browser-review','review_required',review_seq=1,review_kind='best_of_budget',
            candidate_artifact_id=artifact.id,candidate_sha256=artifact.digest,candidate_attempt_id='a'*32,candidate_attempt_number=1)
        task.host_review_evidence(token,epoch,{'request_id':'browser-review','review_seq':1,'candidate_artifact_id':artifact.id,
            'candidate_sha256':artifact.digest,'candidate_attempt_id':'a'*32,'candidate_attempt_number':1,'report':{
              'quality_binding':{'request_id':'browser-review','candidate_sha256':artifact.digest},
              'checks':[{'name':name,'status':'failed','evidence':'fixture'} for name in
                        ('garment_construction','color_material','garment_details','identity_pose','accessories','scene_lighting_framing','text_layout')],
              'skill_report':{'decision':'reject'}}})
        ordinary = self.env['meli.optimization.task'].create({'case_id':detail['id'],'snapshot_id':detail['snapshot']['id'],
            'task_type':'image.generate','payload':task.payload})
        claim2=ordinary.claim(['image.generate']); token2,epoch2=claim2['lease_token'],claim2['lease_epoch']
        ordinary.checkpoint(token2,epoch2,'intent2',{'request_id':'browser-ordinary','state':'unknown'})
        ordinary.host_progress(token2,epoch2,'browser-ordinary','review_required',review_seq=1)
        ordinary.host_review_evidence(token2,epoch2,{'request_id':'browser-ordinary','review_seq':1,'candidate_artifact_id':artifact.id,
            'candidate_sha256':artifact.digest,'candidate_attempt_id':'b'*32,'candidate_attempt_number':1,'report':{
              'quality_binding':{'request_id':'browser-ordinary','candidate_sha256':artifact.digest},
              'checks':[{'name':name,'status':'failed','evidence':'fixture'} for name in
                        ('garment_construction','color_material','garment_details','identity_pose','accessories','scene_lighting_framing','text_layout')],
              'skill_report':{'decision':'reject'}}})
        from datetime import timedelta
        from odoo import fields
        expired = self.env['meli.optimization.task'].create({'case_id':detail['id'],'snapshot_id':detail['snapshot']['id'],
            'task_type':'image.generate','payload':task.payload})
        old = expired.claim(['image.generate'])
        expired.checkpoint(old['lease_token'],old['lease_epoch'],'old-image',{'request_id':'browser-expired','state':'unknown'})
        expired._set({'lease_expires_at':fields.Datetime.now()-timedelta(seconds=1)})
        action_id = self.env.ref('meli_optimization.workbench_action').id
        spawn = ChromeBrowser._spawn_chrome
        def fixed_port_spawn(browser, cmd):
            import time
            import urllib.request
            Path(browser.user_data_dir, 'DevToolsActivePort').write_text('55441\n/devtools/browser\n')
            process, port = spawn(browser, cmd)
            for _ in range(100):
                try:
                    with urllib.request.urlopen('http://127.0.0.1:55441/json/version', timeout=1):
                        return process, port
                except OSError:
                    time.sleep(.1)
            raise AssertionError('Chrome debugger did not become ready')
        original_wait = ChromeBrowser._wait_code_ok
        def verify_reload(browser, *args, **kwargs):
            import time
            result = original_wait(browser, *args, **kwargs)
            scroll = browser._websocket_request('Runtime.evaluate', params={'expression': """(async()=>{const el=document.querySelector('.mw-main');el.scrollTop=Math.min(250,el.scrollHeight-el.clientHeight);await new Promise(r=>setTimeout(r,150));return el.scrollTop;})()""",'awaitPromise':True,'returnByValue':True})['result']['value']
            browser._websocket_request('Runtime.evaluate', params={'expression': 'window.__beforeWorkbenchReload = true'})
            browser._websocket_request('Page.reload')
            expression = """Boolean(!window.__beforeWorkbenchReload && document.querySelector('h1')?.textContent.includes('BEST-REVIEW-UI') && [...document.querySelectorAll('.mw-tabs button')].some(b=>b.classList.contains('active') && b.textContent.includes('图片与质检')) && Math.abs(document.querySelector('.mw-main').scrollTop - %s) < 3)""" % scroll
            for _ in range(150):
                response = browser._websocket_request('Runtime.evaluate',params={'expression':expression,'returnByValue':True})
                if response.get('result',{}).get('value') is True:
                    return result
                time.sleep(.1)
            raise AssertionError('Browser reload did not restore product and image tab')
        with patch.object(ChromeBrowser, 'remote_debugging_port', 55441), patch.object(ChromeBrowser, '_spawn_chrome', fixed_port_spawn), patch.object(ChromeBrowser, '_wait_code_ok', verify_reload):
            self.browser_js('/odoo/action-%s' % action_id, r"""
                (async () => {
                    const wait = async fn => { for (let i=0; i<150; i++) { const value=fn(); if(value) return value; await new Promise(r=>setTimeout(r,100)); } throw Error('Review UI timeout'); };
                    const button = text => [...document.querySelectorAll('.meli-workbench button')].find(b=>b.textContent.includes(text));
                    const row=await wait(()=>[...document.querySelectorAll('.mw-table tbody tr')].find(r=>r.textContent.includes('BEST-REVIEW-UI')));
                    row.querySelector('.mw-link').click();
                    await wait(()=>button('图片与质检')); button('图片与质检').click();
                    const image=await wait(()=>document.querySelector('img[alt="待复核最佳候选图"]'));
                    await wait(()=>image.complete && image.naturalWidth>0);
                    const ordinaryImage=await wait(()=>document.querySelector('img[alt="待人工确认的候选图"]'));
                    await wait(()=>ordinaryImage.complete && ordinaryImage.naturalWidth>0);
                    if(!ordinaryImage.closest('.mw-result')?.textContent.includes('尚未通过质检')) throw Error('Pending candidate warning missing');
                    if (!ordinaryImage.closest('.mw-result')?.textContent.includes('待处理')) throw Error('Review state missing');
                    ordinaryImage.closest('.mw-result').querySelector('button').click();
                    await wait(()=>document.querySelector('.mw-modal')?.textContent.includes('确认重新生成'));
                    if(!document.querySelector('.mw-modal').textContent.includes('原任务')) throw Error('Regeneration must reuse task');
                    document.querySelector('[aria-label="关闭对话框"]').click();
                    await wait(()=>!document.querySelector('.mw-modal'));
                    await wait(()=>button('人工确认使用'));button('人工确认使用').click();
                    await wait(()=>document.querySelector('.mw-modal')?.textContent.includes('原质检报告会保留'));
                    if(!document.querySelector('.mw-modal').textContent.includes('原质检报告会保留')) throw Error('Manual accept warning missing');
                    button('确认使用未通过质检的图片').click();
                    await wait(()=>[...document.querySelectorAll('.mw-result')].some(e=>e.textContent.includes('已记录人工处理决定')));
                    const expired=await wait(()=>[...document.querySelectorAll('.mw-result')].find(e=>e.textContent.includes('租约已过期')));
                    expired.querySelector('button').click();
                    await wait(()=>document.querySelector('.mw-modal')?.textContent.includes('原请求可能已经产生图片或费用'));
                    button('确认重新生成').click();
                    await wait(()=>![...document.querySelectorAll('.mw-result')].some(e=>e.textContent.includes('租约已过期')));
                    console.log('test successful');
                })().catch(e=>console.error(e));
            """, login='admin', timeout=40)

    def test_workbench_browser_create_select_confirm(self):
        self.env.ref('base.user_admin').write({'group_ids': [(4, self.env.ref('meli_optimization.group_editor').id)]})
        ui = self.env['meli.optimization.case']
        detail = ui.workbench_create({'shop': '演示店铺', 'item_id': 'MLM-DEMO', 'title': 'Vestido elegante', 'seller_sku': 'SKU-A', 'batch_name': 'TEST BATCH NAV 3'})
        evidence = [{'id':'fixture', 'source':'test fixture', 'observed_at':'2026-11-15T00:00:00Z'}]
        facts = self.env['meli.optimization.task'].browse(ui.workbench_task(detail['id'], 'product.facts', {}))
        claim = facts.claim(['product.facts'])
        facts_result = {'schema_version':'1', 'evidence':evidence, 'facts':[{'name':'title','value':'Vestido elegante','evidence_ids':['fixture']}], 'uncertainties':[]}
        facts.complete(claim['lease_token'], claim['lease_epoch'], 'facts-fixture', facts_result)
        facts_id = ui.workbench_archive(detail['id'], facts.id, canonical_hash(facts_result))
        keywords = self.env['meli.optimization.task'].browse(ui.workbench_task(detail['id'], 'research.keywords', {'facts_artifact_id':str(facts_id),'site_id':'MLM'}))
        claim = keywords.claim(['research.keywords'])
        keywords_result = {'schema_version':'1', 'evidence':evidence, 'status':'complete', 'keywords':[{'text':'Vestido','evidence_ids':['fixture']}]}
        keywords.complete(claim['lease_token'], claim['lease_epoch'], 'keywords-fixture', keywords_result)
        keywords_id = ui.workbench_archive(detail['id'], keywords.id, canonical_hash(keywords_result))
        task = self.env['meli.optimization.task'].browse(ui.workbench_task(detail['id'], 'title.generate', {'facts_artifact_id':str(facts_id), 'keywords_artifact_id':str(keywords_id), 'language':'es-MX'}))
        claim = task.claim(['title.generate'])
        task.complete(claim['lease_token'], claim['lease_epoch'], 'browser-fixture', {'schema_version': '1',
            'evidence': [{'id': 'fixture', 'source': 'test fixture', 'observed_at': '2026-11-15T00:00:00Z'}],
            'candidates': [{'text': 'Vestido elegante opción %s' % i, 'evidence_ids': ['fixture']} for i in range(10)]})
        action_id = self.env.ref('meli_optimization.workbench_action').id
        spawn = ChromeBrowser._spawn_chrome
        def fixed_port_spawn(browser, cmd):
            # Chrome writes DevToolsActivePort only for port=0. Keep debugger
            # confined to the disposable runner's explicit loopback allowlist.
            Path(browser.user_data_dir, 'DevToolsActivePort').write_text('55441\n/devtools/browser\n')
            return spawn(browser, cmd)
        handle_console = ChromeBrowser._handle_console
        def screenshot_console(browser, type, args=None, **kwargs):
            values = [arg.get('value') for arg in (args or [])]
            handle_console(browser, type, args=args, **kwargs)
            for value in values:
                if isinstance(value, str) and value.startswith('workbench-shot-'):
                    browser.take_screenshot(prefix=value.replace('-', '_') + '_')
        original = ChromeBrowser._wait_code_ok
        def capture(browser, *args, **kwargs):
            result = original(browser, *args, **kwargs)
            browser.take_screenshot(prefix='workbench_').result(timeout=15)
            return result
        with patch.object(ChromeBrowser, 'remote_debugging_port', 55441), patch.object(ChromeBrowser, '_wait_code_ok', capture), patch.object(ChromeBrowser, '_spawn_chrome', fixed_port_spawn), patch.object(ChromeBrowser, '_handle_console', screenshot_console):
            self.browser_js('/odoo/action-%s' % action_id, r"""
                (async () => {
                    const wait = async (fn) => { for(let i=0;i<150;i++){const v=fn();if(v)return v;await new Promise(r=>setTimeout(r,100));}throw Error('UI timeout'); };
                    const button = (text) => [...document.querySelectorAll('.meli-workbench button')].find(b=>b.textContent.includes(text));
                    await wait(()=>document.querySelector('.mw-table .mw-link'));
                    if(getComputedStyle(document.querySelector('.mw-sidebar')).backgroundColor !== 'rgb(23, 34, 56)')throw Error('Native navy styles failed to compile');
                    document.querySelector('.mw-table .mw-link').click();
                    await wait(()=>button('标题候选')); button('标题候选').click();
                    await wait(()=>document.querySelector('.mw-choice input'));
                    console.log('workbench-shot-titles');
                    await new Promise(r=>setTimeout(r,300));
                    document.querySelector('.mw-choice input').click();
                    await wait(()=>button('生成确切方案预览')); button('生成确切方案预览').click();
                    await wait(()=>button('确认以上确切方案'));
                    if(!document.querySelector('.mw-preview').textContent.includes('Vestido elegante opción 0')) throw Error('Exact candidate absent');
                    console.log('workbench-shot-preview');
                    await new Promise(r=>setTimeout(r,300));
                    button('确认以上确切方案').click();
                    await wait(()=>document.querySelector('.mw-task')?.textContent.includes('已确认'));
                    button('返回列表').click(); button('新建优化').click();
                    await wait(()=>{const b=button('手动创建快照');return b && !b.disabled && b});button('手动创建快照').click();
                    await wait(()=>button('保存快照'));
                    if(document.querySelectorAll('.mw-form input').length < 7) throw Error('Product fields missing');
                    console.log('workbench-shot-form');
                    await new Promise(r=>setTimeout(r,300));
                    // Create a fresh case through the real product form.
                    const set = (el,value,event='input') => {el.value=value;el.dispatchEvent(new Event(event,{bubbles:true}));};
                    const inputs=[...document.querySelectorAll('.mw-form input')];
                    set(inputs[0],'Fresh shop');set(inputs[1],'FRESH-CHAIN');set(inputs[2],'Fresh product');
                    button('保存快照').click();
                    await wait(()=>!document.querySelector('.mw-modal') && document.querySelector('h1')?.textContent.includes('FRESH-CHAIN'));
                    button('任务队列').click();await wait(()=>button('创建任务'));button('创建任务').click();
                    await wait(()=>button('加入执行队列'));button('加入执行队列').click();
                    await wait(()=>document.querySelector('.mw-task')?.textContent.includes('整理商品事实'));
                    // Offline Worker fixture: only the test submits known fixture results.
                    const rpc=async(model,method,args)=>{const data=await(await fetch('/web/dataset/call_kw/'+model+'/'+method,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({jsonrpc:'2.0',method:'call',id:1,params:{model,method,args,kwargs:{}}})})).json();if(data.error)throw Error(data.error.data.message);return data.result;};
                    const cases=await rpc('meli.optimization.case','search_read',[[['name','ilike','FRESH-CHAIN']],['id']]);
                    const caseId=cases[0].id;
                    const finish=async(kind,result)=>{const tasks=await rpc('meli.optimization.task','search_read',[[['case_id','=',caseId],['task_type','=',kind]],['id']]);const claim=await rpc('meli.optimization.task','claim',[[tasks[0].id],[kind]]);await rpc('meli.optimization.task','complete',[[tasks[0].id],claim.lease_token,claim.lease_epoch,'fixture-'+kind,result]);};
                    const evidence=[{id:'fixture',source:'offline browser fixture',observed_at:'2026-11-15T00:00:00Z'}];
                    await finish('product.facts',{schema_version:'1',evidence,facts:[{name:'title',value:'Fresh product',evidence_ids:['fixture']}],uncertainties:[]});
                    button('刷新').click();await wait(()=>button('归档结果供后续任务使用'));button('归档结果供后续任务使用').click();
                    await wait(()=>!document.querySelector('.mw-loading'));
                    const createKind=async(kind)=>{button('创建任务').click();await wait(()=>document.querySelector('.mw-form select'));set(document.querySelector('.mw-form select'),kind,'change');await new Promise(r=>setTimeout(r,100));};
                    await createKind('research.keywords');
                    let selects=document.querySelectorAll('.mw-form select');
                    if(selects[1].options.length<2)throw Error('Facts archival missing from selector');
                    const factsArtifact=selects[1].options[1].value;set(selects[1],factsArtifact,'change');
                    button('加入执行队列').click();await wait(()=>!document.querySelector('.mw-modal'));
                    await finish('research.keywords',{schema_version:'1',evidence,status:'complete',keywords:[{text:'Vestido',evidence_ids:['fixture']}]});
                    button('刷新').click();await wait(()=>document.querySelectorAll('.mw-task .primary').length===2);
                    [...document.querySelectorAll('.mw-task')].find(el=>el.textContent.includes('关键词研究')).querySelector('.primary').click();
                    await wait(()=>!document.querySelector('.mw-loading'));
                    await createKind('title.generate');selects=document.querySelectorAll('.mw-form select');
                    const keywordOptions=[...selects[2].options].filter(o=>o.value);
                    if(keywordOptions.length!==1 || keywordOptions[0].value===factsArtifact)throw Error('Keyword selector must contain only the keyword archival');
                    const factOptions=[...selects[1].options].filter(o=>o.value);
                    if(factOptions.length!==1 || factOptions[0].value!==factsArtifact)throw Error('Facts selector contains an incorrect archival');
                    set(selects[1],factsArtifact,'change');
                    const keywordArtifact=keywordOptions[0].value;
                    set(selects[2],keywordArtifact,'change');button('加入执行队列').click();
                    await wait(()=>!document.querySelector('.mw-modal') && document.querySelector('.mw-task')?.textContent.includes('生成标题候选'));
                    console.log('workbench-shot-fresh-chain');await new Promise(r=>setTimeout(r,300));
                    // Independent research flow. Synthetic image/evidence is test-only.
                    button('独立爆款采集').click();await wait(()=>button('新建独立采集'));button('新建独立采集').click();
                    await wait(()=>document.querySelector('.mw-form input'));
                    let collectionInputs=document.querySelectorAll('.mw-form input');
                    set(collectionInputs[0],'TEST ONLY standalone collection');set(collectionInputs[1],'Test-only dress reference');
                    set(collectionInputs[2],'TEST-SOURCE-SKU');set(collectionInputs[3],'https://example.test/garment');
                    const png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC';
                    const fileInput=document.querySelector('.mw-form input[type=file]');
                    const files=new DataTransfer();files.items.add(new File([Uint8Array.from(atob(png),c=>c.charCodeAt(0))],'source.png',{type:'image/png'}));
                    fileInput.files=files.files;fileInput.dispatchEvent(new Event('change',{bubbles:true}));
                    await wait(()=>!button('创建独立采集任务').disabled);
                    console.log('workbench-shot-independent-form');await new Promise(r=>setTimeout(r,300));
                    button('创建独立采集任务').click();
                    await wait(()=>!document.querySelector('.mw-modal') && document.querySelector('h1')?.textContent.includes('TEST ONLY standalone'));
                    const collections=await rpc('meli.optimization.case','workbench_collections',[]);
                    const collection=collections.find(c=>c.name==='TEST ONLY standalone collection');
                    if(!collection)throw Error('Standalone collection not persisted');
                    const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',Uint8Array.from(atob(png),c=>c.charCodeAt(0))))).map(x=>x.toString(16).padStart(2,'0')).join('');
                    const archived=await rpc('meli.optimization.artifact','create',[[{case_id:collection.id,data:png,mime_type:'image/png',digest}]]);
                    const archivedId=Array.isArray(archived)?archived[0]:archived;
                    const galleryId=await rpc('meli.optimization.case','workbench_task',[collection.id,'gallery.collect',{reference_item_id:'TEST-LIBRARY-REF',selected_variant:{seller_sku:'TEST-ONLY-SKU'}}]);
                    const claim=await rpc('meli.optimization.task','claim',[[galleryId],['gallery.collect']]);
                    await rpc('meli.optimization.task','complete',[[galleryId],claim.lease_token,claim.lease_epoch,'test-gallery-browser',{
                        schema_version:'1',evidence:[{id:'fixture',source:'https://example.test/fixture',observed_at:'2026-11-15T00:00:00Z'}],
                        reference_item_id:'TEST-LIBRARY-REF',selected_variant:{seller_sku:'TEST-ONLY-SKU'},observed_at:'2026-11-15T00:00:00Z',
                        gallery_manifest_hash:'a'.repeat(64),completeness_status:'complete',images:[{image_id:'TEST ONLY IMAGE',order:0,role:'front',artifact_id:String(archivedId),source:'https://example.test/fixture',evidence_ids:['fixture'],mime_type:'image/png',width:1,height:1,sha256:digest}]}]);
                    button('刷新').click();await wait(()=>!document.querySelector('.mw-loading'));button('类似爆款').click();
                    await wait(()=>button('保存已验证图片到公司图库'));button('保存已验证图片到公司图库').click();
                    await wait(()=>[...document.querySelectorAll('.o_notification_content')].some(n=>n.textContent.includes('已验证图片已保存')));button('公司图片库').click();
                    await wait(()=>document.querySelector('.mw-library-table tbody tr')?.textContent.includes('TEST-SOURCE-SKU'));
                    document.querySelectorAll('.o_notification_close').forEach(el=>el.click());
                    console.log('workbench-shot-persistent-library');await new Promise(r=>setTimeout(r,300));
                    button('查看图片与来源').click();await wait(()=>document.querySelector('.mw-library-dialog'));
                    if(!document.querySelector('.mw-library-dialog').textContent.includes('TEST-ONLY-SKU'))throw Error('Library provenance missing');
                    document.querySelector('[aria-label="关闭对话框"]').click();
                    let sourceDetail=await rpc('meli.optimization.case','workbench_detail',[caseId]);
                    sourceDetail=await rpc('meli.optimization.case','workbench_snapshot_source_image',[caseId,png,'image/png',sourceDetail.revision]);
                    sourceDetail=await rpc('meli.optimization.case','workbench_snapshot_source_image',[caseId,png,'image/png',sourceDetail.revision]);
                    const sourceRefs=sourceDetail.snapshot.source_images.map(i=>String(i.artifact_id));
                    if(sourceRefs.length!==2 || sourceRefs[0]===sourceRefs[1])throw Error('Snapshot gallery did not retain two source images');
                    button('商品优化').click();
                    const sourceRow=await wait(()=>[...document.querySelectorAll('.mw-table tbody tr')].find(r=>r.textContent.includes('FRESH-CHAIN') && r.textContent.includes('共 2 张') && r.querySelector('img[alt="商品首图"]')?.getAttribute('src').includes('/'+sourceRefs[0]+'/') && !button('刷新').disabled));
                    if(!sourceRow || !sourceRow.textContent.includes('共 2 张') || !sourceRow.querySelector('img[alt="商品首图"]')?.getAttribute('src').includes('/'+sourceRefs[0]+'/'))throw Error('List must show first source image and count');
                    sourceRow.querySelector('.mw-link').click();
                    await wait(()=>document.querySelectorAll('.mw-source-images img').length===2);
                    const displayed=[...document.querySelectorAll('.mw-source-images img')].map(i=>i.getAttribute('src'));
                    if(!displayed.every((url,i)=>url.includes('/'+sourceRefs[i]+'/')))throw Error('Source gallery order changed');
                    await wait(()=>button('图片与质检'));button('图片与质检').click();await wait(()=>button('从公司图库选择换装目标'));if(!document.body.textContent.includes('文本宿主心跳不代表图片能力可用'))throw Error('Missing image host capability guidance');button('从公司图库选择换装目标').click();
                    const libraryRow=await wait(()=>[...document.querySelectorAll('.mw-library-select-table tbody tr')].find(row=>row.textContent.includes('TEST-SOURCE-SKU')));
                    libraryRow.querySelector('button').click();
                    await wait(()=>document.querySelector('.mw-library-dialog .mw-choice input'));document.querySelector('.mw-library-dialog .mw-choice input').click();
                    document.querySelectorAll('.o_notification_close').forEach(el=>el.click());
                    const dialog=document.querySelector('.mw-modal');dialog.scrollTop=dialog.scrollHeight;
                    if(dialog.scrollWidth>dialog.clientWidth+1)throw Error('Library modal horizontal overflow');
                    console.log('workbench-shot-cross-case-selection');await new Promise(r=>setTimeout(r,300));
                    button('冻结计划并创建换装任务').click();
                    await wait(()=>!document.querySelector('.mw-modal') && [...document.querySelectorAll('.mw-task')].some(t=>t.textContent.includes('逐图生成')));
                    const swapped=await rpc('meli.optimization.task','search_read',[[['case_id','=',caseId],['task_type','=','image.generate']],['payload']]);
                    if(swapped.length!==1)throw Error('Selected scope is not exactly one generation task');
                    const plans=await rpc('meli.optimization.artifact','read',[[Number(swapped[0].payload.generation_plan_id)],['payload']]);
                    if(plans[0].payload.target_count!==1 || plans[0].payload.case_id!==caseId || JSON.stringify(plans[0].payload.source_artifact_ids.map(String))!==JSON.stringify(sourceRefs))throw Error('Plan did not freeze destination, source images and target count');
                    button('优化批次').click();
                    await wait(()=>document.querySelector('.mw-batch'));
                    const batchButton=[...document.querySelectorAll('.mw-batch')].find(b=>b.textContent.includes('TEST BATCH NAV 3'));
                    if(!batchButton)throw Error('Batch navigation fixture absent');
                    batchButton.click();
                    await wait(()=>document.querySelector('.mw-table tbody tr')?.textContent.includes('MLM-DEMO'));
                    if(document.querySelectorAll('.mw-table tbody tr').length!==1)throw Error('Batch navigation did not apply scope');
                    if(document.querySelector('[aria-label="批次筛选"]').selectedOptions[0].textContent !== 'TEST BATCH NAV 3')throw Error('Selected batch label does not match applied scope');
                    console.log('workbench-shot-batch-navigation');await new Promise(r=>setTimeout(r,300));
                    console.log('test successful');
                })().catch(e=>console.error(e));
            """, login='admin', timeout=100)

    def test_screened_product_facts_tab(self):
        """Exercises real screening -> completed result -> native facts tab."""
        self.env.ref('base.user_admin').write({'group_ids': [(4, self.env.ref(g).id) for g in ('meli_optimization.group_editor', 'meli_bff.group_reader', 'meli_oerp.group_mercadolibre_manager')]})
        ui = self.env['meli.optimization.case'].with_user(self.env.ref('base.user_admin'))
        shop = self.env['meli.independent.store'].create({'name': 'Facts browser fixture', 'seller_id': '8', 'marketplace_seller_id': '9', 'site': 'MLM'})
        criteria = dict(site_id='MLM', timezone='America/Mexico_City', start='2026-11-01T00:00:00-06:00', end='2026-11-08T00:00:00-06:00', sales_max=1, stock_min=0, sales_policy='paid_gross_units', require_all_skus_in_stock=True)
        preview = ui.screening_preview(shop.id, 'facts-browser', criteria)
        def collect(*args):
            return ([dict(id='FACTS-SCREENED', seller_id='9', site_id='MLM', title='<img src=x onerror="window.factsInjected=1">Observed dress', variations=[
                dict(id='small', seller_custom_field='SKU-S', available_quantity=2, attribute_combinations=[dict(id='SIZE', value_name='S')]),
                dict(id='medium', available_quantity=1, attribute_combinations=[dict(id='SIZE', value_name='M')])])], [], dict(orders_complete=True, provider='isolated-test'))
        with patch.object(type(ui), '_screen_collect', collect):
            screened = ui.screening_execute(preview['id'], preview['digest'])
        current_id = screened['case_ids'][0]
        empty = ui.workbench_create({'shop': 'Facts browser fixture', 'item_id': 'FACTS-EMPTY', 'title': 'No facts'})
        # An older completed result is never shown under the current heading.
        stale = ui.workbench_create({'shop': 'Facts browser fixture', 'item_id': 'FACTS-STALE', 'title': 'Old observation'})
        task_id = ui.workbench_task(stale['id'], 'product.facts', {})
        task = self.env['meli.optimization.task'].browse(task_id)
        claim = task.claim(['product.facts'])
        task.complete(claim['lease_token'], claim['lease_epoch'], 'stale-fixture', {'schema_version': '1',
            'facts': [{'name': 'title', 'value': 'Old observation', 'evidence_ids': ['s']}],
            'evidence': [{'id': 's', 'source': 'snapshot:old', 'observed_at': '2026-11-15T00:00:00Z'}], 'uncertainties': []})
        ui.workbench_snapshot(stale['id'], {'shop': 'Facts browser fixture', 'item_id': 'FACTS-STALE', 'title': 'New snapshot'})
        action_id = self.env.ref('meli_optimization.workbench_action').id
        spawn = ChromeBrowser._spawn_chrome
        def fixed_port_spawn(browser, cmd):
            Path(browser.user_data_dir, 'DevToolsActivePort').write_text('55441\n/devtools/browser\n')
            return spawn(browser, cmd)
        handle_console = ChromeBrowser._handle_console
        def screenshots(browser, type, args=None, **kwargs):
            handle_console(browser, type, args=args, **kwargs)
            for arg in args or []:
                value = arg.get('value')
                if isinstance(value, str) and value.startswith('facts-shot-'):
                    browser.take_screenshot(prefix=value.replace('-', '_') + '_')
        with patch.object(ChromeBrowser, 'remote_debugging_port', 55441), patch.object(ChromeBrowser, '_spawn_chrome', fixed_port_spawn), patch.object(ChromeBrowser, '_handle_console', screenshots):
            self.browser_js('/odoo/action-%s' % action_id, r"""
                (async()=>{
                    const wait=async(fn)=>{for(let i=0;i<120;i++){if(fn())return;await new Promise(r=>setTimeout(r,100));}throw Error('Facts UI timeout');};
                    const button=text=>[...document.querySelectorAll('.meli-workbench button')].find(b=>b.textContent.trim()===text);
                    const open=async(id)=>{await wait(()=>document.querySelector('.mw-table'));const link=[...document.querySelectorAll('.mw-table tbody tr')].find(r=>r.textContent.includes(id))?.querySelector('.mw-link');if(!link)throw Error('Fixture missing '+id);link.click();await wait(()=>document.querySelector('.mw-tabs'));if(!button('商品事实'))throw Error('Dedicated 商品事实 tab missing');button('商品事实').click();await wait(()=>document.querySelector('.mw-facts'));};
                    await open('FACTS-SCREENED');
                    const facts=document.querySelector('.mw-facts');
                    if(!facts.querySelector('.mw-facts-current'))throw Error('Current facts absent');
                    const text=facts.textContent;
                    for(const expected of ['Observed dress','SKU / 尺码库存证据','S','M','snapshot:','采集时间','external truth has not been verified'])if(!text.includes(expected))throw Error('Missing fact detail '+expected);
                    if(facts.querySelector('img') || window.factsInjected)throw Error('Fact text executed as HTML');
                    if(text.includes('[object Object]') || facts.querySelector('pre'))throw Error('Facts must be structured, not raw JSON');
                    console.log('facts-shot-screened');await new Promise(r=>setTimeout(r,350));
                    button('返回列表').click();await open('FACTS-EMPTY');
                    if(!document.querySelector('.mw-facts-empty') || document.querySelector('.mw-facts-current'))throw Error('No-facts case falsely displayed current facts');
                    console.log('facts-shot-empty');await new Promise(r=>setTimeout(r,350));
                    button('返回列表').click();await open('FACTS-STALE');
                    if(!document.querySelector('.mw-facts-stale') || document.querySelector('.mw-facts-current'))throw Error('Stale facts presented as current');
                    const history=document.querySelector('.mw-facts-history');history.open=true;
                    await wait(()=>history.textContent.includes('Old observation'));
                    console.log('facts-shot-stale');await new Promise(r=>setTimeout(r,350));
                    console.log('test successful');
                })().catch(e=>console.error(e));
            """, login='admin', timeout=70)

    def test_title_flow_button_starts_facts_once(self):
        self.env.ref('base.user_admin').write({'group_ids': [(4, self.env.ref('meli_optimization.group_editor').id)]})
        self.env['meli.optimization.case'].workbench_create({'shop':'Test shop', 'item_id':'FLOW-UI', 'title':'Flow UI product', 'site_id':'MLM'})
        action_id = self.env.ref('meli_optimization.workbench_action').id
        spawn = ChromeBrowser._spawn_chrome
        def fixed_port_spawn(browser, cmd):
            Path(browser.user_data_dir, 'DevToolsActivePort').write_text('55441\n/devtools/browser\n')
            return spawn(browser, cmd)
        with patch.object(ChromeBrowser, 'remote_debugging_port', 55441), patch.object(ChromeBrowser, '_spawn_chrome', fixed_port_spawn):
            self.browser_js('/odoo/action-%s' % action_id, r"""
                (async () => {
                    const wait = async fn => { for (let i=0;i<150;i++){const v=fn();if(v)return v;await new Promise(r=>setTimeout(r,100));}throw Error('UI timeout'); };
                    const button = text => [...document.querySelectorAll('.meli-workbench button')].find(b=>b.textContent.includes(text));
                    const row=await wait(()=>[...document.querySelectorAll('.mw-table tbody tr')].find(r=>r.textContent.includes('FLOW-UI')));
                    row.querySelector('button').click(); await wait(()=>button('任务队列'));button('任务队列').click();
                    await wait(()=>button('一键标题优化'));button('一键标题优化').click();
                    await wait(()=>[...document.querySelectorAll('.mw-task')].some(t=>t.textContent.includes('整理商品事实')));
                    await wait(()=>!button('一键标题优化').disabled);button('一键标题优化').click();
                    await new Promise(r=>setTimeout(r,500));
                    if([...document.querySelectorAll('.mw-task')].filter(t=>t.textContent.includes('整理商品事实')).length!==1)throw Error('Duplicate facts task');
                    button('商品快照').click(); await wait(()=>button('上传源图并创建新快照'));button('上传源图并创建新快照').click();
                    const input=await wait(()=>document.querySelector('.mw-modal input[type=file]'));
                    const bytes=Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR4nGP8zwACTGCSAQANHQEDgslx/wAAAABJRU5ErkJggg=='),c=>c.charCodeAt(0));
                    const transfer=new DataTransfer();transfer.items.add(new File([bytes],'source.png',{type:'image/png'}));input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));
                    const save=await wait(()=>{const b=button('创建新快照并绑定源图');return b && !b.disabled && b;});save.click();
                    await wait(()=>!document.querySelector('.mw-modal') && document.querySelector('.mw-card-heading h2')?.textContent.includes('v2'));
                    const image=await wait(()=>document.querySelector('img[alt="商品原图 1"]'));
                    await wait(()=>image.complete && image.naturalWidth>0);
                    const rpc=async(model,method,args)=>{const r=await(await fetch('/web/dataset/call_kw/'+model+'/'+method,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({jsonrpc:'2.0',method:'call',id:1,params:{model,method,args,kwargs:{}}})})).json();if(r.error)throw Error(r.error.data.message);return r.result;};
                    const cases=await rpc('meli.optimization.case','search_read',[[['name','ilike','FLOW-UI']],['id']]);
                    const snaps=await rpc('meli.optimization.snapshot','search_read',[[['case_id','=',cases[0].id]],['payload']]);
                    if(snaps.length!==2 || !snaps.some(s=>!s.payload.source_image_artifact_ids) || !snaps.some(s=>s.payload.source_image_artifact_ids?.length===1))throw Error('Snapshot history not preserved');
                    console.log('test successful');
                })().catch(e=>console.error(e));
            """, login='admin', timeout=60)

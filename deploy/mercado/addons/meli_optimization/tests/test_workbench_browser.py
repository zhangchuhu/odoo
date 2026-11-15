"""Actual native action smoke test against a disposable database, no platform traffic."""
from unittest.mock import patch
from pathlib import Path
from odoo.tests import HttpCase, tagged
from odoo.tests.common import ChromeBrowser


@tagged('post_install', '-at_install', 'optimization_browser')
class WorkbenchBrowserCase(HttpCase):
    browser_size = '1440x1000'

    def test_workbench_browser_create_select_confirm(self):
        self.env.ref('base.user_admin').write({'group_ids': [(4, self.env.ref('meli_optimization.group_editor').id)]})
        ui = self.env['meli.optimization.case']
        detail = ui.workbench_create({'shop': '演示店铺', 'item_id': 'MLM-DEMO', 'title': 'Vestido elegante', 'seller_sku': 'SKU-A', 'batch_name': 'TEST BATCH NAV 3'})
        task = self.env['meli.optimization.task'].create({'case_id': detail['id'], 'snapshot_id': detail['snapshot']['id'],
            'task_type': 'title.generate', 'payload': {'schema_version': '1', 'facts_artifact_id': 'fixture', 'keywords_artifact_id': 'fixture', 'language': 'es-MX'}})
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
                    await wait(()=>document.querySelector('.mw-form'));
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
                    if(selects[2].options.length<3)throw Error('Keyword archival missing from selector');
                    set(selects[1],factsArtifact,'change');
                    const keywordArtifact=[...selects[2].options].find(o=>o.value && o.value!==factsArtifact).value;
                    set(selects[2],keywordArtifact,'change');button('加入执行队列').click();
                    await wait(()=>!document.querySelector('.mw-modal') && document.querySelector('.mw-task')?.textContent.includes('生成标题候选'));
                    console.log('workbench-shot-fresh-chain');await new Promise(r=>setTimeout(r,300));
                    // Independent research flow. Synthetic image/evidence is test-only.
                    button('独立爆款采集').click();await wait(()=>button('新建独立采集'));button('新建独立采集').click();
                    await wait(()=>document.querySelector('.mw-form input'));
                    let collectionInputs=document.querySelectorAll('.mw-form input');
                    set(collectionInputs[0],'TEST ONLY standalone collection');set(collectionInputs[1],'Test-only dress reference');
                    set(collectionInputs[2],'https://example.test/garment');
                    console.log('workbench-shot-independent-form');await new Promise(r=>setTimeout(r,300));
                    button('创建独立采集任务').click();
                    await wait(()=>!document.querySelector('.mw-modal') && document.querySelector('h1')?.textContent.includes('TEST ONLY standalone'));
                    const collections=await rpc('meli.optimization.case','workbench_collections',[]);
                    const collection=collections.find(c=>c.name==='TEST ONLY standalone collection');
                    if(!collection)throw Error('Standalone collection not persisted');
                    const png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC';
                    const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',Uint8Array.from(atob(png),c=>c.charCodeAt(0))))).map(x=>x.toString(16).padStart(2,'0')).join('');
                    const archived=await rpc('meli.optimization.artifact','create',[[{case_id:collection.id,data:png,mime_type:'image/png',digest}]]);
                    const archivedId=Array.isArray(archived)?archived[0]:archived;
                    const galleryId=await rpc('meli.optimization.case','workbench_task',[collection.id,'gallery.collect',{reference_item_id:'TEST-LIBRARY-REF',selected_variant:{seller_sku:'TEST-ONLY-SKU'}}]);
                    const claim=await rpc('meli.optimization.task','claim',[[galleryId],['gallery.collect']]);
                    await rpc('meli.optimization.task','complete',[[galleryId],claim.lease_token,claim.lease_epoch,'test-gallery-browser',{
                        schema_version:'1',evidence:[{id:'fixture',source:'https://example.test/fixture',observed_at:'2026-11-15T00:00:00Z'}],
                        reference_item_id:'TEST-LIBRARY-REF',selected_variant:{seller_sku:'TEST-ONLY-SKU'},observed_at:'2026-11-15T00:00:00Z',
                        gallery_manifest_hash:'a'.repeat(64),completeness_status:'complete',images:[{image_id:'TEST ONLY IMAGE',order:0,role:'front',artifact_id:String(archivedId),source:'https://example.test/fixture',evidence_ids:['fixture'],mime_type:'image/png',width:1,height:1,sha256:digest}]}]);
                    button('刷新').click();await wait(()=>!document.querySelector('.mw-loading'));button('目标图库').click();
                    await wait(()=>button('保存已验证图片到公司图库'));button('保存已验证图片到公司图库').click();
                    await wait(()=>[...document.querySelectorAll('.o_notification_content')].some(n=>n.textContent.includes('已验证图片已保存')));button('公司图片库').click();
                    await wait(()=>document.querySelector('.mw-candidate')?.textContent.includes('TEST-LIBRARY-REF'));
                    document.querySelectorAll('.o_notification_close').forEach(el=>el.click());
                    console.log('workbench-shot-persistent-library');await new Promise(r=>setTimeout(r,300));
                    button('查看图片与来源').click();await wait(()=>document.querySelector('.mw-library-dialog'));
                    if(!document.querySelector('.mw-library-dialog').textContent.includes('TEST-ONLY-SKU'))throw Error('Library provenance missing');
                    document.querySelector('[aria-label="关闭对话框"]').click();
                    const garment=await rpc('meli.optimization.case','workbench_upload_garment',[caseId,png,'image/png']);
                    button('商品优化').click();await wait(()=>document.querySelector('.mw-table .mw-link'));
                    [...document.querySelectorAll('.mw-table tbody tr')].find(r=>r.textContent.includes('FRESH-CHAIN')).querySelector('.mw-link').click();
                    await wait(()=>button('图片与质检'));button('图片与质检').click();await wait(()=>button('从公司图库选择换装目标'));button('从公司图库选择换装目标').click();
                    await wait(()=>button('TEST-LIBRARY-REF'));button('TEST-LIBRARY-REF').click();
                    await wait(()=>document.querySelector('.mw-library-dialog .mw-choice input'));document.querySelector('.mw-library-dialog .mw-choice input').click();
                    set(document.querySelector('.mw-library-dialog select'),String(garment),'change');
                    document.querySelectorAll('.o_notification_close').forEach(el=>el.click());
                    const dialog=document.querySelector('.mw-modal');dialog.scrollTop=dialog.scrollHeight;
                    if(dialog.scrollWidth>dialog.clientWidth+1)throw Error('Library modal horizontal overflow');
                    console.log('workbench-shot-cross-case-selection');await new Promise(r=>setTimeout(r,300));
                    button('冻结计划并创建换装任务').click();
                    await wait(()=>!document.querySelector('.mw-modal') && [...document.querySelectorAll('.mw-task')].some(t=>t.textContent.includes('逐图生成')));
                    const swapped=await rpc('meli.optimization.task','search_read',[[['case_id','=',caseId],['task_type','=','image.generate']],['payload']]);
                    if(swapped.length!==1)throw Error('Selected scope is not exactly one generation task');
                    const plans=await rpc('meli.optimization.artifact','read',[[Number(swapped[0].payload.generation_plan_id)],['payload']]);
                    if(plans[0].payload.target_count!==1 || plans[0].payload.case_id!==caseId)throw Error('Plan did not freeze destination and target count');
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

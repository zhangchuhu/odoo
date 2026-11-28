import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { imageReviewWaiting, collectionSubmission } from '../addons/meli_optimization/static/src/workbench_helpers.js';
globalThis.__imageReviewWaiting = imageReviewWaiting;
globalThis.__collectionSubmission = collectionSubmission;
const source = (await readFile(new URL('../addons/meli_optimization/static/src/workbench.js', import.meta.url), 'utf8'))
    .replace(/^import .*;$/gm, '')
    .replace("registry.category('actions').add('meli_optimization.workbench', OptimizationWorkbench);", '');
const { OptimizationWorkbench } = await import('data:text/javascript,' + encodeURIComponent('class Component {}\nconst imageReviewWaiting=globalThis.__imageReviewWaiting;\nconst collectionSubmission=globalThis.__collectionSubmission;\n' + source));
function fixture() {
    const ui = Object.create(OptimizationWorkbench.prototype);
    ui.state = {loading:false, detail:{id:1,revision:1,snapshot:{id:1},artifacts:[]}, selections:{7:[0]},modal:'',preview:null};
    ui.notification = {add(){}};
    return ui;
}
test('image detail keeps unmapped tasks and hides tasks shown in plan cards', () => {
    const ui=fixture();
    Object.defineProperty(ui,'visibleTasks',{value:[{id:1,task_type:'image.generate'},{id:2,task_type:'image.generate'},{id:3,task_type:'image.qc'}]});
    Object.defineProperty(ui,'imagePlans',{value:[{rows:[{task:{id:1}}]},{rows:null}]});
    ui.state.tab='images';
    assert.deepEqual(ui.unmappedResultTasks.map(t=>t.id),[2,3]);
    ui.state.tab='research';assert.deepEqual(ui.unmappedResultTasks.map(t=>t.id),[1,2,3]);
});
test('expired image regeneration is bound to the current task lease and snapshot', async () => {
    const ui=fixture(), calls=[]; ui.navigationVersion=1;ui.disposed=false;
    ui.rpc=async(method,args)=>{calls.push([method,args]);return {state:'queued'}};ui.load=async()=>{};
    const task={id:253,task_type:'image.generate',state:'running',lease_expired:true,lease_epoch:4,snapshot_id:1};
    assert.equal(ui.expiredImageAvailable({...task,snapshot_id:2}),false);
    assert.equal(ui.expiredImageAvailable({...task,lease_expired:false}),false);
    ui.openExpiredImage(task);assert.equal(ui.state.modal,'expired-image');
    await ui.regenerateExpiredImage();
    assert.deepEqual(calls,[['workbench_regenerate_expired_image',[1,253,4]]]);
});
test('image plan defaults to target order and permits reordering and exclusion', () => {
    const ui=fixture(); ui.state.imagePlanSelections={};
    const plan={planId:8,rows:[{targetId:'a',task:{id:3,state:'succeeded'}},{targetId:'b',task:{id:2,state:'succeeded'}},{targetId:'c',task:{id:1,state:'running'}}]};
    assert.deepEqual(ui.imagePlanSelection(plan),[3,2]);
    ui.moveImagePlan(plan,1,-1); assert.deepEqual(ui.imagePlanSelection(plan),[2,3]);
    ui.toggleImagePlan(plan,3); assert.deepEqual(ui.imagePlanSelection(plan),[2]);
    plan.rows[1].task.id=20; assert.deepEqual(ui.imagePlanSelection(plan),[20]);
    plan.rows[1].task.id=2;
    ui.resetImagePlan(plan); assert.deepEqual(ui.imagePlanSelection(plan),[3,2]);
});
test('publish preview is bound to its confirmed plan and current navigation', async () => {
    const ui=fixture(), calls=[];
    ui.navigationVersion=1; ui.disposed=false;
    ui.rpc=async(method,args)=>{calls.push([method,args]);return {target:{model:'user_product'}}};
    await ui.previewImagePublish({id:7,digest:'digest'});
    assert.deepEqual(calls,[['workbench_image_publish_preview',[1,7,'digest']]]);
    assert.equal(ui.state.modal,'image-publish-preview');
    assert.equal(ui.state.imagePublishPreview.proposal_id,7);
});
test('selecting a SKU requests a new preview scoped to that SKU', async () => {
    const ui=fixture(), calls=[];ui.navigationVersion=1;ui.disposed=false;
    ui.rpc=async(method,args)=>{calls.push([method,args]);return {target:{model:'traditional',selected_variant_id:'12'}}};
    await ui.previewImagePublish({id:7,digest:'digest'},'12');
    assert.deepEqual(calls,[['workbench_image_publish_preview',[1,7,'digest','12']]]);
});
test('publication sends each step once and stops when an outcome needs reconciliation', async () => {
    const ui=fixture(), calls=[]; ui.navigationVersion=1;ui.disposed=false;ui.notification={add() {}};
    ui.state.imagePublishPreview={case_id:1,proposal_id:7,proposal_digest:'p',digest:'d'};
    ui.rpc=async(method,args)=>{calls.push(method); if(method==='workbench_image_publish_start')return {id:9};
      if(method==='workbench_image_publish_reserve')return {nonce:'a',operation:'update'};
      return {state:'reconciliation_required'};};
    ui.load=async()=>{};
    await ui.startImagePublish();
    assert.deepEqual(calls,['workbench_image_publish_start','workbench_image_publish_reserve','workbench_image_publish_dispatch']);
    assert.equal(ui.state.imagePublishResult.state,'reconciliation_required');
});
test('publication can complete a long staged image replacement', async () => {
    const ui=fixture(); ui.navigationVersion=1;ui.disposed=false;ui.load=async()=>{};
    let dispatched=0;
    ui.rpc=async(method)=>method==='workbench_image_publish_reserve' ? {nonce:'a'} : {state:++dispatched===130?'published':'linking'};
    await ui.runImagePublication({version:1,case_id:1,revision:1},9);
    assert.equal(dispatched,130);assert.equal(ui.state.imagePublishResult.state,'published');
});
test('publishing a selected SKU requires explicit source-garment confirmation', async () => {
    const ui=fixture(), calls=[];ui.navigationVersion=1;ui.disposed=false;ui.notification={add(){}};
    ui.state.imagePublishPreview={case_id:1,proposal_id:7,proposal_digest:'p',digest:'d',target:{selected_variant_id:'12'}};
    ui.state.imagePublishSkuConfirmed=false;ui.rpc=async(method,args)=>{calls.push([method,args]);return {id:9}};
    await ui.startImagePublish();assert.equal(calls.length,0);
    ui.state.imagePublishSkuConfirmed=true;ui.runImagePublication=async()=>{};await ui.startImagePublish();
    assert.deepEqual(calls[0],['workbench_image_publish_start',[1,7,'p','d','12',true]]);
});
test('reconciliation checks the existing publication instead of dispatching again', async () => {
    const ui=fixture(), calls=[];ui.navigationVersion=1;ui.disposed=false;ui.notification={add(){}};
    ui.rpc=async(method,args)=>{calls.push([method,args]);return {state:'published'}}; ui.load=async()=>{};
    await ui.reconcileImagePublication({id:9});
    assert.deepEqual(calls,[['workbench_image_publish_reconcile',[1,9]]]);
});
test('unknown upload is abandoned only after explicit confirmation', async () => {
    const ui=fixture(), calls=[];ui.navigationVersion=1;ui.disposed=false;ui.notification={add(){}};
    ui.rpc=async(method,args)=>{calls.push([method,args]);return {state:'failed'}};ui.load=async()=>{};
    globalThis.confirm=()=>false;await ui.abandonImagePublication({id:9});assert.equal(calls.length,0);
    globalThis.confirm=()=>true;await ui.abandonImagePublication({id:9});
    assert.deepEqual(calls,[['workbench_image_publish_abandon',[1,9]]]);
    delete globalThis.confirm;
});
test('refresh restoration reloads the authorized case and tab and falls back if inaccessible', async () => {
    const ui=fixture(), notices=[];
    ui.tabs=[['snapshot','商品快照'],['images','图片与质检']];
    ui.notification={add:message=>notices.push(message)};
    ui.refresh=async()=>true;
    ui.rpc=async(method,args)=>({id:args[0],collection:false});
    await ui.restoreWorkbenchLocation({section:'cases',caseId:23,tab:'images'});
    assert.equal(ui.state.detail.id,23);
    assert.equal(ui.state.tab,'images');
    ui.rpc=async()=>{throw new Error('access denied')};
    await ui.restoreWorkbenchLocation({section:'cases',caseId:23,tab:'images'});
    assert.equal(ui.state.detail,null);
    assert.equal(ui.state.section,'cases');
    assert.match(notices[0],/不可访问或暂时无法加载/);
});
test('expired lease is displayed as interrupted rather than running', () => {
    const ui=fixture();
    assert.equal(ui.taskStatusLabel({state:'running',lease_expired:true}), '租约已过期');
    assert.equal(ui.taskStatusClass({state:'running',lease_expired:true}), 'waiting');
    assert.equal(ui.taskStatusLabel({state:'running',lease_expired:false}), '执行中');
    assert.equal(ui.taskStatusLabel({state:'succeeded',lease_expired:true}), '已完成');
});
test('library recollection targets the clicked record and reuses a pending task', async () => {
    const ui=fixture(), calls=[], notices=[];
    ui.notification={add:(message)=>notices.push(message)};
    ui.rpc=async(method,args)=>{calls.push([method,args]); return {task_id:51,reused:true};};
    ui.fetchLibraryPage=async()=>calls.push(['refresh']);
    await ui.recollectLibrary({id:205});
    assert.deepEqual(calls,[['workbench_library_recollect',[205]],['refresh']]);
    assert.match(notices[0],/复用.*等待人工采集/);
    assert.equal(ui.state.loading,false);
});
test('research with pending image import does not present as completed', async () => {
    const ui=fixture();
    assert.equal(ui.taskStatusLabel({task_type:'research.bestseller',state:'succeeded',status_detail:{code:'research_images_pending',message:'图片入库失败'}}),'结果待入库');
    assert.equal(ui.taskStatusLabel({task_type:'research.bestseller',state:'succeeded'}),'已完成');
    assert.equal(ui.taskStatusLabel({task_type:'research.bestseller',state:'succeeded',result:{candidates:[]}}),'未找到候选商品');
    assert.equal(ui.taskStatusLabel({task_type:'research.keywords',state:'running'}),'执行中');
    const xml=await readFile(new URL('../addons/meli_optimization/static/src/workbench.xml',import.meta.url),'utf8');
    assert.equal((xml.match(/t-esc="taskStatusLabel\(task\)"/g)||[]).length,3);
    assert.ok((xml.match(/t-esc="task.status_detail.message"/g)||[]).length >= 2);
});
test('independent research import failure is not displayed as complete', async () => {
    const ui=fixture(), task={task_type:'research.bestseller',state:'succeeded',status_detail:{code:'derived_action_failed',message:'图库写入失败'}};
    ui.state.detail.collection=true;
    assert.equal(ui.taskStatusLabel(task),'入库失败／待重试');
    assert.equal(ui.taskStatusClass(task),'failed');
    ui.state.detail.collection=false;
    assert.equal(ui.taskStatusLabel(task),'已完成');
    const xml=await readFile(new URL('../addons/meli_optimization/static/src/workbench.xml',import.meta.url),'utf8');
    assert.equal((xml.match(/t-esc="task.status_detail.message"/g)||[]).length,3);
    assert.equal((xml.match(/this.retryDerived\(task\)/g)||[]).length,2);
    assert.equal((xml.match(/taskStatusClass\(task\)/g)||[]).length,3);
});
test('independent collection supplies platform and numeric criteria', async () => {
    const ui=fixture(), calls=[];
    ui.collectionForm();
    assert.deepEqual([ui.state.form.platform,ui.state.form.min_sold,ui.state.form.min_reviews,ui.state.form.min_rating,ui.state.form.result_limit],['mercado-libre-mx',500,50,4.1,15]);
    Object.assign(ui.state.form,{source_sku:'MLM1',min_sold:'0',min_reviews:'50',min_rating:'4.1',result_limit:'15'});
    ui.rpc=async(method,args)=>{calls.push([method,args]);return {id:1}};ui.load=async()=>{};
    await ui.saveCollection();
    const v=calls[0][1][1];assert.equal(v.source_sku,'MLM1');assert.equal(v.min_sold,0);assert.equal(v.min_reviews,50);assert.equal(v.min_rating,4.1);assert.equal(v.result_limit,15);
    assert.equal('garment' in v,false);
});
test('collection searches exact ID and binds selected snapshot and image', async () => {
    const ui=fixture(), calls=[];ui.navigationVersion=1;ui.disposed=false;ui.collectionForm();ui.state.form.source_sku='MLM1';
    const row={case_id:7,snapshot_id:8,revision:2,item_id:'MLM1',images:[{artifact_id:9},{artifact_id:10}]};
    ui.rpc=async(method,args)=>{calls.push([method,args]);return {rows:[row],total:1,page:0}};
    await ui.searchCollectionSource();
    assert.equal(ui.state.collectionSelected.case_id,7);assert.equal(ui.state.form.source_artifact_id,9);
    ui.selectCollectionImage(10);assert.equal(ui.state.form.source_artifact_id,10);
    assert.deepEqual(calls[0],['workbench_collection_source_search',['MLM1',0]]);
});
test('late collection create does not replace a different page', async () => {
    const ui=fixture();ui.navigationVersion=1;ui.disposed=false;ui.collectionForm();ui.state.form.source_sku='MLM1';
    let resolve;ui.rpc=()=>new Promise(r=>{resolve=r});ui.load=async()=>{};
    const pending=ui.saveCollection(false);ui.navigationVersion=2;ui.state.detail={id:99};
    resolve({id:1,snapshot:{id:2},revision:1});await pending;
    assert.equal(ui.state.detail.id,99);
});
test('research dependency options only include current snapshot matching result type', () => {
    const ui=fixture();ui.state.detail.snapshot={id:9};
    ui.state.detail.tasks=[{id:2,task_type:'product.facts',state:'succeeded'},{id:3,task_type:'research.keywords',state:'succeeded'},{id:4,task_type:'product.facts',state:'cancelled'},{id:5,task_type:'research.keywords',state:'succeeded',result:{status:'empty'}}];
    ui.state.detail.artifacts=[{id:20,result_task_id:[2,'facts'],result_snapshot_id:[9,'v']},{id:30,result_task_id:[3,'kw'],result_snapshot_id:[9,'v']},{id:40,result_task_id:[2,'facts'],result_snapshot_id:[8,'old']},{id:50,result_task_id:[4,'cancelled'],result_snapshot_id:[9,'v']},{id:60,result_task_id:[5,'empty'],result_snapshot_id:[9,'v']}];
    assert.deepEqual(ui.artifactsFor('facts_artifact_id').map(x=>x.id),[20]);
    assert.deepEqual(ui.artifactsFor('keywords_artifact_id').map(x=>x.id),[30]);
});
test('collection groups keep distinct source records and isolate missing identifiers', () => {
    const ui=fixture();ui.state.collections=[{id:1,company_id:1,name:'a',snapshot:{source_sku:'SKU-1'}},{id:2,company_id:1,name:'b',snapshot:{source_sku:'SKU-1'}},{id:3,company_id:1,name:'c',snapshot:{}},{id:4,company_id:1,name:'d',snapshot:{}},{id:5,company_id:2,name:'e',snapshot:{source_sku:'SKU-1'}},{id:6,name:'f',snapshot:{source_sku:'SKU-1'}}];
    const groups=ui.collectionGroups;
    assert.deepEqual(groups.map(g=>g.records.map(r=>r.id)),[[1,2],[3],[4],[5],[6]]);
    assert.equal(groups[0].source,'SKU-1');
});
test('collection filters apply and reset using returned fields only', () => {
    const ui=fixture();ui.state.collections=[{id:1,name:'Summer',company_id:1,snapshot:{source_sku:'SKU-1',garment:'dress',collection_config:{platform:'shein-us'}}},{id:2,name:'Winter',company_id:1,snapshot:{source_sku:'SKU-2',collection_config:{platform:'mercado-libre-mx'}}}];
    ui.state.collectionFilters={query:'sku-1',platform:'shein-us'};ui.state.collectionApplied={};
    assert.equal(ui.collectionGroups.length,2);ui.applyCollectionFilters();assert.deepEqual(ui.collectionGroups.flatMap(g=>g.records.map(r=>r.id)),[1]);
    ui.state.collectionFilters.query='winter';assert.equal(ui.collectionGroups.length,1);
    ui.resetCollectionFilters();assert.equal(ui.collectionGroups.length,2);
});
test('collection table resolves first source image safely and presents columns', async () => {
    const ui=fixture();
    assert.equal(ui.collectionSourceImage({snapshot:{source_image_artifact_ids:[42,99]}}),'/web/image/meli.optimization.artifact/42/data');
    assert.equal(ui.collectionSourceImage({snapshot:{source_image_artifact_ids:['12']}}),'/web/image/meli.optimization.artifact/12/data');
    assert.equal(ui.collectionSourceImage({snapshot:{source_image_artifact_ids:['../unsafe']}}),'');
    assert.equal(ui.collectionSourceImage({}), '');
    const xml=await readFile(new URL('../addons/meli_optimization/static/src/workbench.xml',import.meta.url),'utf8');
    for(const name of ['原图','任务名称 / 记录','平台','来源','操作']) assert.ok(xml.includes(`<th scope="col">${name}</th>`));
    assert.ok(!xml.includes('<th scope="col">服装描述</th>'));
    assert.ok(xml.includes('暂无原图'));assert.ok(xml.includes('collectionSourceImage(row)'));
});
test('one-click collection start binds snapshot and ignores stale navigation', async () => {
    const ui=fixture();ui.navigationVersion=0;ui.state.detail.collection=true;ui.state.detail.snapshot.payload={collection_config:{platform:'shein-us'}};let resolve;const calls=[];
    ui.rpc=(method,args)=>{calls.push([method,args]);return new Promise(r=>resolve=r)};ui.load=async()=>{};
    const pending=ui.startCollection();assert.deepEqual(calls,[['workbench_collection_start',[1,1,1]]]);
    ui.back();ui.state.detail={id:2,revision:1,snapshot:{id:2}};resolve({});await pending;assert.equal(ui.state.detail.id,2);assert.notEqual(ui.state.tab,'tasks');
    const xml=await readFile(new URL('../addons/meli_optimization/static/src/workbench.xml',import.meta.url),'utf8');assert.ok(xml.includes('一键开始采集'));assert.ok(xml.includes('t-on-click="startCollection"'));
});
test('late proposal never opens after navigating away', async () => {
    const ui = fixture(); let resolve;
    ui.rpc = () => new Promise(r=>resolve=r);
    const request = ui.propose({id:7,task_type:'title.generate',result_hash:'hash'});
    ui.state.detail = null;
    resolve({id:9,payload:{},digest:'digest'});
    await request;
    assert.equal(ui.state.modal, '');
    assert.equal(ui.state.preview, null);
    assert.equal(ui.imageUrl({artifact_id:'x'}), '');
});
test('confirmation targets preview case identity and discards changed context', async () => {
    const ui = fixture(); const calls=[];
    ui.state.preview={id:9,case_id:1,digest:'digest'};
    ui.state.detail={id:2,revision:1};
    ui.rpc=async (...args)=>calls.push(args);
    await ui.confirm();
    assert.deepEqual(calls, []);
});
test('late proposal is discarded after reopening same case or disposing action', async () => {
    for (const dispose of [false,true]) {
        const ui=fixture();ui.navigationVersion=0;let resolve;
        ui.rpc=()=>new Promise(r=>resolve=r);
        const request=ui.propose({id:7,task_type:'title.generate',result_hash:'hash'});
        if(dispose) ui.disposed=true;
        else {ui.back();ui.state.detail={id:1,revision:1};}
        resolve({id:9,payload:{},digest:'digest'});await request;
        assert.equal(ui.state.preview,null);
        assert.equal(ui.state.modal,'');
    }
});
test('library selection submits target indices and uses current snapshot sources', async () => {
    const ui=fixture(), calls=[];
    ui.state.librarySelection={id:20,payload:{images:[{artifact_id:'8'}]}};
    ui.state.libraryIndices=[0];ui.state.garmentArtifact='9';ui.state.libraryContext={case_id:1,revision:1};
    ui.load=async()=>{};
    ui.rpc=async (...args)=>{calls.push(args);return {task_ids:[30]};};
    await ui.saveLibrarySelection();
    assert.deepEqual(calls,[['workbench_library_generate',[1,20,[0],[],1]]]);
    assert.equal(ui.state.modal,'');
});
test('late library chooser does not reopen after leaving a product', async () => {
    const ui=fixture();ui.navigationVersion=0;let resolve;
    ui.loadLibrary=()=>new Promise(r=>resolve=r);
    const request=ui.selectLibrary();ui.back();resolve();await request;
    assert.equal(ui.state.modal,'');
});
test('batch click during stale refresh queues latest scope and never installs old rows', async () => {
    const ui=fixture();ui.state.detail=null;ui.state.section='batches';ui.state.batch='';ui.state.page=3;
    ui.state.data={rows:[{id:'existing'}]};ui.navigationVersion=0;
    const calls=[];let resolveOld;
    ui.rpc=async(method,args)=>{
        calls.push({method,args});
        if(calls.length===1)return new Promise(r=>resolveOld=r);
        assert.notDeepEqual(ui.state.data.rows,[{id:'wrong-batch'}]);
        return {rows:[{id:'batch3'}]};
    };
    const staleRefresh=ui.refresh();
    const batchClick=ui.openBatch(3);
    resolveOld({rows:[{id:'wrong-batch'}]});
    // The obsolete response must never become visible while the next read starts.
    await new Promise(r=>setImmediate(r));
    await Promise.all([staleRefresh,batchClick]);
    assert.equal(calls.length,2);
    assert.equal(calls[1].args[2],3);
    assert.equal(calls[1].args[1],0);
    assert.deepEqual(ui.state.data.rows,[{id:'batch3'}]);
});
test('rapid batch switches install only final scope after pending refresh', async () => {
    const ui=fixture();ui.state.detail=null;ui.state.section='cases';ui.state.batch=1;ui.navigationVersion=0;
    let resolveOld;const seen=[];
    ui.rpc=async(method,args)=>{seen.push(args[2]);return seen.length===1?new Promise(r=>resolveOld=r):{rows:[{id:args[2]}]};};
    const request=ui.refresh();ui.openBatch(2);ui.openBatch(3);resolveOld({rows:[{id:1}]});await request;
    assert.deepEqual(seen,[1,3]);assert.deepEqual(ui.state.data.rows,[{id:3}]);
});
test('library queries and pages are sent to server', async () => {
    const ui=fixture(); ui.state.libraryFilters={query:'vestido',platform:'SHEIN US',dateFrom:'',dateTo:''}; ui.state.libraryPage=0; ui.state.libraryPageSize=50; const calls=[];
    ui.rpc=async (...args)=>{calls.push(args);return {rows:[{id:51}],total:80,limit:50};};
    await ui.applyLibraryFilters();
    assert.deepEqual(calls[0],['workbench_library',['','',ui.state.libraryFilters,0]]);
    assert.equal(ui.state.libraryTotal,80);
    await ui.libraryPageBy(1);
    assert.equal(calls[1][1][3],50);
});
test('busy library request cannot install filters it did not fetch', async () => {
    const ui=fixture(); let done; ui.state.libraryFilters={query:'first'};ui.state.libraryApplied={};ui.state.libraryPage=0;ui.state.libraryPageSize=50;
    ui.rpc=()=>new Promise(r=>{done=r;}); const pending=ui.applyLibraryFilters();
    ui.state.libraryFilters={query:'second'}; await ui.applyLibraryFilters();
    assert.deepEqual(ui.state.libraryApplied,{query:'first'});
    done({rows:[],total:0,limit:50});await pending;
});
test('title optimization starts for the current case and does not change a later view', async () => {
    const ui=fixture(); ui.navigationVersion=0; const calls=[]; let resolve;
    ui.rpc=(method,args)=>{calls.push([method,args]); return new Promise(r=>resolve=r);};
    ui.load=async()=>{};
    const pending=ui.startTitleFlow();
    ui.back(); ui.state.detail={id:2,revision:1}; ui.state.tab='snapshot';
    resolve({stage:'facts'}); await pending;
    assert.deepEqual(calls,[['workbench_title_flow',[1]]]);
    assert.equal(ui.state.tab,'snapshot');
});
test('offline host hint is limited to active text tasks and a known offline heartbeat', () => {
    const ui=fixture();
    ui.state.detail.tasks=[{state:'running',task_type:'research.keywords'}];
    assert.equal(ui.hostOfflineHint, false);
    ui.state.detail.host_status={online:false};
    assert.equal(ui.hostOfflineHint, true);
    ui.state.detail.host_status.online=true;
    assert.equal(ui.hostOfflineHint, false);
    ui.state.detail.host_status.online=false;
    ui.state.detail.tasks=[{state:'running',task_type:'image.generate'}];
    assert.equal(ui.hostOfflineHint, false);
});
test('source image upload binds original case and snapshot revision without navigating later view', async () => {
    const ui=fixture(); ui.navigationVersion=0; ui.state.form={image_data:'aGVsbG8=',image_mime:'image/jpeg'};
    ui.state.sourceImageContext={version:0,case_id:1,revision:1}; let resolve; const calls=[];
    ui.rpc=(method,args)=>{calls.push([method,args]);return new Promise(r=>resolve=r);};
    const pending=ui.saveSnapshotImage(); ui.back();ui.state.detail={id:2,revision:1};
    resolve({id:1,revision:2}); await pending;
    assert.deepEqual(calls,[['workbench_snapshot_source_image',[1,'aGVsbG8=','image/jpeg',1]]]);
    assert.equal(ui.state.detail.id,2);
});
test('source image selection ignores earlier file reads and a reopened dialog', async () => {
    const ui=fixture(); ui.navigationVersion=0; ui.snapshotImageForm();
    const readers=[]; globalThis.FileReader=class { readAsDataURL() { readers.push(this); } };
    const a=ui.readSnapshotImage({target:{files:[{size:1,type:'image/jpeg'}]}});
    const b=ui.readSnapshotImage({target:{files:[{size:1,type:'image/png'}]}});
    readers[1].result='data:image/png;base64,Qg==';readers[1].onload(); await b;
    readers[0].result='data:image/jpeg;base64,QQ==';readers[0].onload();await a;
    assert.equal(ui.state.form.image_data,'Qg==');
    const c=ui.readSnapshotImage({target:{files:[{size:1,type:'image/jpeg'}]}});
    ui.snapshotImageForm(); readers[2].result='data:image/jpeg;base64,Qw==';readers[2].onload();await c;
    assert.equal(ui.state.form.image_data,undefined);
});
test('collection image reads cannot overwrite newer choice or reopened form', async () => {
    const ui=fixture();ui.collectionForm();const readers=[];globalThis.FileReader=class {readAsDataURL(){readers.push(this);}};
    const a=ui.readGarment({target:{files:[{size:1,type:'image/jpeg'}]}});const b=ui.readGarment({target:{files:[{size:1,type:'image/png'}]}});
    readers[1].result='data:image/png;base64,Qg==';readers[1].onload();await b;readers[0].result='data:image/jpeg;base64,QQ==';readers[0].onload();await a;assert.equal(ui.state.form.image_data,'Qg==');
    const c=ui.readGarment({target:{files:[{size:1,type:'image/jpeg'}]}});ui.collectionForm();readers[2].result='data:image/jpeg;base64,Qw==';readers[2].onload();await c;assert.equal(ui.state.form.image_data,undefined);
});
test('late open cannot override navigation or report stale errors', async () => {
    const ui=fixture();ui.navigationVersion=0;let resolve,reject;ui.rpc=()=>new Promise((r,j)=>{resolve=r;reject=j});
    let pending=ui.open(1);ui.back();ui.state.section='library';resolve({id:1,collection:true});await pending;assert.equal(ui.state.detail,null);assert.equal(ui.state.section,'library');
    pending=ui.open(2);ui.back();ui.state.section='cases';reject(new Error('old error'));await pending;assert.notEqual(ui.state.error,'old error');
});
test('library garment upload binds file and case before and after asynchronous reads', async () => {
    const ui=fixture();ui.navigationVersion=0;ui.state.modal='library-select';ui.state.form={};ui.state.libraryContext={version:0,case_id:1,revision:1};const readers=[];globalThis.FileReader=class {readAsDataURL(){readers.push(this);}};
    const calls=[];ui.rpc=async(method,args)=>{calls.push([method,args]);return 123};ui.load=async()=>{};
    let pending=ui.uploadGarment({target:{files:[{size:1,type:'image/png'}]}});readers[0].result='data:image/png;base64,QQ==';readers[0].onload();await pending;
    assert.deepEqual(calls[0][1],[1,'QQ==','image/png']);assert.equal(ui.state.garmentArtifact,'123');
    pending=ui.uploadGarment({target:{files:[{size:1,type:'image/png'}]}});ui.back();ui.state.detail={id:2,revision:1};ui.state.modal='library-select';readers[1].result='data:image/png;base64,Qg==';readers[1].onload();await pending;assert.equal(calls.length,1);
    ui.state.detail={id:3,revision:1};ui.state.libraryContext={version:ui.navigationVersion,case_id:3,revision:1};let resolve;ui.rpc=()=>new Promise(r=>resolve=r);ui.state.garmentArtifact='';
    pending=ui.uploadGarment({target:{files:[{size:1,type:'image/png'}]}});readers[2].result='data:image/png;base64,Qw==';readers[2].onload();await new Promise(r=>setTimeout(r,0));ui.back();ui.state.detail={id:4,revision:1};resolve(555);await pending;assert.equal(ui.state.garmentArtifact,'');
});
test('library upload locks controls and ignores a second file until finished', async () => {
    const ui=fixture();ui.navigationVersion=0;ui.state.modal='library-select';ui.state.form={};ui.state.libraryContext={version:0,case_id:1,revision:1};const readers=[];globalThis.FileReader=class {readAsDataURL(){readers.push(this);}};let resolve;const calls=[];ui.rpc=(method,args)=>{calls.push(args);return new Promise(r=>resolve=r)};ui.load=async()=>{};
    const a=ui.uploadGarment({target:{files:[{size:1,type:'image/png'}]}});assert.equal(ui.state.garmentUploadBusy,true);readers[0].result='data:image/png;base64,QQ==';readers[0].onload();await new Promise(r=>setTimeout(r,0));
    await ui.uploadGarment({target:{files:[{size:1,type:'image/jpeg'}]}});assert.equal(readers.length,1);assert.equal(calls.length,1);resolve(101);await a;assert.equal(ui.state.garmentUploadBusy,false);assert.equal(ui.state.garmentArtifact,'101');
    const xml=await readFile(new URL('../addons/meli_optimization/static/src/workbench.xml',import.meta.url),'utf8');assert.ok(xml.includes("服装参考图自动使用当前商品快照的全部原图"));assert.ok(xml.includes("!productImages(state.detail.snapshot).length"));
});
test('resume image review submits original task and ignores a later navigation', async () => {
    const ui=fixture(); ui.navigationVersion=0; let resolve; const calls=[];
    ui.rpc=(method,args)=>{calls.push([method,args]);return new Promise(r=>resolve=r);};
    let loaded=false;ui.load=async()=>{loaded=true};
    const pending=ui.resumeImageReview({id:42,snapshot_id:1,status_detail:{code:'host_review_required'}});
    ui.back();ui.state.detail={id:2,revision:1};resolve({});await pending;
    assert.deepEqual(calls,[['workbench_request_host_review_resume',[1,42,null]]]);
    assert.equal(loaded,false);
    await ui.resumeImageReview({id:43,snapshot_id:1,status_detail:{code:'other'}});
    assert.equal(calls.length,1);
});
test('review migration requires another click and does not report requested', async () => {
    const ui=fixture(); ui.navigationVersion=0; const notices=[];
    ui.notification={add:(message)=>notices.push(message)};ui.load=async()=>{};
    ui.rpc=async()=>false;
    await ui.resumeImageReview({id:42,snapshot_id:1,status_detail:{code:'host_review_required'}});
    assert.deepEqual(notices,['旧复核状态已恢复，请再次点击继续复核']);
});

test('best review binds current candidate and exact review round', async () => {
 const ui=fixture();ui.navigationVersion=0;ui.load=async()=>{};const calls=[];ui.rpc=async(...a)=>{calls.push(a);return true;};
 await ui.resumeImageReview({id:42,snapshot_id:1,status_detail:{code:'host_review_required',review_kind:'best_of_budget',candidate_artifact_id:7,candidate_sha256:'digest',candidate_attempt_id:'run1',candidate_attempt_number:2,review_seq:3}});
 assert.deepEqual(calls,[['workbench_request_best_review_resume',[1,42,7,'digest','run1',2,3]]]);
});
test('snapshot gallery sync is bound to case and revision and gallery keys are unique', async () => {
    const ui=fixture(), calls=[]; ui.navigationVersion=0; ui.disposed=false;
    ui.rpc=async (...args)=>{calls.push(args); return {};}; ui.load=async()=>calls.push(['reload']);
    await ui.syncSnapshotGallery();
    assert.deepEqual(calls,[['workbench_archive_snapshot_gallery',[1,1]],['reload']]);
    const xml=await readFile(new URL('../addons/meli_optimization/static/src/workbench.xml',import.meta.url),'utf8');
    assert.ok(xml.includes('t-on-click="syncSnapshotGallery"'));
    assert.equal(xml.includes('t-key="image.image_id"'),false);
});

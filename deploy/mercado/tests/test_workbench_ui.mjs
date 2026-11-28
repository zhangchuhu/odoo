import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { candidateRows, moveSelection, displayValue } from '../addons/meli_optimization/static/src/workbench_helpers.js';
test('independent collection empty state directs operator to start explicitly', () => {
    const xml = readFileSync(new URL('../addons/meli_optimization/static/src/workbench.xml', import.meta.url), 'utf8');
    assert.match(xml, /尚未开始采集。点击「一键开始采集」后/);
});
test('renders real result cards and preserves unknown metrics', () => {
    assert.equal(displayValue(null), '未知');
    assert.equal(displayValue(0), '0');
    assert.equal(displayValue(false), '否');
    assert.deepEqual(candidateRows({facts: [{name:'库存', value:null}]}), [{name:'库存', value:null}]);
});
test('image ordering preserves selected identity and rejects bounds', () => {
    assert.deepEqual(moveSelection([2,0,1], 1, -1), [0,2,1]);
    assert.deepEqual(moveSelection([2,0,1], 0, -1), [2,0,1]);
});

// A stale completed task must never replace facts for the latest snapshot.
const helpers = await import('../addons/meli_optimization/static/src/workbench_helpers.js');
test('new SHEIN collections use the versioned homepage strategy', () => {
    assert.equal(helpers.collectionSubmission({platform:'shein-us',min_sold:'500',min_reviews:'20',min_rating:'4.1',result_limit:'5'}).strategy, 'shein-home-v1');
    assert.equal(helpers.collectionSubmission({platform:'mercado-libre-mx',strategy:'shein-home-v1',min_sold:'500',min_reviews:'20',min_rating:'4.1',result_limit:'5'}).strategy, undefined);
});
test('image plan counts skipped pictures as finished without counting retry history twice', () => {
    const plan = {operation:'garment_swap',snapshot_id:3,selected_indices:[0,1],target_artifact_ids:['a','b']};
    const base={task_type:'image.generate',generation_plan_id:'8',snapshot_id:3};
    const tasks=[{...base,id:1,target_artifact_id:'a',state:'failed'},{...base,id:3,target_artifact_id:'a',state:'succeeded'},
                 {...base,id:2,target_artifact_id:'b',state:'skipped'}];
    const group=helpers.imagePlanGroups(tasks,[{id:8,payload:plan}],3)[0];
    assert.deepEqual([group.completed,group.skipped,group.failed,group.total,group.summary],[1,1,0,2,'已完成']);
});
test('image task queue includes the skip count', () => {
    const tasks = [{id:1,task_type:'image.generate',generation_plan_id:'8',snapshot_id:3,state:'succeeded'},
                   {id:2,task_type:'image.generate',generation_plan_id:'8',snapshot_id:3,state:'skipped'}];
    const group = helpers.groupImageTasks(tasks)[0];
    assert.equal(group.skipped, 1);
});
test('image task queue counts a target once when it has attempts', () => {
    const base={task_type:'image.generate',generation_plan_id:'8',snapshot_id:3};
    const tasks=[{...base,id:1,target_artifact_id:'a',state:'failed'},
                 {...base,id:2,target_artifact_id:'a',state:'succeeded'},
                 {...base,id:3,target_artifact_id:'b',state:'skipped'}];
    const group=helpers.groupImageTasks(tasks)[0];
    assert.deepEqual([group.total,group.completed,group.skipped],[2,1,1]);
});
test('image plan uses frozen library order even when tasks finish in another order', () => {
    const plan = {selected_indices:[8,0,4],target_artifact_ids:['a','b','c'],snapshot_id:3};
    const tasks = [
      {id:4,snapshot_id:3,target_artifact_id:'a',state:'succeeded'},
      {id:2,snapshot_id:3,target_artifact_id:'b',state:'succeeded'},
      {id:3,snapshot_id:3,target_artifact_id:'c',state:'running'}];
    assert.deepEqual(helpers.imagePlanRows(plan,tasks).map(row=>[row.libraryIndex,row.targetId,row.task.id]), [[0,'b',2],[4,'c',3],[8,'a',4]]);
});
test('image plan rejects ambiguous target order rather than guessing', () => {
    assert.equal(helpers.imagePlanRows({selected_indices:[0,0],target_artifact_ids:['a','b']},[]), null);
    assert.equal(helpers.imagePlanRows({selected_indices:[0],target_artifact_ids:['a','b']},[]), null);
    assert.equal(helpers.imagePlanRows({selected_indices:[0,1],target_artifact_ids:['a','a']},[]), null);
});
test('image plan groups only tasks in the same plan and snapshot', () => {
    const tasks=[{id:1,task_type:'image.generate',generation_plan_id:'8',snapshot_id:3,target_artifact_id:'12',state:'succeeded'},
      {id:2,task_type:'image.generate',generation_plan_id:'9',snapshot_id:3,target_artifact_id:'12',state:'succeeded'}];
    const artifacts=[{id:8,payload:{operation:'garment_swap',snapshot_id:3,selected_indices:[1],target_artifact_ids:['12']}},
      {id:9,payload:{operation:'garment_swap',snapshot_id:3,selected_indices:[1],target_artifact_ids:['12']}}];
    assert.deepEqual(helpers.imagePlanGroups(tasks,artifacts,3).map(g=>g.rows[0].task.id),[1,2]);
    assert.deepEqual(helpers.imagePlanGroups(tasks,artifacts,4),[]);
});
test('image results show grouped plan order and ambiguous history explicitly', () => {
    const xml = readFileSync(new URL('../addons/meli_optimization/static/src/workbench.xml', import.meta.url), 'utf8');
    assert.match(xml, /目标构图顺序/);
    assert.match(xml, /顺序待确认/);
    assert.match(xml, /目标构图和生成图汇总/);
});
test('restored location rejects invalid sections, IDs, tabs, and scroll offsets', () => {
    assert.deepEqual(helpers.workbenchLocation({section:'cases',caseId:23,tab:'images',scrollTop:325}),
                     {section:'cases',caseId:23,tab:'images',scrollTop:325});
    assert.deepEqual(helpers.workbenchLocation({section:'invalid',caseId:-1,tab:'bad',scrollTop:-3}),
                     {section:'cases',caseId:null,tab:'snapshot',scrollTop:0});
    assert.deepEqual(helpers.workbenchLocation({section:'library',caseId:23,tab:'images',scrollTop:0}),
                     {section:'library',caseId:null,tab:'snapshot',scrollTop:0});
});
test('list position keeps safe filters and rejects malformed values', () => {
    assert.deepEqual(helpers.workbenchListPosition({query:'MLM1',page:2,batch:5,status:'waiting',shop:'Shop',site:'MLM'}),
        {query:'MLM1',page:2,batch:5,status:'waiting',shop:'Shop',site:'MLM'});
    assert.deepEqual(helpers.workbenchListPosition({query:42,page:-1,batch:-1,status:{},shop:null,site:[]} ),
        {query:'',page:0,batch:'',status:'',shop:'',site:''});
    assert.equal(helpers.workbenchListPosition({batch:'5'}).batch,5);
});
test('image awaiting a user decision is displayed as waiting, not executing', () => {
    assert.equal(helpers.imageReviewWaiting({task_type:'image.generate',state:'running',status_detail:{code:'host_review_required'}}), true);
    assert.equal(helpers.imageReviewWaiting({task_type:'image.generate',state:'running',status_detail:{code:'host_review_resumed'}}), false);
    assert.equal(helpers.imageReviewWaiting({task_type:'product.facts',state:'running',status_detail:{code:'host_review_required'}}), false);
});
test('groups image tasks only by matching plan and snapshot while retaining each task', () => {
    const tasks=[{id:8,task_type:'image.generate',snapshot_id:3,generation_plan_id:'9',target_artifact_id:'12',state:'queued'},
      {id:7,task_type:'image.generate',snapshot_id:3,generation_plan_id:'9',target_artifact_id:'11',state:'succeeded'},
      {id:6,task_type:'image.generate',snapshot_id:4,generation_plan_id:'9',state:'failed'},
      {id:5,task_type:'product.facts',state:'succeeded'}];
    const groups=helpers.groupImageTasks(tasks);
    assert.deepEqual(groups[0].tasks.map(t=>t.id),[7,8]);
    assert.equal(groups[0].completed,1);
    assert.equal(groups[0].total,2);
    assert.deepEqual(groups.slice(1).map(g=>g.tasks[0].id),[6,5]);
});
test('unstarted independent collection hides pending workflow tasks but preserves completed history', () => {
    const tasks=[{id:1,task_type:'product.facts',state:'succeeded'},{id:2,task_type:'research.keywords',state:'queued'},{id:3,task_type:'research.bestseller',state:'waiting'},{id:4,task_type:'image.qc',state:'queued'}];
    assert.deepEqual(helpers.visibleCollectionTasks(tasks,{collection:true,collection_started:false}).map(t=>t.id),[1,4]);
    assert.deepEqual(helpers.visibleCollectionTasks(tasks,{collection:true,collection_started:true}).map(t=>t.id),[1,2,3,4]);
    assert.deepEqual(helpers.visibleCollectionTasks(tasks,{collection:false}).map(t=>t.id),[1,2,3,4]);
});
test('facts view keeps image observations separate from snapshot facts', () => {
    const result={facts:[{name:'title',value:'Dress',evidence_ids:['s']},{name:'sleeves',value:'short',category:'visual',evidence_ids:['i']}],evidence:[{id:'s',source:'snapshot:2',observed_at:'now'},{id:'i',source:'image:sha256:abc',observed_at:'now'}],uncertainties:['Material unknown']};
    const view=helpers.productFactsView({snapshot:{id:2},tasks:[{id:1,task_type:'product.facts',snapshot_id:2,state:'succeeded',result}]}).current;
    assert.deepEqual(view.knownFacts.map(f=>f.name),['原标题']);
    assert.deepEqual(view.visualFacts.map(f=>f.name),['sleeves']);
    assert.equal(view.visualFacts[0].evidence[0].source,'image:sha256:abc');
    assert.deepEqual(view.uncertainties,['Material unknown']);
});
test('facts view separates current completion from historical and pending tasks', () => {
    assert.equal(typeof helpers.productFactsView, 'function', 'Facts detail view is missing');
    const result={raw_output:'PRIVATE MODEL OUTPUT',facts:[{name:'title',value:'Observed',evidence_ids:['s']},{name:'access_token',value:'PRIVATE CREDENTIAL',evidence_ids:['s']}],evidence:[{id:'s',source:'snapshot:2',observed_at:'2026-11-15T00:00:00Z'}],uncertainties:['Unverified']};
    const old={id:9,task_type:'product.facts',snapshot_id:1,state:'succeeded',result};
    const current={...old,id:10,snapshot_id:2};
    const pending={...current,id:11,state:'running'};
    const view=helpers.productFactsView({snapshot:{id:2},tasks:[pending,old,current]});
    assert.equal(view.current.id,10);
    assert.equal(view.current.facts.length,1);
    assert.equal(JSON.stringify(view).includes('PRIVATE'),false);
    assert.equal(view.current.facts[0].name,'原标题');
    assert.equal(view.current.facts[0].evidence[0].source,'snapshot:2');
    assert.deepEqual(view.current.uncertainties,['Unverified']);
    assert.deepEqual(view.history.map(t=>t.id),[9]);
    assert.equal(helpers.productFactsView({snapshot:{id:3},tasks:[old]}).status,'stale');
    assert.equal(helpers.productFactsView({snapshot:{id:2},tasks:[pending]}).status,'missing');
    assert.equal(helpers.productFactsView({snapshot:null,tasks:[old]}).status,'no_snapshot');
});
test('fact values retain zero and unknown nested SKU observations and omit credentials', () => {
    assert.equal(typeof helpers.factValueRows,'function','Structured fact values are missing');
    assert.deepEqual(helpers.factValueRows([{seller_sku:'A',available_quantity:0,size:null,access_token:'PRIVATE'}]),[
        {path:'1 · SKU',value:'A'},{path:'1 · 库存',value:'0'},{path:'1 · 尺码',value:'未知'}]);
    assert.deepEqual(helpers.factValueRows(false),[{path:'',value:'否'}]);
    assert.deepEqual(helpers.factValueRows([]),[{path:'',value:'空列表'}]);
    assert.deepEqual(helpers.factValueRows({}),[{path:'',value:'空记录'}]);
});
test('library filters match source SKU, title, platform and date without treating missing values as matches', () => {
    const rows=[{source_sku:'SKU-01',title:'Vestido Azul',platform:'SHEIN US',listed_at:'2026-01-02'}, {source_sku:false,title:'Other',platform:'Mercado Libre México',listed_at:false}];
    assert.deepEqual(helpers.filterLibraryRows(rows,{query:'azul',platform:'SHEIN US'}),[rows[0]]);
    assert.deepEqual(helpers.filterLibraryRows(rows,{query:'SKU-01',dateFrom:'2026-01-01'}),[rows[0]]);
    assert.deepEqual(helpers.filterLibraryRows(rows,{dateFrom:'2026-01-01'}),[rows[0]]);
});
test('library detail describes evidence metadata provenance', () => {
    assert.equal(helpers.libraryMetadataOrigin('import_frozen'), '采集时归档');
    assert.equal(helpers.libraryMetadataOrigin('legacy_backfill'), '历史记录补显');
    assert.equal(helpers.libraryMetadataOrigin('unknown'), '未知');
    assert.equal(helpers.libraryMetadataOrigin(null), '未知');
});
test('product images retain snapshot order and handle absent images', () => {
    assert.deepEqual(helpers.productSourceImages({source_images:[{artifact_id:4},{url:'https://example.test/b.jpg'}]}),[{artifact_id:4},{url:'https://example.test/b.jpg'}]);
    assert.deepEqual(helpers.productSourceImages({source_image_artifact_ids:[7,3]}),[{artifact_id:7},{artifact_id:3}]);
    assert.deepEqual(helpers.productSourceImages({}),[]);
});

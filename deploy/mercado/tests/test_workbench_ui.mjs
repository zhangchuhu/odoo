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

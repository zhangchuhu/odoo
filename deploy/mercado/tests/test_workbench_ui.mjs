import { test } from 'node:test';
import assert from 'node:assert/strict';
import { candidateRows, moveSelection, displayValue } from '../addons/meli_optimization/static/src/workbench_helpers.js';
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

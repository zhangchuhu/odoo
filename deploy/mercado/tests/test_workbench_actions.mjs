import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const source = (await readFile(new URL('../addons/meli_optimization/static/src/workbench.js', import.meta.url), 'utf8'))
    .replace(/^import .*;$/gm, '')
    .replace("registry.category('actions').add('meli_optimization.workbench', OptimizationWorkbench);", '');
const { OptimizationWorkbench } = await import('data:text/javascript,' + encodeURIComponent('class Component {}\n' + source));
function fixture() {
    const ui = Object.create(OptimizationWorkbench.prototype);
    ui.state = {loading:false, detail:{id:1,revision:1,snapshot:{id:1},artifacts:[]}, selections:{7:[0]},modal:'',preview:null};
    ui.notification = {add(){}};
    return ui;
}
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
test('library selection submits exact indices and source garment separately', async () => {
    const ui=fixture(), calls=[];
    ui.state.librarySelection={id:20,payload:{images:[{artifact_id:'8'}]}};
    ui.state.libraryIndices=[0];ui.state.garmentArtifact='9';ui.state.libraryContext={case_id:1,revision:1};
    ui.load=async()=>{};
    ui.rpc=async (...args)=>{calls.push(args);return {task_ids:[30]};};
    await ui.saveLibrarySelection();
    assert.deepEqual(calls,[['workbench_library_generate',[1,20,[0],[9],1]]]);
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

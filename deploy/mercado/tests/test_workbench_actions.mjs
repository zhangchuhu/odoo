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

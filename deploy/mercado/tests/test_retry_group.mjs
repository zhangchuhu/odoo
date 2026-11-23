import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const s=await readFile(new URL('../addons/meli_optimization/static/src/workbench_helpers.js', import.meta.url),'utf8');
const {groupResearchRetries}=await import('data:text/javascript,'+encodeURIComponent(s));
test('groups only proven retry edges and keeps latest task operations',()=>{
 const tasks=[{id:3,task_type:'research.bestseller',snapshot_id:9,state:'queued'},{id:2,task_type:'research.bestseller',snapshot_id:9,state:'waiting'},{id:1,task_type:'research.bestseller',snapshot_id:9,state:'waiting'},{id:4,task_type:'research.bestseller',snapshot_id:9,state:'queued'}];
 const rows=groupResearchRetries(tasks,[{kind:'unknown_research_retry',task_id:[1,''],receipt:{source_task_id:1,task_id:2}},{kind:'unknown_research_retry',task_id:[2,''],receipt:{source_task_id:2,task_id:3}}]);
 assert.equal(rows.length,2); assert.equal(rows[0].id,4); assert.equal(rows[1].id,3); assert.deepEqual(rows[1].retry_history.map(x=>x.id),[1,2,3]);
});
test('same task attempts retain their epoch and time without inventing outcomes',()=>{
 const rows=groupResearchRetries([{id:10,task_type:'product.facts',snapshot_id:2,state:'running',attempts:[{epoch:1,created_at:'a'},{epoch:2,created_at:'b'}]}],[]);
 assert.deepEqual(rows[0].attempt_history.map(x=>[x.task_id,x.epoch,x.created_at]),[[10,1,'a'],[10,2,'b']]);
});

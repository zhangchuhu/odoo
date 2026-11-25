const test=require('node:test'),assert=require('node:assert/strict');
for(const folder of ['chrome_extension','shein_extension']){
 test(folder+' accepts semantically identical restored cards with reordered object keys',async()=>{
  const {SessionPersistence}=require('../'+folder+'/session_persistence.js');let data={};
  const reorder=x=>Array.isArray(x)?x.map(reorder):x&&typeof x==='object'?Object.fromEntries(Object.keys(x).sort().map(k=>[k,reorder(x[k])])):x;
  const storage={async get(k){return {[k]:reorder(data[k])}},async set(v){Object.assign(data,JSON.parse(JSON.stringify(v)))}};
  const p=new SessionPersistence(storage), session={taskRecordId:'recabc',platform:'p',queries:['x'],queryBlocks:[{observations:[{z:1,a:2}]}],details:[]};
  await p.save(15,'a'.repeat(32),session);session.queryBlocks[0].observations.push({z:3,a:4});
  await p.save(15,'a'.repeat(32),session);
 });
 test(folder+' preserves and isolates task and Chrome session',async()=>{
  const {SessionPersistence}=require('../'+folder+'/session_persistence.js');
  let data={};const storage={async get(k){return {[k]:data[k]}},async set(v){Object.assign(data,JSON.parse(JSON.stringify(v)))}};
  const p=new SessionPersistence(storage);const session={taskRecordId:'recabc',platform:'p',queryBlocks:[{observations:[1]}]};
  await p.save(15,'a'.repeat(32),session);
  assert.deepEqual(await p.load(15,'a'.repeat(32),'recabc'),session);
  assert.equal(await p.load(16,'a'.repeat(32),'recabc'),null);
  assert.equal(await p.load(15,'b'.repeat(32),'recabc'),null);
  assert.equal(await p.load(15,'a'.repeat(32),'recother'),null);
  await assert.rejects(()=>p.save(15,'a'.repeat(32),{...session,queryBlocks:[]}));
  await assert.rejects(()=>p.save(15,'a'.repeat(32),{...session,queryBlocks:[{observations:[2]}]}));
 });
}

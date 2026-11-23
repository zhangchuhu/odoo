const test=require('node:test'),assert=require('node:assert/strict');
for(const name of ['chrome_extension','shein_extension']){
 const {downloadEvidence}=require('../'+name+'/evidence_download.js');
 test(name+' reports only completed download and actual filename',async()=>{
  let n=0;const chrome={downloads:{download:async()=>7,search:async()=>[{id:7,state:++n===1?'in_progress':'complete',filename:'/private/allowed/evidence.json'}]}};
  assert.equal(await downloadEvidence(chrome,'blob:test','evidence.json',async()=>{}),'/private/allowed/evidence.json');
 });
 test(name+' rejects interrupted download',async()=>{
  const chrome={downloads:{download:async()=>7,search:async()=>[{id:7,state:'interrupted',error:'FILE_FAILED'}]}};
  await assert.rejects(downloadEvidence(chrome,'blob:test','evidence.json',async()=>{}),/download_interrupted/);
 });
}

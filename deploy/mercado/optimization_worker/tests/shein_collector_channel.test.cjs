const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
test('SHEIN collector verifies visible search and emits platform-bound receipt',async()=>{
 const actions=[],challenge={version:1,request_id:'req',nonce:'n'.repeat(32),lease_epoch:1,plan_digest:'a'.repeat(64),platform:'shein-us',query_index:0,query:'lace dress'};
 const chrome={runtime:{sendMessage:async(target)=>typeof target==='string'?{ok:true}:{session_id:'b'.repeat(32)},sendNativeMessage:async(_,v)=>{actions.push(v);if(v.operation==='peer_challenge')return {ok:true,token:'proof',epoch:1,anchor_id:'a'.repeat(32)};return v.operation==='poll'?{challenge}:{ok:true,request_id:'req'}}},tabs:{get:async()=>({id:15,windowId:4,url:'https://us.shein.com/pdsearch/lace-dress/'}),sendMessage:async(_,v)=>{assert.equal(v.type,'SHEIN_COLLECTOR_EXTRACT_SEARCH');return {page_url:'https://us.shein.com/pdsearch/lace-dress/',search_query:'lace dress'}}}};
 const status={textContent:''},button={addEventListener:(_,fn)=>button.click=fn};
 const c={chrome,document:{getElementById:id=>id==='binding-connect'?button:status},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:()=>{}};vm.createContext(c);
 for(const file of ['query_gate.js','binding_validation.js','peer_handshake.js','binding.js'])vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/shein_extension/'+file,'utf8'),c);
 assert.equal(actions.length,0);button.click();await new Promise(r=>setImmediate(r));assert.match(status.textContent,/已登记窗口/);assert.equal(actions.find(a=>a.operation==='submit').receipt.platform,'shein-us');
});
test('SHEIN session change requires reopen and stops polling',async()=>{
 const actions=[],status={textContent:''},button={addEventListener:(_,fn)=>button.click=fn};
 const chrome={runtime:{sendMessage:async(target)=>typeof target==='string'?{ok:true}:{session_id:'b'.repeat(32)},sendNativeMessage:async(_,v)=>{actions.push(v);if(v.operation==='peer_challenge')return {ok:true,token:'proof',epoch:1,anchor_id:'a'.repeat(32)};return {ok:false,request_id:'req',error:'session_changed_reopen'}}},tabs:{get:async()=>({id:15,windowId:4,url:'https://us.shein.com/'})}};
 const c={chrome,document:{getElementById:id=>id==='binding-connect'?button:status},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:()=>{}};vm.createContext(c);
 for(const f of ['query_gate.js','binding_validation.js','peer_handshake.js','binding.js'])vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/shein_extension/'+f,'utf8'),c);
 button.click();await new Promise(r=>setImmediate(r));assert.deepEqual(actions.map(x=>x.operation),['binding_status','peer_challenge','select_window']);assert.match(status.textContent,/重新打开/);
});

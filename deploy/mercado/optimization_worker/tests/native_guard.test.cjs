const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
for (const platform of ['chrome_extension','shein_extension']) test(platform+' reports the specific failed guard check without exposing session values',async()=>{
 const id='e'.repeat(32), prefix='chrome-extension://'+id+'/';
 const chrome={tabs:{get:async()=>({id:7,windowId:4})},runtime:{id,getURL:p=>prefix+p,
  sendNativeMessage:async()=>({ok:true,request_id:'r',matches:true,bound_tab_id:7,guard_required:false})}};
 const ctx=vm.createContext({chrome,URL,sessionId:async()=>('b'.repeat(32)),setTimeout,clearInterval,setInterval});
 vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/'+platform+'/native_guard.js','utf8'),ctx);
 const result=await new Promise(resolve=>vm.runInContext('NativeGuard.onMessage',ctx)(
  {type:'ensure_collector_guard',request_id:'r',tab_id:7,window_id:4},{id,url:prefix+'collector.html?tab=7'},resolve));
 assert.equal(result.error,'guard_binding_changed');assert.equal(result.diagnostic,'guard_not_required');
});
for(const platform of ['chrome_extension','shein_extension']) test(platform+' guard opens and disconnect revokes',async()=>{
 let receive,disconnect,sent=[],closed=false;
 const port={onMessage:{addListener:f=>receive=f},onDisconnect:{addListener:f=>disconnect=f},postMessage:m=>sent.push(m),disconnect:()=>{closed=true;disconnect()}};
 const ctx={chrome:{runtime:{connectNative:()=>port},tabs:{get:async()=>({id:7,windowId:4})}},setInterval:()=>1,clearInterval:()=>{}};vm.createContext(ctx);
 vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/'+platform+'/native_guard.js','utf8'),ctx);
 const guard=vm.runInContext("NativeGuard.create(7,4,'"+'a'.repeat(32)+"')",ctx);assert.equal(guard.available(),false);await guard.open();assert.equal(sent[0].operation,'guard_open');receive({ok:true});assert.equal(guard.available(),true);disconnect();assert.equal(guard.available(),false);
});
for(const platform of ['chrome_extension','shein_extension']) for(const failure of ['closed','moved']) test(platform+' disconnects when tab '+failure,async()=>{
 let receive,disconnect,tick,closed=false,sent=[],checks=0;
 const port={onMessage:{addListener:f=>receive=f},onDisconnect:{addListener:f=>disconnect=f},postMessage:m=>sent.push(m),disconnect:()=>{closed=true;disconnect()}};
 const ctx={chrome:{runtime:{connectNative:()=>port},tabs:{get:async()=>{if(!checks++) return {id:7,windowId:4};if(failure==='closed')throw Error('no tab');return {id:7,windowId:5}}}},setInterval:f=>{tick=f;return 1},clearInterval:()=>{}};vm.createContext(ctx);
 vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/'+platform+'/native_guard.js','utf8'),ctx);
 const guard=vm.runInContext("NativeGuard.create(7,4,'"+'a'.repeat(32)+"')",ctx);await guard.open();receive({ok:true});await tick();assert.equal(closed,true);assert.equal(guard.available(),false);assert.equal(sent.length,1);
});
for(const platform of ['chrome_extension','shein_extension']) test(platform+' refuses guard open on missing tab',async()=>{
 let connected=false;
 const ctx={chrome:{runtime:{connectNative:()=>{connected=true}},tabs:{get:async()=>{throw Error('no tab')}}},setInterval:()=>1,clearInterval:()=>{}};vm.createContext(ctx);
 vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/'+platform+'/native_guard.js','utf8'),ctx);
 const guard=vm.runInContext("NativeGuard.create(7,4,'"+'a'.repeat(32)+"')",ctx);await guard.open();assert.equal(connected,false);
});
for (const platform of ['chrome_extension', 'shein_extension']) test(platform+' worker waits for scoped guard ACK and reuses it after the popup closes', async () => {
 const id='e'.repeat(32), session='b'.repeat(32), prefix='chrome-extension://'+id+'/';
 let receive, disconnect, pings, active=false;
 const posts=[];
 const chrome={
  tabs:{get:async()=>({id:7,windowId:4})},
  runtime:{
   id,getURL:path=>prefix+path,
   sendNativeMessage:async()=>({ok:true,request_id:'r',matches:true,bound_tab_id:7,guard_required:true,guard_active:active}),
   connectNative:()=>({onMessage:{addListener:f=>receive=f},onDisconnect:{addListener:f=>disconnect=f},
     postMessage:m=>posts.push(m),disconnect:()=>disconnect()}),
  },
 };
 const ctx=vm.createContext({chrome,URL,sessionId:async()=>session,setInterval:f=>{pings=f;return 1;},clearInterval:()=>{},setTimeout});
 vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/'+platform+'/native_guard.js','utf8'),ctx);
 const send=(msg, from=prefix+'collector.html?tab=7')=>new Promise(resolve=>{
   const accepted=vm.runInContext('NativeGuard.onMessage',ctx)(msg,{id,url:from},resolve);
   if (!accepted) resolve(false);
 });
 const message={type:'ensure_collector_guard',request_id:'r',tab_id:7,window_id:4};
 assert.equal(await send(message,prefix+'collector.html?tab=8'),false);
 let acknowledged=false;
 const opening=send(message).then(reply=>{acknowledged=reply.ok;});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(posts[0].request_id,'r');assert.equal(posts[0].operation,'guard_open');
 assert.equal(acknowledged,false);receive({ok:true});active=true;await opening;assert.equal(acknowledged,true);
 await pings();assert.equal(posts[1].operation,'guard_ping');
 assert.equal((await send(message)).ok,true);assert.equal(posts.filter(p=>p.operation==='guard_open').length,1);
});

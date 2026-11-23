const test=require('node:test');const assert=require('node:assert/strict');
const {createTaskTab}=require('../chrome_extension/task_tab_create.js');
test('creates task tab only after challenge and confirms exact identity',async()=>{
 let calls=[];const api={runtime:{sendNativeMessage:async(_h,m)=>{calls.push(m);return m.operation==='task_tab_challenge'?{ok:true,token:'t',epoch:4,platform:'mercado-libre-mx'}:{ok:true}}},tabs:{create:async v=>{calls.push(v);return {id:12,windowId:v.windowId}},update:async()=>{}}};
 const r=await createTaskTab(api,'host','request','a'.repeat(32),7);assert.equal(r.tab_id,12);assert.equal(calls[1].windowId,7);assert.equal(calls[2].operation,'task_tab_confirm');assert.equal(calls[2].tab_id,12);
});
test('uses SHEIN only when the broker challenge specifies that platform',async()=>{
 let created;const api={runtime:{sendNativeMessage:async(_h,m)=>m.operation==='task_tab_challenge'?{ok:true,token:'t',epoch:1,platform:'shein-us'}:{ok:true}},tabs:{create:async v=>{created=v;return {id:9,windowId:v.windowId}}}};
 await createTaskTab(api,'h','r','a'.repeat(32),8);assert.equal(created.url,'https://us.shein.com/');
});
test('does not create without challenge and does not report success without confirmation',async()=>{
 let count=0;const api={runtime:{sendNativeMessage:async()=>({ok:false})},tabs:{create:async()=>{count++}}};await assert.rejects(()=>createTaskTab(api,'h','r','a'.repeat(32),7));assert.equal(count,0);
});

test('second open reuses the exact live tab from the authenticated broker without creating another',async()=>{
 const session='a'.repeat(32), request='req';
 const reply={ok:true,epoch:1,request_id:request,session_id:session,window_id:7,bound_tab_id:12,platform:'mercado-libre-mx'};
 let count=0;
 const api={runtime:{sendNativeMessage:async()=>reply},tabs:{
  get:async id=>({id,windowId:7,url:'https://articulo.mercadolibre.com.mx/MLM-4123'}),
  create:async()=>{count++;throw Error('unexpected')},
 }};
 assert.deepEqual(await createTaskTab(api,'h',request,session,7),{tab_id:12,window_id:7,reused:true});
 assert.equal(count,0);
 for(const key of ['request_id','window_id','session_id']){
  const previous=reply[key];reply[key]='invalid';
  await assert.rejects(createTaskTab(api,'h',request,session,7),/binding_mismatch/);
  reply[key]=previous;
 }
 api.tabs.get=async id=>({id,windowId:9,url:'https://www.mercadolibre.com.mx/'});
 await assert.rejects(createTaskTab(api,'h',request,session,7),/window_changed/);
 api.tabs.get=async id=>({id,windowId:7,url:'https://www.mercadolibre.com.mx/',pendingUrl:'https://other.invalid/'});
 await assert.rejects(createTaskTab(api,'h',request,session,7),/platform_mismatch/);
});
test('pending task-tab creation polls binding status without opening or requesting another tab',async()=>{
 const operations=[];
 const api={runtime:{sendNativeMessage:async(_,m)=>{
   operations.push(m.operation);
   if(m.operation==='task_tab_challenge')return {ok:false,error:'task_tab_pending'};
   return {ok:true,request_id:'r',anchor_required:true,anchor_active:true,bound_tab_id:14,platform:'mercado-libre-mx'};
 }},tabs:{
   query:async()=>[{id:19,windowId:7}],get:async id=>({id,windowId:7,url:'https://www.mercadolibre.com.mx/'}),
   create:async()=>assert.fail('must reuse existing creation'),
 }};
 const found=await createTaskTab(api,'h','r','a'.repeat(32),7);assert.equal(found.tab_id,14);assert.equal(found.reused,true);
 assert.deepEqual(operations,['task_tab_challenge','binding_status']);
});

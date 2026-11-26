const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
test('activation waits for in-flight polling and blocks new polls',async()=>{
 let tick,release,activated=0,block=false;const elements={};for(const id of ['binding-connect','binding-activate'])elements[id]={addEventListener:(_,fn)=>elements[id].click=fn};elements['binding-status']={};elements['binding-identity']={};
 const chrome={runtime:{sendMessage:async()=>({session_id:'b'.repeat(32)}),sendNativeMessage:async(_,v)=>v.operation==='binding_status'?{ok:true,request_id:'req',bound_tab_id:15,matches:true}:v.operation==='select_window'?{ok:true}:{challenge:null}},tabs:{get:async()=>{if(block)await new Promise(r=>release=r);return {id:15,windowId:4,url:'https://listado.mercadolibre.com.mx/dress'}}}};
 const c={chrome,document:{getElementById:id=>elements[id]},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:fn=>tick=fn,activateCollectorTarget:async()=>{activated++}};vm.createContext(c);vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/binding.js','utf8'),c);
 elements['binding-connect'].click();await new Promise(r=>setTimeout(r,20));block=true;tick();await new Promise(r=>setTimeout(r,5));const pending=elements['binding-activate'].click();block=false;tick();release();await pending;assert.equal(activated,1);
});
test('repeated successful polling does not flood diagnostic log',async()=>{
 let interval; const rows=[],button={addEventListener:(_,fn)=>button.click=fn};
 const status={textContent:''};const chrome={runtime:{sendMessage:async()=>({session_id:'b'.repeat(32)}),sendNativeMessage:async(_,v)=>v.operation==='binding_status'?{ok:true,request_id:'req',bound_tab_id:15,matches:true}:v.operation==='select_window'?{ok:true}:{challenge:null}},tabs:{get:async()=>({id:15,windowId:4,url:'https://listado.mercadolibre.com.mx/dress'})}};
 const c={chrome,document:{getElementById:id=>id==='binding-connect'?button:status},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:fn=>interval=fn,CollectorTrace:{append:async(s,k)=>rows.push([s,k]),render:async()=>{}}};vm.createContext(c);vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/binding.js','utf8'),c);
 button.click();await new Promise(r=>setImmediate(r));for(let n=0;n<5;n++){interval();await new Promise(r=>setImmediate(r))}assert.equal(rows.filter(x=>x[0]==='binding_identity'&&x[1]==='ok').length,1);assert.equal(rows.filter(x=>x[0]==='binding_status'&&x[1]==='started').length,0);
});
test('activation distinguishes disconnected and busy states in diagnostics',async()=>{
 const records=[],elements={};for(const id of ['binding-connect','binding-activate'])elements[id]={addEventListener:(_,fn)=>elements[id].click=fn};
 elements['binding-status']={};elements['binding-identity']={};
 const c={chrome:{tabs:{get:()=>new Promise(()=>{})},runtime:{}},document:{getElementById:id=>elements[id]},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:()=>{},CollectorTrace:{append:async(s,k)=>records.push([s,k]),render:async()=>{}}};vm.createContext(c);
 vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/binding.js','utf8'),c);
 await elements['binding-activate'].click();elements['binding-connect'].click();await elements['binding-activate'].click();
 assert.deepEqual(records.filter(x=>x[0]==='binding_activate').map(x=>x[1]),['clicked','not_selected','clicked','busy']);
});
async function execute(searchQuery,{click=true,selectResponse={ok:true},boundTab=15}={}) {
 const actions=[],status={textContent:''},identity={textContent:''},button={addEventListener:(_,fn)=>button.click=fn,disabled:false},challenge={version:1,request_id:'req',nonce:'n'.repeat(32),lease_epoch:1,plan_digest:'a'.repeat(64),platform:'mercado-libre-mx',query_index:0,query:'vestido'};
 const chrome={runtime:{sendMessage:async()=>({session_id:'b'.repeat(32)}),sendNativeMessage:async(_,data)=>{actions.push(data);return data.operation==='binding_status'?{ok:true,request_id:'req',bound_tab_id:boundTab,matches:boundTab===15}:data.operation==='poll'?{challenge}:data.operation==='select_window'?selectResponse:{ok:true}}},tabs:{get:async()=>({id:15,windowId:4,url:'https://listado.mercadolibre.com.mx/vestido'}),sendMessage:async()=>({page_url:'https://listado.mercadolibre.com.mx/vestido',search_query:searchQuery})}};
 const c={chrome,document:{getElementById:id=>id==='binding-connect'?button:id==='binding-identity'?identity:status},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:()=>{}};vm.createContext(c);
 for(const f of ['query_gate.js','binding_validation.js','binding.js'])vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/'+f,'utf8'),c);
 if(click) button.click(); await new Promise(resolve=>setImmediate(resolve));return {actions,status,identity};
}
test('old collector does not select a window without explicit action',async()=>{const {actions}=await execute('vestido',{click:false});assert.equal(actions.length,0)});
test('wrong target is visible to operator',async()=>{const {identity}=await execute('vestido',{boundTab:44,selectResponse:{ok:false}});assert.match(identity.textContent,/任务标签 44；不匹配/)});
test('broker request identity is displayed',async()=>{const {identity}=await execute('vestido');assert.match(identity.textContent,/任务 req/) });
test('window registration immediately refreshes initial unverified identity',async()=>{
 let selected=false;const identity={textContent:''},status={textContent:''},button={addEventListener:(_,fn)=>button.click=fn};
 const chrome={runtime:{sendMessage:async()=>({session_id:'b'.repeat(32)}),sendNativeMessage:async(_,v)=>{if(v.operation==='binding_status')return {ok:selected,request_id:'req',bound_tab_id:null};if(v.operation==='select_window'){selected=true;return {ok:true}}return {challenge:null}}},tabs:{get:async()=>({id:15,windowId:4,url:'https://listado.mercadolibre.com.mx/vestido'})}};
 const c={chrome,document:{getElementById:id=>id==='binding-connect'?button:id==='binding-identity'?identity:status},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:()=>{}};vm.createContext(c);
 for(const f of ['query_gate.js','binding_validation.js','binding.js'])vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/'+f,'utf8'),c);
 button.click();await new Promise(r=>setImmediate(r));assert.match(identity.textContent,/任务 req.*等待首组绑定/);
});
test('binding status refreshes after first receipt without reopening popup',async()=>{
 let bound=null,tick;const identity={textContent:''},status={textContent:''},button={addEventListener:(_,fn)=>button.click=fn};
 const chrome={runtime:{sendMessage:async()=>({session_id:'b'.repeat(32)}),sendNativeMessage:async(_,v)=>v.operation==='binding_status'?{ok:true,request_id:'req',bound_tab_id:bound,matches:bound===15}:v.operation==='select_window'?{ok:true}:{challenge:null}},tabs:{get:async()=>({id:15,windowId:4,url:'https://listado.mercadolibre.com.mx/vestido'})}};
 const c={chrome,document:{getElementById:id=>id==='binding-connect'?button:id==='binding-identity'?identity:status},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:fn=>{tick=fn}};vm.createContext(c);
 for(const f of ['query_gate.js','binding_validation.js','binding.js'])vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/'+f,'utf8'),c);
 button.click();await new Promise(r=>setImmediate(r));assert.match(identity.textContent,/待绑定/);
 bound=15;tick();await new Promise(r=>setImmediate(r));assert.match(identity.textContent,/任务标签 15；匹配/);
});
test('changed broker request stops an already connected popup',async()=>{
 let broker='req',tick;const actions=[],identity={textContent:''},status={textContent:''},button={addEventListener:(_,fn)=>button.click=fn};
 const chrome={runtime:{sendMessage:async()=>({session_id:'b'.repeat(32)}),sendNativeMessage:async(_,v)=>{actions.push(v.operation);return v.operation==='binding_status'?{ok:true,request_id:broker,bound_tab_id:15,matches:true}:v.operation==='select_window'?{ok:true}:{challenge:null}}},tabs:{get:async()=>({id:15,windowId:4,url:'https://listado.mercadolibre.com.mx/vestido'})}};
 const c={chrome,document:{getElementById:id=>id==='binding-connect'?button:id==='binding-identity'?identity:status},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:fn=>{tick=fn}};vm.createContext(c);
 for(const f of ['query_gate.js','binding_validation.js','binding.js'])vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/'+f,'utf8'),c);
 button.click();await new Promise(r=>setImmediate(r));const count=actions.length;broker='another';tick();await new Promise(r=>setImmediate(r));assert.match(status.textContent,/任务已切换/);assert.deepEqual(actions.slice(count),['binding_status']);
});
test('reopened popup reconnects guard only for matching watch identity',async()=>{
 for(const matches of [true,false]){
  const opened=[],status={textContent:''},button={addEventListener:(_,fn)=>button.click=fn},sid='b'.repeat(32);
  const chrome={runtime:{sendMessage:async message=>{if(message.type==='ensure_collector_guard'){opened.push(message);return {ok:true}}return {session_id:sid}},sendNativeMessage:async(_,v)=>v.operation==='binding_status'?{ok:true,request_id:'req',bound_tab_id:matches?15:16,matches,guard_required:true}:v.operation==='select_window'?{ok:true}:{challenge:null}},tabs:{get:async()=>({id:15,windowId:4,url:'https://articulo.mercadolibre.com.mx/item'})}};
  const c={chrome,document:{getElementById:id=>id==='binding-connect'?button:status},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:()=>{}};vm.createContext(c);
  for(const f of ['query_gate.js','binding_validation.js','binding.js'])vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/'+f,'utf8'),c);
  button.click();await new Promise(r=>setImmediate(r));assert.equal(opened.length,matches?1:0);
 }
});
test('staging uses connected request and browser identity',async()=>{
 const calls=[],sid='b'.repeat(32),button={addEventListener:(_,fn)=>button.click=fn},status={textContent:''};
 const chrome={runtime:{sendMessage:async()=>({session_id:sid}),sendNativeMessage:async(_,v)=>{calls.push(v);return v.operation==='binding_status'?{ok:true,request_id:'req',bound_tab_id:15,matches:true}:v.operation==='select_window'||v.operation==='stage_evidence'?{ok:true}:{challenge:null}}},tabs:{get:async()=>({id:15,windowId:4,url:'https://articulo.mercadolibre.com.mx/MLM123'})}};
 const c={chrome,document:{getElementById:id=>id==='binding-connect'?button:status},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:()=>{}};vm.createContext(c);
 for(const f of ['query_gate.js','binding_validation.js','binding.js'])vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/'+f,'utf8'),c);
 assert.equal(await c.stageCollectorEvidence({task_record_id:'recid'}),false);
 button.click();await new Promise(r=>setImmediate(r));
 assert.equal(await c.stageCollectorEvidence({task_record_id:'recid'}),true);
 const event=calls.find(v=>v.operation==='stage_evidence');assert.equal(event.request_id,'req');assert.equal(event.tab_id,15);assert.equal(event.session_id,sid);
});
test('lost popup gate may rehydrate only a matching broker receipt',async()=>{
 const status={textContent:''},identity={textContent:''},button={addEventListener:(_,fn)=>button.click=fn},sid='b'.repeat(32);
 const receipt={request_id:'req',tab_id:15,window_id:4,session_id:sid,query:'vestido',query_index:1};
 const chrome={runtime:{sendMessage:async()=>({session_id:sid}),sendNativeMessage:async(_,v)=>v.operation==='binding_status'?{ok:true,request_id:'req',bound_tab_id:15,matches:true}:v.operation==='select_window'?{ok:true}:v.operation==='recover_receipt'?{receipt}:{challenge:null}},tabs:{get:async()=>({id:15,windowId:4,url:'https://listado.mercadolibre.com.mx/vestido'})}};
 const c={chrome,document:{getElementById:id=>id==='binding-connect'?button:id==='binding-identity'?identity:status},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:()=>{}};vm.createContext(c);
 for(const f of ['query_gate.js','binding_validation.js','binding.js'])vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/'+f,'utf8'),c);
 button.click();await new Promise(r=>setImmediate(r));assert.equal(await c.recoverCollectorReceipt(1,'vestido'),true);assert.equal(c.CollectorGate.consume(15,sid,'vestido',1),true);
});
test('explicit connection then verified query submits binding',async()=>{const {actions,status,identity}=await execute('vestido');assert.equal(actions[0].operation,'binding_status');assert.match(identity.textContent,/任务标签 15；匹配/);assert.equal(actions[1].operation,'select_window');assert.equal(actions.find(a=>a.operation==='submit').receipt.tab_id,15);assert.match(status.textContent,/已登记窗口/)});
test('mismatched query never submitted',async()=>{const {actions}=await execute('other');assert.equal(actions.filter(a=>a.operation==='submit').length,0)});
test('session change is visible and cannot proceed',async()=>{const {actions,status}=await execute('vestido',{selectResponse:{ok:false,error:'session_changed_reopen'}});assert.equal(actions.length,2);assert.match(status.textContent,/重新打开/)});
test('stalled native status exits busy and reports timeout without exposing details',async()=>{
 const status={textContent:''},button={addEventListener:(_,fn)=>button.click=fn};
 const chrome={runtime:{sendMessage:async()=>({session_id:'b'.repeat(32)}),sendNativeMessage:()=>new Promise(()=>{})},tabs:{get:async()=>({id:15,windowId:4,url:'https://listado.mercadolibre.com.mx/vestido'})}};
 const c={chrome,document:{getElementById:id=>id==='binding-connect'?button:status},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setInterval:()=>{},setTimeout:(fn)=>setTimeout(fn,5),clearTimeout};vm.createContext(c);
 for(const f of ['query_gate.js','binding_validation.js','binding.js'])vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/'+f,'utf8'),c);
 button.click();await new Promise(r=>setTimeout(r,20));assert.match(status.textContent,/后台状态查询超时/);
 button.click();assert.match(status.textContent,/正在连接/);
});
test('ambiguous select timeout reports unknown and does not poll or submit',async()=>{
 const calls=[],status={textContent:''},button={addEventListener:(_,fn)=>button.click=fn};
 const chrome={runtime:{sendMessage:async()=>({session_id:'b'.repeat(32)}),sendNativeMessage:(_,v)=>{calls.push(v.operation);return v.operation==='select_window'?new Promise(()=>{}):Promise.resolve({ok:false,request_id:'req'})}},tabs:{get:async()=>({id:15,windowId:4,url:'https://listado.mercadolibre.com.mx/vestido'})}};
 const c={chrome,document:{getElementById:id=>id==='binding-connect'?button:status},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setInterval:()=>{},setTimeout:(fn)=>setTimeout(fn,5),clearTimeout};vm.createContext(c);
 for(const f of ['query_gate.js','binding_validation.js','binding.js'])vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/'+f,'utf8'),c);
 button.click();await new Promise(r=>setTimeout(r,20));assert.match(status.textContent,/窗口登记结果未知/);assert.deepEqual(calls,['binding_status','select_window']);
});
test('records the precise search verification failure stage',async()=>{
 let handler;const rows=[];const button={addEventListener:(_,fn)=>handler=fn};const status={textContent:''};
 const chrome={runtime:{sendMessage:async()=>({session_id:'b'.repeat(32)}),sendNativeMessage:async(_,v)=>v.operation==='binding_status'?{ok:true,request_id:'req'}:v.operation==='select_window'?{ok:true}:v.operation==='poll'?{challenge:{query:'vestido'}}:{ok:true}},tabs:{get:async()=>({id:15,windowId:4,url:'https://listado.mercadolibre.com.mx/dress'}),sendMessage:async()=>{throw Error('Could not establish connection. Receiving end does not exist.')}}};
 const c={chrome,document:{getElementById:id=>id==='binding-connect'?button:status},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:()=>{},CollectorTrace:{errorCode:()=> 'error',append:async(s,k)=>rows.push([s,k]),appendError:async(s,e)=>rows.push([s,e.message]),render:async()=>{}}};vm.createContext(c);vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/binding.js','utf8'),c);
 handler();await new Promise(r=>setTimeout(r,25));assert.ok(rows.some(x=>x[0]==='query_page_extract'&&x[1]==='Could not establish connection. Receiving end does not exist.'));
});
test('cards completion sends current bound identity to backend',async()=>{
 let btn={addEventListener:(_,fn)=>btn.click=fn};let calls=[];const chrome={runtime:{sendMessage:async()=>({session_id:'b'.repeat(32)}),sendNativeMessage:async(_,v)=>{calls.push(v);return v.operation==='binding_status'?{ok:true,request_id:'req'}:v.operation==='select_window'?{ok:true}:v.operation==='query_cards_complete'?{ok:true}:{challenge:null}}},tabs:{get:async()=>({id:15,windowId:4,url:'https://listado.mercadolibre.com.mx/dress'})}};const c={chrome,document:{getElementById:id=>id==='binding-connect'?btn:{textContent:''}},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:()=>{}};vm.createContext(c);vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/binding.js','utf8'),c);btn.click();await new Promise(r=>setImmediate(r));assert.equal(await c.confirmCollectorQueryCards(0,[{rank:1}]),true);const msg=calls.find(x=>x.operation==='query_cards_complete');assert.equal(msg.request_id,'req');assert.equal(msg.query_index,0);assert.equal(msg.tab_id,15);assert.equal(msg.cards.length,1);
});
test('reconnecting an already selected matching task clears the connecting state',async()=>{
 const status={textContent:''},identity={textContent:''},button={addEventListener:(_,fn)=>button.click=fn};
 const chrome={runtime:{sendMessage:async()=>({session_id:'b'.repeat(32)}),sendNativeMessage:async(_,v)=>v.operation==='binding_status'?{ok:true,request_id:'req',bound_tab_id:15,matches:true}:v.operation==='select_window'?{ok:true}:{challenge:null}},tabs:{get:async()=>({id:15,windowId:4,url:'https://listado.mercadolibre.com.mx/vestido'})}};
 const c={chrome,document:{getElementById:id=>id==='binding-connect'?button:id==='binding-identity'?identity:status},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:()=>{}};vm.createContext(c);vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/binding.js','utf8'),c);
 button.click();await new Promise(r=>setImmediate(r));button.click();await new Promise(r=>setImmediate(r));assert.doesNotMatch(status.textContent,/正在连接/);assert.match(status.textContent,/已连接/);
});
test('opening a newly confirmed tab waits for its browser navigation and selects the same tab after it loads',async()=>{
 let url='about:blank',tick,selected=0;
 const chrome={runtime:{sendMessage:async()=>({session_id:'b'.repeat(32)}),sendNativeMessage:async(_,msg)=>{
  if(msg.operation==='binding_status')return {ok:true,request_id:'r',anchor_required:true,anchor_active:true,matches:true,bound_tab_id:15};
  if(msg.operation==='select_window'){assert.equal(msg.tab_id,15);selected++;}
  return {ok:true};
 }},tabs:{get:async()=>({id:15,windowId:4,url})}};
 const btn={addEventListener:(_,fn)=>btn.click=fn},status={};
 const c={chrome,URL,Date,setTimeout,clearTimeout,setInterval:fn=>tick=fn,document:{getElementById:id=>id==='binding-connect'?btn:status},location:{href:'chrome-extension://x/collector.html?tab=15'}};
 vm.runInNewContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/binding.js','utf8'),c);
 btn.click();await new Promise(r=>setImmediate(r));assert.equal(selected,0);assert.match(status.textContent,/等待目标平台/);
 url='https://www.mercadolibre.com.mx/';await tick();assert.equal(selected,1);
});
test('visual-v2 batch and completion use scoped native messages',async()=>{
 let btn={addEventListener:(_,fn)=>btn.click=fn};let calls=[];
 const chrome={runtime:{sendMessage:async()=>({session_id:'b'.repeat(32)}),sendNativeMessage:async(_,v)=>{calls.push(v);return v.operation==='binding_status'?{ok:true,request_id:'req'}:{ok:true}}},tabs:{get:async()=>({id:15,windowId:4,url:'https://listado.mercadolibre.com.mx/dress'})}};
 const c={chrome,document:{getElementById:id=>id==='binding-connect'?btn:{textContent:''}},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:()=>{}};
 vm.createContext(c);vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/binding.js','utf8'),c);
 btn.click();await new Promise(r=>setImmediate(r));
 assert.equal(await c.saveVisualQueryBatch(8,'dress',0,[{rank:1}]),true);
 assert.equal(await c.finishVisualQuery(8,'dress','home_complete',3,false),true);
 assert.equal(calls.find(x=>x.operation==='visual_v2_batch').request_id,'req');
 assert.equal(calls.find(x=>x.operation==='visual_v2_finish').end_checks,3);
});

test('card-v3 page proof verifies current tab URL and query before confirmation',async()=>{
 let btn={addEventListener:(_,fn)=>btn.click=fn};const calls=[];let pageQuery='dress';
 const chrome={runtime:{sendMessage:async()=>({session_id:'b'.repeat(32)}),sendNativeMessage:async(_,v)=>{
  calls.push(v);if(v.operation==='binding_status')return {ok:true,request_id:'req'};
  if(v.operation==='card_v3_page_challenge')return {challenge:{request_id:'req',plan_digest:'a'.repeat(64),lease_epoch:1,query_index:0,page:1,query:'dress',nonce:'c'.repeat(32),tab_id:15,window_id:4,session_id:'b'.repeat(32),page_url:'https://listado.mercadolibre.com.mx/dress'}};
  return {ok:true};}},tabs:{get:async()=>({id:15,windowId:4,url:'https://listado.mercadolibre.com.mx/dress'}),sendMessage:async()=>({page_url:'https://listado.mercadolibre.com.mx/dress',search_query:pageQuery})}};
 const c={chrome,document:{getElementById:id=>id==='binding-connect'?btn:{textContent:''}},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:()=>{}};
 vm.createContext(c);vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/binding.js','utf8'),c);
 btn.click();await new Promise(r=>setImmediate(r));
 assert.equal(await c.confirmCardV3Page(0,'dress',1),true);
 assert.equal(calls.filter(x=>x.operation==='card_v3_page_confirm').length,1);
 pageQuery='different';assert.equal(await c.confirmCardV3Page(0,'dress',1),false);
 assert.equal(calls.filter(x=>x.operation==='card_v3_page_confirm').length,1);
});

test('card-v3 page completion requires three consistent current page observations',async()=>{
 let btn={addEventListener:(_,fn)=>btn.click=fn};const calls=[];let checks=0,unverified=false;
 const url='https://listado.mercadolibre.com.mx/dress',next=url+'_Desde_51';
 let atEnd=true;
 const chrome={runtime:{sendMessage:async()=>({session_id:'b'.repeat(32)}),sendNativeMessage:async(_,v)=>{calls.push(v);if(v.operation==='binding_status')return {ok:true,request_id:'req'};if(v.operation==='card_v3_page_challenge')return {challenge:{request_id:'req',query_index:0,query:'dress',page:1,tab_id:15,window_id:4,session_id:'b'.repeat(32),page_url:url}};return {ok:true}}},tabs:{get:async()=>({id:15,windowId:4,url}),sendMessage:async(_tab,v)=>{if(v.type==='MERCADO_COLLECTOR_PAGE_BINDING')return {page_url:url,search_query:'dress'};checks++;return {page_url:url,status:unverified?'unverified':'has_next',url:next,at_end:atEnd};}}};
 const c={chrome,document:{getElementById:id=>id==='binding-connect'?btn:{textContent:''}},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:()=>{}};
 vm.createContext(c);vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/binding.js','utf8'),c);
 btn.click();await new Promise(r=>setImmediate(r));
 assert.equal(await c.finishCardV3Page(0,'dress',1),true);assert.equal(checks,3);
 assert.equal(calls.filter(x=>x.operation==='card_v3_page_confirm').length,1);
 const sent=calls.find(x=>x.operation==='card_v3_page_finish');assert.equal(sent.status,'has_next');assert.equal(sent.next_url,next);assert.equal(sent.end_checks,3);
 unverified=true;assert.equal(await c.finishCardV3Page(0,'dress',1),false);
 unverified=false;atEnd=false;assert.equal(await c.finishCardV3Page(0,'dress',1),false);
 assert.equal(calls.filter(x=>x.operation==='card_v3_page_finish').length,1);
});

test('card-v3 batch requires current visible card evidence and screenshot',async()=>{
 let btn={addEventListener:(_,fn)=>btn.click=fn};const calls=[];const url='https://listado.mercadolibre.com.mx/dress';
 const card={product_id:'MLM1',title:'Dress',is_ad:false,bbox:[1,2,30,40],sales_text:'+150 vendidos',sales_status:'known'};
 const chrome={runtime:{sendMessage:async()=>({session_id:'b'.repeat(32)}),sendNativeMessage:async(_,v)=>{calls.push(v);if(v.operation==='binding_status')return {ok:true,request_id:'req'};if(v.operation==='card_v3_page_challenge')return {challenge:{request_id:'req',query_index:0,query:'dress',page:1,tab_id:15,window_id:4,session_id:'b'.repeat(32),page_url:url}};return {ok:true}}},tabs:{get:async()=>({id:15,windowId:4,url}),query:async()=>[{id:15}],captureVisibleTab:async()=> 'data:image/jpeg;base64,YWJj',sendMessage:async(_tab,v)=>v.type==='MERCADO_COLLECTOR_PAGE_BINDING'?{page_url:url,search_query:'dress'}:{page_url:url,verified_scope:true,card_sales_version:3,observations:[card]}}};
 const c={chrome,document:{getElementById:id=>id==='binding-connect'?btn:{textContent:''}},location:{href:'chrome-extension://id/collector.html?tab=15'},URL,Date,setTimeout,clearTimeout,setInterval:()=>{}};
 vm.createContext(c);vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/binding.js','utf8'),c);
 btn.click();await new Promise(r=>setImmediate(r));
 assert.equal(await c.saveCardV3Batch(0,'dress',1,0,[card]),true);
 assert.equal(calls.find(x=>x.operation==='card_v3_batch').screenshot,'YWJj');
});

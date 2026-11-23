'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const anchorId='a'.repeat(32), peerId='b'.repeat(32), session='1'.repeat(32);
function worker({rotated=false,tab=7,platform='mercado-libre-mx'}={}) {
  const events=[];
  const listeners=[];
  let external, guard=false, confirmations=0;
  const chrome={
    runtime:{
      id:anchorId, getURL:name=>'chrome-extension://'+anchorId+'/'+name,
      onMessage:{addListener:fn=>listeners.push(fn)},onMessageExternal:{addListener:fn=>external=fn},
      sendNativeMessage:async (_,message)=>{
        events.push(message.operation);
        if(message.operation==='binding_status') return {ok:guard,request_id:'r',anchor_active:guard,
          anchor_required:true,bound_tab_id:tab, platform,peer_extension_id:peerId};
        if(message.operation==='anchor_challenge') return {ok:true,token:'proof',epoch:2};
        if(message.operation==='anchor_confirm') return {ok:true,rebind_required:rotated && confirmations++===0};
        assert.fail(message.operation);
      },
    },
    tabs:{onCreated:{addListener(){}},get:async id=>({id,windowId:3,url:platform==='shein-us'?'https://us.shein.com/':'https://www.mercadolibre.com.mx/'})},storage:{session:{get:async()=>({collector_session_id:session})}},
  };
  const context={chrome,URL,Date,Uint8Array,setTimeout:fn=>setImmediate(fn),importScripts(){},NativeGuard:{onMessage(){}},
    CollectorTrace:{append:async()=>{},appendError:async()=>{}},
    DedicatedWindow:{ensureWindow:async()=>{events.push('window_proof');return {windowId:3,tabId:33,marker:'f'.repeat(32)}}},
    AnchorGuard:{openAnchorGuard(){guard=true;events.push('anchor_guard_open');return {ready:Promise.resolve(),alive:()=>guard,close(){guard=false}}}},
    AnchorReconnect:class {constructor(identity){this.identity=identity}start(){}stop(){this.stopped=true}},
    TaskTabCreate:{verifyBoundTab:require('../chrome_extension/task_tab_create.js').verifyBoundTab,createTaskTab:async (_,__,id,s,w)=>{assert.equal(id,'r');assert.equal(w,3);assert.equal(s,session);events.push('task_tab_create');tab=14;return {window_id:3,tab_id:14}}},
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../chrome_extension/background.js'),'utf8'),context);
  return {events, send:(peer=null)=>new Promise((resolve,reject)=>{
    const sender={id:peer||anchorId,url:'chrome-extension://'+(peer||anchorId)+'/collector.html?tab=7'};
    const message={type:'recover_task_anchor',request_id:'r',window_id:3,tab_id:7,session_id:session};
    const f=peer?external:listeners.at(-1);
    if(!f(message,sender,resolve))reject(Error('unhandled'));
  })};
}

test('cold worker proves the existing window before recovering its current task tab',async()=>{
  const w=worker();
  const result=await w.send();
  assert.equal(result.ok,true);assert.equal(result.tab_id,7);
  assert.deepEqual(w.events,['binding_status','window_proof','anchor_challenge','anchor_confirm','anchor_guard_open','binding_status']);
});
test('rotated broker re-confirms anchor and creates exactly one tab for concurrent recovery',async()=>{
  const w=worker({rotated:true,tab:null});
  const [a,b]=await Promise.all([w.send(),w.send()]);
  assert.equal(a.tab_id,14);assert.equal(b.tab_id,14);
  assert.equal(w.events.filter(e=>e==='anchor_confirm').length,2);
  assert.equal(w.events.filter(e=>e==='anchor_guard_open').length,1);
  assert.equal(w.events.filter(e=>e==='task_tab_create').length,1);
});
test('only the configured SHEIN peer can ask the owner to restore the anchor',async()=>{
  const w=worker({platform:'shein-us'});
  assert.equal((await w.send('c'.repeat(32))).error,'anchor_request_changed');
  assert.deepEqual(w.events,['binding_status']);
  assert.equal((await w.send(peerId)).ok,true);
});

for (const [dir,platform] of [['chrome_extension','mercado-libre-mx'],['shein_extension','shein-us']]) {
  test(platform+' connection restores the anchor before selecting and opening the watch guard',async()=>{
    let active=false,watch=false;
    const events=[];
    const buttons={};
    const chrome={tabs:{get:async()=>({id:7,windowId:3,url:platform==='shein-us'?'https://us.shein.com/dress-p-4.html':'https://articulo.mercadolibre.com.mx/MLM-4'})},runtime:{
      id:platform==='shein-us'?peerId:anchorId,
      sendNativeMessage:async(_,data)=>{
        events.push(data.operation);
        if(data.operation==='binding_status')return {
          ok:active,request_id:'r',matches:active,guard_required:true,guard_active:watch,
          anchor_required:true,anchor_active:active,platform,peer_extension_id:peerId,anchor_extension_id:anchorId,bound_tab_id:7};
        return {ok:true};
      },
      sendMessage:async(...args)=>{
        const msg=args.at(-1);
        events.push(msg.type);
        if(msg.type==='collector_session_id')return {session_id:session};
        if(msg.type==='recover_task_anchor'){
          if (platform==='shein-us')assert.equal(args[0],anchorId);
          active=true;return {ok:true,request_id:'r',window_id:3,tab_id:7};
        }
        if (msg.type==='ensure_collector_guard'){assert.equal(active,true);watch=true;return {ok:true};}
        assert.fail(msg.type);
      },
    }};
    const c={chrome,URL,Date,setTimeout,clearTimeout,setInterval:()=>{},SheinPeer:{handshake:async()=>events.push('peer_handshake')},
      document:{getElementById:id=>buttons[id]??={addEventListener:(_,fn)=>{buttons[id].click=fn}}},
      location:{href:'chrome-extension://x/collector.html?tab=7'}};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,'..',dir,'binding.js'),'utf8'),c);
    buttons['binding-connect'].click();
    await new Promise(resolve=>setImmediate(resolve));
    assert.ok(events.indexOf('recover_task_anchor')<events.indexOf('select_window'),events);
    assert.ok(events.indexOf('select_window')<events.indexOf('ensure_collector_guard'),events);
    assert.equal(watch,true);
  });
}

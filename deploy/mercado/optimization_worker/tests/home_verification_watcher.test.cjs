'use strict';
const test=require('node:test');const assert=require('node:assert/strict');
const {startWatch,checkWatch}=require('../chrome_extension/home_verification_watcher.js');

test('waits for same readable search page before reporting verification',async()=>{
 const storage={};const calls=[];let blocked=true;
 const browser={storage:{session:{get:async key=>({[key]:storage[key]}),set:async data=>Object.assign(storage,data),remove:async key=>delete storage[key]}},
  alarms:{create:async(name,data)=>calls.push(['alarm',name,data]),clear:async name=>calls.push(['clear',name])},
  runtime:{sendNativeMessage:async(_host,msg)=>{calls.push(['native',msg]);return msg.operation==='home_verification_info'?{ok:true,stage:'search',expected:{query:'vestido'}}:{ok:true}}},
  tabs:{get:async()=>({id:7,windowId:8}),sendMessage:async(_id,msg)=>msg.type==='MERCADO_COLLECTOR_STATUS'?{error:blocked?'captcha':null,page_url:'https://listado.mercadolibre.com.mx/vestido'}:{page_url:'https://listado.mercadolibre.com.mx/vestido',search_query:'vestido',verified_scope:true,observations:[]}}};
 const binding={request_id:'request',tab_id:7,window_id:8,session_id:'a'.repeat(32)};
 assert.equal(await startWatch(browser,'host',binding),true);
 assert.equal(await checkWatch(browser,'host','request'),false);
 assert.equal(calls.filter(x=>x[0]==='native'&&x[1].operation==='home_verification_observed').length,0);
 blocked=false;assert.equal(await checkWatch(browser,'host','request'),true);
 const sent=calls.find(x=>x[0]==='native'&&x[1].operation==='home_verification_observed')[1];
 assert.deepEqual(sent.page,{url:'https://listado.mercadolibre.com.mx/vestido',query:'vestido',blocked:false});
 assert.equal(storage['home-verification:request'],undefined);
});

test('never reports a different browser window or a still blocked page',async()=>{
 const state={'home-verification:request':{request_id:'request',tab_id:7,window_id:8,session_id:'a'.repeat(32),stage:'search',expected:{query:'vestido'},expires:Date.now()+60000}};
 let sent=false;const browser={storage:{session:{get:async key=>({[key]:state[key]}),remove:async key=>delete state[key]}},alarms:{clear:async()=>{}},
  runtime:{sendNativeMessage:async()=>{sent=true;return {ok:true}}},tabs:{get:async()=>({id:7,windowId:9})}};
 assert.equal(await checkWatch(browser,'host','request'),false);assert.equal(sent,false);
});

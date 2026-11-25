const test=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const fs=require('node:fs');
test('activation scrolls verified Mercado target before reading visible search identity',async()=>{
 const ch={version:1,request_id:'r',nonce:'a'.repeat(32),lease_epoch:1,plan_digest:'b'.repeat(64),platform:'mercado-libre-mx',query_index:0,query:'dress'},types=[];let top=false;const chrome={tabs:{get:async()=>({id:123,windowId:4,url:'https://listado.mercadolibre.com.mx/dress'}),sendMessage:async(_,m)=>{types.push(m.type);if(m.type==='MERCADO_COLLECTOR_SCROLL_TOP'){top=true;return {ok:true}}return {page_url:'https://listado.mercadolibre.com.mx/dress',search_query:top?'dress':null}},update:async()=>{}},windows:{update:async()=>{}},runtime:{sendNativeMessage:async(_,m)=>m.operation==='activation_challenge'?{challenge:ch}:{ok:true}}};const c={chrome,URL,Date};vm.createContext(c);for(const f of ['binding_validation.js','activation.js'])vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/'+f,'utf8'),c);assert.equal(await c.activateCollectorTarget({requestId:'r',target:123,windowId:4,session:'c'.repeat(32),extract:'MERCADO_COLLECTOR_EXTRACT_SEARCH'}),true);assert.equal(types[0],'MERCADO_COLLECTOR_SCROLL_TOP');
});
test('activation reads only visible search identity even when card extraction fails',async()=>{
 const ch={version:1,request_id:'r',nonce:'a'.repeat(32),lease_epoch:1,plan_digest:'b'.repeat(64),platform:'mercado-libre-mx',query_index:0,query:'dress'},types=[];const chrome={tabs:{get:async()=>({id:123,windowId:4,url:'https://listado.mercadolibre.com.mx/dress'}),sendMessage:async(_,m)=>{types.push(m.type);if(m.type==='MERCADO_COLLECTOR_SCROLL_TOP')return {ok:true};return m.type==='MERCADO_COLLECTOR_SEARCH_IDENTITY'?{page_url:'https://listado.mercadolibre.com.mx/dress',search_query:'dress'}:{error:'card unavailable'}},update:async()=>{}},windows:{update:async()=>{}},runtime:{sendNativeMessage:async(_,m)=>m.operation==='activation_challenge'?{challenge:ch}:{ok:true}}};const c={chrome,URL,Date};vm.createContext(c);for(const f of ['binding_validation.js','activation.js'])vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/'+f,'utf8'),c);assert.equal(await c.activateCollectorTarget({requestId:'r',target:123,windowId:4,session:'c'.repeat(32),extract:'MERCADO_COLLECTOR_EXTRACT_SEARCH'}),true);assert.deepEqual(types,['MERCADO_COLLECTOR_SCROLL_TOP',...Array(3).fill('MERCADO_COLLECTOR_SEARCH_IDENTITY')]);
});
test('activates only after current bound page and one-use broker proof',async()=>{
 let calls=[]; const ch={version:1,request_id:'r',nonce:'a'.repeat(32),lease_epoch:1,plan_digest:'b'.repeat(64),platform:'mercado-libre-mx',query_index:0,query:'dress'};
 const chrome={tabs:{get:async()=>({id:123,windowId:4,url:'https://listado.mercadolibre.com.mx/dress'}),sendMessage:async(_,m)=>m.type==='MERCADO_COLLECTOR_SCROLL_TOP'?{ok:true}:{page_url:'https://listado.mercadolibre.com.mx/dress',search_query:'dress'},update:async(...v)=>calls.push(v)},windows:{update:async(...v)=>calls.push(v)},runtime:{sendNativeMessage:async(_h,m)=>m.operation==='activation_challenge'?{challenge:ch}:{ok:true}}};
 const c={chrome,URL,Date};vm.createContext(c);vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/binding_validation.js','utf8'),c);vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/activation.js','utf8'),c);
 assert.equal(await c.activateCollectorTarget({requestId:'r',target:123,windowId:4,session:'c'.repeat(32),extract:'MERCADO_COLLECTOR_EXTRACT_SEARCH'}),true);assert.equal(calls.length,2);
 chrome.tabs.sendMessage=async(_,m)=>m.type==='MERCADO_COLLECTOR_SCROLL_TOP'?{ok:true}:{page_url:'https://listado.mercadolibre.com.mx/dress',search_query:'other'};calls=[];await assert.rejects(c.activateCollectorTarget({requestId:'r',target:123,windowId:4,session:'c'.repeat(32),extract:'MERCADO_COLLECTOR_EXTRACT_SEARCH'}));assert.equal(calls.length,0);
});
test('rejects same URL when visible search changes before activating',async()=>{
 let calls=[],reads=0;const ch={version:1,request_id:'r',nonce:'a'.repeat(32),lease_epoch:1,plan_digest:'b'.repeat(64),platform:'mercado-libre-mx',query_index:0,query:'dress'};
 const chrome={tabs:{get:async()=>({id:123,windowId:4,url:'https://listado.mercadolibre.com.mx/dress'}),sendMessage:async(_,m)=>m.type==='MERCADO_COLLECTOR_SCROLL_TOP'?{ok:true}:{page_url:'https://listado.mercadolibre.com.mx/dress',search_query:++reads===1?'dress':'other'},update:async()=>calls.push('tab')},windows:{update:async()=>calls.push('window')},runtime:{sendNativeMessage:async(_h,m)=>m.operation==='activation_challenge'?{challenge:ch}:{ok:true}}};
 const c={chrome,URL,Date};vm.createContext(c);for(const f of ['binding_validation.js','activation.js'])vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/'+f,'utf8'),c);
 await assert.rejects(c.activateCollectorTarget({requestId:'r',target:123,windowId:4,session:'c'.repeat(32),extract:'MERCADO_COLLECTOR_EXTRACT_SEARCH'}));assert.equal(calls.length,0);
});
test('activation records fixed failure stage without sensitive message',async()=>{
 const records=[];const chrome={runtime:{sendNativeMessage:async()=>{throw new TypeError('private token')}}};const c={chrome,Date,CollectorTrace:{append:async(s,k)=>records.push([s,k]),appendError:async(s,e)=>records.push([s,e.name])}};vm.createContext(c);vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/activation.js','utf8'),c);
 await assert.rejects(c.activateCollectorTarget({requestId:'r',target:1,windowId:2,session:'x',extract:'x'}));assert.deepEqual(records,[['activation_challenge','started'],['activation_challenge','TypeError']]);
});
test('records precise initial extraction failure stage',async()=>{
 const records=[];const chrome={runtime:{sendNativeMessage:async()=>({challenge:{request_id:'r',query:'dress'}})},tabs:{get:async()=>({id:1,windowId:2}),sendMessage:async()=>{throw Error('Invalid platform page')}}};
 const c={chrome,Date,CollectorTrace:{append:async(s,k)=>records.push([s,k]),appendError:async(s,e)=>records.push([s,e.message])}};vm.createContext(c);vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/activation.js','utf8'),c);
 await assert.rejects(c.activateCollectorTarget({requestId:'r',target:1,windowId:2,session:'x',extract:'x'}));assert.deepEqual(records.slice(-2),[['activation_page_extract','started'],['activation_page_extract','Invalid platform page']]);
});
for(const [platform,detail,type] of [
 ['chrome_extension','https://articulo.mercadolibre.com.mx/MLM-123456-vestido','MERCADO'],
 ['shein_extension','https://us.shein.com/item-p-123456.html','SHEIN'],
]) test(platform+' reopens the real detail guard before requesting a candidate challenge',async()=>{
 let ready=false,focus=0;
 const identity=type==='MERCADO'?'mercado-libre-mx:MLM123456':'shein-us:123456';
 const chrome={tabs:{get:async()=>({id:7,windowId:4,url:detail}),update:async()=>{focus++},
  sendMessage:async(_,msg)=>({identity_matches:msg.identity===identity,identity,page_url:detail})},
  windows:{update:async()=>{}},runtime:{
   sendMessage:async msg=>{assert.equal(msg.type,'ensure_collector_guard'); ready=true;return {ok:true}},
   sendNativeMessage:async(_,msg)=>{
    if(msg.operation==='activation_confirm')return {ok:true};
    if(!msg.detail_url)return {challenge:{request_id:'r'}};
    assert.equal(ready,true);return {challenge:{request_id:'r',detail_url:detail,detail_identity:identity,nonce:'n'}};
   },
  }};
 const ctx=vm.createContext({chrome,URL,Date});
 vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/'+platform+'/activation.js','utf8'),ctx);
 const params={requestId:'r',target:7,windowId:4,session:'a'.repeat(32),extract:type+'_COLLECTOR_EXTRACT_SEARCH'};
 assert.equal(await ctx.activateCollectorTarget(params),true);assert.equal(focus,1);
 chrome.runtime.sendMessage=async()=>({ok:false,error:'guard_binding_changed'});
 await assert.rejects(ctx.activateCollectorTarget(params),/guard_binding_changed/);assert.equal(focus,1);
});

for (const [extension, platform, search, url, id, type] of [
  ['chrome_extension','mercado-libre-mx','https://listado.mercadolibre.com.mx/vestido','https://articulo.mercadolibre.com.mx/MLM-123-vestido','MLM123','MERCADO'],
  ['shein_extension','shein-us','https://us.shein.com/pdsearch/dress/','https://us.shein.com/dress-p-123.html','123','SHEIN'],
]) test(extension + ' opens precisely the broker-selected next item on its bound tab', async () => {
  const expectedIdentity = platform + ':' + id;
  let current = search, navigation = [], confirmations = [], issued = 0;
  const proof = {ok:true, task:'rec123', request_id:'r', tab_id:3, window_id:4, session_id:'session', candidate:{identity:expectedIdentity,url}};
  const chrome = {
    tabs: {
      get: async target => ({id:target,windowId:4,url:current,status:'complete'}),
      update: async (target,params) => {
        assert.equal(target,3);
        if (params.url) { navigation.push(params.url); current=params.url; }
      },
      sendMessage: async (target, m) => {
        assert.equal(target,3);
        assert.equal(m.type,type+'_COLLECTOR_EXTRACT_DETAIL');
        return {identity:m.identity,identity_matches:m.identity===expectedIdentity,page_url:current};
      }
    },
    windows:{update:async target=>assert.equal(target,4)},
    runtime: {
      sendMessage:async()=>({ok:true}),
      sendNativeMessage: async (_, msg) => {
        assert.equal(msg.request_id,'r');
        if (msg.operation==='activation_challenge') {
          issued++;
          return {challenge:{request_id:'r',detail_identity:expectedIdentity,detail_url:msg.detail_url,nonce:'nonce'}};
        }
        confirmations.push(msg); return {ok:true};
      }
    }
  };
  const c={chrome,Date,URL,setTimeout}; vm.createContext(c);
  for (const f of ['activation.js','candidate_navigation.js']) vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/'+extension+'/'+f,'utf8'),c);
  const opts = {requestId:'r',target:3,windowId:4,session:'session',expectedIdentity,taskRecordId:'rec123',platform,extract:type+'_COLLECTOR_EXTRACT_SEARCH'};
  for(const change of [{tab_id:99},{request_id:'wrong'},{task:'rec999'}, {candidate:{identity:expectedIdentity,url:'https://example.com/fake'}},
                        {candidate:{identity:expectedIdentity,url:url.replace('123','888')}},{candidate:null}]) {
    await assert.rejects(c.navigateCollectorCandidate({...opts,checkpoint: async()=>({...proof,...change})}));
  }
  assert.equal(navigation.length,0);
  assert.equal(await c.navigateCollectorCandidate({...opts,checkpoint:async(op,values)=>{
    assert.equal(op,'next_candidate');assert.deepEqual(Object.keys(values),[]);return proof;
  }}),true);
  assert.deepEqual(navigation,[url]);
  assert.equal(confirmations[0].query,expectedIdentity);
  assert.equal(confirmations[0].url,url);
  assert.ok(issued>0);
});

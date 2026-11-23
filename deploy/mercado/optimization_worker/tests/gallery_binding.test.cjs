const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const base = 'deploy/mercado/optimization_worker/';

for (const [extension, platform, search, detail, id, prefix] of [
  ['chrome_extension','mercado-libre-mx','https://listado.mercadolibre.com.mx/faldas','https://www.mercadolibre.com.mx/up/MLMU4075103096','MLMU4075103096','MERCADO'],
  ['shein_extension','shein-us','https://us.shein.com/pdsearch/skirt/','https://us.shein.com/Blue-Skirt-p-123456.html','123456','SHEIN'],
]) {
  function setup(change = {}) {
    const identity = platform + ':' + id;
    const scope = {request_id:'request',session_id:'a'.repeat(32),tab_id:74,window_id:32};
    const challenge = {...scope, ok:true, platform, epoch:5, identity, detail_url:detail, nonce:'e'.repeat(32), ...change.challenge};
    let current = search, locationReads = 0;
    const events = [];
    const api = {
      tabs: {
        get:async()=>({id:74,windowId:32,active:true,status:'complete',url:current}),
        query:async()=>[{id:change.switched && locationReads > 0 ? 19 : 74}],
        update:async(target, values)=>{assert.equal(target,74);current=values.url||current;events.push(['navigate',current]);},
        sendMessage:async(target, message)=>{
          assert.equal(target,74); assert.equal(message.type, prefix+'_COLLECTOR_EXTRACT_DETAIL');
          locationReads++;
          return {identity, identity_matches:true,page_url:current,title:'Skirt',access_bbox:[0,0,30,10],...change.page};
        },
      },
      windows:{update:async target=>assert.equal(target,32)},
      runtime:{sendNativeMessage:async(_,data)=>{
        events.push([data.operation,data]);
        for(const [key,v] of Object.entries(scope)) if (key!=='request_id' || data.operation!=='binding_status') assert.equal(data[key],v);
        if(data.operation==='gallery_binding_challenge')return challenge;
        if(data.operation==='gallery_binding_confirm')return {ok:true};
        if(data.operation==='binding_status')return {ok:true,request_id:scope.request_id,bound_tab_id:74,matches:true,
          gallery_phase:true,guard_required:true,anchor_active:true};
        throw Error('unexpected '+data.operation);
      }},
    };
    const ctx = vm.createContext({chrome:api,URL,Date,setTimeout});
    for (const file of ['activation.js','candidate_navigation.js','gallery_binding.js']) vm.runInContext(fs.readFileSync(base+extension+'/'+file,'utf8'),ctx);
    const open = () => ctx.openBoundGallery({requestId:scope.request_id,windowId:32,target:74,session:scope.session_id,platform,
      extract:prefix+'_COLLECTOR_EXTRACT_DETAIL',restoreGuard:async()=>{events.push(['guard']);}});
    return {open,events,identity,challenge};
  }
  test(extension+' resumes a committed gallery directly and attaches the guard after its own receipt',async()=>{
    const {open, events, identity} = setup();
    assert.equal(await open(),identity);
    assert.deepEqual(events.map(x=>x[0]),['gallery_binding_challenge','navigate','gallery_binding_confirm','binding_status','guard']);
    const sent=events[2][1];
    assert.equal(sent.identity,identity);assert.equal(sent.detail_url,detail);assert.equal(sent.epoch,5);
    assert.ok(Date.parse(sent.observed_at));assert.equal(sent.nonce,'e'.repeat(32));
  });
  test(extension+' refuses an unrelated session, foreign or wrong product URL, switched tab and inaccessible detail',async()=>{
    for(const change of [{challenge:{session_id:'old'}},{challenge:{detail_url:'https://example.org/'+id}},
      {challenge:{detail_url:detail.replace(/[0-9]+/,'36253')}},{switched:true},{page:{blocked:true,blocking_error:'security_wall'}},
      {page:{identity_matches:false}}]){
      const {open,events} = setup(change);
      await assert.rejects(open());
      assert.ok(!events.some(([op])=>op==='gallery_binding_confirm'||op==='guard'));
    }
  });
}

for(const [dir,url] of [['chrome_extension','https://listado.mercadolibre.com.mx/falda'],['shein_extension','https://us.shein.com/pdsearch/skirt/']]){
  test(dir+' collector button selects the native gallery route, while polling leaves searches untouched',async()=>{
    const actions=[], elements={};
    for(const id of ['binding-connect','binding-activate','binding-status','binding-identity']){
      elements[id]={addEventListener:(_,fn)=>elements[id].click=fn};
    }
    const scope={ok:true,request_id:'r',bound_tab_id:70,matches:true,anchor_active:true,gallery_phase:true,gallery_binding_required:true};
    const chrome={runtime:{sendNativeMessage:async(_host,msg)=>{
      actions.push(msg.operation);
      if(msg.operation==='binding_status')return scope;
      assert.equal(msg.operation,'select_window');return {ok:true};
    },sendMessage:async()=>({session_id:'a'.repeat(32)})},tabs:{get:async()=>({id:70,windowId:19,url})}};
    const c=vm.createContext({chrome,URL,Date,setTimeout,clearTimeout,setInterval:()=>{},location:{href:'chrome-extension://id/collector.html?tab=70'},
      document:{getElementById:id=>elements[id]},SheinPeer:{handshake:async()=>{}},
      openBoundGallery:async args=>{assert.equal(args.target,70);assert.equal(args.requestId,'r');actions.push('gallery');return 'platform:product';}});
    vm.runInContext(fs.readFileSync(base+dir+'/binding.js','utf8'),c);
    elements['binding-connect'].click();
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(elements['binding-activate'].textContent,'打开当前合格商品图库');
    await elements['binding-activate'].click();
    assert.deepEqual(actions,['binding_status','select_window','binding_status','binding_status','gallery']);
  });
}

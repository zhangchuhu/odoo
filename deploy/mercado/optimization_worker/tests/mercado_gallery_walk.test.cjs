'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {walkVisibleGallery} = require('../chrome_extension/gallery_walk.js');
const {appendImage} = require('../chrome_extension/gallery_capture.js');
test('hidden single-image counter is accepted only with one gallery figure and no visible arrows',()=>{
 const {singleViewerPosition}=require('../chrome_extension/gallery_visible.js');
 const counter={textContent:'1 / 1'},arrow={visible:false};
 const viewer={querySelectorAll:s=>s==='.pswp__counter'?[counter]:[arrow]};
 const doc={querySelectorAll:()=>[{}]};
 assert.deepEqual(singleViewerPosition(viewer,doc,x=>!!x.visible),[1,1]);
 assert.equal(singleViewerPosition(viewer,{querySelectorAll:()=>[{},{}]},x=>!!x.visible),null);
 arrow.visible=true;assert.equal(singleViewerPosition(viewer,doc,x=>!!x.visible),null);
});

function fixture(slides, photoTotal, {jump=1, variationChange, first=0, saved}={}) {
  const url='https://www.mercadolibre.com.mx/up/MLMU4075103096';
  const identity='mercado-libre-mx:MLMU4075103096';
  const binding={tab_id:4,window_id:11,request_id:'r',session_id:'s'};
  let position=first;
  const moves=[],writes=[];
  const listen={addListener(){},removeListener(){}};
  const api={tabs:{get:async()=>({id:4,windowId:11,active:true,url,status:'complete'}),
    query:async()=>[{id:4}],onActivated:listen,onUpdated:listen,
    sendMessage:async(_tab,m)=>{
      assert.equal(m.identity,identity);
      const photo=slides[position];
      if(m.type==='COLLECTOR_GALLERY_WALK_MOVE'){
        assert.equal(m.expected.slidePosition,position+1);
        moves.push(m.direction);position+=m.direction==='next'?jump:-1;return {ok:true};
      }
      assert.equal(m.type,'COLLECTOR_GALLERY_WALK_READ');
      return {identity,page_url:url,sourceArea:'viewer',variation:position===variationChange?['B']:['A'],
        kind:photo?'photo':'video',slidePosition:position+1,slideTotal:slides.length,photoTotal,
        rows:photo?[{url:'https://http2.mlstatic.com/'+photo,width:900,height:1200}]:[]};
    }}};
  const key=p=>JSON.stringify([p.page_url,p.variation]);
  const run=()=>walkVisibleGallery({api,binding,identity,draft:saved,variantKey:key,appendImage,
    currentBinding:async()=>binding,persist:async d=>writes.push(d.images.slice()),sleep:async()=>{}});
  return {run,moves,writes};
}
test('visits only adjacent visible slides, persists each photo before advancing and returns to the last photo after a trailing video',async()=>{
  const f=fixture(['a',null,'b',null],2);
  const d=await f.run();
  assert.deepEqual(d.images,['https://http2.mlstatic.com/a','https://http2.mlstatic.com/b']);
  assert.deepEqual(f.writes.map(x=>x.length),[1,2]);
  assert.deepEqual(f.moves,['next','next','next','previous']);
});
test('replays a partially saved prefix and refuses a conflicting image, wrong initial slide, count, variant or skip',async()=>{
  const one=await fixture(['a'],1).run();
  const partial={...one,total:2};
  const f=fixture(['a','b'],2,{saved:partial});
  assert.equal((await f.run()).images.length,2);
  assert.deepEqual(f.writes.map(x=>x.length),[2]);
  for(const bad of [fixture(['x','b'],2,{saved:partial}),fixture(['a','b'],2,{first:1}),fixture(['a',null],2),
    fixture(['a','b'],2,{variationChange:1}),fixture(['a','b','c'],3,{jump:2})]) await assert.rejects(bad.run());
});

test('clicks one visible viewer control only if the previously displayed slot, real image and page still match',()=>{
  const {moveVisibleGallery}=require('../chrome_extension/gallery_visible.js');
  const page={sourceArea:'viewer',slidePosition:1,slideTotal:2,photoTotal:2,kind:'photo',page_url:'https://www.mercadolibre.com.mx/up/MLMU123',
    variation:['S'],rows:[{url:'https://http2.mlstatic.com/a.jpg',width:900,height:1200}]};
  let clicks=0;
  const next={visible:true,getAttribute:()=>null,click:()=>{clicks++;}};
  const viewer={visible:true,querySelectorAll:q=>{assert.match(q,/arrow--right/);return [next,{visible:false}];}};
  const doc={querySelectorAll:q=>{assert.match(q,/pswp/);return [viewer];}};
  const visible=o=>o.visible===true;
  moveVisibleGallery(doc,visible,page,page,'next');
  for(const edit of [{slidePosition:2},{rows:[{...page.rows[0],url:'https://http2.mlstatic.com/stale.jpg'}]},{variation:[]},{page_url:'other'}]){
    assert.throws(()=>moveVisibleGallery(doc,visible,page,{...page,...edit},'next'),/gallery_walk_changed/);
  }
  next.disabled=true;
  assert.throws(()=>moveVisibleGallery(doc,visible,page,page,'next'),/gallery_walk_control_missing/);
  assert.equal(clicks,1);
});

test('differentiates Chrome activity events, loading, pending fragments, window moves and changed document paths',async()=>{
  const url='https://www.mercadolibre.com.mx/up/MLMU4075103096';
  async function diagnosis({event,tab={},selected=[4],page}){
    const binding={request_id:'r',tab_id:4,window_id:11,session_id:'s'};
    const handlers={};
    const listeners=key=>({addListener:fn=>{handlers[key]=fn;},removeListener:()=>{}});
    let fired=false;
    const api={tabs:{get:async()=>({id:4,windowId:11,active:true,url,status:'complete',...tab}),query:async()=>selected.map(id=>({id})),
      onActivated:listeners('activated'),onUpdated:listeners('updated'),sendMessage:async()=>page}};
    const currentBinding=async()=>{if(event && !fired){fired=true;handlers[event[0]](...event.slice(1));}return binding;};
    return walkVisibleGallery({api,binding,identity:'m:a',variantKey:()=>'',appendImage,currentBinding,persist:async()=>{},sleep:async()=>{}});
  }
  for (const [input,code] of [
    [{event:['activated',{windowId:11,tabId:7}]},'other_tab_activated'],
    [{event:['updated',4,{url:url+'#pid=3',status:'loading'}]},'fragment_loading_event'],
    [{event:['updated',4,{status:'loading'}]},'loading_event'],
    [{event:['updated',4,{url:url+'/different'}]},'url_change_event'],
    [{tab:{id:5}},'target_id_mismatch'],[{tab:{windowId:12}},'window_mismatch'],
    [{tab:{active:false}},'target_inactive'],[{selected:[]},'active_count'],[{selected:[7]},'active_mismatch'],
    [{tab:{pendingUrl:url+'#pid=3'}},'pending_fragment'],[{tab:{pendingUrl:url+'/different'}},'pending_navigation'],
    [{tab:{status:'loading'}},'tab_loading'],
    [{page:{identity:'m:b',page_url:url,variation:[],rows:[],sourceArea:'viewer'}},'product_mismatch'],
    [{page:{identity:'m:a',page_url:url+'#pid=3',variation:[],rows:[],sourceArea:'viewer'}},'receipt_fragment_changed'],
  ]) await assert.rejects(diagnosis(input),new RegExp('gallery_walk_'+code));
});

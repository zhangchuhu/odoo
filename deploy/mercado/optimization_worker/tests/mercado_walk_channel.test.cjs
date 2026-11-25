'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ext = path.resolve(__dirname,'../chrome_extension');
const {walkVisibleGallery} = require(ext+'/gallery_walk.js');
const {appendImage} = require(ext+'/gallery_capture.js');

function installedContent() {
  const location = {href:'https://www.mercadolibre.com.mx/up/MLMU4075103096'};
  const identity = 'mercado-libre-mx:MLMU4075103096';
  const rect = {left:0,top:0,right:800,bottom:600,width:800,height:600};
  const el = {getBoundingClientRect:()=>rect,getAttribute:()=>null};
  const src = letter => 'https://http2.mlstatic.com/'+letter+'.webp';
  const img = label => ({...el,complete:true,naturalWidth:900,naturalHeight:1200,currentSrc:src(label)});
  const gallery = {...el,querySelectorAll:q=>q==='button[aria-label]'?[{...el,getAttribute:()=>'Imagen 1 de 2'}]:
    q==='img'?[{...img('a'),closest:()=>null}]:[]};
  const slots = ['a',null,'b'];
  let listener, pos=0, moves=0, neighbors=0, viewerOpen=true;
  const current = {...el,querySelectorAll:q=>q.startsWith('video')?(slots[pos] ? [] : [el]):
    q==='img.pswp__img:not(.pswp__img--placeholder)'?[img(slots[pos])]:[]};
  const neighbor={...el,querySelectorAll:()=>{neighbors++;return [img('preload')];}};
  const viewer={...el,contains:n=>n===current||n===neighbor,querySelectorAll:q=>{
    if(q==='.pswp__counter')return [{...el,textContent:(pos+1)+' / '+slots.length}];
    if(q.startsWith('button.pswp__button--arrow--right'))return [{...el,click(){moves++;pos++;}}];
    if(q.includes('.pswp__item')){neighbors++;return [neighbor,current];}
    return [];
  }};
  const document = {
    querySelectorAll:q=>q==='.ui-pdp-gallery'?[gallery]:q.startsWith('.pswp.')&&viewerOpen?[viewer]:[],
    elementsFromPoint:(x,y)=>{assert.equal(x,400);assert.equal(y,300);return [{closest:q=>q==='.pswp__item'?current:null}];},
  };
  const ctx=vm.createContext({URL,location,document,getComputedStyle:()=>({opacity:'1'}),setTimeout,
    innerWidth:800,innerHeight:600,devicePixelRatio:1,chrome:{runtime:{onMessage:{addListener:fn=>{listener=fn;}}}}});
  const manifest=JSON.parse(fs.readFileSync(ext+'/manifest.json','utf8'));
  for(const file of manifest.content_scripts[0].js)vm.runInContext(fs.readFileSync(path.join(ext,file),'utf8'),ctx);
  assert.equal(typeof listener,'function');
  function send(message){let reply;listener(message,{id:'own'},result=>{reply=result;});return reply;}
  const binding={request_id:'r',tab_id:4,window_id:11,session_id:'s'};
  const event={addListener(){},removeListener(){}};
  const api={tabs:{query:async()=>[{id:4}],get:async()=>({id:4,windowId:11,active:true,url:location.href,status:'complete'}),
    onActivated:event,onUpdated:event,sendMessage:async(_,msg)=>send(msg)}};
  const run=()=>walkVisibleGallery({api,binding,identity,appendImage,variantKey:p=>JSON.stringify(p.variation),
    currentBinding:async()=>binding,persist:async()=>{},sleep:async()=>{}});
  return {send,run,src,api,identity,close:()=>{viewerOpen=false;},counts:()=>({moves,neighbors})};
}
test('manifest-installed Mercado content listener reads then clicks through an actual photo/video/photo sequence and never reads a preload',async()=>{
  const f=installedContent();
  assert.equal(f.send({type:'COLLECTOR_GALLERY_WALK_READ',identity:f.identity})?.kind,'photo');
  assert.deepEqual((await f.run()).images,[f.src('a'),f.src('b')]);
  assert.deepEqual(f.counts(),{moves:2,neighbors:0});
  assert.match(f.send({type:'COLLECTOR_GALLERY_WALK_MOVE',identity:'wrong'}).error,/身份/);
  assert.equal(f.send({type:'UNRELATED',identity:f.identity}),undefined);
});
test('reports the receiver, malformed reply and unopened viewer as distinct failures',async()=>{
  const f=installedContent();
  f.close();
  await assert.rejects(f.run,/gallery_walk_viewer_required/);
  f.api.tabs.sendMessage=async()=>undefined;
  await assert.rejects(f.run,/gallery_walk_receiver_unavailable/);
  f.api.tabs.sendMessage=async()=>{throw Error('Could not establish connection. Receiving end does not exist.');};
  await assert.rejects(f.run,/gallery_walk_receiver_unavailable/);
  f.api.tabs.sendMessage=async()=>({unexpected:true});
  await assert.rejects(f.run,/gallery_walk_response_invalid/);
});

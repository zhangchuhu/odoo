'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {readVisibleGallery}=require('../chrome_extension/gallery_visible.js');

function fixture({video=false,src='https://http2.mlstatic.com/verified.webp',size=440,unsettled=false}={}) {
  const shown={visible:true};
  const image={...shown,currentSrc:src,complete:true,naturalWidth:size,naturalHeight:590,
    getBoundingClientRect:()=>({width:360,height:500})};
  const item={querySelectorAll:q=>q.startsWith('video') ? (video ? [shown] : []) : [image]};
  const viewer={...shown,contains:n=>n===item,getBoundingClientRect:()=>({left:0,top:20,width:1000,height:600}),
    querySelectorAll:q=>q==='.pswp__counter'?[{...shown,textContent:'3 / 8'}]:[]};
  const gallery={...shown,querySelectorAll:q=>q.startsWith('button')?[{...shown,getAttribute:()=>'Imagen 2 de 7 de Conjunto'}]:[]};
  return {querySelectorAll:q => q==='.ui-pdp-gallery' ? [gallery] : q.startsWith('.pswp.')?[viewer]:[],
    elementsFromPoint:(x,y)=>{assert.equal(x,500);assert.equal(y,320);return unsettled ? [] : [{closest:()=>item}];}};
}
const read=d=>readVisibleGallery('mercado-libre-mx',d,el=>el.visible);
test('Mercado opened PhotoSwipe uses the loaded photo in its current slide, with separate media and photo counts',()=>{
  const result=read(fixture());
  assert.deepEqual(result.rows,[{url:'https://http2.mlstatic.com/verified.webp',width:440,height:590}]);
  assert.equal(result.sourceArea,'viewer');assert.equal(result.slidePosition,3);assert.equal(result.slideTotal,8);assert.equal(result.photoTotal,7);
});
test('Mercado opened viewer rejects a video, an ambiguous transitioning slide and an unrelated CDN',()=>{
  assert.throws(()=>read(fixture({video:true})),/gallery_viewer_video/);
  assert.throws(()=>read(fixture({unsettled:true})),/gallery_viewer_unsettled/);
  assert.throws(()=>read(fixture({src:'https://other.invalid/not-product.webp'})),/gallery_source_untrusted/);
});

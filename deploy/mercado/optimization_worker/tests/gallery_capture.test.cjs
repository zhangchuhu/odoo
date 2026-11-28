'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');

test('candidate viewer returns to first item without changing the verified variant', async () => {
  const {returnToFirst} = require('../chrome_extension/gallery_capture.js');
  let position=3;
  const page=()=>({slidePosition:position,slideTotal:4,variant:'pink'});
  const result=await returnToFirst(page(),{read:async()=>page(),move:async()=>{position--},variant:p=>p.variant,sleep:async()=>{}});
  assert.equal(result.slidePosition,1);
  position=2;
  await assert.rejects(returnToFirst(page(),{read:async()=>({slidePosition:1,slideTotal:4,variant:'blue'}),move:async()=>{},variant:p=>p.variant,sleep:async()=>{}}),/规格/);
});

function signal() {
  const handlers = new Set();
  return {
    addListener: f => handlers.add(f),
    removeListener: f => handlers.delete(f),
    emit: (...args) => handlers.forEach(f => f(...args)),
    get size() { return handlers.size; },
  };
}

for (const [directory, platform, item, product, cdn] of [
  ['chrome_extension','mercado-libre-mx','MLM123','https://www.mercadolibre.com.mx/product/p/MLM123','https://http2.mlstatic.com'],
  ['shein_extension','shein-us','123','https://us.shein.com/Dress-p-123.html','https://img.sheincdn.com'],
]) {
  const {appendImage,observe,submitGallery} = require('../'+directory+'/gallery_capture.js');
  const {readVisibleGallery} = require('../'+directory+'/gallery_visible.js');
  const identity = platform+':'+item;
  const source = cdn+'/a.jpg';
  const page = {identity,page_url:product,variation:['红色'],rows:[{url:source,width:1000,height:1000}]};
  const binding = {request_id:'req',session_id:'session',tab_id:20,window_id:11};
  function setup() {
    const tabs = {
      get: async () => ({id:20,windowId:11,active:true,url:product}),
      query: async (args) => { assert.deepEqual(args,{active:true,windowId:11}); return [{id:20}]; },
      sendMessage: async (id,msg) => {
        assert.equal(id,20);
        assert.deepEqual(msg,{type:'COLLECTOR_VISIBLE_GALLERY',identity});
        return page;
      },
      captureVisibleTab: async (id,options) => {
        assert.equal(id,11);
        assert.equal(options.format,'jpeg');
        return 'data:image/jpeg;base64,/9j/2Q==';
      },
      onActivated:signal(), onUpdated:signal(),
    };
    return {tabs};
  }
  function img(url, options = {}) {
    return {
      currentSrc:url,complete:true,naturalWidth:1100,naturalHeight:1500,visible:true,
      getBoundingClientRect: () => ({width:700,height:900}),
      closest: () => null,
      ...options,
    };
  }
  function doc(images) {
    const gallery = {visible:true,querySelectorAll: q => q === "img" ? images : []};
    return {querySelectorAll: q => q.startsWith('.ui-pdp-gallery') || q.startsWith('.product-intro__gallery') ? [gallery] : []};
  }

  test(platform+' reads only a loaded, visible, full-size image of the main gallery', () => {
    const images = [img('https://different.invalid/a.jpg'),img(cdn+'/hidden',{visible:false}),
      img(cdn+'/thumb',{closest:()=>true}),img(cdn+'/small',{naturalWidth:499}),img(cdn+'/abc#private'),img(source)];
    assert.deepEqual(readVisibleGallery(platform,doc(images),el=>el.visible),{
      rows:[{url:source,width:1100,height:1500}],variation:[],sourceArea:"gallery",slidePosition:null,slideTotal:null,photoTotal:null,
    });
    assert.throws(()=>readVisibleGallery(platform,doc(images.slice(0,-1)),el=>el.visible), /gallery_source_untrusted/);
  });

  test(platform+' keeps observed images in order across visits to the same SKU', () => {
    const first = appendImage(null,page,1,2);
    const next = {...page,page_url:product+'?tracking_id=new',rows:[{url:cdn+'/b.jpg'}]};
    assert.equal(appendImage(first,page,1,2),first);
    assert.deepEqual(appendImage(first,next,2,2).images,[source,cdn+'/b.jpg']);
    assert.throws(()=>appendImage(first,next,2,2,source), /未匹配/);
    assert.throws(()=>appendImage(first,{...next,variation:['蓝色']},2,2), /规格/);
    assert.throws(()=>appendImage(first,{...next,page_url:product+'?sku=66'},2,2), /规格/);
    assert.throws(()=>appendImage(first,page,2,2), /冲突/);
  });

  test(platform+' confirms the scoped host receipt and preserves its first screenshot hash on replay', async () => {
    const api=setup();
    const draft=appendImage(null,page,1,1);
    let submitted=0;
    const receiptHash='e'.repeat(64);
    const submit=async (operation,event) => {
      submitted++;
      assert.equal(operation,'gallery_capture');
      assert.deepEqual(event,{
        identity,detail_url:product,manifest:{detail_url:product,images:[source]},screenshot:'/9j/2Q==',
        ...(platform === 'mercado-libre-mx' ? {variant:draft.variant} : {}),
      });
      return {ok:true,identity,image_count:1,screenshot_sha256:receiptHash};
    };
    assert.deepEqual(await submitGallery(api,binding,draft,submit,async()=>binding),{changed:false,confirmed:receiptHash});
    assert.equal(submitted,1);
    assert.equal(api.tabs.onActivated.size+api.tabs.onUpdated.size,0);
    await assert.rejects(submitGallery(api,binding,draft,async()=>({ok:true}),async()=>binding),/宿主未确认/);
  });

  test(platform+' rejects swapped or inactive detail tabs and never sends their pixels', async () => {
    const api=setup();
    const draft=appendImage(null,page,1,1);
    api.tabs.captureVisibleTab = async () => {
      api.tabs.onActivated.emit({windowId:11,tabId:99});
      api.tabs.onActivated.emit({windowId:11,tabId:20});
      return 'data:image/jpeg;base64,/9j/2Q==';
    };
    await assert.rejects(submitGallery(api,binding,draft,()=>assert.fail('unexpected send'),async()=>binding),/变化/);
    assert.equal(api.tabs.onUpdated.size,0);
    api.tabs.query=async()=>[{id:99}];
    await assert.rejects(observe(api,binding,identity),/活动标签/);
    const swapped=setup();
    swapped.tabs.sendMessage=async()=>({...page,identity:platform+':other'});
    await assert.rejects(observe(swapped,binding,identity),/身份/);
  });
}

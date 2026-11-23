'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');

for (const [ext, platform, item, url] of [
  ['chrome_extension','mercado-libre-mx','MLM123','https://www.mercadolibre.com.mx/p/MLM123'],
  ['shein_extension','shein-us','123','https://us.shein.com/Example-p-123.html'],
]) {
  const {inspectGallery, appendImage} = require('../'+ext+'/gallery_capture.js');
  const page = {identity:platform+':'+item,page_url:url,variation:[],sourceArea:'viewer',photoTotal:4,slidePosition:2,slideTotal:5,
    rows:[{url:'https://example.invalid/photo.jpg',width:1200,height:1800}]};
  function api(reply=page) {
    return {tabs:{get:async()=>({id:3,windowId:9,url,active:true}),query:async()=>[{id:3}],sendMessage:async()=>reply}};
  }
  test(platform+' previews locally through the button while disconnected, including image and carousel counts',async()=>{
    const nodes = new Map();
    const get = id => {
      if (!nodes.has(id)) nodes.set(id,{value:'',textContent:'',dataset:{platform},addEventListener(name,fn){this[name]=fn;}});
      return nodes.get(id);
    };
    get('capture-product').value=item;
    const chrome=api();
    const context={URL,document:{getElementById:get},location:{href:'chrome-extension://id/collector.html?tab=3'},chrome,
      currentGalleryBinding:()=>assert.fail('host must not be contacted')};
    vm.runInNewContext(readFileSync(join(__dirname,'..',ext,'gallery_capture.js'),'utf8'),context);
    await get('capture-inspect').click();
    assert.match(get('capture-preview').textContent,/放大查看器，照片总数 4 张，轮播第 2 \/ 5 项/);
    assert.match(get('capture-preview').textContent,/1200 × 1800 px/);
    assert.match(get('capture-preview').textContent,/photo\.jpg/);
  });
  test(platform+' refuses switched identity and ignores mixed carousel length for photo validation',async()=>{
    await assert.rejects(inspectGallery(api({...page,page_url:url+'?different'}),3,page.identity),/身份/);
    const inactive=api();inactive.tabs.query=async()=>[{id:4}];
    await assert.rejects(inspectGallery(inactive,3,page.identity),/活动标签/);
    const mismatch=api({...page,identity:platform+':other'});
    await assert.rejects(inspectGallery(mismatch,3,page.identity),/身份/);
    assert.throws(()=>appendImage(null,page,1,5),/照片总数/);
    assert.equal(appendImage(null,page,1,4).total,4);
    assert.equal(appendImage(null,{...page,photoTotal:null},1,4).total,4);
  });
}

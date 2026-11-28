const test = require('node:test');
const assert = require('node:assert/strict');
const {cardSales, productReviews, productRating, productPrice, noSearchResults} = require('../shein_extension/home_sales.js');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('uses only unambiguous visible sales labels inside a product card', () => {
  const card = {querySelectorAll: () => [{textContent:'100+ sold',visible:true},{textContent:'999+ sold',visible:false}]};
  assert.equal(cardSales(card, node=>node.visible), '100+ sold');
  const ambiguous = {querySelectorAll: () => [{textContent:'100+ sold',visible:true},{textContent:'200+ sold',visible:true}]};
  assert.equal(cardSales(ambiguous, node=>node.visible), null);
  assert.equal(cardSales({querySelectorAll:()=>[]}, node=>node.visible), null);
  assert.equal(cardSales({querySelectorAll:()=>[{textContent:'100+ sold',visible:true},{textContent:'unknown',visible:true}]}, node=>node.visible), null);
  assert.equal(cardSales({querySelectorAll:()=>[{textContent:'+100+ sold',visible:true}]}, node=>node.visible), null);
});

test('only the product header provides review count, not recommendation cards', () => {
  const product = {innerText:'(51 Reviews)', visible:true};
  const doc = {querySelectorAll:s => s === '.product-intro__head-reviews-text' ? [product] : [{innerText:'999 Reviews',visible:true}]};
  assert.equal(productReviews(doc, node=>node.visible), '51');
  assert.equal(productReviews({querySelectorAll:()=>[]}, node=>node.visible), null);
  assert.equal(productReviews({querySelectorAll:()=>[{innerText:'(51 Reviews)',visible:true},{innerText:'(52 Reviews)',visible:true}]}, node=>node.visible), null);
});

test('product rating is optional and read from the visible product header', () => {
  const doc = {querySelectorAll:()=>[{visible:true,getAttribute:()=> 'Average Rating 5.00 12 Reviews'}]};
  assert.equal(productRating(doc, node=>node.visible), '5.00');
  assert.equal(productRating({querySelectorAll:()=>[]}, node=>node.visible), null);
  assert.equal(productRating({querySelectorAll:()=>[{visible:true,getAttribute:()=> 'Average Rating 9.00'}]}, node=>node.visible), null);
});

test('current product price excludes coupons, ranges, and other products', () => {
  const doc = {querySelectorAll:s=>s==='#productMainPriceId' ? [{innerText:'$55.28',visible:true}] : []};
  assert.deepEqual(productPrice(doc, node=>node.visible), {amount:'55.28',currency:'USD',source:'product_detail'});
  assert.equal(productPrice({querySelectorAll:()=>[{innerText:'$55.28 - $99.00',visible:true}]},node=>node.visible),null);
  assert.equal(productPrice({querySelectorAll:()=>[]},node=>node.visible),null);
});

test('homepage card extraction adds scoped sales without changing the legacy contract', () => {
  let listener;
  const rect = {left:0, top:0, right:100, bottom:100, width:100, height:100};
  const elem = {getBoundingClientRect:()=>rect, getAttribute:()=>null};
  const link = {...elem, href:'https://us.shein.com/example-p-123.html', innerText:'Dress'};
  const img = {...elem, src:'https://img.example/test.jpg'};
  const badge = {...elem, textContent:'500+ sold'};
  const parent={matches:s=>s==='.product-list',closest:s=>s==='.product-list-v2__section'?{}:null};
  const card = {...elem, parentElement:parent, matches:s=>s==='.bs-product-card.product-list__item', querySelectorAll(selector) {
    if (selector.includes('a[href')) return [link];
    if (selector==='img') return [img];
    if (selector==='.bs-product-card__sales-label') return [badge];
    return [];
  }};
  const context = {URL, innerWidth:100, innerHeight:100, devicePixelRatio:1,
    getComputedStyle:()=>({display:'block', visibility:'visible', opacity:1}),
    location:{href:'https://us.shein.com/pdsearch/dress/'},
    document:{querySelectorAll:s=>s.startsWith('[role="dialog"]')?[]:[card]},
    SheinVisibleSearch:{visibleSearch:()=> 'dress'},
    chrome:{runtime:{onMessage:{addListener:fn=>listener=fn}}}};
  vm.createContext(context);
  for (const f of ['home_sales.js','content.js']) vm.runInContext(fs.readFileSync(path.join(__dirname,'../shein_extension',f),'utf8'),context);
  function read(type) {let result;listener({type,query:'dress',limit:30},null,x=>result=x);return result;}
  assert.equal(read('SHEIN_COLLECTOR_EXTRACT_HOME_SEARCH').observations[0].sales_text,'500+ sold');
  const offscreenHeading={getBoundingClientRect:()=>({...rect,top:-200,bottom:-100})};
  context.SheinVisibleSearch.visibleSearch=(_doc,rendered)=>rendered(offscreenHeading)?'dress':null;
  assert.equal(read('SHEIN_COLLECTOR_EXTRACT_HOME_SEARCH').search_query,'dress');
  assert.equal('sales_text' in read('SHEIN_COLLECTOR_EXTRACT_SEARCH').observations[0],false);
  badge.getBoundingClientRect=()=>({...rect,top:150,bottom:200});
  assert.equal(read('SHEIN_COLLECTOR_EXTRACT_HOME_SEARCH').observations.length,0);
  badge.getBoundingClientRect=()=>rect;card.parentElement={matches:()=>false,closest:()=>null};
  assert.equal(read('SHEIN_COLLECTOR_EXTRACT_HOME_SEARCH').observations.length,0);
});

test('homepage collector scrolls to end and never asks for a later page', async () => {
  const {collectHome} = require('../shein_extension/home_collector.js');
  const messages=[],saved=[];
  const card={product_id:'123',sales_text:'500+ sold'};
  const result=await collectHome({query:'dress',queryIndex:0,prior:[],nextBatch:0,
    send:async msg=>{messages.push(msg.type);if(msg.type==='SHEIN_COLLECTOR_EXTRACT_HOME_SEARCH')return {page_url:'https://us.shein.com/pdsearch/dress/',search_query:'dress',observations:[card]};return {at_end:true};},
    save:async (...args)=>{saved.push(args);return true;},finish:async()=>true,delay:async()=>{}});
  assert.equal(result.cards.length,1);assert.equal(saved.length,1);
  assert.equal(messages.includes('SHEIN_COLLECTOR_NEXT_PAGE'),false);
  assert.equal(messages.filter(x=>x==='SHEIN_COLLECTOR_SCROLL').length,4);
});

test('an explicit no-results message prevents collecting recommended products', async () => {
  const node={innerText:"We couldn't find any results for blue unicorn but these other products might interest you:"};
  const doc={querySelectorAll:s=>s==='.product-list-v2__hypernym-hint-old'?[node]:[]};
  assert.equal(noSearchResults(doc,()=>true,'blue unicorn'),true);
  assert.equal(noSearchResults(doc,()=>true,'different query'),false);
  const {collectHome}=require('../shein_extension/home_collector.js');let finish;
  const result=await collectHome({query:'blue unicorn',queryIndex:0,prior:[],nextBatch:0,
    send:async()=>({page_url:'https://us.shein.com/pdsearch/blue-unicorn/',search_query:'blue unicorn',observations:[],no_results:true}),
    save:async()=>{throw Error('must not save recommendations')},finish:async(...args)=>{finish=args;return true},delay:async()=>{}});
  assert.equal(result.cards.length,0);assert.equal(finish[2],true);
});

test('home detail keeps reviews and price without inventing detail sales', () => {
  const context={URL,__SHEIN_COLLECTOR_TEST__:{}};vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../shein_extension/collector.js'),'utf8'),context);
  const visible={identity:'shein-us:123',identity_matches:true,product_id:'123',title:'Dress',page_url:'https://us.shein.com/x-p-123.html',reviews_display:'21',rating_display:'4.9',price_observation:{amount:'10.00',currency:'USD',source:'product_detail'}};
  const detail=context.__SHEIN_COLLECTOR_TEST__.homeDetailOutcome('shein-us:123',visible);
  assert.equal(detail.sold_display,null);assert.equal(detail.reviews_display,'21');assert.equal(detail.price_observation.amount,'10.00');
  assert.throws(()=>context.__SHEIN_COLLECTOR_TEST__.homeDetailOutcome('shein-us:999',visible));
});

test('visual photo is bound to the visible SHEIN gallery and must remain unchanged', async () => {
  const {readCandidatePhoto}=require('../shein_extension/gallery_capture.js');
  let source='https://img.sheincdn.com/a.jpg';const url='https://us.shein.com/x-p-123.html';
  const api={tabs:{get:async()=>({id:1,windowId:2,active:true,url}),query:async()=>[{id:1}],
    sendMessage:async()=>({identity:'shein-us:123',page_url:url,variation:[],rows:[{url:source}]})}};
  const photo=await readCandidatePhoto(api,{tab_id:1,window_id:2},'shein-us:123');
  assert.equal(photo.source,source);await photo.recheck();
  source='https://img.sheincdn.com/b.jpg';await assert.rejects(photo.recheck());
});

test('a confirmed visual photo receipt can be resumed without downloading again', async () => {
  const {downloadVisualCandidatePhoto}=require('../shein_extension/gallery_download.js');
  const token='a'.repeat(32),request='01234567-89ab-4cde-8fab-0123456789ab',source='https://img.sheincdn.com/a.jpg';
  const receipt={ok:true,source,receipt:token,token,confirmed:true,filename:`Odoo-Research/${request}/1/${token}.jpg`};
  const result=await downloadVisualCandidatePhoto({api:{},requestId:request,observation:{source},checkpoint:async()=>receipt,recheck:async()=>{}});
  assert.equal(result,receipt);
});

test('SHEIN current product gallery supports the observed main-picture layout', () => {
  const {readVisibleGallery}=require('../shein_extension/gallery_visible.js');
  const img={complete:true,naturalWidth:900,naturalHeight:1200,currentSrc:'https://img.ltwebstatic.com/a.jpg',closest:()=>null,getBoundingClientRect:()=>({width:300,height:400})};
  const gallery={contains:()=>false,querySelectorAll:s=>s==='img'?[img]:[]};
  const doc={querySelectorAll:s=>s.includes('.main-picture')?[gallery]:[]};
  assert.equal(readVisibleGallery('shein-us',doc,()=>true).rows[0].url,img.currentSrc);
});

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function collector(scopes, bodyText = '') {
  const context = {URL, innerWidth: 1200, innerHeight: 800, devicePixelRatio: 1,
    location: {href: 'https://listado.mercadolibre.com.mx/dress'},
    __MERCADO_COLLECTOR_TEST__: {},
    document: {body: {innerText: bodyText}, querySelector: () => null,
      querySelectorAll: selector => selector === '.ui-search-layout' ? scopes : []}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/content.js', 'utf8'), context);
  return context.__MERCADO_COLLECTOR_TEST__;
}

test('only an unambiguous current offer produces a price', () => {
  const api = collector([]);
  assert.equal(api.parseCurrentPrice(['178.78']), 178.78);
  assert.equal(api.parseCurrentPrice(['178.78', '298']), null);
  assert.equal(api.parseCurrentPrice(['not a price']), null);
});

test('visual search refuses missing or ambiguous results containers', () => {
  assert.match(collector([]).extractSearch('dress', [], 100, true).error, /搜索结果区域/);
  assert.match(collector([{}, {}]).extractSearch('dress', [], 100, true).error, /搜索结果区域/);
});

test('an explicit empty result is accepted without searching recommendation cards', () => {
  const value = collector([], 'No encontramos resultados').extractSearch('dress', [], 100, true);
  assert.equal(value.no_results, true);
  assert.equal(value.verified_scope, true);
  assert.equal(value.observations.length, 0);
});

test('card sales protocol advertises its version on a verified empty results page', () => {
  const value = collector([], 'No encontramos resultados').extractSearch('dress', [], 100, true, true);
  assert.equal(value.card_sales_version, 3);
  assert.equal(value.verified_scope, true);
});

test('visual search reads only its verified results container', () => {
  let used = false;
  const scope = {querySelectorAll: () => { used = true; return []; }};
  const value = collector([scope]).extractSearch('dress', [], 100, true);
  assert.equal(value.observations.length, 0);
  assert.equal(used, true);
});

test('card sales reads only an unambiguous visible product-sales label', () => {
  const context={URL,innerWidth:1200,innerHeight:800,devicePixelRatio:1,
    getComputedStyle:()=>({visibility:'visible',display:'block',opacity:1}),__MERCADO_COLLECTOR_TEST__:{}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/content.js','utf8'),context);
  const node=(text,seller=false)=>({innerText:text,textContent:text,children:[],closest:()=>seller?{}:null,
    getBoundingClientRect:()=>({width:10,height:10,bottom:10,right:10,top:0,left:0})});
  const extract=context.__MERCADO_COLLECTOR_TEST__.cardSales;
  assert.equal(extract({querySelectorAll:()=>[node('+500 vendidos')]}).text,'+500 vendidos');
  assert.equal(extract({querySelectorAll:()=>[node('| +1000 vendidos')]}).text,'+1000 vendidos');
  assert.equal(extract({querySelectorAll:()=>[node('4.6 | +1000 vendidos')]}).status,'missing');
  assert.equal(extract({querySelectorAll:()=>[node('| +1000 vendidos',true)]}).status,'missing');
  assert.equal(extract({querySelectorAll:()=>[node('+500 vendidos',true)]}).status,'missing');
  assert.equal(extract({querySelectorAll:()=>[node('+500 vendidos'),node('+100 vendidos')]}).status,'ambiguous');
});

test('pagination accepts only observed same-platform next links and fails closed on absence', () => {
  const context={URL,innerWidth:1200,innerHeight:800,devicePixelRatio:1,
    getComputedStyle:()=>({visibility:'visible',display:'block',opacity:1}),__MERCADO_COLLECTOR_TEST__:{}};
  vm.createContext(context);vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/content.js','utf8'),context);
  const next=href=>({href,children:[],getAttribute:()=>null,getBoundingClientRect:()=>({width:10,height:10,bottom:10,right:10,top:0,left:0})});
  const read=context.__MERCADO_COLLECTOR_TEST__.observedNextPage;
  assert.equal(read({querySelectorAll:()=>[next('https://listado.mercadolibre.com.mx/dress_Desde_51')]},1).status,'has_next');
  assert.equal(read({querySelectorAll:()=>[next('https://example.com/other')]},1).status,'unverified');
  assert.equal(read({querySelectorAll:()=>[]},1).status,'unverified');
  assert.equal(read({querySelectorAll:()=>[]},3).status,'page_limit');
});

test('explicit empty search result is an end marker only outside result containers', () => {
  const context={URL,innerWidth:1200,innerHeight:800,devicePixelRatio:1,__MERCADO_COLLECTOR_TEST__:{}};
  vm.createContext(context);vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/content.js','utf8'),context);
  const read=context.__MERCADO_COLLECTOR_TEST__.cardPageInfo;
  assert.equal(read({body:{innerText:'No encontramos resultados'},querySelectorAll:()=>[]},1).end_evidence,'empty_results');
  assert.equal(read({body:{innerText:'No encontramos resultados'},querySelectorAll:()=>[{}]},1).status,'unverified');
});

test('page binding reads a rendered search input even when scrolled off screen',()=>{
  let hidden=false;
  const input={value:'dress',getBoundingClientRect:()=>({width:100,height:20,top:-500,bottom:-480,left:0,right:100})};
  const context={URL,innerWidth:1200,innerHeight:800,devicePixelRatio:1,location:{href:'https://listado.mercadolibre.com.mx/dress'},
    document:{body:{innerText:''},querySelector:()=>input,querySelectorAll:()=>[]},getComputedStyle:()=>({visibility:hidden?'hidden':'visible',display:'block',opacity:1}),__MERCADO_COLLECTOR_TEST__:{}};
  vm.createContext(context);vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/content.js','utf8'),context);
  assert.equal(context.__MERCADO_COLLECTOR_TEST__.extractBoundSearchIdentity().search_query,'dress');
  hidden=true;assert.equal(context.__MERCADO_COLLECTOR_TEST__.extractBoundSearchIdentity().search_query,null);
});

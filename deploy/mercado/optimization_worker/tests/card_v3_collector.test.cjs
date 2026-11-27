const test=require('node:test');
const assert=require('node:assert/strict');
const {collectPage}=require('../chrome_extension/card_v3_collector.js');

test('resumes durable page batches and saves only new observations before finishing',async()=>{
 const prior=[{product_id:'MLM1',rank:1}],second={product_id:'MLM2',rank:2};let scans=0;const saved=[];
 const result=await collectPage({queryIndex:0,query:'dress',page:1,prior,nextBatch:1,delay:async()=>{},
   send:async msg=>{
     if(msg.type==='MERCADO_COLLECTOR_PAGE_BINDING')return {page_url:'https://listado.mercadolibre.com.mx/dress',search_query:'dress'};
     if(msg.type==='MERCADO_COLLECTOR_EXTRACT_SEARCH'){scans++;return {page_url:'https://listado.mercadolibre.com.mx/dress',verified_scope:true,card_sales_version:3,observations:[...prior,second]};}
     return {at_end:true};
   },save:async(...args)=>{saved.push(args);return true;},finish:async()=>true});
 assert.equal(result.cards.length,2);assert.equal(saved.length,1);assert.equal(saved[0][3],1);assert.equal(saved[0][4][0].product_id,'MLM2');assert.equal(saved[0][5][0].product_id,'MLM1');assert.ok(scans>=4);
});

test('refuses mismatched page or unconfirmed writes',async()=>{
 const base={queryIndex:0,query:'dress',page:1,prior:[],nextBatch:0,delay:async()=>{},finish:async()=>true};
 await assert.rejects(collectPage({...base,send:async()=>({page_url:'https://listado.mercadolibre.com.mx/dress',search_query:'other'}),save:async()=>true}),/binding/);
 await assert.rejects(collectPage({...base,send:async msg=>msg.type==='MERCADO_COLLECTOR_PAGE_BINDING'?{page_url:'https://listado.mercadolibre.com.mx/dress',search_query:'dress'}:{page_url:'https://listado.mercadolibre.com.mx/dress',verified_scope:true,card_sales_version:3,observations:[{product_id:'MLM1',rank:1}]},save:async()=>false}),/checkpoint/);
});

test('collects a search whose displayed capitalization differs from the plan',async()=>{
 const result=await collectPage({queryIndex:1,query:'Conjunto mujer falda top',page:1,prior:[],nextBatch:0,delay:async()=>{},
  send:async msg=>msg.type==='MERCADO_COLLECTOR_PAGE_BINDING'?{page_url:'https://listado.mercadolibre.com.mx/conjunto',search_query:'conjunto mujer falda top'}:
   msg.type==='MERCADO_COLLECTOR_EXTRACT_SEARCH'?{page_url:'https://listado.mercadolibre.com.mx/conjunto',verified_scope:true,card_sales_version:3,observations:[],no_results:true}:{at_end:true},
  save:async()=>true,finish:async()=>true});
 assert.equal(result.cards.length,0);
});

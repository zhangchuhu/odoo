const test = require('node:test');
const assert = require('node:assert/strict');
const { makeReceipt } = require('../extension_binding.cjs');
const challenge={version:1,request_id:'req-1',nonce:'12345678901234567890123456789012',lease_epoch:3,plan_digest:'a'.repeat(64),platform:'mercado-libre-mx',query_index:0,query:'vestido'};
const tab={id:12,windowId:5,url:'https://listado.mercadolibre.com.mx/vestido'};
test('binds an observed browser tab to the exact challenge and query',()=>{
 const result=makeReceipt(challenge,'b'.repeat(32),tab,{page_url:tab.url,search_query:'vestido'},12,'2026-11-19T00:00:00Z');
 assert.equal(result.tab_id,12);assert.equal(result.collector_tab_id,12);assert.equal(result.nonce,challenge.nonce);
});
test('rejects wrong tab, stale page and changed query',()=>{
 for(const [t,p,id] of [[tab,{page_url:tab.url,search_query:'otra'},12],[tab,{page_url:'https://evil.test/',search_query:'vestido'},12],[tab,{page_url:tab.url,search_query:'vestido'},13]]) assert.throws(()=>makeReceipt(challenge,'b'.repeat(32),t,p,id,'2026-11-19T00:00:00Z'));
});
test('SHEIN receipt uses exact visible query and its allowed host',()=>{
 const c={...challenge,platform:'shein-us',query:'lace dress'};
 const t={...tab,url:'https://us.shein.com/pdsearch/lace-dress/'};
 assert.equal(makeReceipt(c,'b'.repeat(32),t,{page_url:t.url,search_query:'lace dress'},12,'2026-11-19T00:00:00Z').platform,'shein-us');
 assert.throws(()=>makeReceipt(c,'b'.repeat(32),t,{page_url:t.url,search_query:null},12,'2026-11-19T00:00:00Z'));
});
test('browser receipt identifies each invalid field without logging values',()=>{
 const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');const ctx={URL};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../chrome_extension/binding_validation.js'),'utf8'),ctx);const f=ctx.CollectorBinding.makeReceipt;
 const args=[challenge,'b'.repeat(32),tab,{page_url:tab.url,search_query:'vestido'},12,'2026-11-19T00:00:00Z'];
 for(const [index,value,code] of [[0,{...challenge,nonce:'bad'},'receipt_nonce_invalid'],[1,'bad','receipt_session_invalid'],[4,13,'receipt_tab_mismatch'],[3,{page_url:tab.url,search_query:'otra'},'receipt_query_mismatch'],[3,{page_url:'https://evil.test/',search_query:'vestido'},'receipt_page_url_mismatch']]) {const a=args.slice();a[index]=value;assert.throws(()=>f(...a),e=>e.code===code && !e.message.includes('otra'));}
});
test('homepage receipt accepts the current query and rejects other platforms',()=>{
 const vm=require('node:vm'),fs=require('node:fs');const ctx={URL};vm.runInNewContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/binding_validation.js','utf8'),ctx);const f=ctx.CollectorBinding.makeReceipt;
 const c={...challenge,version:5,query_index:8};
 assert.equal(f(c,'b'.repeat(32),tab,{page_url:tab.url,search_query:'vestido'},12,'2026-11-19T00:00:00Z').version,5);
 assert.throws(()=>f({...c,platform:'shein-us'},'b'.repeat(32),tab,{page_url:tab.url,search_query:'vestido'},12,'2026-11-19T00:00:00Z'));
});
test('homepage receipt accepts Mercado case normalization but rejects a different term',()=>{
 const vm=require('node:vm'),fs=require('node:fs');const ctx={URL};vm.runInNewContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/binding_validation.js','utf8'),ctx);
 const c={...challenge,version:5,query:'Conjunto mujer falda top'};
 assert.equal(ctx.CollectorBinding.makeReceipt(c,'b'.repeat(32),tab,{page_url:tab.url,search_query:'conjunto mujer falda top'},12,'2026-11-19T00:00:00Z').query,c.query);
 assert.throws(()=>ctx.CollectorBinding.makeReceipt(c,'b'.repeat(32),tab,{page_url:tab.url,search_query:'conjunto mujer falda blusa'},12,'2026-11-19T00:00:00Z'));
});
test('URL mismatch diagnostics distinguish fragment, encoding and navigation without values',()=>{
 const vm=require('node:vm'),fs=require('node:fs');const ctx={URL};vm.runInNewContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/binding_validation.js','utf8'),ctx);const f=ctx.CollectorBinding.makeReceipt;
 const compare=(a,b,expected)=>assert.throws(()=>f(challenge,'b'.repeat(32),{...tab,url:a},{page_url:b,search_query:'vestido'},12,'2026-11-19T00:00:00Z'),e=>e.detailCode===expected && !e.message.includes('listado'));
 compare(tab.url+'#a',tab.url+'#b','receipt_url_fragment_mismatch');
 compare('https://listado.mercadolibre.com.mx/vestido%20rosa','https://listado.mercadolibre.com.mx/vestido rosa','receipt_url_encoding_mismatch');
 compare(tab.url,'https://listado.mercadolibre.com.mx/otro','receipt_url_navigation_mismatch');
});
test('classifies missing URL and content extraction failures without exposing message',()=>{
 const vm=require('node:vm'),fs=require('node:fs');const ctx={URL};vm.runInNewContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/binding_validation.js','utf8'),ctx);const f=ctx.CollectorBinding.makeReceipt;
 const check=(t,p,detail)=>assert.throws(()=>f(challenge,'b'.repeat(32),t,p,12,'2026-11-19T00:00:00Z'),e=>e.detailCode===detail&&!e.message.includes('secret'));
 check({...tab,url:undefined},{page_url:tab.url,search_query:'vestido'},'receipt_tab_url_missing');
 check({...tab,url:'bad'},{page_url:tab.url,search_query:'vestido'},'receipt_tab_url_invalid');
 check(tab,{search_query:'vestido'},'receipt_page_url_missing');
 check(tab,{page_url:'bad',search_query:'vestido'},'receipt_page_url_invalid');
 check(tab,{error:'Collection stopped: blocking page detected (secret)'},'receipt_page_blocked');
 check(tab,{error:'Collection stopped: card link=missing secret'},'receipt_page_card_error');
 check(tab,{error:'Collection stopped: incomplete card secret'},'receipt_page_card_incomplete');
 check(tab,{error:'secret'},'receipt_page_extract_error');
});

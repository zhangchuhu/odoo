'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
test('records fixed stages with timestamps and rejects sensitive values',async()=>{
 let store={};const chrome={storage:{local:{get:async()=>store,set:async v=>Object.assign(store,v)}}};const ctx={chrome,Date,Array,globalThis:{}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../chrome_extension/collector-trace.js'),'utf8'),ctx);
 await ctx.globalThis.CollectorTrace.append('anchor_confirm','ok');await ctx.globalThis.CollectorTrace.append('anchor_confirm','secret-token');
 assert.equal(store.collector_trace_v1.length,1);assert.equal(store.collector_trace_v1[0].stage,'anchor_confirm');assert.ok(!Number.isNaN(Date.parse(store.collector_trace_v1[0].at)));
});
test('each diagnostic attempt shares a random identifier across stages',async()=>{
 let store={};const chrome={storage:{local:{get:async()=>store,set:async v=>Object.assign(store,v)}}};const ctx={chrome,Date,Array,globalThis:{},crypto:require('node:crypto').webcrypto,Uint8Array};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../chrome_extension/collector-trace.js'),'utf8'),ctx);const t=ctx.globalThis.CollectorTrace;await t.begin();await t.append('button','clicked');await t.append('anchor_guard','ok');assert.match(store.collector_trace_v1[0].attempt,/^[a-f0-9]{16}$/);assert.equal(store.collector_trace_v1[0].attempt,store.collector_trace_v1[1].attempt);
});
test('classifies errors without exposing messages',()=>{
 const ctx={chrome:{},Date,Array,globalThis:{}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../chrome_extension/collector-trace.js'),'utf8'),ctx);
 const t=ctx.globalThis.CollectorTrace;
 assert.equal(t.errorCode(new TypeError('private token')),'type_error');
 assert.equal(t.errorCode(new ReferenceError('private token')),'reference_error');
 assert.equal(t.errorCode({name:'private token'}),'other_error');
});
test('identifies known connection failures without persisting arbitrary error text', async()=>{
 let store={}; const chrome={storage:{local:{get:async()=>store,set:async v=>Object.assign(store,v)}}};
 const ctx={chrome,Date,Array,globalThis:{}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../chrome_extension/collector-trace.js'),'utf8'),ctx);
 const t=ctx.globalThis.CollectorTrace;
 await t.appendError('binding_session',new Error('session_timeout'));
 await t.appendError('binding_select',new Error('anchor_select_pending'));
 await t.appendError('collector_action',new Error('private token https://private.example'));
 assert.equal(store.collector_trace_v1[0].code,'session_timeout');
 assert.equal(store.collector_trace_v1[1].code,'anchor_select_pending');
 assert.equal(store.collector_trace_v1[2].message,'[message redacted]');
 assert.ok(!JSON.stringify(store).includes('private.example'));
});
test('records a bounded sanitized exception message with its fixed error code',async()=>{
 let store={};const chrome={storage:{local:{get:async()=>store,set:async v=>Object.assign(store,v)}}};const ctx={chrome,Date,Array,globalThis:{}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../chrome_extension/collector-trace.js'),'utf8'),ctx);
 const t=ctx.globalThis.CollectorTrace;await t.appendError('reconnect_start',new TypeError('Illegal invocation'));
 assert.equal(store.collector_trace_v1[0].code,'type_error');assert.equal(store.collector_trace_v1[0].message,'Illegal invocation');
 await t.appendError('reconnect_start',new Error('secret token https://private.example/path'));
 assert.equal(store.collector_trace_v1[1].message,'[message redacted]');
 await t.appendError('binding_activate',new Error('Activation challenge unavailable'));
 assert.equal(store.collector_trace_v1[2].message,'Activation challenge unavailable');
 let node={};await t.render(node);assert.match(node.textContent,/Illegal invocation/);
});
test('shows newest diagnostics first',async()=>{
 let store={collector_trace_v1:[{at:'2026-11-21T00:00:00Z',stage:'first',code:'ok'},{at:'2026-11-21T00:01:00Z',stage:'latest',code:'error'}]};const chrome={storage:{local:{get:async()=>store,set:async v=>Object.assign(store,v)}}};const ctx={chrome,Date,Array,globalThis:{},crypto:require('node:crypto').webcrypto,Uint8Array};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../chrome_extension/collector-trace.js'),'utf8'),ctx);let node={};await ctx.globalThis.CollectorTrace.render(node);assert.match(node.textContent.split('\n')[0],/latest error/);
});
test('shows failures before newer routine polling while preserving newest failure first',async()=>{
 let store={collector_trace_v1:[{at:'1',stage:'activation_receipt',code:'receipt_page_url_mismatch'},{at:'2',stage:'activation_confirm',code:'error',message:'Activation rejected'},{at:'3',stage:'binding_identity',code:'ok'}]};const ctx={chrome:{storage:{local:{get:async()=>store}}},Date,Array,globalThis:{}};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../chrome_extension/collector-trace.js'),'utf8'),ctx);let node={};await ctx.globalThis.CollectorTrace.render(node);const rows=node.textContent.split('\n');assert.match(rows[0],/activation_confirm error/);assert.match(rows[1],/activation_receipt receipt_page_url_mismatch/);assert.match(rows[2],/binding_identity ok/);
});
test('activation failures take precedence over repeated newer query errors',async()=>{
 let store={collector_trace_v1:[{at:'1',stage:'activation_confirm',code:'error'},{at:'2',stage:'query_receipt',code:'receipt_page_url_mismatch'}]};const ctx={chrome:{storage:{local:{get:async()=>store}}},Date,Array,globalThis:{}};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../chrome_extension/collector-trace.js'),'utf8'),ctx);let node={};await ctx.globalThis.CollectorTrace.render(node);assert.match(node.textContent.split('\n')[0],/activation_confirm error/);
});

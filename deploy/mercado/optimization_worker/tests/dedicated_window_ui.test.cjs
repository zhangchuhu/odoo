'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
test('control page disables create until window confirmed and shows failure',async()=>{
 const els={};for(const id of ['status','register','create'])els[id]={disabled:false,textContent:'',addEventListener(_,f){this.click=f}};
 let fail=true;const chrome={runtime:{sendMessage:async m=>m.type==='dedicated_current_request'?{request_id:'request-242'}:m.type==='dedicated_window_register'?(fail?{error:'x'}:{window_id:7}):{tab_id:9}}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../chrome_extension/dedicated-window-ui.js'),'utf8'),{document:{getElementById:id=>els[id]},chrome,Number});
 assert.equal(els.create.disabled,true);await els.register.click();assert.equal(els.create.disabled,true);assert.match(els.status.textContent,/未能/);
 fail=false;await els.register.click();assert.equal(els.create.disabled,false);assert.match(els.status.textContent,/已确认/);await els.create.click();assert.match(els.status.textContent,/9/);
});
test('reports task discovery and window verification separately',async()=>{
 const els={};for(const id of ['status','register','create'])els[id]={disabled:false,textContent:'',addEventListener(_,f){this.click=f}};
 let discovery=true;const chrome={runtime:{sendMessage:async m=>m.type==='dedicated_current_request'?(discovery?{error:'unavailable'}:{request_id:'request-242'}):{error:'anchor_confirm_failed'}}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../chrome_extension/dedicated-window-ui.js'),'utf8'),{document:{getElementById:id=>els[id]},chrome,Number});
 await els.register.click();assert.match(els.status.textContent,/获取当前任务/);assert.equal(els.create.disabled,true);
 discovery=false;await els.register.click();assert.match(els.status.textContent,/后台确认窗口/);assert.equal(els.create.disabled,true);
});
test('records timestamped control steps without request secrets',async()=>{
 const els={};for(const id of ['status','register','create','diagnostics'])els[id]={disabled:false,textContent:'',addEventListener(_,f){this.click=f}};
 const entries=[];const chrome={runtime:{sendMessage:async m=>m.type==='dedicated_current_request'?{request_id:'request-242'}:{error:'anchor_confirm_failed'}}};
 const Trace={append:async (stage,code)=>{entries.push({stage,code,at:new Date().toISOString()})},render:async el=>{el.textContent=JSON.stringify(entries)}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../chrome_extension/dedicated-window-ui.js'),'utf8'),{document:{getElementById:id=>els[id]},chrome,Number,CollectorTrace:Trace});
 await els.register.click();assert.deepEqual(entries.map(x=>x.stage),['button','task_discovery','window_registration']);assert.equal(entries[2].code,'anchor_confirm_failed');assert.match(els.diagnostics.textContent,/anchor_confirm_failed/);assert.doesNotMatch(JSON.stringify(entries),/request-242/);
});

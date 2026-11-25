'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
test('popup without target offers owned-window entry and never marks it confirmed',async()=>{
 const button={addEventListener(_,fn){this.click=fn},disabled:false},section={hidden:true},status={textContent:''},nodes=[section,button,status,{hidden:false}];let calls=[];
 const chrome={runtime:{sendMessage:async m=>(calls.push(m.type),{windowId:23,tabId:17})},tabs:{update:async(id,opts)=>calls.push(['tab',id,opts.active])},windows:{update:async(id,opts)=>calls.push([id,opts.focused])}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../chrome_extension/control-entry.js'),'utf8'),{document:{getElementById:id=>({'control-entry':section,'control-entry-button':button,'control-entry-status':status}[id]),body:{children:nodes}},location:{href:'chrome-extension://test/collector.html'},URL,chrome,Number});
 assert.equal(section.hidden,false);await button.click();assert.deepEqual(calls,['dedicated_window_prepare',['tab',17,true],[23,true]]);assert.match(status.textContent,/确认/);
});

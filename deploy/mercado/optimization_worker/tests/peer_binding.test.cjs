const test=require('node:test');const assert=require('node:assert/strict');const {verifyPeer}=require('../chrome_extension/peer_binding.js');
test('verifies broker supplied extension and current SHEIN tab',async()=>{
 const api={tabs:{get:async()=>({id:9,windowId:7})}};
 assert.equal(await verifyPeer(api,{id:'shein'}, {tab_id:9,window_id:7},'shein'),true);
 // The sender is an extension collector popup, not the target product tab.
 assert.equal(await verifyPeer(api,{id:'shein',tab:{id:777,windowId:99}}, {tab_id:9,window_id:7},'shein'),true);
 assert.equal(await verifyPeer(api,{id:'other'}, {tab_id:9,window_id:7},'shein'),false);
 assert.equal(await verifyPeer(api,{id:'shein'}, {tab_id:9,window_id:8},'shein'),false);
});

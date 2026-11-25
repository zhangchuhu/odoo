'use strict';
async function verifyPeer(api,sender,proof,expectedId){
 if(!sender||typeof expectedId!=='string'||sender.id!==expectedId||!Number.isSafeInteger(proof?.tab_id)||proof.tab_id<=0||!Number.isSafeInteger(proof.window_id)||proof.window_id<=0)return false;
 try{const tab=await api.tabs.get(proof.tab_id);return tab.id===proof.tab_id&&tab.windowId===proof.window_id;}
 catch(_){return false;}
}
globalThis.PeerBinding={verifyPeer};if(typeof module!=='undefined')module.exports={verifyPeer};

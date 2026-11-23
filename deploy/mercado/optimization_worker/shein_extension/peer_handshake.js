'use strict';
async function handshake(api,host,request,session,windowId,tabId){
 const tab=await api.tabs.get(tabId);
 let url; try { url=new URL(tab.url); } catch (_) { throw Error('peer page unavailable'); }
 if(tab.id!==tabId||tab.windowId!==windowId||url.protocol!=='https:'||url.hostname!=='us.shein.com'||url.port||url.username||url.password)throw Error('peer page mismatch');
 const challenge=await api.runtime.sendNativeMessage(host,{operation:'peer_challenge',request_id:request,session_id:session,window_id:windowId,tab_id:tabId});
 if(challenge?.ok!==true||typeof challenge.token!=='string'||!Number.isSafeInteger(challenge.epoch)||!/^[a-p]{32}$/.test(challenge.anchor_id||''))throw Error('peer challenge unavailable');
 const result=await api.runtime.sendMessage(challenge.anchor_id,{type:'shein_peer_proof',proof:{request_id:request,epoch:challenge.epoch,session_id:session,window_id:windowId,tab_id:tabId,token:challenge.token}});
 if(result?.ok!==true)throw Error('peer proof refused');return true;
}
globalThis.SheinPeer={handshake};if(typeof module!=='undefined')module.exports={handshake};

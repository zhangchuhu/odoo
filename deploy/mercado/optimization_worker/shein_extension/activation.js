'use strict';
// Only a fresh broker challenge and a fresh extraction may activate the bound tab.
globalThis.activateCollectorTarget=async ({requestId,target,windowId,session,extract,expectedIdentity})=>{
 const host='local.odoo.collector_binding';
 const base={request_id:requestId,tab_id:target,window_id:windowId,session_id:session};
 const response=await chrome.runtime.sendNativeMessage(host,{operation:'activation_challenge',...base});
 const challenge=response?.challenge;
 if (!challenge || challenge.request_id!==requestId) throw new Error('Activation challenge unavailable');
 const tab=await chrome.tabs.get(target);
 if(tab.id!==target || tab.windowId!==windowId) throw new Error('Bound window changed');
 if (expectedIdentity && !isCandidateUrl(tab.url)) throw Error('candidate_navigation_redirected');
 if (isCandidateUrl(tab.url)) {
   const live = await chrome.runtime.sendMessage({type:'ensure_collector_guard',request_id:requestId,tab_id:target,window_id:windowId});
   if (live?.ok !== true) {const error=Error(live?.error || 'guard_ack_timeout');error.code=error.message;throw error;}

   const issued = await chrome.runtime.sendNativeMessage(host, {operation:'activation_challenge', ...base, detail_url:tab.url});
   const proof = issued?.challenge;
   if (!proof || proof.request_id !== requestId || proof.detail_url !== tab.url || !proof.detail_identity || (expectedIdentity && proof.detail_identity !== expectedIdentity)) {const error=Error(issued?.error || 'detail_activation_unverified');error.code=error.message;throw error;}
   const verify = async () => {
     const current = await chrome.tabs.get(target);
     if (current.windowId !== windowId || current.id !== target || current.url !== proof.detail_url) throw Error('detail_activation_tab_changed');
     const page = await chrome.tabs.sendMessage(target, {type:'SHEIN_COLLECTOR_EXTRACT_DETAIL', identity:proof.detail_identity});
     if (!page?.identity_matches || page.identity !== proof.detail_identity || page.page_url !== current.url || page.blocked) throw Error('detail_activation_identity_changed');
   };
   await verify();
   const confirmed = await chrome.runtime.sendNativeMessage(host, {operation:'activation_confirm', ...base,
     nonce:proof.nonce, url:tab.url, query:proof.detail_identity, observed_at:new Date().toISOString()});
   if (confirmed?.ok !== true) throw Error('detail_activation_rejected');
   await verify();
   await chrome.tabs.update(target,{active:true});
   await chrome.windows.update(windowId,{focused:true});
   await verify();
   return true;
 }
 const page=await chrome.tabs.sendMessage(target,{type:extract,query:challenge.query,prior:[],limit:30});
 const receipt=CollectorBinding.makeReceipt(challenge,session,tab,page,target,new Date().toISOString());
 const confirmed=await chrome.runtime.sendNativeMessage(host,{operation:'activation_confirm',...base,nonce:receipt.nonce,url:receipt.url,query:page.search_query,observed_at:receipt.observed_at});
 if(confirmed?.ok!==true) throw new Error('Activation rejected');
 const current=await chrome.tabs.get(target);
 if(current.id!==target || current.windowId!==windowId || current.url!==receipt.url) throw new Error('Bound page changed');
 const before=await chrome.tabs.sendMessage(target,{type:extract,query:challenge.query,prior:[],limit:30});
 CollectorBinding.makeReceipt(challenge,session,current,before,target,new Date().toISOString());
 await chrome.tabs.update(target,{active:true});
 await chrome.windows.update(windowId,{focused:true});
 const afterTab=await chrome.tabs.get(target);
 if(afterTab.id!==target || afterTab.windowId!==windowId || afterTab.url!==receipt.url) throw new Error('Bound page changed after activation');
 const after=await chrome.tabs.sendMessage(target,{type:extract,query:challenge.query,prior:[],limit:30});
 CollectorBinding.makeReceipt(challenge,session,afterTab,after,target,new Date().toISOString());
 return true;
};

function isCandidateUrl(raw) {
  try {
    const url = new URL(raw);
    return url.protocol === 'https:' && !url.port && !url.username && !url.password && url.hostname === 'us.shein.com' && /-p-\d+(?:\.html)?\/?$/i.test(url.pathname);
  } catch (_) { return false; }
}

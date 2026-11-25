'use strict';
importScripts('native_guard.js', 'collector_popup.js');
chrome.runtime.onMessage.addListener(NativeGuard.onMessage);
// Observe native UI-created tabs. This cannot distinguish the user from CUA;
// the broker must reject ambiguous candidates and fence the original lease.
const HOST='local.odoo.collector_binding';
const openCollectorPopup = CollectorPopup.opener(chrome, HOST, sessionId);
let sessionPromise;
function sessionId(){
 if (!sessionPromise) sessionPromise=(async()=>{
  const data=await chrome.storage.session.get('collector_session_id');
  if (typeof data.collector_session_id==='string' && /^[a-f0-9]{32}$/.test(data.collector_session_id)) return data.collector_session_id;
  const value=[...crypto.getRandomValues(new Uint8Array(16))].map(x=>x.toString(16).padStart(2,'0')).join('');
  await chrome.storage.session.set({collector_session_id:value});return value;
 })().catch(error=>{sessionPromise=null;throw error;});
 return sessionPromise;
}
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
 if (message?.type === 'open_collector_popup' && sender.id === chrome.runtime.id
     && sender.url === chrome.runtime.getURL('collector.html')) {
  Promise.resolve().then(() => openCollectorPopup(message.tab_id))
    .then(() => respond({ok: true}), () => respond({ok: false}));
  return true;
 }
 if (message?.type!=='collector_session_id' || sender.id!==chrome.runtime.id) return false;
 sessionId().then(id=>respond({session_id:id}),()=>respond({error:'session_unavailable'}));return true;
});
chrome.tabs.onCreated.addListener(async tab=>{
 if (!Number.isSafeInteger(tab.id) || tab.id<=0 || !Number.isSafeInteger(tab.windowId) || tab.windowId<=0) return;
 try {
  const session=await sessionId(), observed_at=new Date().toISOString();
  const response=await chrome.runtime.sendNativeMessage(HOST,{operation:'watch',tab_id:tab.id,window_id:tab.windowId,session_id:session,observed_at});
  const c=response?.challenge;if (!c) return;
  await chrome.runtime.sendNativeMessage(HOST,{operation:'observe_tab',request_id:c.request_id,nonce:c.nonce,lease_epoch:c.lease_epoch,plan_digest:c.plan_digest,tab_id:tab.id,window_id:tab.windowId,session_id:session,observed_at});
 } catch (_) { /* No challenge or broker: never create a binding. */ }
});

'use strict';
importScripts('native_guard.js', 'collector_popup.js');
chrome.runtime.onMessage.addListener(NativeGuard.onMessage);
importScripts('collector-trace.js');
importScripts('dedicated_window.js');
importScripts('task_tab_create.js');
importScripts('anchor_guard.js');
importScripts('peer_binding.js');
importScripts('anchor_reconnect.js');
// Observe native UI-created tabs. This cannot distinguish the user from CUA;
// the broker must reject ambiguous candidates and fence the original lease.
const HOST='local.odoo.collector_binding';
const openCollectorPopup = CollectorPopup.opener(chrome, HOST, sessionId);
let sessionPromise;
let anchorGuard;
let anchorReconnect;
async function reconnectAnchor(identity){
 if(anchorGuard?.alive())return;
 const challenge=await chrome.runtime.sendNativeMessage(HOST,{operation:'anchor_challenge',request_id:identity.request_id,session_id:identity.session_id});
 if(challenge?.ok!==true)throw Error('broker unavailable');
 const confirmed=await chrome.runtime.sendNativeMessage(HOST,{operation:'anchor_confirm',request_id:identity.request_id,epoch:challenge.epoch,session_id:identity.session_id,window_id:identity.window_id,marker:identity.marker,token:challenge.token});
 if(confirmed?.ok!==true)throw Error('anchor unavailable');
 const guard=AnchorGuard.openAnchorGuard(chrome,HOST,identity);await guard.ready;anchorGuard=guard;
}
async function inspectAnchor(identity){
 try{const data=await chrome.storage.session.get('collector_session_id');
 const contexts=await chrome.runtime.getContexts({contextTypes:['TAB']});
 const markerUrl=chrome.runtime.getURL('dedicated-window.html')+'?marker='+identity.marker;
 if(!contexts.some(c=>c.contextType==='TAB'&&c.windowId===identity.window_id&&c.documentUrl===markerUrl))return null;
 return {...identity,session_id:data.collector_session_id};}catch(_){return null}
}
async function nativeStatus({window_id,tab_id,session_id}) {
 return chrome.runtime.sendNativeMessage(HOST,{operation:'binding_status',window_id,tab_id,session_id});
}
const pendingRegistration = new Map();
const pendingRecovery = new Map();
const pendingTaskTab = new Map();
function onceForRequest(pending, key, action) {
 if (!pending.has(key)) pending.set(key,action().finally(()=>pending.delete(key)));
 return pending.get(key);
}
async function registerOnce(request) {
   if (typeof request!=='string' || !request) return {error:'request_unavailable'};
   let session;try{session=await sessionId()}catch(_){return {error:'session_unavailable'}}
   let window;try{window=await DedicatedWindow.ensureWindow(chrome,()=>[...crypto.getRandomValues(new Uint8Array(16))].map(x=>x.toString(16).padStart(2,'0')).join(''))}catch(_){return {error:'window_prepare_failed'}}
   const scoped={request_id:request,window_id:window.windowId,tab_id:window.tabId,session_id:session};
   if(anchorReconnect?.identity?.request_id===request && !anchorReconnect.stopped && anchorGuard?.alive()){
    const live=await nativeStatus(scoped);
    if(live.request_id===request && live.anchor_active===true && anchorReconnect.identity.window_id===window.windowId && anchorReconnect.identity.session_id===session)return {...scoped,created:false};
   }
   await CollectorTrace.append('native_anchor_challenge','sent');
   let challenge;try{challenge=await chrome.runtime.sendNativeMessage(HOST,{operation:'anchor_challenge',request_id:request,session_id:session})}catch(_){await CollectorTrace.append('native_anchor_challenge','transport_error');return {error:'anchor_challenge_failed'}}
   if (challenge?.ok!==true || typeof challenge.token!=='string' || !Number.isSafeInteger(challenge.epoch)){await CollectorTrace.append('native_anchor_challenge','rejected');return {error:'anchor_challenge_failed'}}
   await CollectorTrace.append('native_anchor_challenge','ok');

   await CollectorTrace.append('native_anchor_confirm','sent');
   let confirmation;try{confirmation=await chrome.runtime.sendNativeMessage(HOST,{operation:'anchor_confirm',request_id:request,epoch:challenge.epoch,session_id:session,window_id:window.windowId,marker:window.marker,token:challenge.token})}catch(_){await CollectorTrace.append('native_anchor_confirm','transport_error');return {error:'anchor_confirm_failed'}}
   if (confirmation?.ok!==true){await CollectorTrace.append('native_anchor_confirm','rejected');return {error:'anchor_confirm_failed'}}
   await CollectorTrace.append('native_anchor_confirm','ok');
   if(confirmation.rebind_required === true) {
     await CollectorTrace.append('native_anchor_confirm','rebind_required');
     return {error:'anchor_broker_rotating'};
   }
   if(anchorReconnect)anchorReconnect.stop();
   if(anchorGuard) anchorGuard.close();
   await CollectorTrace.append('anchor_guard','opening');
   try{anchorGuard=AnchorGuard.openAnchorGuard(chrome,HOST,{request_id:request,session_id:session,window_id:window.windowId,marker:window.marker})}catch(_){await CollectorTrace.append('anchor_guard','open_failed');return {error:'anchor_guard_open_failed'}}
   try{await anchorGuard.ready}catch(_){await CollectorTrace.append('anchor_guard','ready_failed');return {error:'anchor_guard_ready_failed'}}
   await CollectorTrace.append('anchor_guard','ok');
   const identity={request_id:request,session_id:session,window_id:window.windowId,marker:window.marker};
   try{anchorReconnect=new AnchorReconnect(identity,reconnectAnchor,()=>inspectAnchor(identity))}catch(error){anchorGuard.close();await CollectorTrace.appendError('reconnect_construct',error);return {error:'anchor_reconnect_failed'}}
   try{anchorReconnect.start()}catch(error){anchorGuard.close();await CollectorTrace.appendError('reconnect_start',error);return {error:'anchor_reconnect_failed'}}
   await CollectorTrace.append('reconnect_start','ok');
   return {...scoped,created:window.created};

}
function registerWindow(request) {
 return onceForRequest(pendingRegistration,request,async()=>{
  let last;
  for(let attempt=0;attempt<4;attempt++){
   last=await registerOnce(request);
   if (!['anchor_broker_rotating','anchor_challenge_failed'].includes(last.error)) return last;
   if (attempt<3) await new Promise(resolve=>setTimeout(resolve,350));
  }
  return last;
 });
}
function getOrCreateTaskTab(request, anchor) {
 const key=JSON.stringify([request,anchor.window_id,anchor.session_id]);
 return onceForRequest(pendingTaskTab,key,()=>TaskTabCreate.createTaskTab(
   chrome,HOST,request,anchor.session_id,anchor.window_id));
}
function recoverTaskAnchor(message, peerId) {
 return onceForRequest(pendingRecovery,message.request_id+':'+(peerId||''),async()=>{
  const {request_id,session_id,window_id,tab_id}=message;
  const before=await nativeStatus({session_id,window_id,tab_id});
  if (before.request_id!==request_id || before.anchor_required!==true ||
      (peerId ? before.platform!=='shein-us' || before.peer_extension_id!==peerId :
                before.platform!=='mercado-libre-mx')) return {error:'anchor_request_changed'};
  const anchor=await registerWindow(request_id);
  if (anchor.error) return anchor;
  let bound=await nativeStatus({...anchor,session_id});
  if (peerId && bound.request_id===request_id && !bound.ok) bound=await nativeStatus(anchor);
  if (bound.request_id!==request_id || bound.anchor_active!==true || bound.ok!==true) return {error:'anchor_rebind_pending'};
  if (Number.isSafeInteger(bound.bound_tab_id) && bound.bound_tab_id>0) {
   const tab=await TaskTabCreate.verifyBoundTab(chrome,bound.bound_tab_id,anchor.window_id,before.platform);
   return {ok:true,request_id,...tab};
  }
  if(bound.bound_tab_id!=null) return {error:'anchor_binding_invalid'};
  const task=await getOrCreateTaskTab(request_id,anchor);
  return {ok:true,request_id,...task};
 });
}
chrome.runtime.onMessageExternal.addListener((message,sender,respond)=>{
 if(message?.type==='recover_task_anchor' && /^[a-p]{32}$/.test(sender?.id||'') &&
    sender.url?.startsWith('chrome-extension://'+sender.id+'/collector.html?')) {
  recoverTaskAnchor(message,sender.id).then(respond,()=>respond({error:'anchor_recovery_unavailable'}));return true;
 }

 if(message?.type!=='shein_peer_proof' || !/^[a-p]{32}$/.test(sender?.id||''))return false;
 (async()=>{
  const proof=message.proof;
  if(!await PeerBinding.verifyPeer(chrome,sender,proof,sender.id))throw Error('invalid peer tab');
  const result=await chrome.runtime.sendNativeMessage(HOST,{operation:'peer_confirm',request_id:proof.request_id,epoch:proof.epoch,session_id:proof.session_id,window_id:proof.window_id,tab_id:proof.tab_id,token:proof.token,peer_id:sender.id});
  return {ok:result?.ok===true};
 })().then(respond,()=>respond({ok:false}));return true;
});
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
 const ownControl=sender.id===chrome.runtime.id && (sender.url?.startsWith(chrome.runtime.getURL('collector.html')) || sender.url?.startsWith(chrome.runtime.getURL('dedicated-window.html')));
 if (message?.type==='dedicated_current_request' && ownControl && sender.tab?.id>0) {
  (async()=>{const session=await sessionId(),tab=await chrome.tabs.get(sender.tab.id);await CollectorTrace.append('native_binding_status','sent');const result=await chrome.runtime.sendNativeMessage(HOST,{operation:'binding_status',tab_id:tab.id,window_id:tab.windowId,session_id:session});await CollectorTrace.append('native_binding_status',typeof result?.request_id==='string'?'request_received':'request_unavailable');return result;})().then(respond,async()=>{await CollectorTrace.append('native_binding_status','transport_error');respond({error:'unavailable'})});return true;
 }
 if (message?.type==='dedicated_task_tab_create' && ownControl) {
  (async()=>{
   const anchor=await registerWindow(message.request_id);
   if(anchor.error)return anchor;
   const task=await getOrCreateTaskTab(message.request_id,anchor);
   const focused=await chrome.tabs.update(task.tab_id,{active:true});
   if(focused.id!==task.tab_id || focused.windowId!==task.window_id)throw Error('task_tab_window_changed');
   await chrome.windows.update(task.window_id,{focused:true});
   return task;
  })().then(respond,error=>respond({error:/^task_tab_[a-z_]+$/.test(error?.message)?error.message:'task_tab_unavailable'}));return true;
 }
 if (message?.type==='dedicated_window_register' && ownControl) {
  registerWindow(message.request_id).then(respond,()=>respond({error:'dedicated_window_unavailable'}));return true;
 }
 if (message?.type==='recover_task_anchor' && ownControl) {
  recoverTaskAnchor(message).then(respond,()=>respond({error:'anchor_recovery_unavailable'}));return true;
 }
 if (message?.type==='dedicated_window_prepare' && sender.id===chrome.runtime.id && sender.url?.startsWith(chrome.runtime.getURL('collector.html'))) {
  DedicatedWindow.ensureWindow(chrome,()=>[...crypto.getRandomValues(new Uint8Array(16))].map(x=>x.toString(16).padStart(2,'0')).join(''))
    .then(result=>respond(result),()=>respond({error:'dedicated_window_unavailable'})); return true;
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

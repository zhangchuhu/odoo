'use strict';
const NativeGuard = (() => {
 function create(tab, window, session, requestId) {
  let port=null, active=false, timer=null, opening=false;
  function reset(current) { if (port!==current) return; port=null; active=false; if(timer!==null) clearInterval(timer); timer=null; }
  async function open() {
   if(port || opening) return; opening=true;
   try {
    const observed=await chrome.tabs.get(tab);
    if (observed?.id!==tab || observed?.windowId!==window) throw new Error('Target tab changed');
    const current=chrome.runtime.connectNative('local.odoo.collector_binding'); port=current;
    current.onDisconnect.addListener(()=>reset(current));
    current.onMessage.addListener(reply=>{if(port!==current) return; if(reply?.ok!==true){active=false;current.disconnect();return;} active=true;});
    current.postMessage({operation:'guard_open',tab_id:tab,window_id:window,session_id:session,...(requestId ? {request_id:requestId} : {})});
    timer=setInterval(async()=>{
     try {
      const observed=await chrome.tabs.get(tab);
      if (port!==current) return;
      if (observed?.id!==tab || observed?.windowId!==window) throw new Error('Target tab changed');
      current.postMessage({operation:'guard_ping'});
     } catch (_) {
      if (port===current) {active=false;try {current.disconnect();} catch (_) {reset(current);}}
     }
    },5000);
   } catch (_) { if(port) reset(port); } finally { opening=false; }
  }
  function close() {
   if (port) { const current=port; reset(current); try { current.disconnect(); } catch (_) {} }
  }
  return {open,close,available:()=>active};
 }
 // Chrome keeps the service worker alive while its native port is connected.
 let owned = null;
 let pending = null;
 function changed(reason) { const error=Error('guard_binding_changed'); error.diagnostic=reason; return error; }
 async function ensure(request, tab, window, session) {
   const key = JSON.stringify([request,tab,window,session]);
   if (pending) {
     if (pending.key !== key) throw changed('guard_pending_conflict');
     return pending.promise;
   }
   async function connect() {
     const observed = await chrome.tabs.get(tab);
     if (observed.id !== tab || observed.windowId !== window) throw changed('guard_tab_changed');
     const identity = {operation:'binding_status',tab_id:tab,window_id:window,session_id:session};
     const host = 'local.odoo.collector_binding';
     const status = await chrome.runtime.sendNativeMessage(host, identity);
     if (status?.ok !== true) throw changed('guard_status_unavailable');
     if (status.request_id !== request) throw changed('guard_request_changed');
     if (status.matches !== true || status.bound_tab_id !== tab) throw changed('guard_tab_unmatched');
     if (status.guard_required !== true) throw changed('guard_not_required');
     if (status.guard_active === true) return {ok:true};
     if (owned) owned.close();
     owned = create(tab,window,session,request);
     await owned.open();
     // The first port message must be acknowledged before opening a detail or restoring it.
     for (let attempt=0; attempt<40; attempt++) {
       if (owned.available()) return {ok:true};
       await new Promise(resolve=>setTimeout(resolve,100));
     }
     owned.close();
     throw Error('guard_ack_timeout');
   }
   pending = {key, promise:connect().finally(()=>{pending=null;})};
   return pending.promise;
 }
 function onMessage(message, sender, respond) {
   if (message?.type !== 'ensure_collector_guard' || sender?.id !== chrome.runtime.id ||
       typeof message.request_id !== 'string' || !Number.isSafeInteger(message.tab_id) || message.tab_id <= 0 ||
       !Number.isSafeInteger(message.window_id) || message.window_id <= 0) return false;
   let page;
   try { page = new URL(sender.url); } catch (_) { return false; }
   if (page.href.split('?')[0] !== chrome.runtime.getURL('collector.html') ||
       page.searchParams.get('tab') !== String(message.tab_id)) return false;
   sessionId().then(session=>ensure(message.request_id,message.tab_id,message.window_id,session))
     .then(respond,error=>respond({ok:false,error:error.message==='guard_binding_changed'?'guard_binding_changed':'guard_ack_timeout',...(error.diagnostic ? {diagnostic:error.diagnostic} : {})}));
   return true;
 }
 return {create,onMessage};
})();

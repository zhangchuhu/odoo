'use strict';
// Only a fresh broker challenge and a fresh extraction may activate the bound tab.
globalThis.activateCollectorTarget=async ({requestId,target,windowId,session,extract,expectedIdentity})=>{
 let stage='activation_challenge';
 const trace=async code=>{if(typeof CollectorTrace!=='undefined')await CollectorTrace.append(stage,code)};
 try{
 await trace('started');
 const host='local.odoo.collector_binding';
 const identityExtract=extract==='MERCADO_COLLECTOR_EXTRACT_SEARCH'?'MERCADO_COLLECTOR_SEARCH_IDENTITY':extract;
 const base={request_id:requestId,tab_id:target,window_id:windowId,session_id:session};
 const response=await chrome.runtime.sendNativeMessage(host,{operation:'activation_challenge',...base});
 const challenge=response?.challenge;
 if (!challenge || challenge.request_id!==requestId) throw new Error('Activation challenge unavailable');
 await trace('ok');stage='activation_tab_get';await trace('started');
 const tab=await chrome.tabs.get(target);
 await trace('ok');stage='activation_tab_identity';await trace('started');
 if(tab.id!==target || tab.windowId!==windowId) throw new Error('Bound window changed');
 if (expectedIdentity && !isCandidateUrl(tab.url)) throw Error('candidate_navigation_redirected');
 if (isCandidateUrl(tab.url)) {
   stage='activation_detail_guard';await trace('started');
   const live = await chrome.runtime.sendMessage({type:'ensure_collector_guard',request_id:requestId,tab_id:target,window_id:windowId});
   if (live?.ok !== true) {const error=Error(live?.error || 'guard_ack_timeout');error.code=error.message; if(live?.diagnostic) error.detailCode=live.diagnostic; throw error;}
   stage='activation_detail_challenge';await trace('started');
   const issued = await chrome.runtime.sendNativeMessage(host, {operation:'activation_challenge', ...base, detail_url:tab.url});
   const proof = issued?.challenge;
   if (!proof || proof.request_id !== requestId || proof.detail_url !== tab.url || !proof.detail_identity || (expectedIdentity && proof.detail_identity !== expectedIdentity)) {const error=Error(issued?.error || 'detail_activation_unverified');error.code=error.message;throw error;}
   const verify = async () => {
     const current = await chrome.tabs.get(target);
     if (current.windowId !== windowId || current.id !== target || current.url !== proof.detail_url) throw Error('detail_activation_tab_changed');
     const page = await chrome.tabs.sendMessage(target, {type:'MERCADO_COLLECTOR_EXTRACT_DETAIL', identity:proof.detail_identity});
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
 if(identityExtract==='MERCADO_COLLECTOR_SEARCH_IDENTITY'){
  stage='activation_scroll_top';await trace('started');
  let url;try{url=new URL(tab.url)}catch(_){const e=new Error('Invalid platform page');e.code='activation_target_url_invalid';throw e}
  if(challenge.platform!=='mercado-libre-mx' || url.protocol!=='https:' || url.hostname!=='listado.mercadolibre.com.mx' || url.port || url.username || url.password){const e=new Error('Invalid platform page');e.code='activation_target_url_invalid';throw e}
  const scrolled=await chrome.tabs.sendMessage(target,{type:'MERCADO_COLLECTOR_SCROLL_TOP'});
  if(scrolled?.ok!==true){const e=new Error('Unable to scroll search page');e.code='activation_scroll_failed';throw e}
  await trace('ok');
 }
 await trace('ok');stage='activation_page_extract';await trace('started');
 const page=await chrome.tabs.sendMessage(target,{type:identityExtract,query:challenge.query,prior:[],limit:30});
 await trace('ok');stage='activation_receipt';await trace('started');
 const receipt=CollectorBinding.makeReceipt(challenge,session,tab,page,target,new Date().toISOString());
 await trace('ok');stage='activation_confirm';await trace('started');
 const confirmed=await chrome.runtime.sendNativeMessage(host,{operation:'activation_confirm',...base,nonce:receipt.nonce,url:receipt.url,query:receipt.query,observed_at:receipt.observed_at});
 if(confirmed?.ok!==true) throw new Error('Activation rejected');
 await trace('ok');stage='activation_before_focus';await trace('started');
 const current=await chrome.tabs.get(target);
 if(current.id!==target || current.windowId!==windowId || current.url!==receipt.url) throw new Error('Bound page changed');
 const before=await chrome.tabs.sendMessage(target,{type:identityExtract,query:challenge.query,prior:[],limit:30});
 CollectorBinding.makeReceipt(challenge,session,current,before,target,new Date().toISOString());
 await trace('ok');stage='activation_focus';await trace('started');
 await chrome.tabs.update(target,{active:true});
 await chrome.windows.update(windowId,{focused:true});
 await trace('ok');stage='activation_after_focus';await trace('started');
 const afterTab=await chrome.tabs.get(target);
 if(afterTab.id!==target || afterTab.windowId!==windowId || afterTab.url!==receipt.url) throw new Error('Bound page changed after activation');
 const after=await chrome.tabs.sendMessage(target,{type:identityExtract,query:challenge.query,prior:[],limit:30});
 CollectorBinding.makeReceipt(challenge,session,afterTab,after,target,new Date().toISOString());
 await trace('ok');
 return true;
 }catch(error){if(typeof CollectorTrace!=='undefined')await CollectorTrace.appendError(stage,error);throw error;}
};

function isCandidateUrl(raw) {
  try {
    const url = new URL(raw);
    return url.protocol === 'https:' && !url.port && !url.username && !url.password && ['www.mercadolibre.com.mx','articulo.mercadolibre.com.mx'].includes(url.hostname) && /(?:^|[^a-z0-9])MLMU?[-_ ]*\d+/i.test(url.pathname);
  } catch (_) { return false; }
}

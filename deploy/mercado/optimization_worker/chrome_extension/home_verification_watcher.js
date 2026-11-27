'use strict';

const HOME_VERIFY_PREFIX='home-verification:';

async function startWatch(api,host,binding) {
  const {request_id,tab_id,window_id,session_id}=binding || {};
  if(typeof request_id!=='string'||!request_id||!Number.isSafeInteger(tab_id)||tab_id<1||!Number.isSafeInteger(window_id)||window_id<1||!/^[a-f0-9]{32}$/.test(session_id||''))return false;
  const info=await api.runtime.sendNativeMessage(host,{operation:'home_verification_info',request_id,tab_id,window_id,session_id});
  if(info?.ok!==true||!['search','detail'].includes(info.stage)||!info.expected||typeof info.expected!=='object')return false;
  const key=HOME_VERIFY_PREFIX+request_id;
  await api.storage.session.set({[key]:{request_id,tab_id,window_id,session_id,stage:info.stage,expected:info.expected,expires:Date.now()+86400000}});
  await api.alarms.create(key,{periodInMinutes:0.5});
  return true;
}

async function checkWatch(api,host,request) {
  const key=HOME_VERIFY_PREFIX+request;
  try {
    const watch=(await api.storage.session.get(key))[key];
    if(!watch||watch.request_id!==request||Date.now()>=watch.expires){await api.alarms.clear(key);await api.storage.session.remove(key);return false;}
    let tab;
    try { tab=await api.tabs.get(watch.tab_id); }
    catch (_) { await api.alarms.clear(key);await api.storage.session.remove(key);return false; }
    if(tab?.id!==watch.tab_id||tab.windowId!==watch.window_id)return false;
    const status=await api.tabs.sendMessage(watch.tab_id,{type:'MERCADO_COLLECTOR_STATUS'});
    if(!status||status.error)return false;
    let page;
    if(watch.stage==='search') {
      const observed=await api.tabs.sendMessage(watch.tab_id,{type:'MERCADO_COLLECTOR_EXTRACT_SEARCH',query:watch.expected.query,prior:[],limit:30,strict_scope:true,with_sales:true});
      if(observed?.error||observed?.verified_scope!==true||!Array.isArray(observed.observations)||observed.search_query!==watch.expected.query||observed.page_url!==status.page_url)return false;
      page={url:observed.page_url,query:observed.search_query,blocked:false};
    } else if(watch.stage==='detail') {
      const observed=await api.tabs.sendMessage(watch.tab_id,{type:'MERCADO_COLLECTOR_EXTRACT_DETAIL',identity:'mercado-libre-mx:'+watch.expected.product_id});
      if(observed?.blocked||observed?.identity_matches!==true||observed?.product_id!==watch.expected.product_id||!observed?.title||observed.page_url!==status.page_url)return false;
      page={url:observed.page_url,product_id:observed.product_id,blocked:false};
    } else return false;
    const {request_id,tab_id,window_id,session_id}=watch;
    const result=await api.runtime.sendNativeMessage(host,{operation:'home_verification_observed',request_id,tab_id,window_id,session_id,page});
    if(result?.ok!==true)return false;
    await api.alarms.clear(key);await api.storage.session.remove(key);return true;
  } catch (_) {return false;}
}

globalThis.HomeVerificationWatcher={startWatch,checkWatch,HOME_VERIFY_PREFIX};
if(typeof module!=='undefined')module.exports={startWatch,checkWatch,HOME_VERIFY_PREFIX};

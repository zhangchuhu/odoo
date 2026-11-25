'use strict';

const platformHomes = {
  'mercado-libre-mx':'https://www.mercadolibre.com.mx/',
  'shein-us':'https://us.shein.com/',
};

function allowedPage(url, platform) {
  try {
    const page = new URL(url);
    const hosts = platform === 'mercado-libre-mx'
      ? ['www.mercadolibre.com.mx','listado.mercadolibre.com.mx','articulo.mercadolibre.com.mx']
      : platform === 'shein-us' ? ['us.shein.com'] : [];
    return page.protocol === 'https:' && !page.port && !page.username && !page.password && hosts.includes(page.hostname);
  } catch (_) { return false; }
}

async function verifyBoundTab(api, tabId, windowId, platform) {
  if (!Number.isSafeInteger(tabId) || tabId <= 0) throw Error('task_tab_identity_invalid');
  let tab;
  try { tab = await api.tabs.get(tabId); } catch (_) { throw Error('task_tab_missing'); }
  if (tab?.id !== tabId || tab.windowId !== windowId) throw Error('task_tab_window_changed');
  if (!allowedPage(tab.pendingUrl || tab.url,platform)) throw Error('task_tab_platform_mismatch');
  return {tab_id:tab.id,window_id:tab.windowId,reused:true};
}

async function waitForConfirmedTab(api,host,request,session,windowId) {
  const [active] = await api.tabs.query({active:true,windowId});
  if (!Number.isSafeInteger(active?.id) || active.windowId!==windowId) throw Error('task_tab_pending');
  for (let i=0;i<10;i++) {
    const status=await api.runtime.sendNativeMessage(host,{
      operation:'binding_status',tab_id:active.id,window_id:windowId,session_id:session,
    });
    if (status?.request_id!==request || status.anchor_required!==true || status.anchor_active!==true) {
      throw Error('task_tab_binding_mismatch');
    }
    if (status.ok && Number.isSafeInteger(status.bound_tab_id) && status.bound_tab_id>0) {
      return verifyBoundTab(api,status.bound_tab_id,windowId,status.platform);
    }
    if (i<9) await new Promise(resolve=>setTimeout(resolve,250));
  }
  throw Error('task_tab_pending');
}

async function createTaskTab(api,host,request,session,windowId) {
  if (typeof request!=='string' || !request || !/^[a-f0-9]{32}$/.test(session) || !Number.isSafeInteger(windowId) || windowId<=0) {
    throw Error('task_tab_identity_invalid');
  }
  const challenge = await api.runtime.sendNativeMessage(host,{
    operation:'task_tab_challenge',request_id:request,session_id:session,window_id:windowId,
  });
  if (challenge?.ok===false && challenge.error==='task_tab_pending') {
    return waitForConfirmedTab(api,host,request,session,windowId);
  }
  const home = platformHomes[challenge?.platform];
  if (challenge?.ok!==true || !Number.isSafeInteger(challenge.epoch) || !home) {
    throw Error(challenge?.error || 'task_tab_challenge_rejected');
  }
  if ('bound_tab_id' in challenge) {
    if (challenge.request_id!==request || challenge.session_id!==session || challenge.window_id!==windowId) {
      throw Error('task_tab_binding_mismatch');
    }
    return verifyBoundTab(api,challenge.bound_tab_id,windowId,challenge.platform);
  }
  if (typeof challenge.token!=='string' || !challenge.token) throw Error('task_tab_challenge_invalid');
  const tab = await api.tabs.create({windowId,url:home,active:true});
  if (!Number.isSafeInteger(tab?.id) || tab.id<=0 || tab.windowId!==windowId) throw Error('task_tab_identity_invalid');
  const result = await api.runtime.sendNativeMessage(host,{
    operation:'task_tab_confirm',request_id:request,session_id:session,window_id:windowId,
    tab_id:tab.id,epoch:challenge.epoch,token:challenge.token,
  });
  if (result?.ok!==true) throw Error('task_tab_confirm_unacknowledged');
  return {tab_id:tab.id,window_id:windowId,reused:false};
}
globalThis.TaskTabCreate={createTaskTab,verifyBoundTab};
if(typeof module!=='undefined') module.exports={createTaskTab,verifyBoundTab};

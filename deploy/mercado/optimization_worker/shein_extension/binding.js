'use strict';
// A native broker supplies short-lived, single-use, task-scoped challenges.
// This extension does not receive Odoo credentials. No binding is accepted
// solely from page content or the Codex model's text.
(() => {
  const HOST='local.odoo.collector_binding';
  const target=Number(new URL(location.href).searchParams.get('tab'));
  if (!Number.isSafeInteger(target) || target<=0) return;
  const popupRequest = new URL(location.href).searchParams.get('request');
  const trackPopup = globalThis.CollectorPopup?.monitor(chrome, HOST, window, target);
  let busy=false, selected=false, enabled=false, requestId=popupRequest;
  const status=document.getElementById("binding-status");
  const connect=document.getElementById("binding-connect");
  const identity=document.getElementById("binding-identity");
  function show(message){if(status) status.textContent=message;}
  let anchorAttempts=0, landingStarted=0;
  async function restoreGuard(state,tab) {
    if (state?.anchor_required === true && state.anchor_active !== true) throw Error('anchor_rebind_pending');
    if (state?.ok === true && state.request_id === requestId && state.matches === true &&
        state.bound_tab_id === target && state.guard_required === true && state.guard_active !== true) {
      const result = await bounded(chrome.runtime.sendMessage({type:'ensure_collector_guard',
        request_id:requestId,tab_id:target,window_id:tab.windowId}), 'guard_ack_timeout');
      if (result?.ok !== true) throw Error(result?.error || 'guard_ack_timeout');
    }
  }
  function bounded(promise, code, timeout = code === 'anchor_recovery_timeout' ? 22000 : 12000) {
    let timer;
    return Promise.race([promise, new Promise((_, reject)=>{ timer=setTimeout(()=>reject(new Error(code)), timeout); })]).finally(()=>clearTimeout(timer));
  }
  async function sessionId() {
    const result=await bounded(chrome.runtime.sendMessage({type:'collector_session_id'}),'session_timeout');
    if (!result || !/^[a-f0-9]{32}$/.test(result.session_id || '')) throw new Error('Session unavailable');
    return result.session_id;
  }
  async function connectAnchor(state,tab,session) {
    if (state.anchor_required !== true || (state.anchor_active === true && state.matches === true)) return state;
    const request = {type:'recover_task_anchor',request_id:requestId,tab_id:target,window_id:tab.windowId,session_id:session};
    if (!/^[a-p]{32}$/.test(state.anchor_extension_id||'') || state.peer_extension_id!==chrome.runtime.id) throw Error('anchor_request_changed');
    const bound=await bounded(chrome.runtime.sendMessage(state.anchor_extension_id,request),'anchor_recovery_timeout');
    if (bound?.ok !== true || bound.request_id !== requestId || !Number.isSafeInteger(bound.tab_id) || bound.tab_id <= 0) {
      throw Error(bound?.error || 'anchor_recovery_unavailable');
    }
    if (bound.tab_id !== target || bound.window_id !== tab.windowId) {
      const next = new URL(location.href);
      next.searchParams.set('tab',String(bound.tab_id));
      next.searchParams.set('resume_request',requestId);
      await chrome.storage.session.set({['anchor_resume:'+bound.tab_id]:requestId});
      enabled=false;
      location.replace(next.href);
      return null;
    }
    const current=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:'binding_status',
      tab_id:target,window_id:tab.windowId,session_id:session}),'status_timeout');
    if (current?.request_id !== requestId || current.anchor_active !== true) throw Error('anchor_rebind_pending');
    return current;
  }
  async function tick() {
    if (!enabled || busy) return; busy=true;
    try {
        const tab=await chrome.tabs.get(target), session=await sessionId();
        let state=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:"binding_status",tab_id:target,window_id:tab.windowId,session_id:session}),'status_timeout');
        if (!state || typeof state.request_id !== 'string') throw new Error('broker_identity_unavailable');
        if (requestId && requestId !== state.request_id) { enabled=false; show('任务已切换，请关闭采集器并重新打开'); return; }
        requestId=state.request_id;
        if (state.anchor_required === true && (state.anchor_active !== true || state.matches !== true)) {
          if (state.matches !== true) selected=false;
          state=await connectAnchor(state,tab,session);
          if (!state) return;
        }
        trackPopup?.(state, session);
        if(identity) identity.textContent=state?.ok ? `任务 ${requestId}；当前标签 ${target}；任务标签 ${state.bound_tab_id ?? "待绑定"}；${state.bound_tab_id == null ? "等待首组绑定" : state.matches ? "匹配" : "不匹配，请返回任务标签"}` : `当前标签 ${target}；后台身份待核验`;
      if (selected && state.ok !== true) throw new Error("binding_unavailable");
      if (selected) await restoreGuard(state,tab);
      if (!selected) {
        if (state.anchor_required === true && state.matches === true &&
            (!tab.url || tab.url==='about:blank' || tab.url.startsWith('chrome://newtab'))) {
          if (!landingStarted) landingStarted=Date.now();
          if (Date.now()-landingStarted<15000) {
            show('任务标签已确认，正在等待目标平台页面载入');return;
          }
          enabled=false;show('目标平台页面尚未载入（task_tab_landing_timeout），请在已绑定标签核对页面后重新连接');return;
        }
        landingStarted=0;
        const url=new URL(tab.url);
        if (url.protocol!=='https:' || !['us.shein.com'].includes(url.hostname) || url.port) throw new Error('Selected window is not on the authorized platform');
        await bounded(SheinPeer.handshake(chrome,HOST,requestId,session,tab.windowId,target),'peer_timeout');
        const reply=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:'select_window',request_id:requestId,tab_id:target,window_id:tab.windowId,session_id:session,observed_at:new Date().toISOString()}),'select_timeout');
        if (reply?.ok !== true) {
          if (state.anchor_required === true && reply?.error !== 'session_changed_reopen') {
            selected=false; throw Error('anchor_select_pending');
          }
          enabled=false;
          show(reply?.error === 'session_changed_reopen' ? '浏览器会话已变化，请重新打开采集扩展' : '后台未确认窗口，请检查任务后重试');
          return;
        }
        selected=true;anchorAttempts=0;
        const confirmed=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:"binding_status",tab_id:target,window_id:tab.windowId,session_id:session}),'status_timeout');
        if (!confirmed || confirmed.request_id !== requestId || confirmed.ok !== true) {
          if(identity) identity.textContent=`当前标签 ${target}；后台身份待核验`;
          show('窗口已登记，等待后台确认身份'); return;
        }
        if(identity) identity.textContent=`任务 ${requestId}；当前标签 ${target}；任务标签 ${confirmed.bound_tab_id ?? "待绑定"}；${confirmed.bound_tab_id == null ? "等待首组绑定" : confirmed.matches ? "匹配" : "不匹配，请返回任务标签"}`;
        state=confirmed;
        trackPopup?.(confirmed, session);
        await restoreGuard(confirmed,tab);
        show('已登记窗口，可以新建标签');
      }
      if (state.gallery_phase === true) {
        if (activate) activate.textContent = '打开当前合格商品图库';
        if (state.gallery_binding_required === true) show('候选研究结果已入库。点击“打开当前合格商品图库”以确认本次会话的商品详情。');
        return;
      }
      if (activate?.textContent === '打开当前合格商品图库') activate.textContent = '切换到已绑定任务标签';
      const response=await chrome.runtime.sendNativeMessage(HOST,{operation:'poll',request_id:requestId,tab_id:target});
      if (response?.error === 'request_changed') { enabled=false; show('任务已切换，请关闭采集器并重新打开'); return; }
      const challenge=response?.challenge;
      if (!challenge) return;
      const observedTab=await chrome.tabs.get(target);
      const page=await chrome.tabs.sendMessage(target,{type:'SHEIN_COLLECTOR_EXTRACT_SEARCH',query:challenge.query,prior:[],limit:30});
      const receipt=CollectorBinding.makeReceipt(challenge,await sessionId(),observedTab,page,target,new Date().toISOString());
      const acknowledgement=await chrome.runtime.sendNativeMessage(HOST,{operation:'submit',receipt});
      CollectorGate.grant(receipt, acknowledgement?.ok === true);
    } catch (error) {
      if (/^(?:anchor_|window_prepare_failed|session_unavailable)/.test(error?.message || '')) {
        selected=false;
        const transient=['anchor_recovery_timeout','anchor_recovery_unavailable','anchor_challenge_failed','anchor_guard_ready_failed','anchor_rebind_pending','anchor_select_pending'].includes(error.message);
        enabled=transient && ++anchorAttempts<3;
        show('专用窗口恢复未完成（'+error.message+'）'+(enabled?'，正在等待后台重新确认':'，请核对锚点后重新连接当前任务'));return;
      }
      if (!selected) { enabled=false; const code=error?.message; show(code==='status_timeout' ? '后台状态查询超时，请检查原生连接后重试' : code==='session_timeout' ? '扩展会话获取超时，请重新打开采集器' : code==='select_timeout' ? '窗口登记结果未知，请重新打开采集器核验' : '后台连接不可用，请检查后重试'); }
      else show('已登记窗口，可以新建标签；等待目标搜索词核验');
    } finally { busy=false; }
  }
  globalThis.currentGalleryBinding = async () => {
    if (!selected || !enabled || !requestId) throw Error('请先连接当前任务');
    const tab = await chrome.tabs.get(target), session = await sessionId();
    const state = await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:'binding_status',
      tab_id:target,window_id:tab.windowId,session_id:session}), 'status_timeout');
    if (state?.request_id !== requestId || state.matches !== true || state.bound_tab_id !== target || state.guard_required !== true) {
      throw Error('当前详情标签与任务的图库阶段不匹配');
    }
    await restoreGuard(state,tab);
    return {request_id:requestId,window_id:tab.windowId,tab_id:target,session_id:session};
  };
  globalThis.collectorCheckpoint = async (operation, values) => {
    if (!selected || !enabled || !requestId) throw Error('checkpoint_disconnected：请先连接当前任务');
    const tab = await chrome.tabs.get(target);
    const message = {...values, operation, request_id:requestId, tab_id:target, window_id:tab.windowId, session_id:await sessionId()};
    let reply = await bounded(chrome.runtime.sendNativeMessage(HOST, message), 'checkpoint_timeout',
      operation === 'gallery_download_saved' ? 95000 : 12000);
    if (reply?.error === 'checkpoint_guard_pending' && (operation === 'collector_progress' || operation === 'checkpoint_detail' || operation === 'next_candidate')) {
      const ready = await bounded(chrome.runtime.sendMessage({type:'ensure_collector_guard',
        request_id:requestId,tab_id:target,window_id:tab.windowId}), 'guard_ack_timeout');
      if (ready?.ok !== true) throw Error(ready?.error || 'guard_ack_timeout');
      reply = await bounded(chrome.runtime.sendNativeMessage(HOST, message), 'checkpoint_timeout');
    }
    if (reply?.ok !== true) throw Error([reply?.error || 'checkpoint_unavailable', reply?.message].filter(Boolean).join('：'));
    return reply;
  };
  globalThis.confirmSheinHomePage=async(queryIndex,query)=>{
    if(!selected || !enabled || !requestId || !Number.isInteger(queryIndex) || queryIndex<0 || queryIndex>8 || typeof query!=='string' || !query.trim())return false;
    try {
      const tab=await chrome.tabs.get(target),session=await sessionId();
      const reply=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:'card_v3_page_challenge',request_id:requestId,
        tab_id:target,window_id:tab.windowId,session_id:session,query_index:queryIndex,query,page:1}),'cards_timeout');
      const challenge=reply?.challenge;
      if(!challenge || challenge.platform!=='shein-us' || challenge.request_id!==requestId || challenge.query_index!==queryIndex || challenge.query!==query || challenge.page!==1 || challenge.tab_id!==target || challenge.window_id!==tab.windowId || challenge.session_id!==session || challenge.page_url!==tab.url)return false;
      const observed=await chrome.tabs.sendMessage(target,{type:'SHEIN_COLLECTOR_EXTRACT_SEARCH',query,prior:[],limit:30});
      const current=await chrome.tabs.get(target);
      if(observed?.error || observed?.page_url!==tab.url || observed?.search_query!==query || current.url!==tab.url)return false;
      const result=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:'card_v3_page_confirm',request_id:requestId,
        tab_id:target,window_id:tab.windowId,session_id:session,receipt:{...challenge,observed_at:new Date().toISOString()}}),'cards_timeout');
      return result?.ok===true;
    } catch (_) {return false;}
  };
  globalThis.saveSheinHomeBatch=async(queryIndex,query,batchIndex,cards,prior=[])=>{
    if(!Number.isInteger(batchIndex) || batchIndex<0 || !Array.isArray(cards) || !cards.length || !Array.isArray(prior))return false;
    try {
      if(!await globalThis.confirmSheinHomePage(queryIndex,query))return false;
      const tab=await chrome.tabs.get(target),session=await sessionId();
      const verify=async()=>{
        const active=await chrome.tabs.query({active:true,windowId:tab.windowId}),current=await chrome.tabs.get(target);
        if(active.length!==1 || active[0].id!==target || current.url!==tab.url)return false;
        const observed=await chrome.tabs.sendMessage(target,{type:'SHEIN_COLLECTOR_EXTRACT_HOME_SEARCH',query,prior,limit:10000});
        if(observed?.error || observed?.page_url!==tab.url || observed?.search_query!==query || !Array.isArray(observed.observations))return false;
        const keys=['product_id','title','is_ad','sales_text','bbox'];
        return cards.every(card=>observed.observations.some(row=>keys.every(key=>JSON.stringify(row[key])===JSON.stringify(card[key]))));
      };
      if(!await verify())return false;
      const image=await chrome.tabs.captureVisibleTab(tab.windowId,{format:'jpeg',quality:50});
      const match=/^data:image\/jpeg;base64,([A-Za-z0-9+/]+={0,2})$/.exec(image || '');
      if(!match || match[1].length>=850000 || !await verify())return false;
      const reply=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:'card_v3_batch',request_id:requestId,tab_id:target,
        window_id:tab.windowId,session_id:session,query_index:queryIndex,query,page:1,page_url:tab.url,batch_index:batchIndex,cards,screenshot:match[1]}),'cards_timeout');
      return reply?.ok===true;
    } catch (_) {return false;}
  };
  globalThis.finishSheinHomePage=async(queryIndex,query,noResults=false)=>{
    try {
      if(!await globalThis.confirmSheinHomePage(queryIndex,query))return false;
      const tab=await chrome.tabs.get(target),session=await sessionId();
      for(let i=0;i<3;i++){
        const state=await chrome.tabs.sendMessage(target,noResults?{type:'SHEIN_COLLECTOR_EXTRACT_HOME_SEARCH',query,prior:[],limit:10000}:{type:'SHEIN_COLLECTOR_SCROLL'}),current=await chrome.tabs.get(target);
        if(state?.error || (noResults ? state?.no_results!==true : state?.at_end!==true) || current.url!==tab.url)return false;
      }
      const reply=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:'card_v3_page_finish',request_id:requestId,tab_id:target,
        window_id:tab.windowId,session_id:session,query_index:queryIndex,query,page:1,page_url:tab.url,status:noResults?'end_of_results':'home_complete',next_url:null,end_checks:3,end_evidence:noResults?'empty_results':'scroll_end'}),'cards_timeout');
      return reply?.ok===true;
    } catch (_) {return false;}
  };
  globalThis.openCollectorCandidate = async (expectedIdentity, taskRecordId) => {
    if (!selected || !enabled || !requestId) throw Error('checkpoint_disconnected');
    const tab = await chrome.tabs.get(target);
    return navigateCollectorCandidate({
      requestId, target, windowId:tab.windowId, session:await sessionId(), expectedIdentity, taskRecordId,
      platform:'shein-us', checkpoint:globalThis.collectorCheckpoint, extract:'SHEIN_COLLECTOR_EXTRACT_SEARCH',
    });
  };
  globalThis.stageCollectorEvidence=async evidence=>{
    if (!selected || !enabled || !requestId || !evidence || typeof evidence!=='object') return false;
    try {
      const tab=await chrome.tabs.get(target), session=await sessionId();
      const reply=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:'stage_evidence',request_id:requestId,tab_id:target,window_id:tab.windowId,session_id:session,evidence}),'stage_timeout');
      return reply?.ok===true;
    } catch (_) { return false; }
  };
  globalThis.confirmCollectorQueryCards = async (queryIndex, cards) => {
    await globalThis.collectorCheckpoint('query_cards_complete', {query_index: queryIndex, cards});
    return true;
  };
  globalThis.recoverCollectorReceipt=async (queryIndex,query)=>{
    if (!selected || !enabled || !requestId || !Number.isInteger(queryIndex) || typeof query!=='string') return false;
    try {
      const tab=await chrome.tabs.get(target), session=await sessionId();
      const result=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:'recover_receipt',request_id:requestId,tab_id:target,window_id:tab.windowId,session_id:session,query_index:queryIndex,query}),'recover_timeout');
      const receipt=result?.receipt;
      if (!receipt || receipt.request_id!==requestId || receipt.tab_id!==target || receipt.window_id!==tab.windowId || receipt.session_id!==session || receipt.query_index!==queryIndex || receipt.query!==query) return false;
      CollectorGate.grant(receipt,true); return true;
    } catch (_) { return false; }
  };
  const activate=document.getElementById('binding-activate');
  if (activate && typeof activate.addEventListener === 'function') activate.addEventListener('click',async()=>{
    if (!selected || !enabled || !requestId || busy) { show('请先连接当前任务'); return; }
    busy=true;
    try {
      const tab=await chrome.tabs.get(target), session=await sessionId();
      const state=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:'binding_status',
        tab_id:target,window_id:tab.windowId,session_id:session}),'status_timeout');
      if (state?.request_id !== requestId || state.ok !== true) throw Error('binding_unavailable');
      if (state.gallery_phase === true) {
        if (state.matches !== true || state.bound_tab_id !== target || state.anchor_active !== true) throw Error('gallery_tab_changed');
        const product=await openBoundGallery({requestId,target,windowId:tab.windowId,session,
          platform:'shein-us',extract:'SHEIN_COLLECTOR_EXTRACT_DETAIL',restoreGuard});
        const productInput=document.getElementById('capture-product');
        if (productInput && productInput.value !== product.split(':')[1]) {
          productInput.value=product.split(':')[1];
          productInput.dispatchEvent(new Event('input'));
        }
        show('已核验图库商品 '+product+'，可继续采集当前规格原图');
        return;
      }
      await globalThis.activateCollectorTarget({requestId,target,windowId:tab.windowId,session,extract:'SHEIN_COLLECTOR_EXTRACT_SEARCH'});
      show('已切换到核验过的任务标签');
    } catch (error) { show('任务标签核验或切换失败（'+(error.code||'binding_unavailable')+'）'); }
    finally {busy=false;}
  });
  if (connect) connect.addEventListener('click',()=>{
    if (busy) return;
    anchorAttempts=0;landingStarted=0; enabled=true; show('正在连接当前任务'); tick();
  });
  const resumeRequest=new URL(location.href).searchParams.get('resume_request');
  if (resumeRequest) {
    const key='anchor_resume:'+target;
    chrome.storage.session.get(key).then(async saved=>{
      if (saved[key]!==resumeRequest) return;
      await chrome.storage.session.remove(key);
      requestId=resumeRequest;enabled=true;await tick();
    }).catch(()=>show('请连接当前任务以恢复采集'));
  }
  setInterval(tick,5000);
})();

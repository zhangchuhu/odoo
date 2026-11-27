'use strict';
// A native broker supplies short-lived, single-use, task-scoped challenges.
// This extension does not receive Odoo credentials. No binding is accepted
// solely from page content or the Codex model's text.
(() => {
  const sameObservedQuery=(left,right)=>typeof left==='string' && typeof right==='string' && left.normalize('NFC').toLowerCase()===right.normalize('NFC').toLowerCase();
  const HOST='local.odoo.collector_binding';
  const target=Number(new URL(location.href).searchParams.get('tab'));
  if (!Number.isSafeInteger(target) || target<=0) return;
  const popupRequest = new URL(location.href).searchParams.get('request');
  const trackPopup = globalThis.CollectorPopup?.monitor(chrome, HOST, window, target);
  let busy=false, selected=false, enabled=false, requestId=popupRequest, reconnecting=false, activationPending=false;
  const status=document.getElementById("binding-status");
  const connect=document.getElementById("binding-connect");
  const identity=document.getElementById("binding-identity");
  const dedicated=document.getElementById('dedicated-window-register');
  const taskTab=document.getElementById('dedicated-task-tab-create');
  function show(message){if(status) status.textContent=message;}
  const pollTraceLast={};
  async function trace(stage,code,error){
    if(typeof CollectorTrace==='undefined')return;
    if(stage==='binding_status'||stage==='binding_identity'){
      const value=error?CollectorTrace.errorCode(error):code;
      if(pollTraceLast[stage]===value)return;
      pollTraceLast[stage]=value;
    }
    if(error)await CollectorTrace.appendError(stage,error);else await CollectorTrace.append(stage,code);
    await CollectorTrace.render(document.getElementById('binding-trace'));
  }
  let anchorAttempts=0, landingStarted=0;
  const verificationWatch=document.getElementById('home-watch-verification');
  if(typeof verificationWatch?.addEventListener === 'function')verificationWatch.addEventListener('click',async()=>{
    try {
      if(!selected || !enabled || !requestId)throw Error('请先连接当前任务');
      const tab=await chrome.tabs.get(target),session=await sessionId();
      const result=await chrome.runtime.sendMessage({type:'home_verification_watch_start',request_id:requestId,tab_id:target,window_id:tab.windowId,session_id:session});
      if(result?.ok!==true)throw Error('验证观察未准备好');
      show('已开启验证观察，页面恢复后将自动继续任务');
    } catch(error) { show(error?.message || '验证观察启动失败'); }
  });
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
    const bound=await bounded(chrome.runtime.sendMessage(request),'anchor_recovery_timeout');
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
    if (!enabled || busy || activationPending) return; busy=true;
    let stage='binding_status';
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
        await trace('binding_identity',state.ok===true?'ok':'unverified');
        trackPopup?.(state, session);
        if(identity) identity.textContent=state?.ok ? `任务 ${requestId}；当前标签 ${target}；任务标签 ${state.bound_tab_id ?? "待绑定"}；${state.bound_tab_id == null ? "等待首组绑定" : state.matches ? "匹配" : "不匹配，请返回任务标签"}` : `当前标签 ${target}；后台身份待核验`;
      if (selected && state.ok !== true) throw new Error("binding_unavailable");
      if (selected) {
        await restoreGuard(state,tab);
        if(reconnecting){
          reconnecting=false;
          await trace('binding_reconnect',state.matches===true?'ok':'unverified');
          show(state.matches===true?'已连接当前任务；请按采集进度核验搜索词':'已连接后台；任务标签尚未匹配');
        }
      }
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
        if (url.protocol!=='https:' || !['www.mercadolibre.com.mx','listado.mercadolibre.com.mx','articulo.mercadolibre.com.mx'].includes(url.hostname) || url.port) throw new Error('Selected window is not on the authorized platform');
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
        await trace('binding_select','ok');
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
      stage='query_challenge';
      const response=await chrome.runtime.sendNativeMessage(HOST,{operation:'poll',request_id:requestId,tab_id:target});
      if (response?.error === 'request_changed') { enabled=false; show('任务已切换，请关闭采集器并重新打开'); return; }
      const challenge=response?.challenge;
      if (!challenge) return;
      await trace(stage,'ok'); stage='query_tab_get';await trace(stage,'started');
      const observedTab=await chrome.tabs.get(target);
      await trace(stage,'ok');stage='query_page_extract';await trace(stage,'started');
      const page=await chrome.tabs.sendMessage(target,{type:'MERCADO_COLLECTOR_EXTRACT_SEARCH',query:challenge.query,prior:[],limit:30});
      await trace(stage,'ok');stage='query_receipt';await trace(stage,'started');
      const receipt=CollectorBinding.makeReceipt(challenge,await sessionId(),observedTab,page,target,new Date().toISOString());
      await trace(stage,'ok');stage='query_submit';await trace(stage,'started');
      const acknowledgement=await chrome.runtime.sendNativeMessage(HOST,{operation:'submit',receipt});
      await trace(stage,acknowledgement?.ok===true?'ok':'rejected');
      CollectorGate.grant(receipt, acknowledgement?.ok === true);
    } catch (error) {
      await trace(stage,'failed',error);
      if (/^(?:anchor_|window_prepare_failed|session_unavailable)/.test(error?.message || '')) {
        selected=false;
        const transient=['anchor_recovery_timeout','anchor_recovery_unavailable','anchor_broker_rotating','anchor_challenge_failed','anchor_guard_ready_failed','anchor_rebind_pending','anchor_select_pending'].includes(error.message);
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
      ['gallery_download_saved','gallery_download_next'].includes(operation) ? 95000 : 12000);
    if (reply?.error === 'checkpoint_guard_pending' && (operation === 'collector_progress' || operation === 'checkpoint_detail' || operation === 'next_candidate')) {
      const ready = await bounded(chrome.runtime.sendMessage({type:'ensure_collector_guard',
        request_id:requestId,tab_id:target,window_id:tab.windowId}), 'guard_ack_timeout');
      if (ready?.ok !== true) throw Error(ready?.error || 'guard_ack_timeout');
      reply = await bounded(chrome.runtime.sendNativeMessage(HOST, message), 'checkpoint_timeout');
    }
    if (reply?.ok !== true) throw Error([reply?.error || 'checkpoint_unavailable', reply?.message].filter(Boolean).join('：'));
    return reply;
  };
  globalThis.openCollectorCandidate = async (expectedIdentity, taskRecordId) => {
    if (!selected || !enabled || !requestId) throw Error('checkpoint_disconnected');
    const tab = await chrome.tabs.get(target);
    return navigateCollectorCandidate({
      requestId, target, windowId:tab.windowId, session:await sessionId(), expectedIdentity, taskRecordId,
      platform:'mercado-libre-mx', checkpoint:globalThis.collectorCheckpoint, extract:'MERCADO_COLLECTOR_EXTRACT_SEARCH',
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
  globalThis.confirmCollectorQueryCards=async (queryIndex,cards,options={})=>{
    if (!selected || !enabled || !requestId || !Number.isInteger(queryIndex) || queryIndex<0 || queryIndex>5 || !Array.isArray(cards)) return false;
    try {
      await trace('query_cards_submit','started');
      const tab=await chrome.tabs.get(target), session=await sessionId();
      const reply=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:'query_cards_complete',request_id:requestId,tab_id:target,window_id:tab.windowId,session_id:session,query_index:queryIndex,cards,...(options.exhausted===true ? {exhausted:true,query:options.query} : {})}),'cards_timeout');
      await trace('query_cards_submit',reply?.ok===true?'ok':'rejected');
      return reply?.ok===true;
    } catch (error) {await trace('query_cards_submit','failed',error);return false;}
  };
  globalThis.saveVisualQueryBatch=async (queryIndex,query,batchIndex,cards)=>{
    if (!selected || !enabled || !requestId || !Number.isInteger(queryIndex) || queryIndex<0 || queryIndex>8 ||
        typeof query!=='string' || !query.trim() || !Number.isInteger(batchIndex) || batchIndex<0 || !Array.isArray(cards) || !cards.length) return false;
    try {
      const tab=await chrome.tabs.get(target), session=await sessionId();
      const reply=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:'visual_v2_batch',request_id:requestId,
        tab_id:target,window_id:tab.windowId,session_id:session,query_index:queryIndex,query,batch_index:batchIndex,cards}),'cards_timeout');
      return reply?.ok===true;
    } catch (_) {return false;}
  };
  globalThis.confirmCardV3Page=async(queryIndex,query,page)=>{
    if(!selected || !enabled || !requestId || !Number.isInteger(queryIndex) || queryIndex<0 || queryIndex>8 ||
       typeof query!=='string' || !query.trim() || !Number.isInteger(page) || page<1 || page>3)return false;
    try {
      const tab=await chrome.tabs.get(target),session=await sessionId();
      const reply=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:'card_v3_page_challenge',request_id:requestId,
        tab_id:target,window_id:tab.windowId,session_id:session,query_index:queryIndex,query,page}),'cards_timeout');
      const challenge=reply?.challenge;
      if(!challenge || challenge.request_id!==requestId || challenge.query_index!==queryIndex || challenge.query!==query || challenge.page!==page ||
         challenge.tab_id!==target || challenge.window_id!==tab.windowId || challenge.session_id!==session || challenge.page_url!==tab.url)return false;
      const observed=await chrome.tabs.sendMessage(target,{type:'MERCADO_COLLECTOR_PAGE_BINDING'});
      const current=await chrome.tabs.get(target);
      if(observed?.error || observed?.page_url!==challenge.page_url || !sameObservedQuery(observed?.search_query,query) || current.url!==challenge.page_url)return false;
      const result=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:'card_v3_page_confirm',request_id:requestId,
        tab_id:target,window_id:tab.windowId,session_id:session,receipt:{...challenge,observed_at:new Date().toISOString()}}),'cards_timeout');
      return result?.ok===true;
    } catch (_) {return false;}
  };
  globalThis.saveCardV3Batch=async(queryIndex,query,page,batchIndex,cards,prior=[])=>{
    if(!selected || !enabled || !requestId || !Number.isInteger(queryIndex) || queryIndex<0 || queryIndex>8 ||
       typeof query!=='string' || !query.trim() || !Number.isInteger(page) || page<1 || page>3 ||
       !Number.isInteger(batchIndex) || batchIndex<0 || !Array.isArray(cards) || !cards.length || !Array.isArray(prior))return false;
    try {
      if(await globalThis.confirmCardV3Page(queryIndex,query,page)!==true)return false;
      const tab=await chrome.tabs.get(target),session=await sessionId();
      const verify=async()=>{
        const active=await chrome.tabs.query({active:true,windowId:tab.windowId});
        const current=await chrome.tabs.get(target);
        if(active.length!==1 || active[0].id!==target || current.url!==tab.url)return false;
        const observed=await chrome.tabs.sendMessage(target,{type:'MERCADO_COLLECTOR_EXTRACT_SEARCH',query,prior,limit:10000,strict_scope:true,with_sales:true});
        if(observed?.page_url!==tab.url || observed?.verified_scope!==true || observed?.card_sales_version!==3 || !Array.isArray(observed.observations))return false;
        const keys=['product_id','title','is_ad','sales_text','sales_status','bbox'];
        return cards.every(card=>observed.observations.some(row=>keys.every(key=>JSON.stringify(row[key])===JSON.stringify(card[key]))));
      };
      if(!await verify())return false;
      const image=await chrome.tabs.captureVisibleTab(tab.windowId,{format:'jpeg',quality:50});
      const match=/^data:image\/jpeg;base64,([A-Za-z0-9+/]+={0,2})$/.exec(image || '');
      if(!match || match[1].length>=850000 || !await verify())return false;
      const reply=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:'card_v3_batch',request_id:requestId,
        tab_id:target,window_id:tab.windowId,session_id:session,query_index:queryIndex,query,page,page_url:tab.url,batch_index:batchIndex,cards,screenshot:match[1]}),'cards_timeout');
      return reply?.ok===true;
    } catch (_) {return false;}
  };
  globalThis.finishCardV3Page=async(queryIndex,query,page,home=false)=>{
    if(!selected || !enabled || !requestId || !Number.isInteger(queryIndex) || queryIndex<0 || queryIndex>8 ||
       typeof query!=='string' || !query.trim() || !Number.isInteger(page) || page<1 || page>3)return false;
    try {
      if(await globalThis.confirmCardV3Page(queryIndex,query,page)!==true)return false;
      const tab=await chrome.tabs.get(target),session=await sessionId(),observations=[];
      for(let i=0;i<3;i++){
        const info=await chrome.tabs.sendMessage(target,{type:'MERCADO_COLLECTOR_PAGE_INFO',page,home});
        const current=await chrome.tabs.get(target);
        if(!info || info.error || info.at_end!==true || info.page_url!==tab.url || current.url!==tab.url ||
           !(home ? ['home_complete','end_of_results'] : ['has_next','end_of_results','page_limit']).includes(info.status))return false;
        observations.push(info);
      }
      const first=observations[0];
      if(observations.some(x=>x.status!==first.status || x.url!==first.url || x.end_evidence!==first.end_evidence))return false;
      const nextUrl=first.status==='has_next'?first.url:null;
      if(first.status==='has_next' && typeof nextUrl!=='string')return false;
      const endEvidence=first.end_evidence || (first.status==='has_next'?'next_link':first.status==='end_of_results'?'disabled_next':'page_limit');
      const reply=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:'card_v3_page_finish',request_id:requestId,
        tab_id:target,window_id:tab.windowId,session_id:session,query_index:queryIndex,query,page,page_url:tab.url,
        status:first.status,next_url:nextUrl,end_checks:observations.length,end_evidence:endEvidence}),'cards_timeout');
      return reply?.ok===true;
    } catch (_) {return false;}
  };
  globalThis.finishVisualQuery=async (queryIndex,query,status,endChecks,noResults)=>{
    if (!selected || !enabled || !requestId || !Number.isInteger(queryIndex) || queryIndex<0 || queryIndex>8 ||
        typeof query!=='string' || !query.trim() || !['home_complete','card_limit_reached'].includes(status) ||
        !Number.isInteger(endChecks) || typeof noResults!=='boolean') return false;
    try {
      const tab=await chrome.tabs.get(target), session=await sessionId();
      const reply=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:'visual_v2_finish',request_id:requestId,
        tab_id:target,window_id:tab.windowId,session_id:session,query_index:queryIndex,query,status,end_checks:endChecks,no_results:noResults}),'cards_timeout');
      return reply?.ok===true;
    } catch (_) {return false;}
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
    await trace('binding_activate','clicked');
    if (!selected || !enabled || !requestId || activationPending) {const code=busy?'busy':!selected?'not_selected':!enabled?'not_enabled':!requestId?'request_missing':'busy';await trace('binding_activate',code);show(code==='busy'?'连接处理中，请稍候重试':'请先连接当前任务'); return; }
    activationPending=true;
    const deadline=Date.now()+12000;
    while(busy && Date.now()<deadline) await new Promise(resolve=>setTimeout(resolve,100));
    if(busy){activationPending=false;await trace('binding_activate','busy_timeout');show('连接等待超时，请检查连接后重试');return;}
    if(!selected || !enabled || !requestId){activationPending=false;await trace('binding_activate','binding_changed');show('任务连接已变化，请重新连接后重试');return;}
    busy=true;
    try {
      const tab=await chrome.tabs.get(target), session=await sessionId();
      const state=await bounded(chrome.runtime.sendNativeMessage(HOST,{operation:'binding_status',
        tab_id:target,window_id:tab.windowId,session_id:session}),'status_timeout');
      if (state?.request_id !== requestId || state.ok !== true) throw Error('binding_unavailable');
      if (state.gallery_phase === true) {
        if (state.matches !== true || state.bound_tab_id !== target || state.anchor_active !== true) throw Error('gallery_tab_changed');
        const product=await openBoundGallery({requestId,target,windowId:tab.windowId,session,
          platform:'mercado-libre-mx',extract:'MERCADO_COLLECTOR_EXTRACT_DETAIL',restoreGuard});
        const productInput=document.getElementById('capture-product');
        if (productInput && productInput.value !== product.split(':')[1]) {
          productInput.value=product.split(':')[1];
          productInput.dispatchEvent(new Event('input'));
        }
        show('已核验图库商品 '+product+'，可继续采集当前规格原图');
        return;
      }
      await globalThis.activateCollectorTarget({requestId,target,windowId:tab.windowId,session,extract:'MERCADO_COLLECTOR_EXTRACT_SEARCH'});
      await trace('binding_activate','ok');
      show('已切换到核验过的任务标签');
    } catch (error) { await trace('binding_activate','failed',error);show('任务标签核验或切换失败（'+(error.code||'binding_unavailable')+'）'); }
    finally {busy=false;activationPending=false;}
  });
  if (connect) connect.addEventListener('click',()=>{
    if (busy) return;
    void trace('binding_connect','clicked');
    reconnecting=selected; anchorAttempts=0;landingStarted=0; enabled=true; show('正在连接当前任务'); tick();
  });
  if (dedicated && typeof dedicated.addEventListener === 'function') dedicated.addEventListener('click',async()=>{
    if (busy) return;
    busy=true;
    try {
      if(!requestId) requestId=await bounded(RequestDiscovery.discoverRequest(chrome,HOST,target),'status_timeout');
      const result=await bounded(chrome.runtime.sendMessage({type:'dedicated_window_register',request_id:requestId}),'window_timeout');
      if (!Number.isSafeInteger(result?.window_id) || result.window_id<=0) throw Error('window unavailable');
      show(`专用采集窗口已确认：${result.window_id}`);
    } catch (_) {show('专用采集窗口未确认，请检查连接并保持任务暂停');}
    finally {busy=false;}
  });
  if (taskTab && typeof taskTab.addEventListener === 'function') taskTab.addEventListener('click',async()=>{
    if (!requestId || busy) {show('请先连接当前任务');return;}
    busy=true;
    try {
      const result=await bounded(chrome.runtime.sendMessage({type:'dedicated_task_tab_create',request_id:requestId}),'tab_timeout');
      if (!Number.isSafeInteger(result?.tab_id) || result.tab_id<=0) throw Error('tab unavailable');
      show(`已打开${result.reused?'原任务标签':'新任务标签'} ${result.tab_id}，请按当前采集阶段继续`);
    } catch (_) {show('任务标签未确认，请检查连接并保持任务暂停');}
    finally {busy=false;}
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

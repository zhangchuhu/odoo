'use strict';

// The broker picks the next product from the committed evidence. Only an
// explicit click can navigate the assigned tab or confirm its visible detail.
globalThis.openBoundGallery = async function ({requestId, target, windowId, session, platform, extract, restoreGuard}) {
  const host = 'local.odoo.collector_binding';
  const scope = {request_id:requestId, tab_id:target, window_id:windowId, session_id:session};
  const native = message => chrome.runtime.sendNativeMessage(host, message);
  const fail = code => { const error = Error(code); error.code = code; throw error; };
  const trace = async (stage, code) => { if (globalThis.CollectorTrace) await CollectorTrace.append(stage, code); };
  async function boundTab(active = false) {
    const tab = await chrome.tabs.get(target);
    if (tab.id !== target || tab.windowId !== windowId) fail('gallery_tab_changed');
    if (active) {
      const tabs = await chrome.tabs.query({active:true, windowId});
      if (!tab.active || tabs.length !== 1 || tabs[0].id !== target) fail('gallery_tab_inactive');
    }
    return tab;
  }
  let stage = 'gallery_challenge';
  try {
    await trace(stage,'started');
    const original = await boundTab();
    const proof = await native({operation:'gallery_binding_challenge', ...scope});
    if (!proof?.ok) fail(proof?.error || 'gallery_challenge_unavailable');
    if (Object.entries(scope).some(([key,value]) => proof[key] !== value) || proof.platform !== platform ||
        !Number.isSafeInteger(proof.epoch) || proof.epoch < 1 || typeof proof.nonce !== 'string' ||
        !/^[\w-]{20,128}$/.test(proof.nonce) || !proof.identity || collectorDetailIdentity(platform, proof.detail_url) !== proof.identity) {
      fail('gallery_challenge_invalid');
    }
    await trace(stage,'ok');
    stage = 'gallery_navigation';
    const tab = await boundTab();
    if (tab.url !== original.url || tab.pendingUrl && tab.pendingUrl !== tab.url) fail('gallery_tab_changed');
    const destination = collectorDetailIdentity(platform, tab.url) === proof.identity ? {} : {url:proof.detail_url};
    await chrome.tabs.update(target, {active:true, ...destination});
    await chrome.windows.update(windowId, {focused:true});
    await trace(stage,'ok');
    stage = 'gallery_visible_detail';
    const deadline = Date.now() + 30000;
    let actual;
    while (Date.now() < deadline) {
      const current = await boundTab(true);
      if (current.status !== 'loading' && !current.pendingUrl) {
        if (collectorDetailIdentity(platform,current.url) === proof.identity) {
          let page;
          try { page = await chrome.tabs.sendMessage(target, {type:extract, identity:proof.identity}); }
          catch (_) {} // The document may have committed before its content script loaded.
          if (page?.blocked) fail('gallery_detail_blocked');
          if (page && (page.identity_matches !== true || page.identity !== proof.identity || page.page_url !== current.url)) {
            fail('gallery_detail_mismatch');
          }
          let visible = Boolean(page?.title?.trim() && Array.isArray(page.access_bbox) &&
            page.access_bbox.length === 4 && page.access_bbox[2] > 0 && page.access_bbox[3] > 0);
          // A restored photo viewer can cover the product heading. Its own
          // selected-SKU, visible full-size image is also valid page evidence.
          if (page && !visible) {
            const gallery = await chrome.tabs.sendMessage(target,{type:'COLLECTOR_VISIBLE_GALLERY',identity:proof.identity});
            visible = gallery?.identity === proof.identity && gallery.page_url === current.url &&
              gallery.sourceArea === 'viewer' && Array.isArray(gallery.rows) && gallery.rows.length > 0;
          }
          if (visible) {
            const after = await boundTab(true);
            if (after.url !== current.url || after.pendingUrl || after.status === 'loading') fail('gallery_tab_changed');
            actual = after.url;
            break;
          }
        } else if (current.url !== original.url) {
          fail('gallery_navigation_redirected');
        }
      }
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    if (!actual) fail('gallery_detail_not_visible');
    await trace(stage,'ok');
    stage = 'gallery_confirm';
    const confirmed = await native({operation:'gallery_binding_confirm', ...scope, epoch:proof.epoch,
      nonce:proof.nonce, identity:proof.identity, detail_url:actual, observed_at:new Date().toISOString()});
    if (confirmed?.ok !== true) fail(confirmed?.error || 'gallery_confirm_rejected');
    await trace(stage,'ok');
    stage = 'gallery_guard';
    const state = await native({operation:'binding_status',tab_id:target,window_id:windowId,session_id:session});
    if (state?.ok !== true || state.request_id !== requestId || state.bound_tab_id !== target || state.matches !== true ||
        state.gallery_phase !== true || state.guard_required !== true || state.anchor_active !== true) fail('gallery_guard_unavailable');
    const current = await boundTab(true);
    if (current.url !== actual || current.pendingUrl || current.status === 'loading') fail('gallery_tab_changed');
    await restoreGuard(state,current);
    await trace(stage,'ok');
    return proof.identity;
  } catch (error) {
    if (globalThis.CollectorTrace) await CollectorTrace.appendError(stage,error);
    throw error;
  }
};

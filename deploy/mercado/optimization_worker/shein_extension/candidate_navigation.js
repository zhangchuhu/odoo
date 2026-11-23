'use strict';

function collectorDetailIdentity(platform, raw) {
  if (!isCandidateUrl(raw)) return null;
  const path = new URL(raw).pathname;
  if (platform === 'mercado-libre-mx') {
    const match = /(?:^|[^a-z0-9])(MLMU?)[-_ ]*(\d+)(?!\d)/i.exec(path);
    return match && platform + ':' + match[1].toUpperCase() + match[2];
  }
  const match = /-p-(\d+)(?:\.html)?\/?$/i.exec(path);
  return match && platform + ':' + match[1];
}

// Only the Native Messaging service selects the destination. Both the collector
// and the current browser binding must agree before modifying the task tab.
globalThis.navigateCollectorCandidate = async function ({
  requestId, target, windowId, session, expectedIdentity, taskRecordId, platform, checkpoint, extract,
}) {
  const currentTab = async () => {
    const tab = await chrome.tabs.get(target);
    if (tab.id !== target || tab.windowId !== windowId) throw Error('candidate_binding_changed');
    return tab;
  };
  const initial = await currentTab();
  const url = new URL(initial.url);
  const search = platform === 'mercado-libre-mx'
    ? url.hostname === 'listado.mercadolibre.com.mx'
    : url.hostname === 'us.shein.com' && /^\/pdsearch\//i.test(url.pathname);
  if (url.protocol !== 'https:' || url.username || url.password || url.port || (!search && !collectorDetailIdentity(platform, initial.url))) {
    throw Error('candidate_current_page_unverified');
  }
  const proof = await checkpoint('next_candidate', {});
  if (proof?.ok !== true || proof.request_id !== requestId || !taskRecordId || proof.task !== taskRecordId || proof.tab_id !== target ||
      proof.window_id !== windowId || proof.session_id !== session) throw Error('candidate_binding_changed');
  if (!proof.candidate) throw Error('candidate_review_complete');
  const candidate = proof.candidate;
  if (!expectedIdentity || candidate.identity !== expectedIdentity ||
      collectorDetailIdentity(platform, candidate.url) !== expectedIdentity) throw Error('candidate_progress_mismatch');
  const tab = await currentTab();
  if (tab.url !== initial.url || (tab.pendingUrl && tab.pendingUrl !== tab.url)) throw Error('candidate_binding_changed');
  if (collectorDetailIdentity(platform, tab.url) !== expectedIdentity) await chrome.tabs.update(target, {url:candidate.url});

  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const current = await currentTab();
    if (current.status !== 'loading' && !current.pendingUrl && collectorDetailIdentity(platform, current.url) === expectedIdentity) {
      // A newly committed document can precede installation of its content script.
      let page;
      try { page = await chrome.tabs.sendMessage(target, {type:extract.replace('EXTRACT_SEARCH','EXTRACT_DETAIL'), identity:expectedIdentity}); }
      catch (_) {}
      if (page?.blocked) throw Error(page.blocking_error || 'detail_access_blocked');
      if (page?.identity_matches && page.identity === expectedIdentity && page.page_url === current.url) {
        return activateCollectorTarget({requestId, target, windowId, session, extract, expectedIdentity});
      }
    }
    if (current.status === 'complete' && current.url !== tab.url && !current.pendingUrl && collectorDetailIdentity(platform, current.url) !== expectedIdentity) {
      throw Error('candidate_navigation_redirected');
    }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw Error('candidate_navigation_timeout');
};

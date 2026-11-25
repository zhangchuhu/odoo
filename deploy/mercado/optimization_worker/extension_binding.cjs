'use strict';
// Validation primitive only. Native messaging and trusted challenge delivery
// must be installed before this can be used as an authenticated receipt.
function makeReceipt(challenge, session, tab, page, collectorTabId, observedAt) {
  if (!challenge || challenge.version !== 1 || typeof challenge.request_id !== 'string' || !challenge.request_id || typeof challenge.nonce !== 'string' || !/^[A-Za-z0-9_-]{32,128}$/.test(challenge.nonce) || !Number.isSafeInteger(challenge.lease_epoch) || challenge.lease_epoch < 1 || !/^[a-f0-9]{64}$/.test(challenge.plan_digest) || !['mercado-libre-mx','shein-us'].includes(challenge.platform) || !Number.isSafeInteger(challenge.query_index) || challenge.query_index < 0 || challenge.query_index > 2 || typeof challenge.query !== 'string' || !challenge.query.trim() || !/^[a-f0-9]{32}$/.test(session) || !tab || !Number.isSafeInteger(tab.id) || tab.id <= 0 || !Number.isSafeInteger(tab.windowId) || tab.windowId <= 0 || collectorTabId !== tab.id || !page || page.page_url !== tab.url || page.search_query !== challenge.query || typeof observedAt !== 'string' || !Number.isFinite(Date.parse(observedAt))) throw new Error('Invalid observed binding');
  const url = new URL(tab.url);
  if (url.protocol !== 'https:' || url.hostname !== (challenge.platform === 'shein-us' ? 'us.shein.com' : 'listado.mercadolibre.com.mx') || url.port) throw new Error('Invalid platform page');
  return {...challenge, session_id:session, tab_id:tab.id,window_id:tab.windowId,collector_tab_id:collectorTabId,url:tab.url,observed_at:observedAt};
}
module.exports={makeReceipt};

'use strict';
// Validation primitive only. Native messaging and trusted challenge delivery
// must be installed before this can be used as an authenticated receipt.
function makeReceipt(challenge, session, tab, page, collectorTabId, observedAt) {
  const requireField=(condition,code)=>{if(!condition){const error=new Error('Invalid observed binding');error.code=code;throw error;}};
  requireField(!!challenge,'receipt_challenge_missing');
  requireField(challenge.version===1,'receipt_version_invalid');
  requireField(typeof challenge.request_id==='string' && !!challenge.request_id,'receipt_request_invalid');
  requireField(typeof challenge.nonce==='string' && /^[A-Za-z0-9_-]{32,128}$/.test(challenge.nonce),'receipt_nonce_invalid');
  requireField(Number.isSafeInteger(challenge.lease_epoch) && challenge.lease_epoch>=1,'receipt_lease_invalid');
  requireField(typeof challenge.plan_digest==='string' && /^[a-f0-9]{64}$/.test(challenge.plan_digest),'receipt_plan_invalid');
  requireField(['mercado-libre-mx','shein-us'].includes(challenge.platform),'receipt_platform_invalid');
  requireField(Number.isSafeInteger(challenge.query_index) && challenge.query_index>=0 && challenge.query_index<=2,'receipt_query_index_invalid');
  requireField(typeof challenge.query==='string' && !!challenge.query.trim(),'receipt_query_invalid');
  requireField(typeof session==='string' && /^[a-f0-9]{32}$/.test(session),'receipt_session_invalid');
  requireField(!!tab && Number.isSafeInteger(tab.id) && tab.id>0,'receipt_tab_invalid');
  requireField(Number.isSafeInteger(tab.windowId) && tab.windowId>0,'receipt_window_invalid');
  requireField(collectorTabId===tab.id,'receipt_tab_mismatch');
  requireField(!!page,'receipt_page_missing');
  const failure=(detail)=>{const error=new Error('Invalid observed binding');error.code='receipt_page_url_mismatch';error.detailCode=detail;throw error;};
  if(page.error){const e=String(page.error);failure(e.startsWith('Collection stopped: blocking page detected')?'receipt_page_blocked':e.startsWith('Collection stopped: card link=')?'receipt_page_card_error':e.startsWith('Collection stopped: incomplete card')?'receipt_page_card_incomplete':'receipt_page_extract_error');}
  if(typeof tab.url!=='string'||!tab.url)failure('receipt_tab_url_missing');
  try{new URL(tab.url)}catch(_){failure('receipt_tab_url_invalid')}
  if(typeof page.page_url!=='string'||!page.page_url)failure('receipt_page_url_missing');
  try{new URL(page.page_url)}catch(_){failure('receipt_page_url_invalid')}
  if(page.page_url!==tab.url){
    const error=new Error('Invalid observed binding');error.code='receipt_page_url_mismatch';error.detailCode='receipt_url_navigation_mismatch';
    try{const a=new URL(tab.url),b=new URL(page.page_url);if(a.origin===b.origin && a.pathname===b.pathname && a.search===b.search && a.hash!==b.hash)error.detailCode='receipt_url_fragment_mismatch';else if(a.origin===b.origin && decodeURI(a.href)===decodeURI(b.href))error.detailCode='receipt_url_encoding_mismatch';}catch(_){error.detailCode='receipt_url_invalid';}
    throw error;
  }
  requireField(page.search_query===challenge.query,'receipt_query_mismatch');
  requireField(typeof observedAt==='string' && Number.isFinite(Date.parse(observedAt)),'receipt_time_invalid');
  const url = new URL(tab.url);
  if (url.protocol !== 'https:' || url.hostname !== (challenge.platform === 'shein-us' ? 'us.shein.com' : 'listado.mercadolibre.com.mx') || url.port) throw new Error('Invalid platform page');
  return {...challenge, session_id:session, tab_id:tab.id,window_id:tab.windowId,collector_tab_id:collectorTabId,url:tab.url,observed_at:observedAt};
}
globalThis.CollectorBinding={makeReceipt};

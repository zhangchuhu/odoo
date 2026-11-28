'use strict';
async function collectHome({query,queryIndex,prior,nextBatch,send,save,finish,delay,onProgress=()=>{}}) {
  if(typeof query!=='string' || !query.trim() || !Number.isInteger(queryIndex) || queryIndex<0 || queryIndex>8 ||
     !Array.isArray(prior) || !Number.isInteger(nextBatch) || nextBatch<0 ||
     [send,save,finish,delay].some(fn=>typeof fn!=='function')) throw Error('invalid_home_state');
  let cards=prior.slice(),batch=nextBatch,url=null,endChecks=0;
  for(;;) {
    const page=await send({type:'SHEIN_COLLECTOR_EXTRACT_HOME_SEARCH',query,prior:cards,limit:10000});
    if(page?.error || page?.search_query!==query || typeof page?.page_url!=='string' || (url && page.page_url!==url) ||
       !Array.isArray(page.observations) || page.observations.length<cards.length) throw Error('home_page_binding_mismatch');
    url=page.page_url;
    if(page.no_results===true) {
      if(cards.length || page.observations.length)throw Error('home_empty_results_conflict');
      if(await finish(queryIndex,query,true)!==true)throw Error('home_finish_unconfirmed');
      return {cards,nextBatch:batch,pageUrl:url};
    }
    for(let i=0;i<cards.length;i++) if(page.observations[i]?.product_id!==cards[i]?.product_id) throw Error('home_checkpoint_conflict');
    const added=page.observations.slice(cards.length);
    for(let i=0;i<added.length;i+=100) {
      const chunk=added.slice(i,i+100);
      if(await save(queryIndex,query,batch,chunk,cards.slice())!==true)throw Error('home_batch_unconfirmed');
      cards.push(...chunk);batch++;onProgress(cards.length);
    }
    const scroll=await send({type:'SHEIN_COLLECTOR_SCROLL'});
    if(scroll?.error)throw Error(scroll.error);
    endChecks=scroll?.at_end===true && !added.length ? endChecks+1 : 0;
    if(endChecks>=3) {
      if(!cards.length)throw Error('home_empty_results_unverified');
      if(await finish(queryIndex,query)!==true)throw Error('home_finish_unconfirmed');
      return {cards,nextBatch:batch,pageUrl:url};
    }
    await delay(endChecks?1000:350);
  }
}
if(typeof module!=='undefined')module.exports={collectHome};
if(typeof globalThis!=='undefined')globalThis.SheinHomeCollector={collectHome};

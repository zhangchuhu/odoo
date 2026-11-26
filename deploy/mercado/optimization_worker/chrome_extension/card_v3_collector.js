'use strict';
// One page only. Navigation and task ownership remain under the native broker.
async function collectPage({queryIndex,query,page,prior,nextBatch,send,save,finish,delay,onProgress=()=>{}}){
  if(!Number.isInteger(queryIndex)||queryIndex<0||queryIndex>8||typeof query!=='string'||!query.trim()||
     !Number.isInteger(page)||page<1||page>3||!Array.isArray(prior)||!Number.isInteger(nextBatch)||nextBatch<0||
     [send,save,finish,delay].some(fn=>typeof fn!=='function'))throw Error('invalid page state');
  let cards=prior.slice(),batch=nextBatch,url=null,endChecks=0;
  for(;;){
    const binding=await send({type:'MERCADO_COLLECTOR_PAGE_BINDING'});
    if(binding?.error || binding?.search_query!==query || typeof binding?.page_url!=='string' || (url && url!==binding.page_url))throw Error('page_binding_mismatch');
    url=binding.page_url;
    const result=await send({type:'MERCADO_COLLECTOR_EXTRACT_SEARCH',query,prior:cards,limit:10000,strict_scope:true,with_sales:true});
    if(result?.error)throw Error(result.error);
    if(result?.page_url!==url || result?.verified_scope!==true || result?.card_sales_version!==3 || !Array.isArray(result.observations) || result.observations.length<cards.length)throw Error('page_extraction_unverified');
    for(let i=0;i<cards.length;i++)if(result.observations[i]?.product_id!==cards[i]?.product_id)throw Error('page_checkpoint_conflict');
    const added=result.observations.slice(cards.length);
    for(let i=0;i<added.length;i+=100){
      const chunk=added.slice(i,i+100);
      if(await save(queryIndex,query,page,batch,chunk,cards.slice())!==true)throw Error('page_checkpoint_unconfirmed');
      cards.push(...chunk);batch++;onProgress(cards.length);
    }
    const scroll=await send({type:'MERCADO_COLLECTOR_SCROLL'});
    if(scroll?.error)throw Error(scroll.error);
    if(scroll?.at_end===true && added.length===0)endChecks++;else endChecks=0;
    if(endChecks>=3){
      if(!cards.length && result.no_results!==true)throw Error('page_end_unverified');
      if(await finish(queryIndex,query,page)!==true)throw Error('page_finish_unconfirmed');
      return {cards,nextBatch:batch,pageUrl:url};
    }
    await delay(endChecks?1000:350);
  }
}
if(typeof module!=='undefined')module.exports={collectPage};
if(typeof globalThis!=='undefined')globalThis.CardV3Collector={collectPage};

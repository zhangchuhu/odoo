'use strict';
class QueryGate {
 constructor(now=()=>Date.now()){this.now=now;this.entry=null;}
 matches(query,observation){return typeof query==='string' && observation?.search_query===query;}
 grant(receipt,acknowledged){
  if (acknowledged!==true || !receipt || !Number.isSafeInteger(receipt.tab_id) || typeof receipt.session_id!=='string' || typeof receipt.query!=='string' || !Number.isSafeInteger(receipt.query_index)) return;
  this.entry={tab_id:receipt.tab_id,session_id:receipt.session_id,query:receipt.query,query_index:receipt.query_index,expires:this.now()+30000};
 }
 consume(tab,session,query,index){
  const v=this.entry;
  if (!v || this.now()>v.expires || v.tab_id!==tab || v.session_id!==session || v.query!==query || v.query_index!==index) return false;
  this.entry=null;return true;
 }
}
globalThis.CollectorGate=new QueryGate();

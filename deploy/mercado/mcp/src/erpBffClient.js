'use strict';
const BFF_PREFIX='/api/meli';
function createErpBffClient({erpBaseUrl,token}) {
  const base=String(erpBaseUrl||'').trim().replace(/\/+$/,'');
  const bearer=String(token||'').trim();let parsed;
  try{parsed=new URL(base);}catch{throw new Error('erpBaseUrl must be an HTTP(S) origin');}
  if(!['http:','https:'].includes(parsed.protocol)||parsed.username||parsed.password||parsed.search||parsed.hash||parsed.pathname!=='/')throw new Error('erpBaseUrl must be an HTTP(S) origin without credentials or path');
  if(!bearer||/[\s\r\n]/.test(bearer))throw new Error('Odoo API key is required');
  return {erpBaseUrl:base,BASE:base+BFF_PREFIX,baseUrl:base+BFF_PREFIX,BFF_PREFIX,
    authHeaders(extra={}){return {...extra,Authorization:`Bearer ${bearer}`};}};
}
module.exports={createErpBffClient,BFF_PREFIX};

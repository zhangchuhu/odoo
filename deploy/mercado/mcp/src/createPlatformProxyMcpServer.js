'use strict';
const {McpServer}=require('@modelcontextprotocol/sdk/server/mcp.js');
const {z}=require('zod');
const Ajv2020=require('ajv/dist/2020').default;
const {createErpBffClient}=require('./erpBffClient');
const text=(v,isError=false)=>({...(isError?{isError:true}:{}),content:[{type:'text',text:JSON.stringify(v)}]});
const MAX_RESPONSE=2*1024*1024, MAX_FILE=10*1024*1024;
async function bounded(response){
 if(Number(response.headers.get('content-length'))>MAX_RESPONSE){await response.body?.cancel();throw Error('oversize');}
 let length=0,parts=[];if(response.body)for await(const c of response.body){length+=c.length;if(length>MAX_RESPONSE)throw Error('oversize');parts.push(c);}
 return Buffer.concat(parts).toString('utf8');
}
function createPlatformProxyMcpServer({erpBaseUrl,token}){
 const {BASE,authHeaders}=createErpBffClient({erpBaseUrl,token});
 const request=async(path,options={})=>fetch(BASE+path,{...options,redirect:'error',headers:authHeaders(options.headers),signal:AbortSignal.timeout(120000)});
 async function catalog(){
  const r=await request('/operations');if(!r.ok){await r.body?.cancel();throw Error('catalog failed');}
  const d=JSON.parse(await bounded(r));if(typeof d.contract_version!=='string'||!Array.isArray(d.operations))throw Error('invalid catalog');
  return {version:d.contract_version,operations:d.operations.filter(x=>x.exposure==='mcp'&&typeof x.operation_id==='string'&&/^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$/.test(x.operation_id))};
 }
 const server=new McpServer({name:'odoo-mercado-libre',version:'1.0.0'});
 const guarded=fn=>async a=>{try{return await fn(a);}catch{return text({code:'BFF_REQUEST_FAILED',message:'Odoo request failed; check the result before retrying a write.'},true);}};
 server.registerTool('listing_shops',{description:'List Odoo shops accessible to this identity.',inputSchema:z.object({}).strict()},guarded(async()=>{
  const r=await request('/shops');if(!r.ok){await r.body?.cancel();return text({code:'BFF_OPERATION_FAILED',http_status:r.status},true);}
  const data=JSON.parse(await bounded(r));if(!Array.isArray(data.shops))throw Error('invalid shops response');return text(data);
 }));
 server.registerTool('listing_module_router',{description:'List domains in the authorized Odoo catalog.',inputSchema:z.object({}).strict()},guarded(async()=>{const c=await catalog(),domains=[...new Set(c.operations.map(o=>o.domain))].sort();return text({modules:domains.map(key=>({key})),moduleCount:domains.length,totalOperations:c.operations.length});}));
 server.registerTool('listing_list_operations',{description:'List Odoo operations visible to this identity.',inputSchema:z.object({}).strict()},guarded(async()=>{const c=await catalog();return text({contract_version:c.version,operationCount:c.operations.length,operations:c.operations});}));
 server.registerTool('listing_operations_for_modules',{description:'List operations in selected domains.',inputSchema:z.object({modules:z.array(z.string()).min(1).max(100)}).strict()},guarded(async({modules})=>{const c=await catalog(),operations=c.operations.filter(o=>modules.includes(o.domain));return text({contract_version:c.version,operationCount:operations.length,operations});}));
 server.registerTool('listing_call',{description:'Execute an authorized Odoo operation. Writes are never retried automatically.',inputSchema:z.object({operation:z.string().max(100),shop_id:z.number().int().positive().max(9999999999),arguments:z.record(z.string(),z.unknown()),file:z.object({filename:z.string().max(128),mime_type:z.enum(['image/jpeg','image/png']),base64:z.string().max(13981016)}).strict().optional()}).strict()},guarded(async input=>{
  const c=await catalog(),op=c.operations.find(x=>x.operation_id===input.operation);
  if(!op)return text({code:'UNKNOWN_OPERATION'},true);
  let valid;try{valid=new Ajv2020({strict:false,allErrors:false}).compile(op.input_schema)(input.arguments);}catch{return text({code:'INVALID_SCHEMA'},true);}
  if(!valid)return text({code:'INVALID_ARGUMENTS'},true);
  if(op.response_kind==='binary')return text({code:'BINARY_NOT_SUPPORTED',message:'Authenticated temporary file references are not yet available.'},true);
  const envelope={contract_version:c.version,shop_id:input.shop_id,arguments:input.arguments};let body,headers={};
  if(op.request_kind==='multipart'){
   const f=input.file;if(!f||!/^[-A-Za-z0-9_. ]+$/.test(f.filename)||!f.base64||!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(f.base64))return text({code:'INVALID_FILE'},true);
   const bytes=Buffer.from(f.base64,'base64');if(bytes.length>MAX_FILE||bytes.toString('base64')!==f.base64)return text({code:'INVALID_FILE'},true);
   body=new FormData();body.set('request',JSON.stringify(envelope));body.set('file',new Blob([bytes],{type:f.mime_type}),f.filename);
  }else{if(input.file)return text({code:'INVALID_FILE'},true);headers['Content-Type']='application/json';body=JSON.stringify(envelope);}
  const r=await request('/operations/'+encodeURIComponent(op.operation_id)+'/execute',{method:'POST',headers,body});
  if(r.status===409){await r.body?.cancel();return text({code:'CONTRACT_CHANGED',message:'Refresh the operation catalog and decide whether to retry.'},true);}
  if(!r.ok){await r.body?.cancel();return text({code:'BFF_OPERATION_FAILED',http_status:r.status,message:'Verify the result before retrying a write.'},true);}
  if(!/^application\/json(?:;|$)/i.test(r.headers.get('content-type')||'')){await r.body?.cancel();return text({code:'UNSUPPORTED_RESPONSE'},true);}
  return text(JSON.parse(await bounded(r)));
 }));
 return server;
}
module.exports={createPlatformProxyMcpServer};

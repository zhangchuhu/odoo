'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const http=require('node:http');
const {Client}=require('@modelcontextprotocol/sdk/client/index.js');
const {StreamableHTTPClientTransport}=require('@modelcontextprotocol/sdk/client/streamableHttp.js');
const {createHttpServer}=require('../src/httpServer');
const tA='a'.repeat(40),tB='b'.repeat(40);
const unpack=result=>JSON.parse(result.content[0].text);
async function fixture(t){
 const calls=[]; let version='v1', conflict=false;
 const bff=http.createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;calls.push({path:req.url,auth:req.headers.authorization,body});res.setHeader('Content-Type','application/json');
  if(req.url==='/api/meli/operations'){return res.end(JSON.stringify({contract_version:version,operations:[{operation_id:'items.read',domain:'items',title:'Read item',description:'Read',permission:'read',scope:'shop',request_kind:'json',response_kind:'json',exposure:'mcp',input_schema:{type:'object',properties:{item_id:{type:'string',pattern:'^CBT[0-9]+$'}},required:['item_id'],additionalProperties:false}},{operation_id:'pictures.upload',domain:'pictures',title:'Upload',description:'Upload',permission:'write',scope:'shop',request_kind:'multipart',response_kind:'json',exposure:'mcp',input_schema:{type:'object',properties:{},additionalProperties:false}},{operation_id:'shipments.label',domain:'shipments',title:'Label',description:'Label',permission:'read',scope:'shop',request_kind:'json',response_kind:'binary',exposure:'mcp',input_schema:{type:'object',properties:{},additionalProperties:false}}]}));}
  if(req.url==='/api/meli/operations/items.read/execute'){if(conflict){res.writeHead(409);return res.end('{}');}return res.end(JSON.stringify({owner:req.headers.authorization,version:JSON.parse(body).contract_version}));}
  if(req.url==='/api/meli/shops')return res.end(JSON.stringify({shops:[{id:42,name:req.headers.authorization}]}));
  if(req.url==='/api/meli/operations/pictures.upload/execute')return res.end('{"uploaded":true}');
  res.writeHead(404);res.end('{}');
 });
 await new Promise(r=>bff.listen(0,'127.0.0.1',r));
 const s=createHttpServer({erpBaseUrl:`http://127.0.0.1:${bff.address().port}`,credentials:[{mcpToken:tA,odooApiKey:'key-a'},{mcpToken:tB,odooApiKey:'key-b'}]});await new Promise(r=>s.listen(0,'127.0.0.1',r));
 const url=`http://127.0.0.1:${s.address().port}/mcp`,clients=[];
 t.after(async()=>{for(const c of clients)await c.close();await new Promise(r=>s.close(r));await new Promise(r=>bff.close(r));});
 async function connect(token){const c=new Client({name:'test',version:'1'});clients.push(c);await c.connect(new StreamableHTTPClientTransport(new URL(url),{requestInit:{headers:{Authorization:`Bearer ${token}`}}}));return c;}
 return {url,calls,connect,setVersion:v=>version=v,setConflict:v=>conflict=v};
}
test('real Streamable HTTP tools use the authorized catalog and per-user identity',async t=>{
 const f=await fixture(t),a=await f.connect(tA),b=await f.connect(tB);
 assert.equal((await a.listTools()).tools.length,5);
 const shopA=unpack(await a.callTool({name:'listing_shops',arguments:{}}));
 assert.equal(shopA.shops[0].name,'Bearer key-a');
 const list=unpack(await a.callTool({name:'listing_list_operations',arguments:{}}));assert.equal(list.operations[0].operation_id,'items.read');
 const invoke=c=>c.callTool({name:'listing_call',arguments:{operation:'items.read',shop_id:1,arguments:{item_id:'CBT123'}}});
 const [ra,rb]=await Promise.all([invoke(a),invoke(b)]);assert.equal(unpack(ra).owner,'Bearer key-a');assert.equal(unpack(rb).owner,'Bearer key-b');
 assert.equal(f.calls.filter(x=>x.path.endsWith('/execute')).length,2);
 f.setVersion('v2');assert.equal(unpack(await invoke(a)).version,'v2');
});
test('contract conflict is reported without replaying an operation',async t=>{
 const f=await fixture(t),c=await f.connect(tA);f.setConflict(true);
 const result=await c.callTool({name:'listing_call',arguments:{operation:'items.read',shop_id:1,arguments:{item_id:'CBT123'}}});
 assert.equal(result.isError,true);assert.equal(unpack(result).code,'CONTRACT_CHANGED');
 assert.equal(f.calls.filter(x=>x.path.endsWith('/execute')).length,1);
});
test('files are bounded and malformed data or binary downloads never reach execute',async t=>{
 const f=await fixture(t),c=await f.connect(tA);
 const call=(operation,file)=>c.callTool({name:'listing_call',arguments:{operation,shop_id:1,arguments:{},...(file?{file}:{})}});
 assert.equal((await call('pictures.upload',{filename:'../bad.jpg',mime_type:'image/jpeg',base64:'YWJj'})).isError,true);
 assert.equal((await call('pictures.upload',{filename:'good.jpg',mime_type:'image/jpeg',base64:'!!!'})).isError,true);
 assert.equal((await call('shipments.label')).isError,true);
 assert.equal(f.calls.some(x=>x.path.endsWith('/execute')),false);
 assert.equal(unpack(await call('pictures.upload',{filename:'good.jpg',mime_type:'image/jpeg',base64:'YWJj'})).uploaded,true);
 const body=f.calls.find(x=>x.path.endsWith('/execute')).body;
 assert.match(body,/name="request"/);assert.match(body,/filename="good.jpg"/);assert.match(body,/"shop_id":1/);
});
test('reject untrusted hosts, origins, credentials, paths and invalid operation parameters',async t=>{
 const f=await fixture(t);assert.equal((await fetch(f.url,{method:'POST'})).status,401);assert.equal((await fetch(f.url,{headers:{Authorization:`Bearer ${tA}`,Origin:'https://evil.test'}})).status,403);
 const c=await f.connect(tA);const before=f.calls.length;
 const bad=await c.callTool({name:'listing_call',arguments:{operation:'items.read',shop_id:1,arguments:{item_id:'../bad'}}});assert.equal(bad.isError,true);assert.equal(f.calls.slice(before).some(x=>x.path.endsWith('/execute')),false);
 const unknown=await c.callTool({name:'listing_call',arguments:{operation:'../admin',shop_id:1,arguments:{}}});assert.equal(unknown.isError,true);
});

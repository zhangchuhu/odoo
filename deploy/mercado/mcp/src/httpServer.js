'use strict';
const http = require('node:http');
const { timingSafeEqual, createHash } = require('node:crypto');
const { StreamableHTTPServerTransport } = require('@modelcontextprotocol/sdk/server/streamableHttp.js');
const { createPlatformProxyMcpServer } = require('./createPlatformProxyMcpServer');
const { createErpBffClient } = require('./erpBffClient');
const MAX_BODY = 14 * 1024 * 1024;
const digest = value => createHash('sha256').update(value).digest();

function validateConfig(config) {
  if (!Array.isArray(config.credentials) || !config.credentials.length) throw new Error('credentials mapping is required');
  const seen = new Set();
  for (const identity of config.credentials) {
    if (typeof identity.mcpToken !== 'string' || !/^[A-Za-z0-9_-]{32,}$/.test(identity.mcpToken) || seen.has(identity.mcpToken)) throw new Error('MCP tokens must be unique and at least 32 URL-safe characters');
    seen.add(identity.mcpToken);
    createErpBffClient({erpBaseUrl: config.erpBaseUrl, token: identity.odooApiKey});
  }
  for (const field of ['allowedHosts','allowedOrigins']) {
    if (config[field] !== undefined && (!Array.isArray(config[field]) || config[field].some(x=>typeof x !== 'string'))) throw new Error(`Invalid ${field}`);
  }
}
function readBody(req) {
  return new Promise((resolve,reject)=>{
    let size=0, exceeded=false;const chunks=[];
    req.on('data',chunk=>{size+=chunk.length;if(size>MAX_BODY){if(!exceeded){exceeded=true;reject(Object.assign(new Error('Request too large'),{status:413}));}return;}chunks.push(chunk);});
    req.on('end',()=>{if(exceeded)return;try{resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));}catch{reject(Object.assign(new Error('Invalid JSON'),{status:400}));}});
    req.on('error',reject);
    req.on('aborted',()=>reject(new Error('Request aborted')));
  });
}
function createHttpServer(config) {
  validateConfig(config);
  // Keep identity immutable and private to the HTTP service, never in tool arguments.
  const identities=config.credentials.map(x=>({hash:digest(x.mcpToken),key:x.odooApiKey.trim()}));
  const server=http.createServer(async(req,res)=>{
    const fail=(status,message)=>{if(res.headersSent||res.destroyed)return;res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store',...(status===401?{'WWW-Authenticate':'Bearer'}:{})});res.end(JSON.stringify({error:message}));};
    const port=server.address()?.port;
    const hosts=config.allowedHosts || [`127.0.0.1:${port}`,`localhost:${port}`,`[::1]:${port}`];
    if(!hosts.includes(req.headers.host))return fail(403,'Host not allowed');
    if(req.headers.origin && !(config.allowedOrigins||[]).includes(req.headers.origin))return fail(403,'Origin not allowed');
    if(req.url==='/health' && req.method==='GET'){res.writeHead(200,{'Content-Type':'application/json'});return res.end('{"status":"pass"}');}
    if(req.url!=='/mcp')return fail(404,'Not found');
    const match=/^Bearer ([A-Za-z0-9_-]+)$/i.exec(req.headers.authorization||'');
    const hash=digest(match?.[1]||'');let identity;
    for(const candidate of identities)if(timingSafeEqual(candidate.hash,hash))identity=candidate;
    if(!match||!identity)return fail(401,'Invalid MCP access token');
    if(req.method!=='POST'){res.setHeader('Allow','POST');return fail(405,'Stateless MCP supports POST only');}
    if((req.headers['content-type']||'').split(';')[0].trim()!=='application/json')return fail(415,'JSON required');
    if(Number(req.headers['content-length'])>MAX_BODY){req.resume();return fail(413,'Request too large');}
    let mcp,transport;
    try {
      const body=await readBody(req);
      mcp=createPlatformProxyMcpServer({erpBaseUrl:config.erpBaseUrl,token:identity.key});
      transport=new StreamableHTTPServerTransport({sessionIdGenerator:undefined,enableJsonResponse:true});
      res.once('close',()=>{void mcp.close().catch(()=>{});});
      await mcp.connect(transport);
      await transport.handleRequest(req,res,body);
    } catch(error) {
      fail(error.status||500,error.status?error.message:'MCP request failed');
      if(mcp)await mcp.close().catch(()=>{});
    }
  });
  server.requestTimeout=150000;
  server.headersTimeout=15000;
  return server;
}
module.exports={createHttpServer,validateConfig,MAX_BODY};

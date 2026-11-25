'use strict';
const fs=require('node:fs');
const {createHttpServer}=require('./httpServer');
try {
 const path=process.env.MCP_CONFIG;
 if(!path)throw Error('MCP_CONFIG must point to a private JSON configuration file');
 const stat=fs.statSync(path);
 if(!stat.isFile()||(stat.mode&0o077)!==0||(process.getuid&&stat.uid!==process.getuid()))throw Error('Configuration must be an owner-only file');
 const config=JSON.parse(fs.readFileSync(path,'utf8'));
 const host=config.host||'127.0.0.1',port=config.port||8071;
 if(host!=='127.0.0.1'||!Number.isInteger(port)||port<1||port>65535)throw Error('Only a local IPv4 listener with a valid port is supported');
 const server=createHttpServer(config);
 server.listen(port,host,()=>process.stdout.write(`MCP listening on ${host}:${port}\n`));
 for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>server.close(()=>process.exit(0)));
 server.on('error',()=>{process.stderr.write('MCP listener failed\n');process.exitCode=1;});
}catch(e){process.stderr.write(`MCP startup failed: ${e.message}\n`);process.exitCode=1;}

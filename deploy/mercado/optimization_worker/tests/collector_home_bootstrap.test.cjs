const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');
for (const platform of ['chrome_extension','shein_extension']) test(platform+' opens a bound collector from its home page',async()=>{
 const source=fs.readFileSync(path.join(__dirname,'..',platform,'collector.js'),'utf8');
 const body=source.match(/  async function ensureDedicatedWindow\(\) \{([\s\S]*?)\n  \}/)[1];
 let opened,closed=false;const url=platform==='chrome_extension'?'https://www.mercadolibre.com.mx/':'https://us.shein.com/';
 const chrome={tabs:{query:async()=>[{id:7,url}]},runtime:{getURL:()=> 'chrome-extension://fixture/collector.html'},windows:{create:async value=>{opened=value}}};
 const fn=new Function('tabId','chrome','window','isSearchPage','canonicalProductId',`return (async()=>{${body}})()`);
 assert.equal(await fn(0,chrome,{close(){closed=true}},()=>false,()=>null),false);assert.match(opened.url,/tab=7/);assert.equal(closed,true);
});

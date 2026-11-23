'use strict';
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');

for (const [ext, platform, product, cdn] of [
  ['chrome_extension', 'mercado-libre-mx', 'MLM13', 'http2.mlstatic.com'],
  ['shein_extension', 'shein-us', '13', 'img.ltwebstatic.com'],
]) {
  const {downloadGallery} = require('../'+ext+'/gallery_download.js');
  const identity = platform+':'+product;
  const request = '2f2051c9-cd0e-4232-a2a3-5dc4a163f45a';
  const draft = {identity, total:2, confirmed:'d'.repeat(64), images:[1,2].map(n=>'https://'+cdn+'/'+n+'.jpg')};

  function fixture(t) {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(),'gallery-download-test-'));
    t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
    const state = {receipts:[], files:new Map(), storage:new Map(), started:[], scopedLookups:[], verifies:0};
    const api={runtime:{id:'test-extension'},storage:{local:{
      get:async key=>({[key]:state.storage.get(key)}), set:async obj=>Object.entries(obj).forEach(([key,val])=>state.storage.set(key,val)),
      remove:async key=>state.storage.delete(key),
    }},downloads:{
      download:async ({url,filename,saveAs,conflictAction})=>{
        assert.equal(saveAs,false);assert.equal(conflictAction,'uniquify');
        const file=path.join(directory,filename);fs.mkdirSync(path.dirname(file),{recursive:true}); fs.writeFileSync(file,Buffer.from('test raster '+url));
        const id=state.started.push(url);state.files.set(id,{id,url,finalUrl:url,byExtensionId:'test-extension',state:'complete',exists:true,
          filename:file,fileSize:fs.statSync(file).size,danger:'safe'});return id;
      },search:async options=>{
        assert.ok(options.id || (options.url && options.filenameRegex));state.scopedLookups.push(options);
        return [...state.files.values()].filter(item=>options.id ? item.id===options.id : options.url===item.url && new RegExp(options.filenameRegex).test(item.filename));
      },
    }};
    async function checkpoint(op, data) {
      const order=state.receipts.length;
      assert.equal(data.identity,identity);
      const token=String(order+1).padStart(32,'0'); const filename='Odoo-Research/'+request+'/5/'+token+'.jpg';
      if(op==='gallery_download_next') return order===draft.total ? {ok:true,identity,stage:'complete'} :
        {ok:true,identity,stage:'image',order,source:draft.images[order],filename,token};
      assert.equal(op,'gallery_download_saved');assert.equal(data.order,order);assert.equal(data.token,token);
      assert.equal(data.filename,filename);assert.equal(data.source,draft.images[order]);assert.equal(data.final_url,data.source);
      const raw=fs.readFileSync(path.join(directory,data.filename));assert.equal(data.size,raw.length);
      const receipt={ok:true,identity,order,artifact_id:String(800+order),digest:crypto.createHash('sha256').update(raw).digest('hex')};
      state.receipts.push(receipt);return receipt;
    }
    const run=(opts={})=>downloadGallery({api,draft,requestId:request,platform,checkpoint,verifyPage:async()=>{state.verifies++;},...opts});
    return {api,state,checkpoint,run};
  }

  test(platform+' saves verified originals to scoped slots in order and reads back each receipt',async t=>{
    const {state,run}=fixture(t);
    assert.equal(await run(),2);assert.deepEqual(state.started,draft.images);
    assert.deepEqual(state.receipts.map(x=>x.order),[0,1]);assert.ok(state.verifies>=4);
    assert.equal(await run(),2);assert.equal(state.started.length,2);
  });
  test(platform+' collector button saves only its confirmed active candidate and reports business completion',async t=>{
    const {api,state,checkpoint}=fixture(t);
    const url=platform==='mercado-libre-mx' ? 'https://www.mercadolibre.com.mx/p/'+product : 'https://us.shein.com/dress-p-'+product+'.html';
    const page={identity,page_url:url,variation:[],rows:[{url:draft.images[0],width:1000,height:1200}]};
    const binding={request_id:request,session_id:'session',tab_id:3,window_id:9};
    const original={...draft,variant:JSON.stringify([url,[],[]])};
    state.storage.set('verified-gallery:'+request+':'+identity,original);
    api.tabs={get:async()=>({id:3,windowId:9,url,active:true}),query:async()=>[{id:3}],sendMessage:async()=>page};
    const nodes=new Map(); const get=id=>{
      if(!nodes.has(id)) nodes.set(id,{dataset:{platform},value:'',textContent:'',replaceChildren(){},append(){},addEventListener(type,fn){this[type]=fn;}});
      return nodes.get(id);
    };
    const load=(ctx,name)=>require('node:vm').runInNewContext(fs.readFileSync(path.join(__dirname,'..',ext,name),'utf8'),ctx);
    const ctx={URL,location:{href:'chrome-extension://test/collector.html?tab=3'},document:{getElementById:get,createElement:()=>({append(){}})},chrome:api,
      currentGalleryBinding:async()=>{if(state.receipts.length===2)throw Error('next candidate required');return binding;},collectorCheckpoint:checkpoint,
      navigator:{locks:{request:async(key,options,action)=>{assert.equal(key,'gallery-download:'+request+':'+identity);assert.equal(options.ifAvailable,true);return action({});}}}};
    load(ctx,'gallery_download.js');load(ctx,'gallery_capture.js');get('capture-product').value=product;
    await get('capture-restore').click();assert.equal(get('capture-download').hidden,false);
    await get('capture-download').click();assert.match(get('capture-status').textContent,/已全部入库/);assert.equal(state.receipts.length,2);
    await get('capture-download').click();assert.match(get('capture-status').textContent,/全部入库/);assert.equal(state.started.length,2);
  });
  test(platform+' after a lost upload reply the host decides the next index',async t=>{
    const {state,checkpoint,run}=fixture(t);
    await assert.rejects(run({checkpoint:async(op,data)=>{const ack=await checkpoint(op,data);if(op==='gallery_download_saved') throw Error('lost reply');return ack;}}));
    assert.equal(state.receipts.length,1);assert.equal(state.started.length,1);
    assert.equal(await run(),2);assert.equal(state.started.length,2);
  });
  test(platform+' reuses the exact Chrome download when the popup lost its download ID',async t=>{
    const {state,api,run}=fixture(t);
    const start=api.downloads.download;api.downloads.download=async opts=>{await start(opts);throw Error('popup closed');};
    await assert.rejects(run());api.downloads.download=start;
    assert.equal(await run(),2);assert.deepEqual(state.started,draft.images);
    assert.ok(state.scopedLookups.some(x=>x.url===draft.images[0] && x.filenameRegex));
  });
  test(platform+' explicitly suggests the host filename only for the exact Chrome download',async t=>{
    const {api,state,run}=fixture(t);const handlers=new Set();
    api.downloads.onDeterminingFilename={addListener:f=>handlers.add(f),removeListener:f=>handlers.delete(f)};
    const download=api.downloads.download;
    api.downloads.download=async options=>{
      const id=await download(options);
      const row=state.files.get(id), filename=row.filename;
      row.filename=path.join(path.dirname(filename),path.basename(row.url));row.state='in_progress';
      for(const handler of handlers) {
        handler({id:id+99, byExtensionId:'other'},suggestion=>assert.equal(suggestion,undefined));
        handler({id:id+100,byExtensionId:api.runtime.id,url:options.url},suggestion=>assert.equal(suggestion,undefined));
        handler({id,byExtensionId:api.runtime.id,url:options.url},suggestion=>{
          assert.deepEqual(suggestion,{filename:options.filename,conflictAction:'uniquify'});row.filename=filename;row.state='complete';
        });
      }
      return id;
    };
    assert.equal(await run({delay:()=>new Promise(resolve=>setImmediate(resolve))}),2);assert.equal(handlers.size,0);
  });
  test(platform+' repairs a completed pre-fix CDN-named file once and refuses a fresh override',async t=>{
    const {api,state,run}=fixture(t);
    const key='gallery-download:'+request+':'+identity, order=0, token=String(order+1).padStart(32,'0');
    const filename='Odoo-Research/'+request+'/5/'+token+'.jpg', source=draft.images[0];
    state.storage.set(key,{token,source,filename,id:42});
    state.files.set(42,{id:42,url:source,byExtensionId:'test-extension',filename:'/Downloads/1.jpg',state:'complete',finalUrl:source});
    const start=api.downloads.download;api.downloads.download=async options=>{const id=await start(options);state.files.get(id).filename='/Downloads/1.jpg';return id;};
    await assert.rejects(run(),/gallery_download_filename_mismatch/);
    await assert.rejects(run(),/gallery_download_filename_mismatch/);assert.equal(state.started.length,1);assert.equal(state.receipts.length,0);
  });
  test(platform+' rejects wrong source, slot escape, foreign redirects and modified download paths',async t=>{
    for(const bad of [{source:'https://unrelated.example/a.jpg'}, {filename:'Odoo-Research/'+request+'/5/../evil.jpg'}]) {
      const {checkpoint,state,run}=fixture(t);
      await assert.rejects(run({checkpoint:async(op,data)=>({...await checkpoint(op,data),...bad})}));assert.equal(state.started.length,0);
    }
    for (const [field,code] of [[{finalUrl:'https://unrelated.example/p.jpg'},'gallery_download_redirect'],
      [{byExtensionId:'other'},'gallery_download_owner_mismatch'],[{filename:'/tmp/unrelated.jpg'},'gallery_download_filename_mismatch'],
      [{finalUrl:''},'gallery_download_final_url_invalid'],[{exists:false},'gallery_download_missing'],
      [{fileSize:0},'gallery_download_size_invalid'],[{danger:'uncommon'},'gallery_download_security']]) {
      const {api,run,state}=fixture(t);const search=api.downloads.search;api.downloads.search=async opts=>(await search(opts)).map(v=>({...v,...field}));
      await assert.rejects(run(),new RegExp('^Error: '+code+'：'));assert.equal(state.receipts.length,0);
    }
  });
}

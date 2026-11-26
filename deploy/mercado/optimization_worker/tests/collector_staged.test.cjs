const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const context={__MERCADO_COLLECTOR_TEST__:{},URL};vm.createContext(context);
vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/collector.js','utf8'),context);
const api=context.__MERCADO_COLLECTOR_TEST__;
test('visual-v2 compares card business fields while retaining backend screenshot coordinates',()=>{
 const c={__MERCADO_COLLECTOR_TEST__:{},URL,equal:(a,b)=>JSON.stringify(a)===JSON.stringify(b)};vm.createContext(c);
 vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/collector.js','utf8'),c);
 const card={query:'uno',rank:1,is_ad:false,title:'dress',url:'https://articulo.mercadolibre.com.mx/MLM1',product_id:'MLM1',thumbnail_url:null};
 assert.equal(c.__MERCADO_COLLECTOR_TEST__.sameCards([card],[{...card,bbox:[1,2,3,4]}],'visual-v2'),true);
 assert.equal(c.__MERCADO_COLLECTOR_TEST__.sameCards([card],[{...card,title:'different',bbox:[1,2,3,4]}],'visual-v2'),false);
});
test('visual-v2 keeps first-seen order and does not prioritize repeated products',()=>{
 const queries=['uno','dos','tres'];
 const session=api.createSession({taskRecordId:'rec123',platform:'mercado-libre-mx',queries,cardLimit:100,resultLimit:2,strategy:'visual-v2'});
 const card=(query,id)=>({query,rank:1,is_ad:false,title:'vestido',product_id:'MLM'+id,url:'https://articulo.mercadolibre.com.mx/MLM'+id,thumbnail_url:null});
 api.addSearchObservations(session,{query:'uno',cards:[card('uno',1)]});
 api.addSearchObservations(session,{query:'dos',cards:[card('dos',2)]});
 api.addSearchObservations(session,{query:'tres',cards:[card('tres',2)]});
 session.exhaustedQueries=[0,1,2];
 assert.equal(api.nextExpectedIdentity(session),'mercado-libre-mx:MLM1');
});
test('visual-v2 only updates browser session after durable batch confirmation',async()=>{
 const session=api.createSession({taskRecordId:'rec123',platform:'mercado-libre-mx',queries:['uno','dos','tres'],cardLimit:100,resultLimit:2,strategy:'visual-v2'});
 const cards=[{query:'uno',rank:1,is_ad:false,title:'vestido',product_id:'MLM1',url:'https://articulo.mercadolibre.com.mx/MLM1',thumbnail_url:null}];
 await assert.rejects(api.persistVisualCards(session,0,0,cards,async()=>false));
 assert.equal(session.queryBlocks[0].observations.length,0);
 await api.persistVisualCards(session,0,0,cards,async()=>true);
 assert.equal(session.queryBlocks[0].observations.length,1);
});
test('staged collector reviews singleton candidates and requires expansion before partial submission',()=>{
 const queries=['uno','dos','tres'];
 const session=api.createSession({taskRecordId:'rec123',platform:'mercado-libre-mx',queries,cardLimit:30,resultLimit:15,strategy:'staged-v1'});
 for(let group=0;group<3;group++){
  const cards=Array.from({length:30},(_,i)=>({query:queries[group],rank:i+1,is_ad:false,title:'vestido',product_id:'MLM'+(group*100+i+1),url:'https://articulo.mercadolibre.com.mx/MLM'+(group*100+i+1),thumbnail_url:null}));
  api.addSearchObservations(session,{query:queries[group],cards});
 }
 assert.equal(api.nextExpectedIdentity(session),'mercado-libre-mx:MLM1');
 assert.throws(()=>api.buildEvidence(session));
 for(let i=0;i<40;i++){
  const identity=api.nextExpectedIdentity(session);
  api.addDetailOutcome(session,{identity,status:'rejected',reason:'detail_inaccessible',detail_url:null,product_id:null,title:null,category:null,visual_category_code:null,sold_display:null,reviews_display:null,rating_display:null,match_level:null,visual_features:null});
 }
 assert.throws(()=>api.buildEvidence(session));
 const extended=api.createSession({taskRecordId:'rec123',platform:'mercado-libre-mx',queries:[...queries,'cuatro'],cardLimit:30,resultLimit:15,strategy:'staged-v1'});
 for(const block of session.queryBlocks)api.addSearchObservations(extended,{query:block.query,cards:block.observations});
 const cards=Array.from({length:30},(_,i)=>({query:'cuatro',rank:i+1,is_ad:false,title:'vestido',product_id:'MLM'+(1000+i),url:'https://articulo.mercadolibre.com.mx/MLM'+(1000+i),thumbnail_url:null}));
 api.addSearchObservations(extended,{query:'cuatro',cards});
 for(const detail of session.details)api.addDetailOutcome(extended,detail);
 assert.equal(api.nextExpectedIdentity(extended),'mercado-libre-mx:MLM1000');
 const short=api.createSession({taskRecordId:'rec123',platform:'mercado-libre-mx',queries:[...queries,'cuatro'],cardLimit:30,resultLimit:15,strategy:'staged-v1'});
 for(const block of session.queryBlocks)api.addSearchObservations(short,{query:block.query,cards:block.observations});
 api.addSearchObservations(short,{query:'cuatro',cards:cards.slice(0,1)});
 short.exhaustedQueries=[3];
 for(const detail of session.details)api.addDetailOutcome(short,detail);
 api.addDetailOutcome(short,{identity:api.nextExpectedIdentity(short),status:'rejected',reason:'detail_inaccessible',detail_url:null,product_id:null,title:null,category:null,visual_category_code:null,sold_display:null,reviews_display:null,rating_display:null,match_level:null,visual_features:null});
 assert.equal(api.buildEvidence(short).queries[3].observations.length,1);
});
test('receipt version 2 permits only bounded staged query indices',()=>{
 const ctx={URL};vm.createContext(ctx);
 vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/binding_validation.js','utf8'),ctx);
 const challenge={version:2,request_id:'request',nonce:'a'.repeat(32),lease_epoch:1,plan_digest:'b'.repeat(64),platform:'mercado-libre-mx',query_index:3,query:'vestido'};
 const tab={id:1,windowId:2,url:'https://listado.mercadolibre.com.mx/vestido'};
 const page={page_url:tab.url,search_query:'vestido'};
 assert.equal(ctx.CollectorBinding.makeReceipt(challenge,'c'.repeat(32),tab,page,1,new Date().toISOString()).query_index,3);
 assert.throws(()=>ctx.CollectorBinding.makeReceipt({...challenge,version:1},'c'.repeat(32),tab,page,1,new Date().toISOString()));
 assert.throws(()=>ctx.CollectorBinding.makeReceipt({...challenge,query_index:6},'c'.repeat(32),tab,page,1,new Date().toISOString()));
});
test('visual-v2 receipt permits up to nine Mercado queries',()=>{
 const ctx={URL};vm.createContext(ctx);
 vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/binding_validation.js','utf8'),ctx);
 const challenge={version:3,request_id:'request',nonce:'a'.repeat(32),lease_epoch:1,plan_digest:'b'.repeat(64),platform:'mercado-libre-mx',query_index:8,query:'vestido'};
 const tab={id:1,windowId:2,url:'https://listado.mercadolibre.com.mx/vestido'};
 const page={page_url:tab.url,search_query:'vestido'};
 assert.equal(ctx.CollectorBinding.makeReceipt(challenge,'c'.repeat(32),tab,page,1,new Date().toISOString()).query_index,8);
 assert.equal(ctx.CollectorBinding.makeReceipt({...challenge,version:4},'c'.repeat(32),tab,page,1,new Date().toISOString()).version,4);
 assert.throws(()=>ctx.CollectorBinding.makeReceipt({...challenge,version:4,platform:'shein-us'},'c'.repeat(32),tab,page,1,new Date().toISOString()));
 assert.throws(()=>ctx.CollectorBinding.makeReceipt({...challenge,query_index:9},'c'.repeat(32),tab,page,1,new Date().toISOString()));
 assert.throws(()=>ctx.CollectorBinding.makeReceipt({...challenge,platform:'shein-us'},'c'.repeat(32),tab,page,1,new Date().toISOString()));
});
test('recovery retains confirmed details when expansion search has not started',async()=>{
 const q=['uno','dos','tres','cuatro'],nodes=new Map();
 const get=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',hidden:false,addEventListener(_n,fn){this.action=fn}});return nodes.get(id)};
 get('record-id').value='rec123';get('card-limit').value='30';
 const cards=query=>Array.from({length:30},(_,i)=>({query,rank:i+1,is_ad:false,title:'Dress',product_id:'MLM'+(i+1),url:'https://articulo.mercadolibre.com.mx/MLM'+(i+1),thumbnail_url:null}));
 const details=[1,2].map(i=>({identity:'mercado-libre-mx:MLM'+i,status:'rejected',reason:'detail_inaccessible',detail_url:null,product_id:null,title:null,category:null,visual_category_code:null,sold_display:null,reviews_display:null,rating_display:null,match_level:null,visual_features:null}));
 let stored;
 const old={taskRecordId:'rec123',platform:'mercado-libre-mx',strategy:'staged-v1',queries:q.slice(0,3),cardLimit:30,resultLimit:5,queryBlocks:q.slice(0,3).map(query=>({query,observations:cards(query)})),details:[]};
 class Persistence{async load(){return old} async save(_tab,_sid,session){stored=session}}
 const c={URL,Date,setTimeout,clearTimeout,equal:(a,b)=>JSON.stringify(a)===JSON.stringify(b),SessionPersistence:Persistence,location:{href:'chrome-extension://id/collector.html?tab=7'},document:{getElementById:get},chrome:{storage:{local:{}},runtime:{sendMessage:async()=>({session_id:'a'.repeat(32)})}},collectorCheckpoint:async(_op,{query_index:i})=>({task:'rec123',platform:'mercado-libre-mx',strategy:'staged-v1',queries:q,result_limit:5,query_index:i,cards:i<3?cards(q[i]):null,acknowledged:i<3,details:i===2?details:[]})};
 vm.createContext(c);vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/collector.js','utf8'),c);
 await new Promise(setImmediate);await get('recover-session').action();
 assert.ok(stored,get('error').textContent);assert.equal(stored.details.length,2);
 assert.match(get('status').textContent,/已确认 3\/4.*已核验 2/);
});
test('visual-v2 restores a short completed homepage with the configured cap',async()=>{
 const q=['uno','dos','tres'],nodes=new Map();
 const get=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',hidden:false,addEventListener(_n,fn){this.action=fn}});return nodes.get(id)};
 const cards=query=>[{query,rank:1,is_ad:false,title:'Dress',product_id:'MLM1',url:'https://articulo.mercadolibre.com.mx/MLM1',thumbnail_url:null}];
 let stored;
 class Persistence{async load(){return null} async save(_tab,_sid,session){stored=session}}
 const c={URL,Date,setTimeout,clearTimeout,equal:(a,b)=>JSON.stringify(a)===JSON.stringify(b),SessionPersistence:Persistence,location:{href:'chrome-extension://id/collector.html?tab=7'},document:{getElementById:get},chrome:{storage:{local:{}},runtime:{sendMessage:async()=>({session_id:'a'.repeat(32)})}},collectorCheckpoint:async(_op,{query_index:i})=>({task:'rec123',platform:'mercado-libre-mx',strategy:'visual-v2',queries:q,result_limit:5,query_index:i,cards:cards(q[i]),acknowledged:true,exhausted:true,details:[]})};
 vm.createContext(c);vm.runInContext(fs.readFileSync('deploy/mercado/optimization_worker/chrome_extension/collector.js','utf8'),c);
 await new Promise(setImmediate);await get('recover-session').action();
 assert.ok(stored,get('error').textContent);assert.equal(stored.cardLimit,100);assert.equal(stored.acknowledgedQueries.length,3);
});

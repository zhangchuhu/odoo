const test=require('node:test'),assert=require('node:assert/strict'); const {QueryGate}=require('../query_gate.cjs');
test('requires live backend verified matching target, session and query before one collection',()=>{
 let clock=1000;const gate=new QueryGate(()=>clock); assert.equal(gate.consume(3,'session','vestido',0),false);
 gate.grant({tab_id:3,session_id:'session',query:'vestido',query_index:0},true);
 assert.equal(gate.consume(4,'session','vestido',0),false);assert.equal(gate.consume(3,'other','vestido',0),false);assert.equal(gate.consume(3,'session','otra',0),false);
 assert.equal(gate.consume(3,'session','vestido',0),true);assert.equal(gate.consume(3,'session','vestido',0),false);
 gate.grant({tab_id:3,session_id:'session',query:'vestido',query_index:0},true); clock+=30001;assert.equal(gate.consume(3,'session','vestido',0),false);
});
test('unacknowledged receipt never grants collection',()=>{const g=new QueryGate(()=>0);g.grant({tab_id:1,session_id:'s',query:'a',query_index:0},false);assert.equal(g.consume(1,'s','a',0),false)});
test('SHEIN extraction must retain exact visible query on every page',()=>{const g=new QueryGate();assert.equal(g.matches('lace dress',{search_query:'lace dress'}),true);assert.equal(g.matches('lace dress',{search_query:null}),false);assert.equal(g.matches('lace dress',{search_query:'other'}),false)});

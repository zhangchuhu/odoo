const test=require('node:test');const assert=require('node:assert/strict');const {AnchorReconnect}=require('../chrome_extension/anchor_reconnect.js');
test('reconnects after broker rotation using original identity',async()=>{let attempts=0,tick;const identity={request_id:'r',session_id:'a'.repeat(32),window_id:3,marker:'b'.repeat(32)};
 const r=new AnchorReconnect(identity,async i=>{assert.deepEqual(i,identity);attempts++},async()=>identity,(f)=>{tick=f;return 1},()=>{});r.start();await tick();await tick();assert.equal(attempts,2);r.stop()});
test('stops rather than reconnecting after browser session change',async()=>{let tick,calls=0;const i={request_id:'r',session_id:'a'.repeat(32),window_id:3,marker:'b'.repeat(32)};
 const r=new AnchorReconnect(i,async()=>{calls++},async()=>({...i,session_id:'c'.repeat(32)}),(f)=>{tick=f;return 1},()=>{});r.start();await tick();assert.equal(calls,0);assert.equal(r.stopped,true)});
test('stops bounded retries when broker never returns',async()=>{let tick;const i={request_id:'r',session_id:'a'.repeat(32),window_id:3,marker:'b'.repeat(32)};const r=new AnchorReconnect(i,async()=>{throw Error('gone')},async()=>i,(f)=>{tick=f;return 1},()=>{});r.start();for(let n=0;n<12;n++)await tick();assert.equal(r.stopped,true)});
test('uses browser global as receiver for native timers',()=>{
 const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
 const ctx=vm.createContext({});
 vm.runInContext(`globalThis.setInterval=function(){if(this!==globalThis)throw new TypeError('Illegal invocation');return 7};globalThis.clearInterval=function(){if(this!==globalThis)throw new TypeError('Illegal invocation')}`,ctx);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../chrome_extension/anchor_reconnect.js'),'utf8'),ctx);
 vm.runInContext(`const reconnect=new AnchorReconnect({},async()=>{},async()=>({})); reconnect.start(); reconnect.stop()`,ctx);
});

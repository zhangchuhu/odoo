const test=require('node:test');const assert=require('node:assert/strict');
const {openAnchorGuard}=require('../chrome_extension/anchor_guard.js');
test('opens guard bound to request/session/window and sends heartbeats',async()=>{
 let messages=[],receiver,timer,cleared=false;const port={postMessage:m=>messages.push(m),onMessage:{addListener:f=>receiver=f},onDisconnect:{addListener:()=>{}}};
 const guard=openAnchorGuard({runtime:{connectNative:()=>port}},'host',{request_id:'r',session_id:'a'.repeat(32),window_id:1,marker:'b'.repeat(32)},(fn)=>{timer=fn;return 3},()=>{cleared=true});
 assert.equal(messages[0].operation,'anchor_guard_open');receiver({ok:true});await guard.ready;timer();assert.deepEqual(messages[1],{operation:'guard_ping'});guard.close();assert.ok(cleared);
});
test('rejects readiness on disconnect before confirmation',async()=>{
 let disconnected;const port={postMessage:()=>{},onMessage:{addListener:()=>{}},onDisconnect:{addListener:f=>disconnected=f}};
 const guard=openAnchorGuard({runtime:{connectNative:()=>port}},'host',{});disconnected();await assert.rejects(guard.ready,/disconnected/);
});
test('rejects readiness and closes when initial native send throws',async()=>{
 let closed=0;const port={postMessage:()=>{throw Error('native down')},disconnect:()=>closed++,onMessage:{addListener:()=>{}},onDisconnect:{addListener:()=>{}}};
 const guard=openAnchorGuard({runtime:{connectNative:()=>port}},'host',{});await assert.rejects(guard.ready,/disconnected/);assert.equal(closed,1);
});

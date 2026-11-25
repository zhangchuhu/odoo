'use strict';
function openAnchorGuard(api,host,identity,schedule=setInterval,cancel=clearInterval){
 const port=api.runtime.connectNative(host);let timer=null,closed=false,settle;
 const ready=new Promise((resolve,reject)=>{settle={resolve,reject};});
 const deadline=setTimeout(()=>{if(settle){settle.reject(Error('guard timeout'));settle=null;close()}},12000);
 const close=()=>{if(closed)return;closed=true;clearTimeout(deadline);if(settle){settle.reject(Error('guard disconnected'));settle=null}if(timer!==null)cancel(timer);try{port.disconnect?.()}catch(_){}};
 port.onMessage.addListener(m=>{if(m?.ok!==true){close();return}if(settle){clearTimeout(deadline);settle.resolve();settle=null}if(timer===null)timer=schedule(()=>{try{port.postMessage({operation:'guard_ping'})}catch(_){close()}},5000)});
 port.onDisconnect.addListener(close);
 try{port.postMessage({operation:'anchor_guard_open',...identity})}catch(_){close()}
 return {close,ready,alive:()=>!closed&&settle===null};
}
globalThis.AnchorGuard={openAnchorGuard};if(typeof module!=='undefined')module.exports={openAnchorGuard};

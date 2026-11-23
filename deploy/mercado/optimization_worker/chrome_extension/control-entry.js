'use strict';
(() => {
 const id=Number(new URL(location.href).searchParams.get('tab'));
 if(Number.isSafeInteger(id) && id>0)return;
 const section=document.getElementById('control-entry');
 const button=document.getElementById('control-entry-button');
 const status=document.getElementById('control-entry-status');
 for(const node of document.body.children) if(node!==section && node.tagName!=='SCRIPT') node.hidden=true;
 section.hidden=false;
 button.addEventListener('click',async()=>{
  if(button.disabled)return;button.disabled=true;status.textContent='正在打开专用窗口';
  try {
   const result=await chrome.runtime.sendMessage({type:'dedicated_window_prepare'});
   if(!Number.isSafeInteger(result?.windowId) || result.windowId<=0 || !Number.isSafeInteger(result?.tabId) || result.tabId<=0)throw Error('window');
   await chrome.tabs.update(result.tabId,{active:true});
   await chrome.windows.update(result.windowId,{focused:true});
   status.textContent='请在控制页确认当前任务与窗口';
  }catch(_){status.textContent='未能打开专用窗口，请检查扩展连接后重试';}
  finally{button.disabled=false;}
 });
})();

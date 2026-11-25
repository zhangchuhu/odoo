'use strict';
async function discoverRequest(api,host,tabId){
 if(!Number.isSafeInteger(tabId)||tabId<=0)throw Error('invalid tab');
 const tab=await api.tabs.get(tabId);const session=await api.runtime.sendMessage({type:'collector_session_id'});
 if(!Number.isSafeInteger(tab?.windowId)||tab.windowId<=0||!/^[a-f0-9]{32}$/.test(session?.session_id||''))throw Error('invalid browser session');
 const state=await api.runtime.sendNativeMessage(host,{operation:'binding_status',tab_id:tabId,window_id:tab.windowId,session_id:session.session_id});
 if(typeof state?.request_id!=='string'||!state.request_id)throw Error('current request unavailable');return state.request_id;
}
globalThis.RequestDiscovery={discoverRequest};if(typeof module!=='undefined')module.exports={discoverRequest};

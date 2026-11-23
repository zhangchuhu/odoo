'use strict';
let requestId=null;
const status=document.getElementById('status');
const register=document.getElementById('register');
const create=document.getElementById('create');
create.disabled=true;
const trace=typeof CollectorTrace==='undefined'?null:CollectorTrace;
const diagnostic=document.getElementById('diagnostics');
async function record(stage,code){if(trace){await trace.append(stage,code);await trace.render(diagnostic)}}
if(trace)trace.render(diagnostic);
register.addEventListener('click',async()=>{
 if(register.disabled)return;
 register.disabled=true;create.disabled=true;requestId=null;
 if(trace?.begin)await trace.begin();
 await record('button','clicked');
 let phase='request';
 try {
  status.textContent='正在连接当前任务';
  const state=await chrome.runtime.sendMessage({type:'dedicated_current_request'});
  if(typeof state?.request_id!=='string' || !state.request_id)throw Error('request');
  await record('task_discovery','ok');
  phase='window';
  status.textContent='正在准备并确认专用采集窗口';
  const reply=await chrome.runtime.sendMessage({type:'dedicated_window_register',request_id:state.request_id});
  if(!Number.isSafeInteger(reply?.window_id) || reply.window_id<=0)throw Error(reply?.error || 'window');
  await record('window_registration','ok');
  requestId=state.request_id;create.disabled=false;
  status.textContent=`已确认任务 ${requestId} 的专用窗口`;
 }catch(error){await record(phase==='request'?'task_discovery':'window_registration',phase==='request'?'request_unavailable':(/^[a-z_]+$/.test(error?.message||'')?error.message:'unknown'));requestId=null;const messages={session_unavailable:'浏览器会话不可用',anchor_challenge_failed:'获取窗口登记凭证失败',window_prepare_failed:'创建或恢复专用窗口失败',anchor_confirm_failed:'后台确认窗口失败',anchor_guard_failed:'窗口连接保持失败',anchor_guard_open_failed:'窗口守护连接创建失败',anchor_guard_ready_failed:'窗口守护连接确认失败',anchor_reconnect_failed:'窗口重连服务启动失败'};status.textContent=phase==='request'?'未能获取当前任务，请检查后台连接后重试':`未能确认任务和窗口：${messages[error?.message] || '窗口登记失败'}，请检查后台连接后重试`;}
 finally {register.disabled=false;}
});
create.addEventListener('click',async()=>{
 if(!requestId || create.disabled){status.textContent='请先确认当前任务和窗口';return;}
 create.disabled=true;status.textContent='正在创建并绑定任务标签';
 try {
  const reply=await chrome.runtime.sendMessage({type:'dedicated_task_tab_create',request_id:requestId});
  if(!Number.isSafeInteger(reply?.tab_id) || reply.tab_id<=0)throw Error(reply?.error || 'task_tab_unavailable');
  status.textContent=`已打开${reply.reused?'原任务标签':'新任务标签'} ${reply.tab_id}，请按当前采集阶段继续`;
 }catch(error){status.textContent='未能确认任务标签（'+(typeof error?.message==='string'&&/^[a-z_]+$/.test(error.message)?error.message:'task_tab_unavailable')+'），请核对当前连接';create.disabled=false;}
});

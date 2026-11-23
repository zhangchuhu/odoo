'use strict';
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])]));
  return value;
}
function equal(a,b) { return JSON.stringify(canonical(a)) === JSON.stringify(canonical(b)); }
class SessionPersistence {
  constructor(storage) { this.storage=storage; }
  key(tab, chromeSession, task) {
    if (!Number.isSafeInteger(tab) || tab<=0 || !/^[a-f0-9]{32}$/.test(chromeSession) || !/^rec[A-Za-z0-9_-]+$/.test(task)) throw new Error('Invalid session identity');
    return `collector:v1:${chromeSession}:${tab}:${task}`;
  }
  async load(tab, chromeSession, task) {
    const key=this.key(tab,chromeSession,task), data=await this.storage.get(key), entry=data[key];
    if (!entry) return null;
    if (entry.version!==1 || entry.task!==task || entry.tab!==tab || entry.chromeSession!==chromeSession || !entry.session || entry.session.taskRecordId!==task || !Array.isArray(entry.session.queryBlocks)) throw new Error('Invalid saved collector session');
    return entry.session;
  }
  async save(tab, chromeSession, session) {
    const key=this.key(tab,chromeSession,session?.taskRecordId);
    if (!Array.isArray(session.queryBlocks) || !session.queryBlocks.length) throw new Error('Invalid collector state');
    const old=await this.load(tab,chromeSession,session.taskRecordId);
    if (old && (!equal(old.queries,session.queries) || old.platform!==session.platform || old.queryBlocks.some((b,i)=>!session.queryBlocks[i] || (b.observations.length>session.queryBlocks[i].observations.length || !equal(b.observations,session.queryBlocks[i].observations.slice(0,b.observations.length)))) || ((old.details?.length||0)>(session.details?.length||0) || !equal(old.details||[],(session.details||[]).slice(0,(old.details||[]).length))))) throw new Error('Existing collector progress cannot be overwritten');
    await this.storage.set({[key]:{version:1,tab,chromeSession,task:session.taskRecordId,session}});
  }
}
if(typeof module!=='undefined') module.exports={SessionPersistence};

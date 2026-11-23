'use strict';
class AnchorReconnect {
 constructor(identity,connect,inspect,schedule=(...args)=>globalThis.setInterval(...args),cancel=(...args)=>globalThis.clearInterval(...args)){this.identity=identity;this.connect=connect;this.inspect=inspect;this.schedule=schedule;this.cancel=cancel;this.stopped=false;this.busy=false;this.timer=null;this.failures=0;}
 start(){if(this.timer!==null||this.stopped)throw Error('already started');this.timer=this.schedule(()=>this.tick(),5000)}
 async tick(){if(this.stopped||this.busy)return;this.busy=true;try{const current=await this.inspect();if(!current||Object.keys(this.identity).some(k=>current[k]!==this.identity[k])){this.stop();return}await this.connect(this.identity);this.failures=0}catch(_){if(++this.failures>=12)this.stop()}finally{this.busy=false}}
 stop(){this.stopped=true;if(this.timer!==null){this.cancel(this.timer);this.timer=null}}
}
globalThis.AnchorReconnect=AnchorReconnect;if(typeof module!=='undefined')module.exports={AnchorReconnect};

'use strict';
async function downloadEvidence(chromeApi, url, filename, wait=()=>new Promise(r=>setTimeout(r,250))) {
  const id=await chromeApi.downloads.download({url,filename,conflictAction:'overwrite',saveAs:false});
  for(let i=0;i<120;i++) {
    const items=await chromeApi.downloads.search({id});
    const item=items.find(v=>v.id===id);
    if(item?.state==='interrupted') throw new Error('download_interrupted');
    if(item?.state==='complete' && typeof item.filename==='string' && item.filename) return item.filename;
    await wait();
  }
  throw new Error('download_confirmation_timeout');
}
if(typeof module!=='undefined') module.exports={downloadEvidence};

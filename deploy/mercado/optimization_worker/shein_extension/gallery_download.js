'use strict';
(() => {
  const maxBytes = 8 * 1024 * 1024;
  const wait = () => new Promise(resolve => setTimeout(resolve, 500));

  function fail(code, message) { throw Error(code + '：' + message); }

  function allowedSource(source, platform) {
    let url;
    try { url = new URL(source); } catch (_) { return false; }
    const domain = platform === 'mercado-libre-mx' ? /(^|\.)mlstatic\.com$/ : /(^|\.)(?:ltwebstatic|sheincdn)\.com$/;
    return ['mercado-libre-mx','shein-us'].includes(platform) && typeof source === 'string' && url.href === source &&
      source.length <= 2048 && url.protocol === 'https:' && domain.test(url.hostname) &&
      !url.username && !url.password && !url.port && !url.hash && url.pathname.length > 1;
  }

  function validSlot(step, requestId) {
    if (typeof step.filename !== 'string' || !/^[0-9a-f]{32}$/.test(step.token)) return false;
    const parts = step.filename.split('/');
    return parts.length === 4 && parts[0] === 'Odoo-Research' && parts[1] === requestId &&
      /^[0-9a-f-]{36}$/.test(parts[1]) && /^[1-9]\d{0,18}$/.test(parts[2]) &&
      parts[3].startsWith(step.token+'.') && /^[0-9a-f]{32}\.(?:png|jpe?g|webp)$/.test(parts[3]);
  }

  function ownedDownload(item, step, extensionId) {
    return item.byExtensionId === extensionId && item.url === step.source &&
      typeof item.filename === 'string' && item.filename.endsWith('/' + step.filename);
  }

  // Chrome can ignore download({filename}) when another extension has a filename listener.
  // Suggest only for this extension's exact returned download ID and verified source.
  async function downloadToSlot(api, key, step, platform, delay) {
    const event = api.downloads.onDeterminingFilename;
    let bindId;
    const selected = new Promise(resolve => { bindId=resolve; });
    const listener = (item, suggest) => {
      if (item.byExtensionId !== api.runtime.id || item.url !== step.source || item.incognito) {
        suggest();
        return;
      }
      // In Chrome the event can precede delivery of the download() Promise to the popup.
      // Never stall another download of this same URL if the start acknowledgement is lost.
      let timer;
      const deadline = new Promise(resolve=>{timer=setTimeout(()=>resolve(null),3000);});
      Promise.race([selected,deadline]).then(id=>{
        clearTimeout(timer);
        if (id === item.id) suggest({filename:step.filename, conflictAction:'uniquify'});
        else suggest();
      });
      return true;
    };
    event?.addListener(listener);
    try {
      const id = await acquireDownload(api,key,step);
      bindId(id);
      return await completedDownload(api,step,platform,id,key,delay);
    } finally {
      bindId(null);
      event?.removeListener(listener);
    }
  }

  async function acquireDownload(api, storageKey, step) {
    const storage = api.storage.local;
    const original = {token:step.token, source:step.source, filename:step.filename, namingVersion:1};
    let prior = (await storage.get(storageKey))[storageKey];
    if (prior?.token === step.token && prior.source === step.source && prior.filename === step.filename &&
        !prior.namingVersion && Number.isSafeInteger(prior.id)) {
      const [old] = await api.downloads.search({id:prior.id});
      // The pre-fix version could leave this specific download named like the CDN asset.
      // It cannot have written to the signed slot. A fresh ticket proves this order still needs an upload.
      if (old?.id === prior.id && old.state === 'complete' && old.byExtensionId === api.runtime.id &&
          old.url === step.source && typeof old.filename === 'string' && !ownedDownload(old,step,api.runtime.id)) prior = null;
    }
    let id;
    if (prior?.token === step.token) {
      if (prior.source !== step.source || prior.filename !== step.filename) fail('gallery_slot_changed', '已保存的下载意图与宿主签发的槽位不同');
      id = prior.id;
      if (!Number.isSafeInteger(id)) {
        const quoted = step.filename.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
        const matches = (await api.downloads.search({url:step.source, filenameRegex:'/'+quoted+'$', limit:2}))
          .filter(item => ownedDownload(item, step, api.runtime.id));
        if (matches.length !== 1) fail('gallery_download_unknown', '上一次下载请求尚无唯一回执，请核对原下载');
        id = matches[0].id;
      }
    } else {
      // Persist before Chrome observes the action, so reopening the collector cannot start it twice.
      await storage.set({[storageKey]:original});
      id = await api.downloads.download({url:step.source, filename:step.filename, conflictAction:'uniquify', saveAs:false});
    }
    if (!Number.isSafeInteger(id) || id < 0) fail('gallery_download_unknown', '浏览器未返回有效下载编号');
    await storage.set({[storageKey]:{...original,id}});
    return id;
  }

  async function completedDownload(api, step, platform, id, storageKey, delay) {
    for (let i=0;i<120;i++) {
      const [item] = await api.downloads.search({id});
      if (!item || item.id !== id || item.byExtensionId !== api.runtime.id || item.url !== step.source || item.incognito) {
        fail('gallery_download_owner_mismatch', '下载编号、发起扩展或原图地址发生变化');
      }
      if (item.finalUrl && !allowedSource(item.finalUrl,platform)) fail('gallery_download_redirect', '原图下载跳转到平台允许范围之外');
      if (item.state === 'interrupted') {
        await api.storage.local.remove(storageKey);
        fail('gallery_download_interrupted', 'Chrome 中断本张下载，浏览器错误 '+(item.error || 'unknown'));
      }
      if (item.state === 'complete') {
        if (!ownedDownload(item,step,api.runtime.id)) fail('gallery_download_filename_mismatch', 'Chrome 已完成下载，但实际文件名与任务专用位置不一致。请用已核验的原图链接逐张另存');
        if (!allowedSource(item.finalUrl,platform)) fail('gallery_download_final_url_invalid', '下载完成后缺少已核验的平台最终地址');
        if (!item.exists) fail('gallery_download_missing', 'Chrome 在目标位置找不到已完成的文件');
        if (!Number.isSafeInteger(item.fileSize) || item.fileSize < 1 || item.fileSize > maxBytes) fail('gallery_download_size_invalid', '原图字节数无效或超过 8 MiB');
        if (!['safe','accepted'].includes(item.danger)) fail('gallery_download_security', 'Chrome 尚未确认该下载的安全状态');
        return item;
      }
      await delay();
    }
    fail('gallery_download_wait', '原图仍在下载，请稍后从同一步继续');
  }

  async function downloadGallery({api, draft, requestId, platform, checkpoint, verifyPage, progress = () => {}, delay = wait}) {
    if (!draft?.identity?.startsWith(platform+':') || !/^[0-9a-f]{64}$/.test(draft.confirmed) || !Array.isArray(draft.images) ||
        !Number.isInteger(draft.total) || draft.total < 1 || draft.total > 100 || draft.total !== draft.images.length ||
        new Set(draft.images).size !== draft.total || !draft.images.every(source => allowedSource(source,platform))) {
      fail('gallery_manifest_unconfirmed', '请先回传并核验该候选的全部原图清单');
    }
    const identity = draft.identity;
    const key = 'gallery-download:'+requestId+':'+identity;
    let expected;
    for (let n=0;n<=draft.total;n++) {
      const step = await checkpoint('gallery_download_next',{identity});
      if (step?.ok !== true || step.identity !== identity) fail('gallery_download_step_invalid', '宿主未确认当前候选');
      if (step.stage === 'complete') {
        if (expected !== undefined && expected !== draft.total) fail('gallery_download_order', '宿主提前结束了原图清单');
        await api.storage.local.remove(key);
        return draft.total;
      }
      if (step.stage !== 'image' || !Number.isInteger(step.order) || step.order < 0 || step.order >= draft.total ||
          (expected !== undefined && step.order !== expected) || draft.images[step.order] !== step.source || !validSlot(step,requestId)) {
        fail('gallery_download_step_invalid', '宿主要求的序号、原图或专用保存位置与已核验的清单不符');
      }
      await verifyPage();
      progress('正在保存第 '+(step.order+1)+' / '+draft.total+' 张原图');
      const item = await downloadToSlot(api,key,step,platform,delay);
      await verifyPage();
      const receipt = await checkpoint('gallery_download_saved',{identity, order:step.order, source:step.source,
        token:step.token, filename:step.filename, download_id:item.id, final_url:item.finalUrl, size:item.fileSize});
      if (receipt?.ok !== true || receipt.identity !== identity || receipt.order !== step.order ||
          !/^[1-9][0-9]*$/.test(String(receipt.artifact_id)) || !/^[0-9a-f]{64}$/.test(receipt.digest)) {
        fail('gallery_image_receipt_invalid', '原图的入库回执尚未核实，恢复时将向宿主核对');
      }
      expected = step.order + 1;
      progress('第 '+expected+' / '+draft.total+' 张已确认入库');
    }
    fail('gallery_download_order', '宿主尚未确认图库完成');
  }

  async function downloadVisualCandidatePhoto({api,requestId,checkpoint,observation,recheck}) {
    const step=await checkpoint('visual_photo_start',observation);
    if(!step?.ok || step.source!==observation.source || !validSlot(step,requestId) || step.receipt!==step.token)
      fail('visual_photo_slot_invalid','候选原图下载位置无法确认');
    if(step.confirmed)return step;
    const key='visual-photo:'+step.receipt;
    const download=()=>downloadToSlot(api,key,step,'shein-us',wait);
    const item=await (globalThis.navigator?.locks ? navigator.locks.request(key,download) : download());
    await recheck();
    const receipt=await checkpoint('visual_photo_saved',{receipt:step.receipt,download_id:item.id,final_url:item.finalUrl,size:item.fileSize});
    if(!receipt?.ok || receipt.receipt!==step.receipt || !/^[a-f0-9]{64}$/.test(receipt.sha256))
      fail('visual_photo_receipt_invalid','候选原图凭证未确认');
    await api.storage.local.remove(key);
    return receipt;
  }
  if (typeof module !== 'undefined') module.exports = {downloadGallery,downloadVisualCandidatePhoto};
  globalThis.downloadVisualCandidatePhoto=downloadVisualCandidatePhoto;
  globalThis.downloadVerifiedGallery = downloadGallery;
})();

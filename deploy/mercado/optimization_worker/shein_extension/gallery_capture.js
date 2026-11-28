'use strict';
(() => {
  function candidateIdentity(platform, product) {
    const id = product.trim();
    if (!(platform === 'shein-us' ? /^\d+$/ : /^MLMU?\d+$/i).test(id)) throw Error('请填写当前合格候选的商品 ID');
    return platform + ':' + id.toUpperCase();
  }

  function detailUrl(value) {
    const url = new URL(value);
    url.hash = '';
    url.search = '';
    return url.href;
  }

  function variantKey(page) {
    const url = new URL(page.page_url);
    const sku = [...url.searchParams.entries()].filter(([key]) => /^(?:sku(?:_code|_?id)?|searchVariation|variation(?:_?id)?|color(?:_?id)?|size(?:_?id)?)$/i.test(key));
    sku.sort(([keyA],[keyB]) => keyA.localeCompare(keyB));
    return JSON.stringify([detailUrl(page.page_url), sku, page.variation]);
  }

  async function observe(api, binding, identity) {
    const tab = await api.tabs.get(binding.tab_id);
    const [active] = await api.tabs.query({active:true, windowId:binding.window_id});
    if (tab.id !== binding.tab_id || tab.windowId !== binding.window_id || active?.id !== tab.id || !tab.active) {
      throw Error('专属窗口的当前活动标签不是该候选详情');
    }
    const page = await api.tabs.sendMessage(tab.id, {type:'COLLECTOR_VISIBLE_GALLERY', identity});
    if (page?.error) throw Error(page.error);
    if (!page || page.identity !== identity || page.page_url !== tab.url || !Array.isArray(page.variation) || !page.rows?.length) {
      throw Error('当前候选的详情身份或可见原图无法核验');
    }
    return page;
  }

  async function inspectGallery(api, tabId, identity) {
    if (!Number.isSafeInteger(tabId) || tabId < 1) throw Error('当前采集器缺少目标标签');
    const target = await api.tabs.get(tabId);
    const page = await observe(api, {tab_id:tabId, window_id:target.windowId}, identity);
    const [current, active] = await Promise.all([
      api.tabs.get(tabId), api.tabs.query({active:true, windowId:target.windowId}),
    ]);
    if (current.windowId !== target.windowId || active[0]?.id !== tabId || !current.active ||
        current.url !== page.page_url || (current.pendingUrl && current.pendingUrl !== current.url)) {
      throw Error('查看期间活动标签或商品详情发生变化');
    }
    const area = page.sourceArea === 'viewer' ? '放大查看器' : '商品图库';
    const photoCount = Number.isSafeInteger(page.photoTotal) && page.photoTotal > 0 ? page.photoTotal + ' 张' : '页面未标明';
    const slide = Number.isSafeInteger(page.slidePosition) && Number.isSafeInteger(page.slideTotal)
      ? '，轮播第 ' + page.slidePosition + ' / ' + page.slideTotal + ' 项' : '';
    const lines = [page.identity + ' · ' + area + '，照片总数 ' + photoCount + slide];
    for (const row of page.rows) lines.push(row.width + ' × ' + row.height + ' px · ' + row.url);
    return lines.join('\n');
  }

  async function readCandidatePhoto(api,binding,identity) {
    const page=await observe(api,binding,identity),variant=variantKey(page);
    const source=page.rows[0]?.url;
    if(!source)throw Error('visual_photo_missing');
    return {source,variant,recheck:async()=>{
      const current=await observe(api,binding,identity);
      if(variantKey(current)!==variant || current.rows[0]?.url!==source)throw Error('visual_photo_changed');
    }};
  }

  function appendImage(draft, page, position, total, source) {
    if (!Number.isSafeInteger(total) || total < 1 || total > 100 ||
        !Number.isSafeInteger(position) || position < 1 || position > total) throw Error('请按页面所示填写图片总数和序号');
    if (Number.isSafeInteger(page.photoTotal) && page.photoTotal > 0 && total !== page.photoTotal) {
      throw Error('填写的图片总数与当前规格可核验的照片总数不一致');
    }
    const image = source ? page.rows.find(row => row.url === source) : page.rows.length === 1 ? page.rows[0] : null;
    if (!image) throw Error('所选地址未匹配当前可见的可信原图。请在详情页显示该图，再填入确切原图网址');
    const key = variantKey(page);
    if (!draft) draft = {identity:page.identity, detail_url:detailUrl(page.page_url), variant:key, total, images:[]};
    if (draft.identity !== page.identity || draft.variant !== key || draft.total !== total) throw Error('当前规格、商品或图片总数与已记录清单不一致');
    if (draft.images[position-1] === image.url) return draft;
    if (draft.confirmed || position !== draft.images.length + 1 || draft.images.includes(image.url)) throw Error('图片顺序或原图地址与已记录清单冲突');
    return {...draft, images:[...draft.images,image.url]};
  }

  async function submitGallery(api, binding, draft, submit, currentBinding) {
    if (!draft || draft.images.length !== draft.total || !draft.total) throw Error('请先按顺序核验当前规格的全部主图');
    let changed = false;
    const activated = info => { if (info.windowId === binding.window_id && info.tabId !== binding.tab_id) changed = true; };
    const updated = (id, info) => { if (id === binding.tab_id && (info.url || info.status === 'loading')) changed = true; };
    api.tabs.onActivated.addListener(activated);
    api.tabs.onUpdated.addListener(updated);
    async function verify() {
      const now = await currentBinding();
      if (Object.keys(binding).some(key => now[key] !== binding[key])) throw Error('采集任务或浏览器绑定已变化');
      const page = await observe(api,binding,draft.identity);
      if (Number.isSafeInteger(page.photoTotal) && page.photoTotal > 0 && draft.total !== page.photoTotal) {
        throw Error('已记录的图片总数与当前规格可核验的照片总数不一致');
      }
      if (changed || variantKey(page) !== draft.variant || !page.rows.some(row => draft.images.includes(row.url))) {
        throw Error('采集期间商品、规格或当前可见主图发生变化');
      }
      return JSON.stringify(page.rows);
    }
    try {
      const visible = await verify();
      const manifest = {detail_url:draft.detail_url, images:draft.images};
      const body = {identity:draft.identity, detail_url:draft.detail_url, manifest};
      for (const quality of [78, 50, 30]) {
        if (await verify() !== visible) throw Error('截图前的主图已发生变化');
        let data;
        try { data = await api.tabs.captureVisibleTab(binding.window_id, {format:'jpeg',quality}); }
        catch (_) { throw Error('详情页截图不可用。请在该活动详情标签点击已授权的采集扩展，再恢复本候选图库'); }
        if (await verify() !== visible) throw Error('截图前后的主图不一致');
        const match = /^data:image\/jpeg;base64,([A-Za-z0-9+/]+={0,2})$/.exec(data || '');
        if (!match) throw Error('未取得有效 JPEG 详情截图');
        const event = {...body,screenshot:match[1]};
        const bytes = new TextEncoder().encode(JSON.stringify({...binding,operation:'gallery_capture',...event})).length;
        if (bytes >= 899000) {
          // Chrome permits at most two captureVisibleTab calls per second.
          if (quality !== 30) await new Promise(resolve=>setTimeout(resolve,550));
          continue;
        }
        const receipt = await submit('gallery_capture',event);
        if (receipt?.ok !== true || receipt.identity !== draft.identity || receipt.image_count !== draft.total ||
            !/^[a-f0-9]{64}$/.test(receipt.screenshot_sha256 || '')) throw Error('宿主未确认该图库清单，请核对后恢复');
        // The receipt is durable even if the user has moved on while it was returning.
        try { if (await verify() !== visible) changed = true; } catch (_) { changed = true; }
        return {confirmed:receipt.screenshot_sha256, changed};
      }
      throw Error('详情截图超过本机回传大小上限，请缩小该窗口后重试');
    } finally {
      api.tabs.onActivated.removeListener(activated);
      api.tabs.onUpdated.removeListener(updated);
    }
  }

  if (typeof module !== 'undefined') module.exports = {appendImage, submitGallery, observe, inspectGallery, readCandidatePhoto};
  globalThis.readCandidatePhoto=readCandidatePhoto;
  if (typeof document === 'undefined') return;
  const el = id => document.getElementById(id);
  const section = el('gallery-capture');
  const tabId = Number(new URL(location.href).searchParams.get('tab'));
  if (!section || !Number.isSafeInteger(tabId) || tabId < 1) return;
  section.hidden = false;
  const platform = section.dataset.platform;
  const status = el('capture-status');
  const links = el('capture-links');
  const buttons = ['capture-add','capture-restore','capture-submit','capture-inspect','capture-download'].map(el);

  function render(draft) {
    el('capture-download').hidden = !draft?.confirmed;
    links.replaceChildren();
    for (const source of draft?.images || []) {
      const item = document.createElement('li');
      if (draft.confirmed) {
        const link = document.createElement('a');
        link.href = source;
        link.textContent = source;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        item.append(link);
      } else item.textContent = source;
      links.append(item);
    }
    el('capture-position').value = draft ? Math.min(draft.images.length+1,draft.total) : 1;
    if (draft) el('capture-total').value = draft.total;
    status.textContent = draft?.confirmed ? '宿主已保存清单和详情截图。可保存此候选全部原图，也可右键原图链接，逐张另存到宿主指定位置。'
      : draft ? '已记录 '+draft.images.length+' / '+draft.total+' 张可见主图' : '当前候选暂无已记录的主图';
  }

  async function run(mode) {
    if (buttons.some(button=>button.disabled)) return;
    buttons.forEach(button=>{button.disabled=true;});
    try {
      const identity = candidateIdentity(platform, el('capture-product').value);
      if (mode === 'inspect') {
        el('capture-preview').textContent = '';
        el('capture-preview').textContent = await inspectGallery(chrome, tabId, identity);
        return;
      }
      let binding;
      try { binding = await currentGalleryBinding(); }
      catch (error) {
        if (mode !== 'download') throw error;
        // After the last image advances the guard to the next product, only read the original candidate's completion.
        const receipt = await collectorCheckpoint('gallery_download_next',{identity});
        if (receipt?.ok !== true || receipt.identity !== identity || receipt.stage !== 'complete') throw error;
        status.textContent = '宿主已确认该候选图库全部入库。';
        return;
      }
      const key = 'verified-gallery:'+binding.request_id+':'+identity;
      let draft = (await chrome.storage.local.get(key))[key];
      if (mode === 'add') {
        const page = await observe(chrome,binding,identity);
        draft = appendImage(draft,page,Number(el('capture-position').value),Number(el('capture-total').value),el('capture-source').value.trim());
        await chrome.storage.local.set({[key]:draft});
        el('capture-source').value='';
      }
      if (mode === 'download') {
        const beforeWrite = async () => {
          const current = await currentGalleryBinding();
          if (Object.keys(binding).some(key => current[key] !== binding[key]) || candidateIdentity(platform,el('capture-product').value) !== identity) {
            throw Error('gallery_binding_changed：当前任务、窗口或候选已变化');
          }
          const page = await observe(chrome,binding,identity);
          if (!draft || variantKey(page) !== draft.variant || !page.rows.some(row => draft.images.includes(row.url))) {
            throw Error('gallery_variant_changed：请返回已核验商品的同一规格图库');
          }
        };
        await navigator.locks.request('gallery-download:'+binding.request_id+':'+identity, {ifAvailable:true}, async lock => {
          if (!lock) throw Error('gallery_download_busy：另一个采集器窗口正在保存该候选的原图');
          const count = await downloadVerifiedGallery({api:chrome, draft, requestId:binding.request_id, platform,
            checkpoint:collectorCheckpoint, verifyPage:beforeWrite, progress:message=>{status.textContent=message;}});
          status.textContent = '该候选图库已全部入库，共 '+count+' 张原图。';
        });
        return;
      }
      if (mode === 'submit') {
        const result = await submitGallery(chrome,binding,draft,collectorCheckpoint,currentGalleryBinding);
        draft = {...draft,confirmed:result.confirmed};
        await chrome.storage.local.set({[key]:draft});
        render(draft);
        if (result.changed) status.textContent += ' 当前活动页已变化，继续前请重新核验任务详情。';
        return;
      }
      render(draft);
    } catch (error) {
      const output = mode === 'inspect' ? el('capture-preview') : status;
      output.textContent = error.message || '图库核验失败';
    } finally { buttons.forEach(button=>{button.disabled=false;}); }
  }

  el('capture-product').addEventListener('input',()=>{el('capture-download').hidden=true;links.replaceChildren();status.textContent='';el('capture-preview').textContent='';});
  buttons.forEach((button,i)=>button.addEventListener('click',()=>run(['add','restore','submit','inspect','download'][i])));
})();

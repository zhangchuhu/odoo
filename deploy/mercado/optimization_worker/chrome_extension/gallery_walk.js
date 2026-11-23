'use strict';

async function walkVisibleGallery({api,binding,identity,draft,variantKey,appendImage,currentBinding,persist,
  sleep = ms => new Promise(resolve=>setTimeout(resolve,ms))}) {
  let expectedSlide = 1, observedPhotos = 0, stage = 'gallery_walk_tab';
  const fail = (code,message) => {
    const error = Error(code+'：'+message+'（预期轮播第 '+expectedSlide+' 项，本次已核验 '+observedPhotos+' 张）');
    error.code = code;
    error.stage = stage;
    throw error;
  };
  async function send(message) {
    let reply;
    try { reply = await api.tabs.sendMessage(binding.tab_id,message); }
    catch (_) { fail('gallery_walk_receiver_unavailable','当前详情页未响应新版步进消息，请重新载入已绑定的商品详情页'); }
    if (reply === undefined || reply === null) fail('gallery_walk_receiver_unavailable','详情页未返回步进消息回执，请重新载入已绑定的商品详情页');
    return reply;
  }
  let first, change;
  const startTab = await api.tabs.get(binding.tab_id).catch(()=>fail('gallery_walk_target_unavailable','无法读取已绑定的标签'));
  let startUrl;
  try { startUrl = new URL(startTab.url); } catch (_) { fail('gallery_walk_initial_url_invalid','无法读取商品标签的初始页面地址'); }
  const base = url => typeof url === 'string' ? url.split('#')[0] : null;
  const original = base(startUrl.href);
  // Keep the earliest event, even if the user switches back before the next read.
  const activated = info => {
    if (info.windowId === binding.window_id && info.tabId !== binding.tab_id) {
      change ??= ['gallery_walk_other_tab_activated','Chrome 通知专用窗口已激活其他标签'];
    }
  };
  const updated = (id, info) => {
    if (id !== binding.tab_id) return;
    if (info.url && base(info.url) !== original) change ??= ['gallery_walk_url_change_event','Chrome 通知商品标签的页面地址已变化'];
    else if (info.status === 'loading') {
      change ??= info.url && info.url.includes('#')
        ? ['gallery_walk_fragment_loading_event','Chrome 在同页带片段的网址上发出了加载事件']
        : ['gallery_walk_loading_event','Chrome 对商品标签发出了加载事件'];
    }
  };
  async function active() {
    stage = 'gallery_walk_tab';
    const tab = await api.tabs.get(binding.tab_id).catch(()=>fail('gallery_walk_target_unavailable','无法读取已绑定的标签'));
    const tabs = await api.tabs.query({active:true, windowId:binding.window_id}).catch(()=>fail('gallery_walk_window_unavailable','无法读取专用窗口的活动标签'));
    if (change) fail(...change);
    if (tab.id !== binding.tab_id) fail('gallery_walk_target_id_mismatch','读取到的标签 ID 与绑定不一致');
    if (tab.windowId !== binding.window_id) fail('gallery_walk_window_mismatch','绑定标签已移出专用窗口');
    if (!tab.active) fail('gallery_walk_target_inactive','绑定标签当前未激活');
    if (tabs.length !== 1) fail('gallery_walk_active_count','无法在专用窗口中确认唯一活动标签');
    if (tabs[0].id !== tab.id) fail('gallery_walk_active_mismatch','专用窗口当前活动标签与绑定不一致');
    if (tab.pendingUrl) {
      if (base(tab.pendingUrl) === original && tab.pendingUrl !== tab.url) {
        fail('gallery_walk_pending_fragment','Chrome 正在提交商品页的片段变化');
      }
      fail('gallery_walk_pending_navigation','绑定标签存在未完成的导航');
    }
    if (tab.status === 'loading') fail('gallery_walk_tab_loading','核验时绑定标签仍在加载');
    if (base(tab.url) !== original) fail('gallery_walk_page_changed','已提交的商品页地址发生变化');
    return tab;
  }
  async function fence() {
    stage = 'gallery_walk_guard';
    const now = await currentBinding();
    if (Object.keys(binding).some(key=>now[key] !== binding[key])) fail('gallery_walk_binding_changed','任务、窗口或浏览器会话发生变化');
    await active();
  }
  function signature(page) {
    return JSON.stringify([page.kind,page.slidePosition,page.slideTotal,page.photoTotal,variantKey(page),page.rows]);
  }
  async function read(position, previous) {
    expectedSlide = position;
    let last = null;
    for (let attempt = 0; attempt < 30; attempt++) {
      const tab = await active();
      stage = 'gallery_walk_read';
      const page = await send({type:'COLLECTOR_GALLERY_WALK_READ',identity});
      if (page?.error) {
        last = null;
        if (!/^gallery_(viewer_unsettled|viewer_image_missing|image_not_loaded)/.test(page.error)) throw Error(page.error);
      } else {
        if (!['viewer','gallery'].includes(page.sourceArea) || !Array.isArray(page.variation) || !Array.isArray(page.rows) ||
            typeof page.identity !== 'string' || typeof page.page_url !== 'string') fail('gallery_walk_response_invalid','详情页返回了无法核验的步进回执');
        if (page.identity !== identity) fail('gallery_walk_product_mismatch','详情页回执中的商品身份与任务不符');
        if (page.page_url !== tab.url) {
          if (base(page.page_url) === base(tab.url)) fail('gallery_walk_receipt_fragment_changed','读取标签和提取页面回执之间，图库网址片段发生变化');
          fail('gallery_walk_receipt_url_mismatch','详情页回执网址与标签网址不符');
        }
        if (page.sourceArea !== 'viewer') fail('gallery_walk_viewer_required','请先放大当前规格的第一张主图');
        if (!Number.isSafeInteger(page.photoTotal) || page.photoTotal < 1 || page.photoTotal > 100 ||
            !Number.isSafeInteger(page.slideTotal) || page.slideTotal < page.photoTotal || page.slideTotal > 200 ||
            !Number.isSafeInteger(page.slidePosition) || page.slidePosition < 1 || page.slidePosition > page.slideTotal) {
          fail('gallery_walk_count_unknown','当前页面无法确认照片总数和轮播序号，可使用逐张核验');
        }
        if (first && (page.photoTotal !== first.photoTotal || page.slideTotal !== first.slideTotal ||
            variantKey(page) !== variantKey(first))) fail('gallery_walk_variant_changed','商品规格或图库总数发生变化');
        if (page.slidePosition !== position && (!previous || page.slidePosition !== previous.slidePosition)) {
          fail('gallery_walk_position_changed', '当前轮播位置与本次操作不一致，请从第一张图片开始');
        }
        if (page.slidePosition === position) {
          const recognized = page.kind === 'video' && !page.rows.length || page.kind === 'photo' && page.rows.length === 1;
          if (!recognized) fail('gallery_walk_ambiguous','无法确认当前项是一张原图或一个视频，可使用逐张核验');
          // Counters sometimes update before the central image has finished moving.
          const oldPhoto = previous?.kind === 'photo' && page.kind === 'photo' && previous.rows[0].url === page.rows[0].url;
          const value = signature(page);
          if (!oldPhoto && last === value) { await active(); return page; }
          last = oldPhoto ? null : value;
        } else last=null;
      }
      await sleep(180);
    }
    fail('gallery_walk_unsettled','当前项的位置和已加载原图未能稳定匹配');
  }
  async function move(page,direction) {
    await fence();
    stage = 'gallery_walk_move';
    const answer = await send({type:'COLLECTOR_GALLERY_WALK_MOVE',identity,expected:page,direction});
    if (!answer?.ok) throw Error(answer?.error || 'gallery_walk_move_unconfirmed');
    return read(page.slidePosition + (direction === 'next' ? 1 : -1),page);
  }
  api.tabs.onActivated.addListener(activated);
  api.tabs.onUpdated.addListener(updated);
  try {
    await fence();
    first = await read(1);
    if (first.kind !== 'photo') fail('gallery_walk_first_photo','请在第一项为商品主图的放大查看器中开始');
    const history = [];
    let page=first, count=0, lastPhoto=0;
    for (let position=1; position<=first.slideTotal; position++) {
      if (position>1) page=await move(page,'next');
      history.push(signature(page));
      if (page.kind === 'photo') {
        count++;
        if (count>first.photoTotal) fail('gallery_walk_photo_count','当前照片数量超过页面标明的总数');
        const next=appendImage(draft,page,count,first.photoTotal,page.rows[0].url);
        await fence();
        if (next !== draft) await persist(next);
        draft=next;
        observedPhotos=count;
        lastPhoto=position;
      }
    }
    if (count !== first.photoTotal) fail('gallery_walk_photo_count','逐项观察到的照片数量与页面标明的总数不一致');
    // Capture an actual photo even when the last carousel item was a video.
    while(page.slidePosition>lastPhoto) {
      page=await move(page,'previous');
      if (signature(page)!==history[page.slidePosition-1]) fail('gallery_walk_changed','返回截图位置时图库项已变化');
    }
    await fence();
    return draft;
  } finally {
    api.tabs.onActivated.removeListener(activated);
    api.tabs.onUpdated.removeListener(updated);
  }
}
if (typeof module !== 'undefined') module.exports = {walkVisibleGallery};

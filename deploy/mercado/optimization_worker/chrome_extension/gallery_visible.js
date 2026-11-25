'use strict';

function galleryPhotoCount(container, isVisible) {
  if (!container) return null;
  const totals = new Set();
  for (const button of container.querySelectorAll('button[aria-label]')) {
    if (!isVisible(button)) continue;
    const match = /^Imagen\s+(\d+)\s+de\s+(\d+)\b/i.exec(button.getAttribute('aria-label') || '');
    if (match && Number(match[1]) > 0 && Number(match[1]) <= Number(match[2])) totals.add(Number(match[2]));
  }
  return totals.size === 1 ? [...totals][0] : null;
}

function galleryError(code, message) {
  throw Error(code + '：' + message);
}

// Only use loaded image URLs from the visible main gallery, including its opened viewer.
function readVisibleGallery(platform, doc, isVisible, {includeVideo = false} = {}) {
  const mercado = platform === 'mercado-libre-mx';
  const containers = mercado ? '.ui-pdp-gallery' : '.product-intro__gallery, .product-intro__gallery-wrap, .product-intro__galleryWrap, [data-testid="product-gallery"]';
  const roots = [...doc.querySelectorAll(containers)].filter(isVisible);
  const galleries = roots.filter(node => !roots.some(other => other !== node && other.contains(node)));
  const viewers = mercado ? [...doc.querySelectorAll('.pswp.pswp--open, .pswp[aria-hidden="false"]')].filter(isVisible) : [];
  if (galleries.length > 1 || viewers.length > 1) galleryError('gallery_ambiguous', '当前页面存在多个可见主图库');
  const viewer = viewers[0], gallery = galleries[0];
  if (!gallery && !viewer) galleryError('gallery_not_visible', '请在当前商品详情中显示主图库');
  let slidePosition = null, slideTotal = null;
  let images, video = false;
  if (viewer) {
    // PhotoSwipe renders its current and neighboring slides outside .ui-pdp-gallery.
    // Select the slide at the center of the actual viewer, never a preloaded neighbor.
    const box = viewer.getBoundingClientRect();
    const stack = doc.elementsFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    const item = stack.map(node => node.closest('.pswp__item')).find(node => node && viewer.contains(node));
    if (!item) galleryError('gallery_viewer_unsettled', '放大层当前项未稳定，请等图片显示后重试');
    if (includeVideo) {
      const slide = item.getBoundingClientRect();
      if (item.getAttribute('aria-hidden') === 'true' ||
          Math.abs((slide.left + slide.width / 2) - (box.left + box.width / 2)) > Math.max(3,box.width * 0.015)) {
        galleryError('gallery_viewer_unsettled', '当前轮播项尚未移至查看器中央');
      }
    }
    video = [...item.querySelectorAll('video, iframe[title*="video" i], iframe[src*="player" i], [class*="video-player"]')].some(isVisible);
    if (video && !includeVideo) galleryError('gallery_viewer_video', '当前项为视频，请切换到下一张商品图');
    images = video ? [] : [...item.querySelectorAll('img.pswp__img:not(.pswp__img--placeholder)')];
    if (!video && !images.length) galleryError('gallery_viewer_image_missing', '当前放大项未显示已加载的商品原图。如为视频，请切换到下一张');
    const counter = [...viewer.querySelectorAll('.pswp__counter')].find(isVisible);
    const match = /^(\d+)\s*\/\s*(\d+)$/.exec(counter?.textContent.trim() || '');
    if (match) [slidePosition, slideTotal] = match.slice(1).map(Number);
  } else {
    images = [...gallery.querySelectorAll('img')].filter(img => !img.closest('button[aria-label^="Imagen"], [class*="thumbnail" i], [class*="__thumb" i]'));
  }
  const rows = [];
  let loaded = false, large = false;
  const permitted = mercado ? /(^|\.)mlstatic\.com$/ : /(^|\.)(ltwebstatic|sheincdn)\.com$/;
  for (const img of images) {
    if (!isVisible(img) || !img.complete || !img.naturalWidth || !img.naturalHeight) continue;
    loaded = true;
    const box = img.getBoundingClientRect();
    if (box.width < 150 || box.height < 150) continue;
    if (!viewer && (img.naturalWidth < 500 || img.naturalHeight < 500)) continue;
    large = true;
    let url;
    try { url = new URL(img.currentSrc || img.src); } catch (_) { continue; }
    if (url.protocol !== 'https:' || !permitted.test(url.hostname) || url.hash || !url.pathname.slice(1) ||
        url.href.length > 2048 || url.username || url.password || url.port) continue;
    if (!rows.some(row => row.url === url.href)) rows.push({url:url.href,width:img.naturalWidth,height:img.naturalHeight});
  }
  if (!video && !rows.length) {
    if (!loaded) galleryError('gallery_image_not_loaded', '尚未观察到已加载的可见原图，请打开当前商品的放大图库');
    if (!large) galleryError('gallery_thumbnail_only', '当前只能确认较小的预览图，请显示放大图库中的原图');
    galleryError('gallery_source_untrusted', '当前可见图片没有可核验的平台 HTTPS 原图地址');
  }
  const selectors = mercado
    ? '.ui-pdp-variations [aria-checked="true"], .ui-pdp-variations [aria-pressed="true"], .ui-pdp-variations__selected'
    : '.product-intro__color [aria-checked="true"], .product-intro__size [aria-checked="true"], .product-intro__color-title, .product-intro__color-block_active';
  const variation = [...doc.querySelectorAll(selectors)].filter(isVisible).map(el => [el.getAttribute('aria-label'), el.getAttribute('title'), el.innerText].filter(Boolean).join(' ').trim());
  return {rows, variation, ...(includeVideo ? {kind:video ? 'video' : 'photo'} : {}), sourceArea:viewer ? 'viewer' : 'gallery', slidePosition, slideTotal,
    photoTotal:mercado ? galleryPhotoCount(gallery,isVisible) : null};
}
// Validate the current visible item again immediately before clicking its own
// viewer control. Neither a hidden next slide nor a guessed URL is consulted.
function moveVisibleGallery(doc, isVisible, page, expected, direction) {
  if (!['next','previous'].includes(direction) || page.sourceArea !== 'viewer' || !expected ||
      !Number.isSafeInteger(page.slidePosition) || !Number.isSafeInteger(page.slideTotal) ||
      page.slidePosition !== expected.slidePosition || page.slideTotal !== expected.slideTotal ||
      page.photoTotal !== expected.photoTotal || page.kind !== expected.kind ||
      page.page_url !== expected.page_url || JSON.stringify(page.variation) !== JSON.stringify(expected.variation) ||
      JSON.stringify(page.rows) !== JSON.stringify(expected.rows)) galleryError('gallery_walk_changed','翻页前当前图库项发生变化');
  const next = direction === 'next';
  if (next ? page.slidePosition >= page.slideTotal : page.slidePosition <= 1) galleryError('gallery_walk_boundary','当前项已到达图库边界');
  const viewers = [...doc.querySelectorAll('.pswp.pswp--open, .pswp[aria-hidden="false"]')].filter(isVisible);
  const selector = next ? 'button.pswp__button--arrow--right,button.pswp__button--arrow--next'
    : 'button.pswp__button--arrow--left,button.pswp__button--arrow--prev';
  const controls = viewers.length === 1 ? [...viewers[0].querySelectorAll(selector)].filter(button =>
    isVisible(button) && !button.disabled && button.getAttribute('aria-disabled') !== 'true') : [];
  if (controls.length !== 1) galleryError('gallery_walk_control_missing','无法唯一确认当前查看器的翻页按钮，可使用逐张操作');
  controls[0].click();
}
if (typeof module !== 'undefined') module.exports = {readVisibleGallery,moveVisibleGallery};

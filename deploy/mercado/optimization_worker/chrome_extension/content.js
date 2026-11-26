"use strict";

(() => {
  const BLOCK_TEXT = /(?:captcha|verify you are human|security verification|sign\s*in|log\s*in|region (?:is )?(?:unavailable|unsupported)|not available in your (?:country|region))/i;

  function canonicalProductId(value) {
    try {
      const url = new URL(value);
      if (url.protocol !== "https:" || !["www.mercadolibre.com.mx", "listado.mercadolibre.com.mx", "articulo.mercadolibre.com.mx"].includes(url.hostname) || url.port || url.username || url.password) return null;
      const match = /(?:^|[^a-z0-9])(MLMU?)[-_ ]*(\d+)(?!\d)/i.exec(url.pathname);
      return match ? `${match[1].toUpperCase()}${match[2]}` : null;
    } catch (_error) { return null; }
  }

  function normalizeBox(rect, viewport) {
    const scale = Number.isFinite(viewport?.scale) && viewport.scale > 0 ? viewport.scale : 1;
    const viewportWidth = Number(viewport?.width);
    const viewportHeight = Number(viewport?.height);
    if (!(viewportWidth > 0) || !(viewportHeight > 0)) {
      return null;
    }
    const left = Math.max(0, Math.floor(Number(rect.left)));
    const top = Math.max(0, Math.floor(Number(rect.top)));
    const right = Math.min(viewportWidth, Math.ceil(Number(rect.right)));
    const bottom = Math.min(viewportHeight, Math.ceil(Number(rect.bottom)));
    if (![left, top, right, bottom].every(Number.isFinite) || right <= left || bottom <= top) {
      return null;
    }
    return [left, top, right - left, bottom - top].map((value) => Math.round(value * scale));
  }

  function classifyVisibleAd(card) {
    const labels = Array.isArray(card?.visibleLabels) ? card.visibleLabels : [];
    const normalized = labels.map((value) => String(value).trim()).filter(Boolean);
    const adLabels = normalized.filter((value) => /^(?:Patrocinado|Publicidad)$/i.test(value));
    const unknownLabels = normalized.filter((value) => !/^(?:Patrocinado|Publicidad)$/i.test(value));
    const organic = card?.visiblyOrganic === true;
    if (unknownLabels.length || (adLabels.length > 0 && organic) || (adLabels.length === 0 && !organic)) {
      throw new Error("ambiguous ad state");
    }
    return adLabels.length > 0;
  }

  function mergeFirstVisible(cards, prior) {
    if (!Array.isArray(cards) || !Array.isArray(prior)) {
      throw new TypeError("cards and prior must be arrays");
    }
    const merged = prior.map((card) => ({...card}));
    const identities = new Set();
    for (const card of merged) {
      const identity = canonicalProductId(card.url);
      if (!identity || (card.product_id != null && String(card.product_id) !== identity)) {
        throw new Error("product identity disagreement");
      }
      identities.add(identity);
    }
    for (const raw of cards) {
      const identity = canonicalProductId(raw?.url);
      if (!identity || (raw.product_id != null && String(raw.product_id) !== identity)) {
        throw new Error("product identity disagreement");
      }
      if (typeof raw.is_ad !== "boolean") {
        throw new Error("ambiguous ad state");
      }
      if (!identities.has(identity)) {
        merged.push({...raw, product_id: identity, rank: merged.length + 1});
        identities.add(identity);
      }
    }
    return merged;
  }

  function isVisible(element) {
    if (!element || typeof element.getBoundingClientRect !== "function") {
      return false;
    }
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0 || rect.bottom <= 0 || rect.right <= 0 || rect.top >= innerHeight || rect.left >= innerWidth) {
      return false;
    }
    const style = getComputedStyle(element);
    return style.visibility !== "hidden" && style.display !== "none" && Number(style.opacity || 1) !== 0;
  }

  function visibleText(element) {
    return isVisible(element) ? (element.innerText || element.textContent || "").trim() : "";
  }

  function detectBlockingWall() {
    const candidates = document.querySelectorAll('[role="dialog"], iframe[title], form, h1, h2, [class*="captcha" i], [id*="captcha" i]');
    for (const element of candidates) {
      const text = `${visibleText(element)} ${element.getAttribute?.("title") || ""}`.trim();
      if (text && BLOCK_TEXT.test(text)) {
        return `Collection stopped: blocking page detected (${text.slice(0, 80)}).`;
      }
    }
    return null;
  }

  function candidateCards(root = document) {
    const selectors = [
      ".ui-search-layout__item", ".poly-card", '[class*="ui-search-result__wrapper" i]',
    ];
    const result = [];
    const seen = new Set();
    for (const element of root.querySelectorAll(selectors.join(","))) {
      if (!seen.has(element) && isVisible(element)) {
        seen.add(element);
        result.push(element);
      }
    }
    return result.filter((element) => !result.some((other) => other !== element && other.contains(element))).sort((left, right) => {
      const a = left.getBoundingClientRect();
      const b = right.getBoundingClientRect();
      return a.top - b.top || a.left - b.left;
    });
  }

  function cardTitle(card, link, image) {
    const elements = card.querySelectorAll('[class*="title" i], [data-title], h2, h3');
    for (const element of elements) {
      const text = visibleText(element);
      if (text) return text;
    }
    return visibleText(link);
  }

  function adState(card) {
    const markers = card.querySelectorAll('[class*="sponsor" i], [class*="advert" i], [class*="promoted" i], [data-ad], [aria-label*="patrocinado" i]');
    const labels = [];
    let ambiguousMarker = false;
    for (const marker of markers) {
      const text = visibleText(marker);
      if (!text) continue;
      if (/^(?:Patrocinado|Publicidad)$/i.test(text)) labels.push(text);
      else ambiguousMarker = true;
    }
    for (const element of card.querySelectorAll("*")) {
      const text = visibleText(element);
      if (/^(?:Patrocinado|Publicidad)$/i.test(text) && !labels.includes(text)) labels.push(text);
    }
    return classifyVisibleAd({visibleLabels: ambiguousMarker ? [...labels, "ambiguous"] : labels, visiblyOrganic: labels.length === 0 && !ambiguousMarker});
  }

  function cardSales(card) {
    const found=[];
    for (const element of card.querySelectorAll('span, p, div')) {
      if (!isVisible(element) || element.children?.length || element.closest('[class*="seller" i], [class*="vendedor" i]')) continue;
      const text=visibleText(element).replace(/\s+/g,' ').trim();
      if (/^\+?\s*(?:\d{1,3}(?:\.\d{3})+|\d+(?:[.,]\d{1,2})?)\s*(?:mil\s+)?vendidos?$/i.test(text)) found.push(text);
    }
    const unique=[...new Set(found)];
    return unique.length===1 ? {status:'known',text:unique[0]} : {status:unique.length?'ambiguous':'missing',text:null};
  }

  function observedNextPage(root, page) {
    if (!Number.isSafeInteger(page) || page<1 || page>3) return {status:'unverified'};
    if (page===3) return {status:'page_limit'};
    const matches=[...root.querySelectorAll('a[rel="next"], [aria-label="Siguiente"]')].filter(isVisible);
    if (matches.length!==1) return {status:'unverified'};
    const next=matches[0];
    if (next.getAttribute('aria-disabled')==='true') return {status:'end_of_results'};
    try {
      const url=new URL(next.href);
      if (url.protocol!=='https:' || url.hostname!=='listado.mercadolibre.com.mx' || url.port || url.username || url.password)
        return {status:'unverified'};
      return {status:'has_next',url:url.href};
    } catch (_) { return {status:'unverified'}; }
  }

  function cardPageInfo(root,page) {
    const empty=/no (?:hay|encontramos) (?:publicaciones|resultados|productos)(?: que coincidan)?|no encontramos resultados/i.test(String(root.body?.innerText || ''));
    if(empty && root.querySelectorAll('.ui-search-layout').length===0) return {status:'end_of_results',end_evidence:'empty_results'};
    if(empty) return {status:'unverified'};
    const result=observedNextPage(root,page);
    return {...result,end_evidence:result.status==='has_next'?'next_link':result.status==='end_of_results'?'disabled_next':result.status==='page_limit'?'page_limit':null};
  }

  function extractSearch(query, prior, limit, strictScope = false, withSales = false) {
    const blockingError = detectBlockingWall();
    if (blockingError) return {error: blockingError};
    const noResults=/no (?:hay|encontramos) (?:publicaciones|resultados|productos)(?: que coincidan)?|no encontramos resultados/i.test(String(document.body?.innerText || ''));
    const scopes = strictScope ? [...document.querySelectorAll('.ui-search-layout')] : [];
    if (strictScope && scopes.length !== 1 && !(scopes.length === 0 && noResults)) {
      return {error: '无法确认搜索结果区域，请等待页面加载后重试'};
    }
    const viewport = {width: innerWidth, height: innerHeight, scale: devicePixelRatio || 1};
    const visible = [];
    const observedIds = new Set(prior.map((item) => item.product_id));
    for (const card of strictScope && !scopes.length ? [] : candidateCards(strictScope ? scopes[0] : document)) {
      const encodedLinks = [...card.querySelectorAll('a[href*="MLM"]')];
      const cardIds = new Set(encodedLinks.map((element) => canonicalProductId(element.href)).filter(Boolean));
      if (cardIds.size === 1 && observedIds.has([...cardIds][0])) continue;
      const link = [...card.querySelectorAll('a[href*="MLM"]')].find(isVisible);
      const image = [...card.querySelectorAll("img")].find(isVisible);
      if (!link || !image) {
        const pendingLink = [...card.querySelectorAll('a[href*="MLM"]')].some((element) => element.getBoundingClientRect().top >= innerHeight);
        const pendingImage = [...card.querySelectorAll("img")].some((element) => element.getBoundingClientRect().top >= innerHeight);
        if ((!link && pendingLink) || (!image && pendingImage)) break;
        const anyLink = card.querySelector('a[href]');
        const anyImage = card.querySelector('img');
        const title = (card.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 120);
        const matching = [...card.querySelectorAll('a[href*="MLM"]')];
        const linkRect = matching[0]?.getBoundingClientRect();
        const imageRect = anyImage?.getBoundingClientRect();
        let linkPath = '';
        try { const u = new URL(anyLink?.href); linkPath = `${u.hostname}${u.pathname}`.slice(0, 160); } catch (_) {}
        return {error: `Collection stopped: card link=${link ? 'visible' : anyLink ? 'not visible or lacks MLM' : 'missing'}; matching=${matching.length}; linkY=${linkRect ? Math.round(linkRect.top) : 'none'}; image=${image ? 'visible' : anyImage ? 'outside viewport' : 'missing'}; imageY=${imageRect ? Math.round(imageRect.top) : 'none'}; viewport=${innerHeight}; firstLink=${linkPath}; card=${card.className}; text=${title}`};
      }
      const url = link.href;
      const productId = canonicalProductId(url);
      const explicit = productId;
      const title = cardTitle(card, link, image);
      const bbox = normalizeBox(card.getBoundingClientRect(), viewport);
      if (!title || !productId || !bbox) {
        const titles = [...card.querySelectorAll('[class*="title" i], [data-title], h2, h3')];
        const tr = titles[0]?.getBoundingClientRect();
        let path = ''; try { const u = new URL(url); path = `${u.hostname}${u.pathname}`.slice(0, 160); } catch (_) {}
        return {error: `Collection stopped: incomplete card title=${Boolean(title)}; id=${Boolean(productId)}; bbox=${Boolean(bbox)}; titleY=${tr ? Math.round(tr.top) : 'none'}; viewport=${innerHeight}; link=${path}`};
      }
      try {
        const sales = withSales ? cardSales(card) : null;
        visible.push({
          query,
          is_ad: adState(card),
          title,
          url,
          product_id: explicit,
          thumbnail_url: image.currentSrc || image.src || null,
          bbox,
          ...(withSales ? {sales_text:sales.text,sales_status:sales.status} : {}),
        });
      } catch (error) {
        return {error: `Collection stopped: ${error.message}.`};
      }
    }
    try {
      const observations = mergeFirstVisible(visible, prior).slice(0, limit);
      const input = document.querySelector('input[name="as_word"], input[name="q"], input.nav-search-input');
      const searchQuery = input && isVisible(input) ? input.value : null;
      return {page_url: location.href, search_query: searchQuery, observations, no_results:noResults,
              ...(strictScope ? {verified_scope:true} : {}), ...(withSales ? {card_sales_version:3} : {})};
    } catch (error) {
      return {error: `Collection stopped: ${error.message}.`};
    }
  }

  function extractSearchIdentity() {
    const blockingError=detectBlockingWall();
    if(blockingError) return {error:blockingError};
    const input=document.querySelector('input[name="as_word"], input[name="q"], input.nav-search-input');
    return {page_url:location.href,search_query:input && isVisible(input)?input.value:null};
  }

  function extractBoundSearchIdentity() {
    const blockingError=detectBlockingWall();
    if(blockingError)return {error:blockingError};
    const input=document.querySelector('input[name="as_word"], input[name="q"], input.nav-search-input');
    const rect=input?.getBoundingClientRect(),style=input && getComputedStyle(input);
    const rendered=!!rect && rect.width>0 && rect.height>0 && style.visibility!=='hidden' && style.display!=='none' && Number(style.opacity || 1)!==0;
    return {page_url:location.href,search_query:rendered?input.value:null};
  }

  function firstVisible(selectors) {
    for (const selector of selectors) {
      const element = [...document.querySelectorAll(selector)].find(isVisible);
      if (element) return element;
    }
    return null;
  }

  function unionBoxes(boxes) {
    if (!boxes.length) return null;
    const left = Math.min(...boxes.map((box) => box[0]));
    const top = Math.min(...boxes.map((box) => box[1]));
    const right = Math.max(...boxes.map((box) => box[0] + box[2]));
    const bottom = Math.max(...boxes.map((box) => box[1] + box[3]));
    return [left, top, right - left, bottom - top];
  }

  function extractVisibleMetrics(entries) {
    const found = {sold: new Set(), reviews: new Set(), rating: new Set()};
    for (const entry of entries) {
      const text = String(entry?.text || "").trim();
      if (!text || !Array.isArray(entry.bbox)) continue;
      if (entry.kind === "sold") {
        const match = /(?:más de\s*)?[+]?\s*[\d.,]+\s*(?:mil|k)?\s*\+?\s+vendidos\b/i.exec(text);
        if (match && !/vendedor|seguidores/i.test(text)) found.sold.add(match[0].trim());
      } else if (entry.kind === "reviews") {
        const match = /(?:\(?\s*([\d.,]+)\s*\)?\s*(?:opiniones|reseñas|calificaciones))/i.exec(text);
        if (match) found.reviews.add(match[1]);
      } else if (entry.kind === "rating" && /^[0-5](?:[.,]\d+)?$/.test(text)) found.rating.add(text);
    }
    const single = (values) => values.size === 1 ? [...values][0] : null;
    return {sold_display: single(found.sold), reviews_display: single(found.reviews), rating_display: single(found.rating), metrics_bbox: null};
  }

  function extractDetail(expectedIdentity) {
    const blockingError = detectBlockingWall();
    const viewport = {width: innerWidth, height: innerHeight, scale: devicePixelRatio || 1};
    if (blockingError) return {
      blocked: true,
      blocking_error: blockingError,
      page_url: location.href,
      access_bbox: normalizeBox({left: 0, top: 0, right: innerWidth, bottom: innerHeight}, viewport),
    };
    const productId = canonicalProductId(location.href);
    const actualIdentity = productId ? `mercado-libre-mx:${productId}` : null;
    const titleElement = firstVisible(["h1", '[class*="product-title" i]', '[class*="goods-title" i]']);
    const imageElement = firstVisible(['[class*="product" i] img', "main img"]);
    const metricEntries = [];
    const metricSelectors = {
      reviews: '[class*="review" i], [class*="opinion" i]',
      rating: '[class*="rating" i], [aria-label*="rating" i], [title*="rating" i], [data-rating], [itemprop="ratingValue"]',
      sold: '.ui-pdp-subtitle, [class*="sold" i], [class*="sales" i]',
    };
    for (const [kind, selector] of Object.entries(metricSelectors)) {
      for (const element of document.querySelectorAll(selector)) {
        const text = visibleText(element);
        const bbox = isVisible(element) ? normalizeBox(element.getBoundingClientRect(), viewport) : null;
        if (text && text.length <= 120 && bbox) metricEntries.push({kind, text, bbox});
      }
    }
    const metrics = extractVisibleMetrics(metricEntries);
    const visualBox = imageElement ? normalizeBox(imageElement.getBoundingClientRect(), viewport) : null;
    const accessBox = titleElement
      ? normalizeBox(titleElement.getBoundingClientRect(), viewport)
      : normalizeBox({left: 0, top: 0, right: innerWidth, bottom: innerHeight}, viewport);
    return {
      page_url: location.href,
      identity: actualIdentity,
      identity_matches: actualIdentity === expectedIdentity,
      product_id: productId,
      title: titleElement ? visibleText(titleElement) : null,
      category: visibleText(firstVisible(['nav[aria-label*="breadcrumb" i]', '[class*="breadcrumb" i]'])) || null,
      sold_display: metrics.sold_display,
      reviews_display: metrics.reviews_display,
      rating_display: metrics.rating_display,
      visual_bbox: visualBox,
      metrics_bbox: metrics.metrics_bbox,
      access_bbox: accessBox,
    };
  }

  function galleryManifest(pageUrl, expectedId, rows) {
    if (canonicalProductId(pageUrl) !== expectedId) throw new Error("gallery identity mismatch");
    if (!Array.isArray(rows) || !rows.length || rows.some(x => x.total !== rows.length)) throw new Error("gallery incomplete; expand all main gallery images");
    const images=[];
    rows.forEach((row,i)=>{
      if(row.position !== i+1) throw new Error("gallery order ambiguous");
      const u=new URL(row.url);
      if(u.protocol !== "https:" || !/(^|\.)mlstatic\.com$/.test(u.hostname) || u.username || u.password) throw new Error("untrusted gallery image");
      if(images.includes(u.href)) throw new Error("duplicate gallery image");
      images.push(u.href);
    });
    const u=new URL(pageUrl);u.search="";u.hash="";
    return {detail_url:u.href,images};
  }

  function extractGallery(expectedId) {
    const error=detectBlockingWall(); if(error) return {error};
    try {
      const containers=[...document.querySelectorAll('.ui-pdp-gallery')].filter(isVisible);
      if(containers.length!==1) throw new Error("main gallery is missing or ambiguous");
      const buttons=[...containers[0].querySelectorAll('button[aria-label]')].filter(isVisible);
      const rows=[];
      for(const button of buttons){
        const m=/^Imagen\s+(\d+)\s+de\s+(\d+)/i.exec(button.getAttribute('aria-label') || '');
        if(!m) continue;
        const imgs=[...button.querySelectorAll('img')].filter(isVisible);
        if(imgs.length!==1) throw new Error("gallery thumbnail is not visibly available");
        if (imgs[0].naturalWidth < 500 || imgs[0].naturalHeight < 500) throw new Error("thumbnail is not a verified full-size gallery image");
        const src=imgs[0].currentSrc || imgs[0].src;
        rows.push({position:Number(m[1]),total:Number(m[2]),url:src});
      }
      return {manifest:galleryManifest(location.href,expectedId,rows)};
    } catch(e){return {error:e.message};}
  }

  function scrollPage() {
    const blockingError = detectBlockingWall();
    if (blockingError) return {error: blockingError};
    const before = scrollY;
    const overlap = Math.floor(window.innerHeight * 0.20);
    const step = Math.max(1, window.innerHeight - overlap);
    window.scrollBy({top: step, left: 0, behavior: "instant"});
    return {before, step, at_end: Math.ceil(scrollY + innerHeight) >= document.documentElement.scrollHeight};
  }

  if (globalThis.__MERCADO_COLLECTOR_TEST__) {
    Object.assign(globalThis.__MERCADO_COLLECTOR_TEST__, {
      canonicalProductId,
      normalizeBox,
      classifyVisibleAd,
      mergeFirstVisible,
      extractVisibleMetrics,
      extractSearch,
      cardSales,
      observedNextPage,
      cardPageInfo,
      extractBoundSearchIdentity,
      galleryManifest,
    });
  }

  if (typeof chrome !== "undefined" && chrome.runtime?.onMessage) {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (message?.type === "MERCADO_COLLECTOR_STATUS") {
        sendResponse({error: detectBlockingWall(), page_url: location.href});
      } else if (message?.type === "MERCADO_COLLECTOR_SEARCH_IDENTITY") {
        sendResponse(extractSearchIdentity());
      } else if (message?.type === "MERCADO_COLLECTOR_PAGE_BINDING") {
        sendResponse(extractBoundSearchIdentity());
      } else if (message?.type === "MERCADO_COLLECTOR_SCROLL_TOP") {
        const blockingError=detectBlockingWall();
        if(blockingError) sendResponse({ok:false});
        else {window.scrollTo({top:0,left:0,behavior:'instant'});sendResponse({ok:true});}
      } else if (message?.type === "MERCADO_COLLECTOR_EXTRACT_SEARCH") {
        sendResponse(extractSearch(message.query, message.prior || [], message.limit, message.strict_scope === true, message.with_sales === true));
      } else if (message?.type === "MERCADO_COLLECTOR_PAGE_INFO") {
        const blockingError=detectBlockingWall();
        sendResponse(blockingError ? {error:blockingError} : {...cardPageInfo(document,message.page),page_url:location.href,
          at_end:Math.ceil(scrollY+innerHeight)>=document.documentElement.scrollHeight});
      } else if (message?.type === "MERCADO_COLLECTOR_SCROLL") {
        sendResponse(scrollPage());
      } else if (message?.type === "MERCADO_COLLECTOR_EXTRACT_GALLERY") {
        sendResponse(extractGallery(message.product_id));
      } else if (message?.type === 'COLLECTOR_VISIBLE_GALLERY' || message?.type === 'COLLECTOR_GALLERY_WALK_READ' ||
                 message?.type === 'COLLECTOR_GALLERY_WALK_MOVE') {
        const detail = extractDetail(message.identity);
        if (detail.blocked || !detail.identity_matches) sendResponse({error:detail.blocking_error || '图库商品身份与当前详情不一致'});
        else {
          try {
            const page = {identity:detail.identity, page_url:detail.page_url,
              ...readVisibleGallery('mercado-libre-mx', document, isVisible, {includeVideo:message.type !== 'COLLECTOR_VISIBLE_GALLERY'})};
            if (message.type === 'COLLECTOR_GALLERY_WALK_MOVE') {
              moveVisibleGallery(document,isVisible,page,message.expected,message.direction);
              sendResponse({ok:true});
            } else sendResponse(page);
          } catch (error) { sendResponse({error:error.message}); }
        }
      } else if (message?.type === "MERCADO_COLLECTOR_EXTRACT_DETAIL") {
        sendResponse(extractDetail(message.identity));
      }
      return false;
    });
  }
})();

"use strict";

(() => {
  const BLOCK_TEXT = /(?:captcha|verify you are human|security verification|sign\s*in|log\s*in|region (?:is )?(?:unavailable|unsupported)|not available in your (?:country|region))/i;

  function canonicalProductId(value) {
    try {
      const url = new URL(value);
      if (url.protocol !== "https:" || url.hostname !== "us.shein.com" || url.port) {
        return null;
      }
      const match = /-p-(\d+)\.html$/i.exec(url.pathname);
      return match ? match[1] : null;
    } catch (_error) {
      return null;
    }
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
    const adLabels = normalized.filter((value) => /^(?:Sponsored|Ad)$/i.test(value));
    const unknownLabels = normalized.filter((value) => !/^(?:Sponsored|Ad)$/i.test(value));
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

  function candidateCards() {
    const selectors = [
      "[data-goods-id]", "[data-product-id]", ".S-product-item", ".product-card",
      '[class*="product-card" i]', '[class*="product-item" i]',
    ];
    const result = [];
    const seen = new Set();
    for (const element of document.querySelectorAll(selectors.join(","))) {
      if (!seen.has(element) && isVisible(element)) {
        seen.add(element);
        result.push(element);
      }
    }
    return result.sort((left, right) => {
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
    const markers = card.querySelectorAll('[class*="sponsor" i], [class*="advert" i], [data-ad], [aria-label*="sponsor" i]');
    const labels = [];
    let ambiguousMarker = false;
    for (const marker of markers) {
      const text = visibleText(marker);
      if (!text) continue;
      if (/^(?:Sponsored|Ad)$/i.test(text)) labels.push(text);
      else ambiguousMarker = true;
    }
    for (const element of card.querySelectorAll("*")) {
      const text = visibleText(element);
      if (/^(?:Sponsored|Ad)$/i.test(text) && !labels.includes(text)) labels.push(text);
    }
    return classifyVisibleAd({visibleLabels: ambiguousMarker ? [...labels, "ambiguous"] : labels, visiblyOrganic: labels.length === 0 && !ambiguousMarker});
  }

  function extractSearch(query, prior, limit) {
    const blockingError = detectBlockingWall();
    if (blockingError) return {error: blockingError};
    const viewport = {width: innerWidth, height: innerHeight, scale: devicePixelRatio || 1};
    const visible = [];
    for (const card of candidateCards()) {
      const link = [...card.querySelectorAll('a[href*="-p-"][href*=".html"]')].find(isVisible);
      const image = [...card.querySelectorAll("img")].find(isVisible);
      if (!link || !image) continue;
      const url = link.href;
      const productId = canonicalProductId(url);
      const explicit = card.getAttribute("data-goods-id") || card.getAttribute("data-product-id") || productId;
      const title = cardTitle(card, link, image);
      const bbox = normalizeBox(card.getBoundingClientRect(), viewport);
      if (!title || !productId || !bbox) continue;
      try {
        visible.push({
          query,
          is_ad: adState(card),
          title,
          url,
          product_id: explicit,
          thumbnail_url: image.currentSrc || image.src || null,
          bbox,
        });
      } catch (error) {
        return {error: `Collection stopped: ${error.message}.`};
      }
    }
    try {
      const observations = mergeFirstVisible(visible, prior).slice(0, limit);
      return {page_url: location.href, search_query: SheinVisibleSearch.visibleSearch(document, isVisible), observations};
    } catch (error) {
      return {error: `Collection stopped: ${error.message}.`};
    }
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
    let reviews = null;
    let rating = null;
    let sold = null;
    const evidenceBoxes = [];
    for (const entry of entries) {
      const text = String(entry?.text || "").trim();
      if (!text || !Array.isArray(entry.bbox)) continue;
      let matched = false;
      if (entry.kind === "reviews") {
        const match = /([\d,.]+\+?)\s*(?:reviews?|ratings?)/i.exec(text)
          || /\bcustomer\s+reviews?\s*\(\s*([\d,.]+\+?)\s*\)/i.exec(text);
        if (match) { reviews ??= match[1]; matched = true; }
      } else if (entry.kind === "rating") {
        const match = /\baverage\s+rating\s*:?\s*([0-5](?:\.\d+)?)\b/i.exec(text)
          || /(?:^|\brating\s*:?\s*)([0-5](?:\.\d+)?)\s*(?:out\s+of|\/)\s*5\b/i.exec(text)
          || /^\s*rating\s*:?\s*([0-5](?:\.\d+)?)\s*$/i.exec(text)
          || /^\s*([0-5](?:\.\d+)?)\s*$/.exec(text);
        if (match) { rating ??= match[1]; matched = true; }
      } else if (entry.kind === "sold") {
        const match = /([\d,.]+\+?\s*sold)/i.exec(text);
        if (match) { sold ??= match[1]; matched = true; }
      }
      if (matched) evidenceBoxes.push(entry.bbox);
    }
    return {sold_display: sold, reviews_display: reviews, rating_display: rating, metrics_bbox: unionBoxes(evidenceBoxes)};
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
    const actualIdentity = productId ? `shein-us:${productId}` : null;
    const titleElement = firstVisible(["h1", '[class*="product-title" i]', '[class*="goods-title" i]']);
    const imageElement = firstVisible(['[class*="product" i] img', "main img"]);
    const metricEntries = [];
    const metricSelectors = {
      reviews: '[class*="review" i], [aria-label*="review" i], [title*="review" i]',
      rating: '[class*="rating" i], [aria-label*="rating" i], [title*="rating" i], [data-rating], [itemprop="ratingValue"]',
      sold: '[class*="sold" i], [aria-label*="sold" i]',
    };
    for (const [kind, selector] of Object.entries(metricSelectors)) {
      for (const element of document.querySelectorAll(selector)) {
        const text = visibleText(element);
        const bbox = isVisible(element) ? normalizeBox(element.getBoundingClientRect(), viewport) : null;
        if (text && text.length <= 120 && bbox) metricEntries.push({kind, text, bbox});
        // Accessible labels belong to visible controls; do not read hidden DOM
        // or data-rating values as a substitute for displayed product evidence.
        if (bbox) {
          const label = (element.getAttribute("aria-label") || "").trim();
          if (label && label.length <= 120 && /\baverage\s+rating\b/i.test(label)) {
            metricEntries.push({kind: "rating", text: label, bbox});
            metricEntries.push({kind: "reviews", text: label, bbox});
          }
        }
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

  function scrollPage() {
    const blockingError = detectBlockingWall();
    if (blockingError) return {error: blockingError};
    const before = scrollY;
    const overlap = Math.floor(window.innerHeight * 0.20);
    const step = Math.max(1, window.innerHeight - overlap);
    window.scrollBy({top: step, left: 0, behavior: "instant"});
    return {before, step, at_end: Math.ceil(scrollY + innerHeight) >= document.documentElement.scrollHeight};
  }

  function nextSearchPage() {
    const blockingError = detectBlockingWall();
    if (blockingError) return {error: blockingError};
    const navigation = document.querySelector('[aria-label="Pagination Navigation"]');
    const next = navigation?.querySelector('[aria-label="Next page"]');
    if (!next || next.disabled || next.getAttribute("aria-disabled") === "true") return {clicked: false};
    next.scrollIntoView({block: "center", behavior: "instant"});
    if (!isVisible(next)) return {error: "Collection stopped: next page control is not visible."};
    setTimeout(() => next.click(), 0);
    return {clicked: true};
  }

  if (globalThis.__SHEIN_COLLECTOR_TEST__) {
    Object.assign(globalThis.__SHEIN_COLLECTOR_TEST__, {
      canonicalProductId,
      normalizeBox,
      classifyVisibleAd,
      mergeFirstVisible,
      extractVisibleMetrics,
      extractDetail,
      nextSearchPage,
    });
  }

  if (typeof chrome !== "undefined" && chrome.runtime?.onMessage) {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (message?.type === "SHEIN_COLLECTOR_STATUS") {
        sendResponse({error: detectBlockingWall(), page_url: location.href, search_ready: candidateCards().length > 0});
      } else if (message?.type === "SHEIN_COLLECTOR_EXTRACT_SEARCH") {
        sendResponse(extractSearch(message.query, message.prior || [], message.limit));
      } else if (message?.type === "SHEIN_COLLECTOR_SCROLL") {
        sendResponse(scrollPage());
      } else if (message?.type === "SHEIN_COLLECTOR_NEXT_PAGE") {
        sendResponse(nextSearchPage());
      } else if (message?.type === "SHEIN_COLLECTOR_SEARCH_TOP") {
        window.scrollTo({top: 0, left: 0, behavior: "instant"});
        sendResponse({page_url: location.href});
      } else if (message?.type === "COLLECTOR_VISIBLE_GALLERY") {
        const detail = extractDetail(message.identity);
        if (detail.blocked || !detail.identity_matches) sendResponse({error:detail.blocking_error || '图库商品身份与当前详情不一致'});
        else {
          try {
            sendResponse({identity:detail.identity, page_url:detail.page_url,
              ...readVisibleGallery('shein-us', document, isVisible)});
          } catch (error) { sendResponse({error:error.message}); }
        }
      } else if (message?.type === "SHEIN_COLLECTOR_EXTRACT_DETAIL") {
        sendResponse(extractDetail(message.identity));
      }
      return false;
    });
  }
})();

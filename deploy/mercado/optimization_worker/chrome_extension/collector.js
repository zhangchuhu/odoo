"use strict";

(() => {
  const RECORD_ID = /^rec[A-Za-z0-9_-]+$/;
  const CATEGORY_CODES = new Set(["dress", "formal_gown", "jumpsuit", "overalls", "womens_pants_set", "womens_shorts_set", "womens_skirt_set", "womens_suit", "top", "blouse", "shirt", "vest", "blazer", "jacket", "coat", "sweater", "cardigan", "pants", "shorts", "skirt", "shoes"]);
  const DETAIL_KEYS = [
    "identity", "status", "reason", "detail_url", "product_id", "title", "category", "visual_category_code",
    "sold_display", "reviews_display", "rating_display", "match_level", "visual_features",
  ];
  const REJECTION_REASONS = new Set([
    "visual_category_mismatch", "imagery_ambiguous_or_inaccessible",
    "metric_missing_or_ambiguous", "threshold_failure", "identity_changed",
    "detail_inaccessible",
  ]);

  function canonicalProductId(value) {
    try {
      const url = new URL(value);
      if (url.protocol !== "https:" || !["www.mercadolibre.com.mx", "listado.mercadolibre.com.mx", "articulo.mercadolibre.com.mx"].includes(url.hostname) || url.port || url.username || url.password) return null;
      const match = /(?:^|[^a-z0-9])(MLMU?)[-_ ]*(\d+)(?!\d)/i.exec(url.pathname);
      return match ? `${match[1].toUpperCase()}${match[2]}` : null;
    } catch (_error) { return null; }
  }

  function isSearchPage(value) {
    try { const url = new URL(value); return url.protocol === "https:" && url.hostname === "listado.mercadolibre.com.mx" && !url.port; } catch (_) { return false; }
  }

  function checkSearchContext(result, query, boundUrl) {
    if (!isSearchPage(result.page_url) || (boundUrl !== null && result.page_url !== boundUrl) ||
        (result.search_query !== query && !(boundUrl !== null && result.search_query === null))) {
      throw new Error("current page does not match the exact Codex query");
    }
    return result.page_url;
  }

  function createSession({taskRecordId, platform, queries, cardLimit, resultLimit}) {
    if (typeof taskRecordId !== "string" || !RECORD_ID.test(taskRecordId)) throw new Error("invalid task record ID");
    if (platform !== "mercado-libre-mx") throw new Error("platform must be mercado-libre-mx");
    if (!Array.isArray(queries) || queries.length !== 3 || queries.some((query) => typeof query !== "string" || !query.trim()) || new Set(queries).size !== 3) {
      throw new Error("exactly three distinct Codex queries are required");
    }
    if (!Number.isInteger(cardLimit) || cardLimit < 30 || cardLimit > 50) throw new Error("card limit must be 30-50");
    if (!Number.isInteger(resultLimit) || resultLimit < 1) throw new Error("task result limit must be a positive integer");
    return {
      taskRecordId,
      platform,
      queries: [...queries],
      cardLimit,
      resultLimit,
      queryBlocks: queries.map((query) => ({query, observations: []})),
      details: [],
      stopped: false,
    };
  }

  function addSearchObservations(session, {query, cards, pageUrl = null}) {
    const blockIndex = session.queries.indexOf(query);
    if (blockIndex < 0 || (pageUrl !== null && !isSearchPage(pageUrl))) throw new Error("search page does not match exact Codex query");
    const block = session.queryBlocks[blockIndex];
    if (!Array.isArray(cards) || block.observations.length + cards.length > session.cardLimit) throw new Error("search observations exceed card limit");
    const seen = new Set(block.observations.map((card) => card.product_id));
    const additions = [];
    for (let index = 0; index < cards.length; index += 1) {
      const card = cards[index];
      const productId = canonicalProductId(card?.url);
      const expectedRank = block.observations.length + index + 1;
      if (!productId || String(card.product_id) !== productId) throw new Error("search card identity disagreement");
      if (card.query !== query || card.rank !== expectedRank || typeof card.is_ad !== "boolean") throw new Error("search cards must have contiguous visible ranks and unambiguous ads");
      if (typeof card.title !== "string" || !card.title) throw new Error("search card visible data is incomplete");
      if (seen.has(productId)) throw new Error("duplicate product in one query");
      if (card.thumbnail_url !== null && (typeof card.thumbnail_url !== "string" || !card.thumbnail_url)) throw new Error("search card thumbnail is invalid");
      seen.add(productId);
      additions.push({
        query: card.query,
        rank: card.rank,
        is_ad: card.is_ad,
        title: card.title,
        url: card.url,
        product_id: productId,
        thumbnail_url: card.thumbnail_url,
      });
    }
    block.observations.push(...additions);
    return block.observations.length;
  }

  function recurringPool(session) {
    const hits = new Map();
    for (const block of session.queryBlocks) {
      for (const card of block.observations) {
        const identity = `mercado-libre-mx:${card.product_id}`;
        if (!hits.has(identity)) hits.set(identity, {queries: new Set(), organic: [], sponsored: []});
        const value = hits.get(identity);
        value.queries.add(block.query);
        (card.is_ad ? value.sponsored : value.organic).push(card.rank);
      }
    }
    return [...hits]
      .filter(([, value]) => value.queries.size >= 2)
      .sort(([leftIdentity, left], [rightIdentity, right]) => {
        const leftOrganic = left.organic.length ? Math.min(...left.organic) : null;
        const rightOrganic = right.organic.length ? Math.min(...right.organic) : null;
        const leftRank = leftOrganic ?? Math.min(...left.sponsored);
        const rightRank = rightOrganic ?? Math.min(...right.sponsored);
        return right.queries.size - left.queries.size
          || Number(leftOrganic === null) - Number(rightOrganic === null)
          || leftRank - rightRank
          || (leftIdentity < rightIdentity ? -1 : leftIdentity > rightIdentity ? 1 : 0);
      })
      .map(([identity]) => identity);
  }

  function searchesComplete(session) {
    return session.queryBlocks.every((block) => block.observations.length === session.cardLimit);
  }

  function qualifyingCount(session) {
    return session.details.filter((detail) => detail.status === "qualified").length;
  }

  function nextExpectedIdentity(session) {
    if (!searchesComplete(session)) throw new Error("complete all three search queries before detail collection");
    const pool = recurringPool(session);
    if (qualifyingCount(session) >= session.resultLimit) throw new Error("task result limit has already been reached");
    if (session.details.length >= pool.length) throw new Error("recurring pool is exhausted");
    return pool[session.details.length];
  }

  function addDetailOutcome(session, detail) {
    if (!detail || typeof detail !== "object" || DETAIL_KEYS.some((key) => !(key in detail)) || Object.keys(detail).length !== DETAIL_KEYS.length) {
      throw new Error("detail has an invalid schema");
    }
    const expectedIdentity = nextExpectedIdentity(session);
    if (detail.identity !== expectedIdentity) throw new Error(`detail must follow recurring-pool order; expected ${expectedIdentity}`);
    const expectedId = expectedIdentity.slice("mercado-libre-mx:".length);
    const detailUrlId = canonicalProductId(detail.detail_url);
    const qualified = detail.status === "qualified" && detail.reason === null;
    const rejected = detail.status === "rejected" && REJECTION_REASONS.has(detail.reason);
    if (!qualified && !rejected) throw new Error("detail status or reason is invalid");
    if (detail.visual_category_code !== null && !CATEGORY_CODES.has(detail.visual_category_code)) throw new Error("visual category is invalid");
    if (detail.reason === "imagery_ambiguous_or_inaccessible" && detail.visual_category_code !== null) throw new Error("ambiguous imagery cannot claim category");
    if (detail.reason === "visual_category_mismatch" && detail.visual_category_code === null) throw new Error("category mismatch requires visual category");
    if (detail.reason === "identity_changed") {
      if (!detailUrlId || detailUrlId === expectedId || detail.product_id !== detailUrlId) throw new Error("detail identity binding failed");
    } else if (detail.reason === "detail_inaccessible") {
      const absent = ["detail_url", "product_id", "title", "category", "visual_category_code", "sold_display", "reviews_display", "rating_display"];
      if (absent.some((key) => detail[key] !== null)) throw new Error("inaccessible detail cannot claim visible fields");
    } else if (detail.product_id !== expectedId || detailUrlId !== expectedId) {
      throw new Error("detail identity binding failed");
    }
    if (qualified) {
      if (!detail.visual_category_code) throw new Error("qualified detail requires visual category");
      if (!(["同款", "高度相似", "类似竞品"].includes(detail.match_level)) || !Array.isArray(detail.visual_features) || detail.visual_features.length === 0 || detail.visual_features.some((value) => typeof value !== "string" || !value.trim())) {
        throw new Error("qualified detail requires match evidence");
      }
      if (typeof detail.title !== "string" || !detail.title || typeof detail.sold_display !== "string" || !detail.sold_display) {
        throw new Error("qualified detail requires visible title and metrics");
      }
    } else if (detail.match_level !== null || detail.visual_features !== null) {
      throw new Error("rejected detail cannot contain match evidence");
    }
    const output = {};
    for (const key of DETAIL_KEYS) output[key] = key === "visual_features" && Array.isArray(detail[key]) ? [...detail[key]] : detail[key];
    session.details.push(output);
    return output;
  }

  function buildEvidence(session) {
    for (const block of session.queryBlocks) {
      if (block.observations.length < 30 || block.observations.length > 50 || block.observations.length !== session.cardLimit) throw new Error("each query must contain the chosen 30-50 observations");
      if (block.observations.some((card, index) => card.rank !== index + 1)) throw new Error("query ranks must be contiguous");
    }
    const pool = recurringPool(session);
    const identities = session.details.map((detail) => detail.identity);
    if (identities.some((identity, index) => identity !== pool[index])) throw new Error("detail outcomes must be an exact recurring-pool prefix in order");
    const qualified = qualifyingCount(session);
    if (qualified > session.resultLimit) throw new Error("detail outcomes continue after the result limit");
    if (qualified === session.resultLimit) {
      const reachedAt = session.details.findIndex((_detail, index) => qualifyingCount({details: session.details.slice(0, index + 1)}) === session.resultLimit);
      if (reachedAt !== session.details.length - 1) throw new Error("detail outcomes continue after the result limit");
    } else if (session.details.length !== pool.length) {
      throw new Error("detail outcomes stopped before the recurring pool was exhausted");
    }
    return {
      task_record_id: session.taskRecordId,
      platform: session.platform,
      queries: session.queryBlocks,
      details: session.details,
    };
  }

  function serializeEvidence(session) {
    return `${JSON.stringify(buildEvidence(session), null, 2)}\n`;
  }

  function requireSafeResponse(response) {
    if (!response || typeof response !== "object") throw new Error("Collection stopped: target page did not respond.");
    if (typeof response.error === "string" && response.error) throw new Error(response.error);
    return response;
  }

  function detailOutcomePlan(expectedIdentity, visible, operator) {
    const base = {
      identity: expectedIdentity,
      status: "rejected",
      reason: null,
      detail_url: null,
      product_id: null,
      title: null,
      category: null,
      visual_category_code: null,
      sold_display: null,
      reviews_display: null,
      rating_display: null,
      match_level: null,
      visual_features: null,
    };
    if (visible.blocked) return {...base, reason: "detail_inaccessible"};
    if (!visible.identity_matches) {
      return {...base, reason: "identity_changed", detail_url: visible.page_url, product_id: visible.product_id, title: visible.title,
        category: visible.category, sold_display: visible.sold_display, reviews_display: visible.reviews_display, rating_display: visible.rating_display};
    }
    const status = operator.status;
    return {...base, status, reason: status === "qualified" ? null : operator.reason, detail_url: visible.page_url,
      product_id: visible.product_id, title: visible.title, category: visible.category, visual_category_code: operator.visualCategoryCode || null, sold_display: visible.sold_display,
      reviews_display: visible.reviews_display, rating_display: visible.rating_display,
      match_level: status === "qualified" ? operator.matchLevel : null,
      visual_features: status === "qualified" ? operator.visualFeatures : null};
  }

  const testExports = globalThis.__MERCADO_COLLECTOR_TEST__;
  if (testExports) {
    Object.assign(testExports, {
      createSession, addSearchObservations, addDetailOutcome, buildEvidence,
      serializeEvidence, requireSafeResponse, detailOutcomePlan, nextExpectedIdentity,
      checkSearchContext,
    });
  }

  if (typeof document === "undefined" || typeof chrome === "undefined") return;

  const source = new URL(location.href).searchParams;
  const tabId = Number(source.get("tab"));
  let session = null;
  const persistence = new SessionPersistence(chrome.storage.local);
  async function browserSession() {
    const r=await chrome.runtime.sendMessage({type:"collector_session_id"});
    if (!r || !/^[a-f0-9]{32}$/.test(r.session_id || "")) throw new Error("浏览器会话不可用");
    return r.session_id;
  }
  let collecting = false;
  function collectOnce(action) {
    return async event => {
      if (collecting) return;
      collecting = true;
      try { await action(event); } finally { collecting = false; }
    };
  }
  async function persist() { await persistence.save(tabId, await browserSession(), session); }

  async function restoreDetails(target, stored, local) {
    if (!Array.isArray(stored)) throw Error('detail_checkpoint_missing');
    const shared = Math.min(stored.length, local.length);
    if (!equal(stored.slice(0, shared), local.slice(0, shared))) {
      throw Error('detail_checkpoint_conflict：本地与后台候选详情不一致');
    }
    const restored = {...target, details: []};
    for (const detail of stored) addDetailOutcome(restored, detail);
    for (const detail of local.slice(shared)) {
      const index = restored.details.length;
      addDetailOutcome(restored, detail);
      await globalThis.collectorCheckpoint('checkpoint_detail', {detail_index: index, detail});
    }
    target.details = restored.details;
  }

  async function readConfirmedDetails(index, binding) {
    if (!binding || typeof binding.request_id !== 'string' || !binding.request_id ||
        !/^[a-f0-9]{64}$/.test(binding.plan_digest) || !Number.isSafeInteger(binding.lease_epoch) ||
        binding.lease_epoch < 1 || binding.tab_id !== tabId || !Number.isSafeInteger(binding.window_id) ||
        binding.window_id < 1 || !/^[a-f0-9]{32}$/.test(binding.session_id)) throw Error('checkpoint_binding_invalid');
    const expires = Date.now() + 8000;
    while (Date.now() < expires) {
      let timer;
      try {
        const read = globalThis.collectorCheckpoint('collector_progress', {query_index: index, expected_binding: binding});
        const deadline = new Promise((_, reject) => {
          timer = setTimeout(() => reject(Error('checkpoint_guard_pending')), Math.max(1, expires - Date.now()));
        });
        const progress = await Promise.race([read, deadline]);
        if (!equal(progress.binding, binding)) throw Error('checkpoint_identity_changed');
        if (progress.task !== session.taskRecordId || progress.platform !== session.platform ||
            !equal(progress.queries, session.queries) || progress.query_index !== index ||
            progress.result_limit !== session.resultLimit) throw Error('checkpoint_plan_mismatch');
        if (progress.acknowledged === true) return progress;
      } catch (error) {
        const code = String(error.message || error).split('：')[0];
        if (!['checkpoint_anchor_pending', 'checkpoint_guard_pending', 'broker_unavailable', 'guard_ack_timeout'].includes(code)) throw error;
      } finally {
        clearTimeout(timer);
      }
      if (Date.now() < expires) await delay(Math.min(300, expires - Date.now()));
    }
    throw Error('checkpoint_guard_pending：第三组已保存，等待当前标签连接后继续');
  }

  async function finishSearchGroup(index, priorProgress) {
    const next = {...session, acknowledgedQueries: [...session.acknowledgedQueries, index]};
    if (index === 2) {
      const progress = await readConfirmedDetails(index, priorProgress.binding);
      if (!equal(progress.cards, session.queryBlocks[index].observations)) throw Error('checkpoint_cards_conflict');
      await restoreDetails(next, progress.details, session.details);
    }
    await persistence.save(tabId, await browserSession(), next);
    session = next;
    if (index === 2) showNextProduct();
  }


  function element(id) { return document.getElementById(id); }
  function setStatus(message) { element("status").textContent = message; }
  function clearError() { element("error").textContent = ""; }
  function stopWithError(error) {
    const message = error instanceof Error ? error.message : String(error);
    element("error").textContent = message.startsWith("Collection stopped:") ? message : `Collection stopped: ${message}`;
    // Intermediate business writes are idempotent. Keep the current step and
    // its controls available after reconnecting or correcting a rejected input.
    if (session) setStatus("本步骤已暂停。核对错误后可在原任务继续。");
  }
  async function sendToPage(message) {
    return requireSafeResponse(await chrome.tabs.sendMessage(tabId, message));
  }
  function showNextProduct() {
    element("open-next-candidate").hidden = qualifyingCount(session) >= session.resultLimit || session.details.length === recurringPool(session).length;
    if (element("open-next-candidate").hidden) {
      element("next-product").textContent = "候选核验已完成，可回传证据。";
      return;
    }
    const identity = nextExpectedIdentity(session);
    element("next-product").textContent = `下一候选 ${identity}`;
  }
  function delay(milliseconds) { return new Promise((resolve) => setTimeout(resolve, milliseconds)); }

  async function ensureDedicatedWindow() {
    if (Number.isInteger(tabId) && tabId > 0) return true;
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    let home = false;
    try { const page = new URL(tab.url); home = page.protocol === 'https:' && page.hostname === 'www.mercadolibre.com.mx' && !page.port && !page.username && !page.password && page.pathname === '/'; } catch (_) {}
    if (!Number.isInteger(tab?.id) || !(home || isSearchPage(tab.url) || canonicalProductId(tab.url))) throw new Error("open the target Mercado homepage, search, or product tab before launching the collector");
    const opened = await chrome.runtime.sendMessage({type: "open_collector_popup", tab_id: tab.id});
    if (opened?.ok !== true) throw new Error("Could not open collector popup");
    window.close();
    return false;
  }

  ensureDedicatedWindow().then((ready) => {
    if (!ready) return;
    if (typeof createGalleryController === "function" && typeof ObservedGallery === "function") {
    const observed = createGalleryController(ObservedGallery);
    const observedBinding = () => ({identity:element("observed-id").value.trim().toUpperCase(), detail_url:element("observed-detail").value.trim(), total:Number(element("observed-total").value)});
    const observedAction = (fn) => {try { clearError(); element("observed-progress").textContent=fn(); } catch(e) {element("error").textContent=String(e.message || e);} };
    element("observed-start").addEventListener("click", () => observedAction(() => observed.start(observedBinding())));
    element("observed-add").addEventListener("click", () => observedAction(() => observed.add({...observedBinding(), position:Number(element("observed-position").value), url:element("observed-url").value.trim(), width:Number(element("observed-width").value), height:Number(element("observed-height").value)})));
    element("observed-export").addEventListener("click", async () => {try {clearError();const manifest=observed.export();const url=URL.createObjectURL(new Blob([JSON.stringify(manifest,null,2)],{type:"application/json"}));try {await chrome.downloads.download({url,filename:`gallery-${element("observed-id").value.trim().toUpperCase()}.json`,conflictAction:"uniquify",saveAs:false});} finally {setTimeout(()=>URL.revokeObjectURL(url),1000);}element("observed-progress").textContent="已导出人工核验清单";}catch(e){element("error").textContent=String(e.message || e);}});
    }
    element("export-gallery")?.addEventListener("click", async () => {
      try {
        clearError();
        const id=element("gallery-id").value.trim().toUpperCase();
        if (!/^MLMU?\d+$/.test(id)) throw new Error("enter the exact detail-page product ID");
        const result=await sendToPage({type:"MERCADO_COLLECTOR_EXTRACT_GALLERY",product_id:id});
        const jsonUrl=URL.createObjectURL(new Blob([JSON.stringify(result.manifest,null,2)],{type:"application/json"}));
        try { await chrome.downloads.download({url:jsonUrl,filename:`gallery-${id}.json`,conflictAction:"uniquify",saveAs:false}); }
        finally { setTimeout(()=>URL.revokeObjectURL(jsonUrl),1000); }
        setStatus(`Exported ${result.manifest.images.length} ordered main-gallery images. Retain an actual page screenshot and verify the selected variant.`);
      } catch(error) { element("error").textContent=String(error.message || error); }
    });
    element("setup").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        clearError();
        session = createSession({
          taskRecordId: element("record-id").value,
          platform: element("platform").value,
          queries: [...document.querySelectorAll(".query")].map((input) => input.value),
          cardLimit: Number(element("card-limit").value),
          resultLimit: Number(element("result-limit").value),
        });
        await persist();
        element("setup").hidden = true;
        element("controls").hidden = false;
        setStatus("Ready. Keep the selected Mercado tab open on the first exact query.");
      } catch (error) { stopWithError(error); }
    });

    element("recover-session").addEventListener("click", collectOnce(async () => {
      try {
        clearError();
        const groups = [];
        for (let i = 0; i < 3; i++) {
          groups.push(await globalThis.collectorCheckpoint('collector_progress', {query_index: i}));
        }
        const plan = groups[0];
        const requested = element("record-id").value.trim();
        if ((requested && requested !== plan.task) || groups.some(group => group.task !== plan.task)) {
          throw Error("checkpoint_task_mismatch：任务已变化，请核对任务绑定");
        }
        const saved = await persistence.load(tabId, await browserSession(), plan.task);
        const firstGroup = groups.find(group => group.cards?.length);
        const cardLimit = firstGroup?.cards.length || saved?.cardLimit || Number(element("card-limit").value);
        const next = createSession({taskRecordId: plan.task, platform: plan.platform, queries: plan.queries,
                                    cardLimit, resultLimit: plan.result_limit});
        if (saved && (!equal(saved.queries, next.queries) || saved.platform !== next.platform || saved.cardLimit !== next.cardLimit || saved.resultLimit !== next.resultLimit)) {
          throw Error("checkpoint_plan_mismatch：本地进度与后台计划不一致");
        }
        next.acknowledgedQueries = [];
        for (const group of groups) {
          const index = group.query_index;
          const local = saved?.queryBlocks[index]?.observations || [];
          if (group.cards && !equal(local, group.cards.slice(0, local.length))) {
            throw Error("checkpoint_cards_conflict：本地与后台卡片不一致");
          }
          addSearchObservations(next, {query: next.queries[index], cards: group.cards || local});
          if (group.acknowledged) next.acknowledgedQueries.push(index);
        }
        if (next.acknowledgedQueries.length === 3) {
          await restoreDetails(next, groups[2].details, saved?.details || []);
        }
        session = next;
        await persist();
        element("setup").hidden = true;
        element("controls").hidden = false;
        if (session.acknowledgedQueries.length === 3) showNextProduct();
        setStatus(`已载入当前任务：已确认 ${session.acknowledgedQueries.length}/3 组搜索，已核验 ${session.details.length} 个候选。`);
      } catch(error) { element("error").textContent=String(error.message || error); }
    }));

    element("collect-search").addEventListener("click", collectOnce(async () => {
      try {
        clearError();
        if (!session || session.stopped) throw new Error("start a new collector session");
        session.acknowledgedQueries ||= [];
        const blockIndex = session.acknowledgedQueries.length;
        const block = session.queryBlocks[blockIndex];
        if (!block) throw new Error("all three search queries are complete");
        const progress = await globalThis.collectorCheckpoint('collector_progress', {query_index: blockIndex});
        if (progress.task !== session.taskRecordId || progress.queries[blockIndex] !== block.query || progress.result_limit !== session.resultLimit) {
          throw Error('checkpoint_plan_mismatch：任务与采集器设置不一致');
        }
        if (progress.cards) {
          if (!equal(block.observations, progress.cards.slice(0, block.observations.length))) throw Error('checkpoint_cards_conflict');
          addSearchObservations(session, {query: block.query, cards: progress.cards.slice(block.observations.length)});
        }
        if (block.observations.length === session.cardLimit) {
          if (!progress.acknowledged && !await globalThis.confirmCollectorQueryCards(blockIndex, block.observations)) throw Error('checkpoint_cards_unconfirmed');
          await finishSearchGroup(blockIndex, progress);
          setStatus(`Completed query ${blockIndex + 1}.`);
          return;
        }
        const sessionReply=await chrome.runtime.sendMessage({type:'collector_session_id'});
        let verified=CollectorGate.consume(tabId, sessionReply?.session_id, block.query, session.queryBlocks.indexOf(block));
        if (!verified && typeof recoverCollectorReceipt === "function" && await recoverCollectorReceipt(session.queryBlocks.indexOf(block),block.query)) {
          verified=CollectorGate.consume(tabId, sessionReply?.session_id, block.query, session.queryBlocks.indexOf(block));
        }
        if (!verified) {
          element("error").textContent="当前查询尚无有效核验；请在同一任务、同一标签重新核验查询并采集卡片。回执不代表卡片已保存，不得跳过本组。";
          return;
        }
        let boundUrl = null;
        while (block.observations.length < session.cardLimit) {
          const result = await sendToPage({type: "MERCADO_COLLECTOR_EXTRACT_SEARCH", query: block.query, prior: block.observations, limit: session.cardLimit});
          boundUrl = checkSearchContext(result, block.query, boundUrl);
          const newCards = result.observations.slice(block.observations.length);
          if (newCards.length) {
            addSearchObservations(session, {query: block.query, cards: newCards, pageUrl: result.page_url});
            await persist();
            setStatus(`${block.query}: ${block.observations.length}/${session.cardLimit} first-visible cards.`);
          }
          if (block.observations.length >= session.cardLimit) break;
          const scroll = await sendToPage({type: "MERCADO_COLLECTOR_SCROLL"});
          if (scroll.at_end && !newCards.length) throw new Error("page ended before 30 visible unique cards were collected");
          await delay(350);
        }
        if (typeof globalThis.confirmCollectorQueryCards!=='function' || !await globalThis.confirmCollectorQueryCards(blockIndex,block.observations)) {element('error').textContent='本组卡片已保存，但后台尚未确认；请检查连接后再次点击采集。';return;}
        await finishSearchGroup(blockIndex, progress);
        setStatus(`Completed query ${session.queryBlocks.indexOf(block) + 1}. Navigate the Mercado tab to the next exact query or the displayed detail.`);
      } catch (error) { stopWithError(error); }
    }));

    element("open-next-candidate").addEventListener("click", async () => {
      const button = element("open-next-candidate");
      if (button.hidden || button.disabled) return;
      button.disabled = true;
      try {
        clearError();
        if (session?.acknowledgedQueries?.length !== 3) throw Error("checkpoint_search_incomplete");
        await globalThis.openCollectorCandidate(nextExpectedIdentity(session), session.taskRecordId);
      } catch (error) { stopWithError(error); }
      finally { button.disabled = false; }
    });
    element("capture-review-photo").addEventListener("click",collectOnce(async()=>{
      try {
        clearError();
        if (!session || session.acknowledgedQueries?.length !== 3) throw Error('请先恢复已核验的三组卡片');
        const identity = nextExpectedIdentity(session);
        const binding = await globalThis.currentGalleryBinding();
        const photo = Number(element('review-photo-position').value);
        if (!Number.isInteger(photo) || photo < 1 || photo > 100) throw Error('图片位置无效');
        const observed = await globalThis.readCandidatePhoto(chrome,binding,identity,photo);
        const page = await sendToPage({type:'MERCADO_COLLECTOR_EXTRACT_DETAIL',identity});
        if (page.identity !== identity || page.identity_matches !== true || page.blocked)
          throw Error('下载时商品身份已变化');
        const detail = detailOutcomePlan(identity,page,{status:'rejected',reason:'imagery_ambiguous_or_inaccessible'});
        await observed.recheck();
        setStatus('正在下载已核验的候选原图…');
        const result = await globalThis.downloadVisualCandidatePhoto({
          api:chrome, requestId:binding.request_id, checkpoint:globalThis.collectorCheckpoint,
          observation:{detail,photo,source:observed.source,variant:observed.variant},recheck:observed.recheck,
        });
        setStatus('候选原图凭证已保存。请通过本机回传通道读取原图并提交款式判定。'+result.receipt);
      } catch (error) { stopWithError(error); }
    }));
    function collectDetail(salesOnly) {
      return collectOnce(async () => {
        try {
          clearError();
          if (!session || session.stopped) throw new Error("start a new collector session");
          if (session.acknowledgedQueries?.length !== 3) throw Error("checkpoint_search_incomplete：请先确认三组卡片");
          const identity = nextExpectedIdentity(session);
          const visible = await sendToPage({type: "MERCADO_COLLECTOR_EXTRACT_DETAIL", identity});
          if (visible.blocked) throw Error(visible.blocking_error || "detail_access_blocked");
          if (salesOnly && (visible.identity_matches !== true || visible.identity !== identity)) {
            throw Error('checkpoint_identity_changed：销量来自其他商品详情');
          }
          const selectedStatus = element("detail-status").value;
          if (!salesOnly && visible.identity_matches) {
            const message=detailInputMessage(selectedStatus,element("visual-category-code").value,element("match-level").value,element("visual-features").value);
            if (message) { element("error").textContent=message; return; }
          }
          const outcome = detailOutcomePlan(identity, visible, salesOnly
            ? {status: "rejected", reason: "threshold_failure"} : {
            status: selectedStatus,
            reason: selectedStatus === "qualified" ? null : element("detail-reason").value || null,
            visualCategoryCode: element("visual-category-code").value || null,
            matchLevel: element("match-level").value || null,
            visualFeatures: element("visual-features").value.split("\n").map(value=>value.trim()).filter(Boolean),
          });
          const next = {...session, details: [...session.details]};
          addDetailOutcome(next, outcome);
          const receipt = await globalThis.collectorCheckpoint('checkpoint_detail', {
            detail_index: session.details.length, detail: outcome, ...(salesOnly ? {sales_only: true} : {}),
          });
          if (salesOnly && receipt.sales_screen !== 'rejected') {
            if (receipt.sales_screen === 'visual_required') {
              setStatus('销量门槛已通过。请核验商品图片和款式。');
              return;
            }
            if (receipt.sales_screen === 'metric_missing') {
              setStatus('详情页未显示明确商品销量。请按销量缺失规则处理。');
              return;
            }
            throw Error('销量筛选回执未确认');
          }
          session = next;
          await persist();
          const completed = qualifyingCount(session) >= session.resultLimit || session.details.length === recurringPool(session).length;
          showNextProduct();
          if (salesOnly) setStatus(`${identity} 已保存销量条件未通过（${visible.sold_display}）。${completed ? '可回传证据。' : '可打开下一候选。'}`);
          else setStatus(completed ? `Collected detail ${identity}. Evidence is complete.` : `Collected detail ${identity}. Next required identity: ${nextExpectedIdentity(session)}.`);
        } catch (error) { stopWithError(error); }
      });
    }
    element("collect-detail").addEventListener("click", collectDetail(false));
    element("screen-detail-sales").addEventListener("click", collectDetail(true));

    element("stage-evidence").addEventListener("click", collectOnce(async () => {
      try {
        clearError();
        if (!session || session.stopped) throw new Error("start a new collector session");
        if (!await globalThis.stageCollectorEvidence(JSON.parse(serializeEvidence(session)))) throw new Error("任务证据未暂存，请核验任务绑定和守护连接");
        setStatus("任务证据已安全暂存，可通过 submit_staged 提交。");
      } catch(error) { element("error").textContent=String(error.message || error); }
    }));
    element("export").addEventListener("click", async () => {
      try {
        clearError();
        if (!session || session.stopped) throw new Error("start a new collector session");
        const root = `find-best-seller-product/${session.taskRecordId}/`;
        const jsonUrl = URL.createObjectURL(new Blob([serializeEvidence(session)], {type: "application/json"}));
        try {
          const actualPath=await downloadEvidence(chrome,jsonUrl,root + "evidence.json");
          setStatus(`Downloaded evidence.json: ${actualPath}`);
        } finally {
          URL.revokeObjectURL(jsonUrl);
        }
      } catch (error) {
        element("error").textContent = error instanceof Error ? error.message : String(error);
      }
    });
  }).catch(stopWithError);
})();

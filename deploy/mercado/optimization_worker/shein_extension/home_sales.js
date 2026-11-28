(function (root) {
  "use strict";

  // A sales badge belongs to the card containing it, never to the page or store.
  function cardSales(card, isVisible) {
    const labels = Array.from(card.querySelectorAll('.bs-product-card__sales-label'))
      .filter(isVisible)
      .map(node => (node.innerText ?? node.textContent ?? '').trim());
    if (!labels.length || labels.some(text => !/^(?:\+\s*)?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d{1,2})?)\s*k?\s*\+?\s+sold$/i.test(text) || (text.startsWith('+') && /\+\s+sold$/i.test(text)))) return null;
    const distinct = [...new Set(labels)];
    return distinct.length === 1 ? distinct[0] : null;
  }

  function productReviews(doc, isVisible) {
    const values = Array.from(doc.querySelectorAll('.product-intro__head-reviews-text'))
      .filter(isVisible).map(node => (node.innerText ?? node.textContent ?? '').trim());
    if (!values.length) return null;
    const parsed = values.map(text => /^\(?\s*((?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*k?\s*\+?)\s+reviews?\s*\)?$/i.exec(text)?.[1] ?? null);
    if (parsed.some(value => value === null) || new Set(parsed).size !== 1) return null;
    return parsed[0];
  }

  function productRating(doc, isVisible) {
    const values = Array.from(doc.querySelectorAll('.product-intro__head-reviews-btn'))
      .filter(isVisible).map(node => /\bAverage Rating\s+([0-5](?:\.\d{1,2})?)\b/i.exec(node.getAttribute('aria-label') || '')?.[1] ?? null);
    if (!values.length || values.some(value => value === null || Number(value) > 5) || new Set(values).size !== 1) return null;
    return values[0];
  }

  function productPrice(doc, isVisible) {
    const values = Array.from(doc.querySelectorAll('#productMainPriceId')).filter(isVisible)
      .map(node => (node.innerText ?? node.textContent ?? '').trim());
    if (!values.length || new Set(values).size !== 1) return null;
    const match = /^\$\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?$/.exec(values[0]);
    if (!match) return null;
    return {amount: match[1].replaceAll(',', '') + '.' + (match[2] || '00'), currency: 'USD', source:'product_detail'};
  }

  function noSearchResults(doc,isVisible,query) {
    if(typeof query!=='string' || !query.trim())return false;
    const rows=Array.from(doc.querySelectorAll('.product-list-v2__hypernym-hint-old')).filter(isVisible);
    if(rows.length!==1)return false;
    const text=(rows[0].innerText ?? rows[0].textContent ?? '').trim();
    const match=/^We couldn't find any results for (.+?) but these other products might interest you:?$/i.exec(text);
    const norm=value=>value.trim().replace(/\s+/g,' ').toLowerCase();
    return !!match && norm(match[1])===norm(query);
  }

  const api = {cardSales, productReviews, productRating, productPrice, noSearchResults};
  if (typeof module !== 'undefined') module.exports = api;
  root.SheinHomeSales = api;
})(globalThis);

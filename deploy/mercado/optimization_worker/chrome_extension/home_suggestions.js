"use strict";

function extractSuggestions(root,input,prefix,isVisible) {
  if (!root || !input || typeof prefix!=='string' || !prefix.trim() || typeof isVisible!=='function' ||
      root.activeElement!==input || !isVisible(input) || typeof input.value!=='string' ||
      input.value.trim().toLocaleLowerCase()!==prefix.trim().toLocaleLowerCase())
    return {state:'unverified',suggestions:[]};
  const id=input.getAttribute('aria-controls');
  if (input.getAttribute('aria-expanded')!=='true' || !id) return {state:'unavailable',suggestions:[]};
  const list=root.getElementById(id);
  if (!list || typeof list.querySelectorAll!=='function') return {state:'unavailable',suggestions:[]};
  const options=[...list.querySelectorAll('[role="option"]')].filter(isVisible);
  const suggestions=[];
  for (const [index,option] of options.entries()) {
    const text=String(option.innerText||'').replace(/\s+/g,' ').trim();
    if (!text || text.length>120 || !text.toLocaleLowerCase().startsWith(prefix.trim().toLocaleLowerCase())) continue;
    suggestions.push({text,position:index+1});
  }
  return suggestions.length ? {state:'observed',suggestions} : {state:'unavailable',suggestions:[]};
}

if (typeof module!=='undefined') module.exports={extractSuggestions};
if (typeof globalThis!=='undefined') globalThis.HomeSuggestions={extractSuggestions};

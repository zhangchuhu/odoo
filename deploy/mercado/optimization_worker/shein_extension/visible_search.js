'use strict';
function visibleSearch(doc,isVisible){
 const inputs=[...doc.querySelectorAll('input[type="search"],input[aria-label*="search" i],input[placeholder*="search" i]')].filter(isVisible);
 if(inputs.length!==1 || typeof inputs[0].value!=='string' || !inputs[0].value.trim()) return null;
 return inputs[0].value;
}
globalThis.SheinVisibleSearch={visibleSearch};

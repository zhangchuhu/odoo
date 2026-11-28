'use strict';
function visibleSearch(doc,isVisible,expected){
 const inputs=[...doc.querySelectorAll('input[type="search"],input[aria-label*="search" i],input[placeholder*="search" i]')].filter(isVisible);
 if(inputs.length===1 && typeof inputs[0].value==='string' && inputs[0].value.trim()) return inputs[0].value;
 if(inputs.length>1 || typeof expected!=='string' || !expected.trim()) return null;
 const headings=[...doc.querySelectorAll('h1')].filter(isVisible).map(x=>(x.textContent||'').trim());
 const matches=headings.map(x=>x.match(/^search results for\s*["“]([^"”]+)["”]\s*$/i)).filter(Boolean);
 const norm=x=>x.trim().replace(/\s+/g,' ').toLocaleLowerCase('en-US');
 return matches.length===1 && norm(matches[0][1])===norm(expected) ? expected : null;
}
module.exports={visibleSearch};

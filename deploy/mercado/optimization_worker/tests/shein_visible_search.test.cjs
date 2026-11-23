const test=require('node:test'),assert=require('node:assert/strict');const {visibleSearch}=require('../shein_visible_search.cjs');
test('reads only unique visible nonempty search input',()=>{
 const doc={querySelectorAll:()=>[{value:'lace dress', visible:true},{value:'hidden',visible:false}]};
 assert.equal(visibleSearch(doc,x=>x.visible),'lace dress');
});
test('ambiguous, absent and blank search boxes fail closed',()=>{
 for(const elems of [[],[{value:'',visible:true}],[{value:'a',visible:true},{value:'b',visible:true}]]) assert.equal(visibleSearch({querySelectorAll:()=>elems},x=>x.visible),null);
});

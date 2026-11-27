const {test}=require('node:test');
const assert=require('node:assert/strict');
const {extractSuggestions}=require('../chrome_extension/home_suggestions.js');

function fixture(value, options, {expanded='true',visible=true}={}) {
  const input={value,getAttribute:(name)=>({ 'aria-controls':'suggestions','aria-expanded':expanded })[name]||null};
  const list={querySelectorAll:()=>options.map(text=>({innerText:text,visible:true}))};
  const root={activeElement:input,getElementById:(id)=>id==='suggestions'?list:null};
  return {root,input,isVisible:(node)=>node===input?visible:node.visible};
}
test('reads ordered visible options for the focused matching search input',()=>{
  const x=fixture('conjunto mujer',['Conjunto mujer formal','Conjunto mujer falda blusa']);
  assert.deepEqual(extractSuggestions(x.root,x.input,'conjunto mujer',x.isVisible),{
    state:'observed',suggestions:[{text:'Conjunto mujer formal',position:1},{text:'Conjunto mujer falda blusa',position:2}]});
});
test('unverified input or hidden dropdown is not reported as empty',()=>{
  const x=fixture('conjunto mujer',[],{expanded:'false'});
  assert.equal(extractSuggestions(x.root,x.input,'conjunto mujer',x.isVisible).state,'unavailable');
  assert.equal(extractSuggestions(x.root,x.input,'different',x.isVisible).state,'unverified');
});

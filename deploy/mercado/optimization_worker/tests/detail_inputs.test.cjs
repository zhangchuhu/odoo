const test=require('node:test'),assert=require('node:assert/strict');
for(const platform of ['chrome_extension','shein_extension']){
 const {detailInputMessage}=require('../'+platform+'/detail_inputs.js');
 test(platform+' requires category, match and visible features for qualified detail',()=>{
  assert.match(detailInputMessage('qualified','','',''),/类别/);
  assert.match(detailInputMessage('qualified','dress','',''),/匹配等级/);
  assert.match(detailInputMessage('qualified','dress','类似竞品',' \n '),/视觉特征/);
  assert.equal(detailInputMessage('qualified','dress','类似竞品','圆领\n收腰'),'');
  assert.equal(detailInputMessage('rejected','','',''),'');
 });
}

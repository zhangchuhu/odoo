'use strict';
function detailInputMessage(status,category,match,features) {
  if(status!=='qualified') return '';
  if(!category) return '请选择可见服装类别，再采集详情。';
  if(!['同款','高度相似','类似竞品'].includes(match)) return '请选择匹配等级，再采集详情。';
  if(!features.split('\n').some(v=>v.trim())) return '请填写至少一条实际可见的服装视觉特征，再采集详情。';
  return '';
}
if(typeof module!=='undefined') module.exports={detailInputMessage};

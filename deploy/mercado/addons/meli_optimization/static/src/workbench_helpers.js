/** @odoo-module **/
export function displayValue(value) {
    if (typeof value === 'boolean') return value ? '是' : '否';
    return value === null || value === undefined ? '未知' : typeof value === 'object' ? JSON.stringify(value) : String(value);
}
export function candidateRows(result) {
    return result?.candidates || result?.facts || result?.keywords || result?.images || result?.checks || (result?.image ? [result.image] : []);
}
export function moveSelection(selection, index, delta) {
    const copy = [...selection];
    const target = index + delta;
    if (target >= 0 && target < copy.length) [copy[index], copy[target]] = [copy[target], copy[index]];
    return copy;
}

// Facts are observations from a completed task, never inferred from today's snapshot.
const factLabels = {
    shop:'店铺',site_id:'站点',item_id:'商品 ID',id:'ID',title:'原标题',
    seller_sku:'SKU',seller_custom_field:'卖家 SKU',available_quantity:'库存',
    sales_units:'窗口销量',value_status:'数据状态',source:'来源',variations:'SKU 明细',
    sku_size_evidence:'SKU / 尺码库存证据',sku_id:'SKU ID',size:'尺码',
    attribute_combinations:'规格',value_name:'显示值',value_id:'值 ID',
    screening_criteria:'筛选条件',observed_at:'采集时间',start:'开始',end:'结束',
    stock_min:'最低库存',sales_max:'最高销量',require_all_skus_in_stock:'全部 SKU 有库存',
    sales_policy:'销量口径',timezone:'时区',image_url:'商品图片 URL',
};
// Do not surface worker diagnostics or credential-bearing fields in this view.
const confidentialFact = key => /token|secret|password|authorization|cookie|credential|raw[_-]?(response|output)|prompt|lease|checkpoint/i.test(key);
const factLabel = key => Object.hasOwn(factLabels,key) ? factLabels[key] : key;

export function factValueRows(value) {
    const rows = [];
    const walk = (item, path, depth) => {
        if (rows.length >= 100) return;
        if (item !== null && typeof item === 'object') {
            if (depth >= 6) { rows.push({path,value:'层级较深，请核对来源记录'}); return; }
            const entries = Array.isArray(item) ? item.map((v,i)=>[String(i+1),v]) : Object.entries(item).filter(([key])=>!confidentialFact(key));
            if (!entries.length) { rows.push({path,value:Array.isArray(item)?'空列表':'空记录'}); return; }
            for (const [key, child] of entries) walk(child,[path,factLabel(key)].filter(Boolean).join(' · '),depth+1);
        } else {
            rows.push({path,value:displayValue(item)});
        }
    };
    walk(value,'',0);
    if (rows.length >= 100) rows.push({path:'',value:'仅展示前 100 项，请核对完整来源记录'});
    return rows;
}

export function productFactsView(detail) {
    const snapshotId = detail?.snapshot?.id;
    const completed = (detail?.tasks || []).filter(t=>t.task_type==='product.facts' && t.state==='succeeded')
        .sort((a,b)=>b.id-a.id);
    const present = task => {
        const result = task.result || {};
        const evidence = Array.isArray(result.evidence) ? result.evidence : [];
        return {id:task.id,snapshot_id:task.snapshot_id,
            facts:(Array.isArray(result.facts) ? result.facts : []).filter(f=>typeof f.name==='string' && !confidentialFact(f.name)).map(f=>({
                name:factLabel(f.name), rows:factValueRows(f.value),
                evidence:(Array.isArray(f.evidence_ids)?f.evidence_ids:[]).map(id=>{
                    const e=evidence.find(e=>e.id===id);
                    return {id,source:e?.source || '来源缺失',observed_at:e?.observed_at || '时间未知'};
                }),
            })),
            uncertainties:Array.isArray(result.uncertainties)?result.uncertainties.filter(v=>typeof v==='string'):[]};
    };
    const current = snapshotId ? completed.find(t=>t.snapshot_id===snapshotId) : null;
    return {status:!snapshotId?'no_snapshot':current?'current':completed.length?'stale':'missing',
        current:current?present(current):null,
        history:completed.filter(t=>t.snapshot_id!==snapshotId).map(present)};
}

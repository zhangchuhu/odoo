/** @odoo-module **/
export function collectionSubmission(source) {
    const form = {...source};
    for (const key of ['min_sold','min_reviews','min_rating','result_limit']) form[key] = Number(form[key]);
    if (form.platform === 'shein-us') form.strategy = 'shein-home-v1';
    else delete form.strategy;
    return form;
}
export function visibleCollectionTasks(tasks, detail) {
    if (!detail?.collection || detail.collection_started !== false) return tasks;
    const workflow = new Set(['product.facts', 'research.keywords', 'research.bestseller']);
    return tasks.filter(t => !workflow.has(t.task_type) || ['succeeded', 'cancelled'].includes(t.state));
}
// Collapse only explicitly audited retry edges; same snapshot alone is not proof.
export function groupResearchRetries(tasks, events) {
    const byId = new Map(tasks.map(t => [t.id, t]));
    const parent = new Map();
    for (const event of events || []) {
        if (event.kind !== 'unknown_research_retry') continue;
        const source = event.receipt?.source_task_id, target = event.receipt?.task_id;
        const a = byId.get(source), b = byId.get(target);
        if (a?.task_type === 'research.bestseller' && b?.task_type === a.task_type && a.snapshot_id === b.snapshot_id && source < target) parent.set(target, source);
    }
    const groups = new Map();
    for (const task of tasks) {
        let root = task.id;
        while (parent.has(root)) root = parent.get(root);
        if (!groups.has(root)) groups.set(root, []);
        groups.get(root).push(task);
    }
    return [...groups.values()].map(items => {
        items.sort((a,b) => a.id - b.id);
        const latest = items[items.length - 1];
        const attempts = items.flatMap(t => (t.attempts || []).map(a => ({...a, task_id: t.id})));
        return {...latest, ...(items.length > 1 ? {retry_history: items} : {}), ...(attempts.length ? {attempt_history: attempts} : {})};
    }).sort((a,b) => b.id - a.id);
}
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

// Frozen library indices, not task IDs or completion times, determine the initial order.
export function imagePlanRows(plan, tasks) {
    const indices = plan?.selected_indices, targets = plan?.target_artifact_ids;
    if (!Array.isArray(indices) || !Array.isArray(targets) || !indices.length || indices.length !== targets.length
        || indices.some(i => !Number.isSafeInteger(i) || i < 0) || new Set(indices).size !== indices.length
        || targets.some(id => typeof id !== 'string' || !id) || new Set(targets).size !== targets.length) return null;
    return indices.map((libraryIndex, i) => {
        const targetId = targets[i];
        const matches = (tasks || []).filter(t => String(t.target_artifact_id) === targetId && t.snapshot_id === plan.snapshot_id);
        const task = matches.sort((a,b) => b.id - a.id).find(t => t.state === 'succeeded') || matches[0] || null;
        return {libraryIndex, targetId, task};
    }).sort((a,b) => a.libraryIndex - b.libraryIndex);
}

export function imagePlanGroups(tasks, artifacts, snapshotId) {
    return groupImageTasks((tasks || []).filter(t => t.task_type === 'image.generate' && t.snapshot_id === snapshotId))
        .filter(group => group.isImage)
        .map(group => {
            const planId = group.tasks[0].generation_plan_id;
            const artifact = (artifacts || []).find(a => String(a.id) === String(planId));
            const payload = artifact?.payload;
            const rows = payload?.operation === 'garment_swap' && payload.snapshot_id === snapshotId
                ? imagePlanRows(payload, group.tasks) : null;
            const selected = rows ? rows.map(row => row.task) : group.tasks;
            const total = selected.length;
            const completed = selected.filter(t => t?.state === 'succeeded').length;
            const skipped = selected.filter(t => t?.state === 'skipped').length;
            const failed = selected.filter(t => ['failed','cancelled'].includes(t?.state)).length;
            const done = completed + skipped + failed;
            const summary = done < total ? '处理中' : failed ? '部分失败' : completed ? '已完成' : '已结束，无可用图片';
            return {...group, planId, rows, total, completed, skipped, failed, done, summary};
        });
}

// Facts are observations from a completed task, never inferred from today's snapshot.
const factLabels = {
    shop:'店铺',site_id:'站点',item_id:'商品 ID',id:'ID',title:'原标题',
    seller_sku:'SKU',seller_custom_field:'卖家 SKU',available_quantity:'库存',
    sales_units:'窗口销量',sales_scope:'销量范围',value_status:'数据状态',source:'来源',variations:'SKU 明细',
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
            rows.push({path,value:path === '销量范围' && item === 'product' ? '商品总销量' : displayValue(item)});
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
        const facts=(Array.isArray(result.facts) ? result.facts : []).filter(f=>typeof f.name==='string' && !confidentialFact(f.name)).map(f=>({
                name:factLabel(f.name), category:f.category === 'visual' ? 'visual' : 'known', rows:factValueRows(f.name === 'sales_scope' && f.value === 'product' ? '商品总销量' : f.value),
                evidence:(Array.isArray(f.evidence_ids)?f.evidence_ids:[]).map(id=>{
                    const e=evidence.find(e=>e.id===id);
                    return {id,source:e?.source || '来源缺失',observed_at:e?.observed_at || '时间未知'};
                }),
            }));
        return {id:task.id,snapshot_id:task.snapshot_id, facts,
            knownFacts:facts.filter(f=>f.category==='known'), visualFacts:facts.filter(f=>f.category==='visual'),
            uncertainties:Array.isArray(result.uncertainties)?result.uncertainties.filter(v=>typeof v==='string'):[]};
    };
    const current = snapshotId ? completed.find(t=>t.snapshot_id===snapshotId) : null;
    return {status:!snapshotId?'no_snapshot':current?'current':completed.length?'stale':'missing',
        current:current?present(current):null,
        history:completed.filter(t=>t.snapshot_id!==snapshotId).map(present)};
}

export function filterLibraryRows(rows, filters = {}) {
    const query = (filters.query || '').trim().toLocaleLowerCase();
    return (rows || []).filter(row => {
        const haystack = [row.source_sku, row.title, row.url, row.visual_features].filter(Boolean).join(' ').toLocaleLowerCase();
        const date = typeof row.listed_at === 'string' ? row.listed_at.slice(0, 10) : '';
        return (!query || haystack.includes(query)) && (!filters.platform || row.platform === filters.platform) &&
            (!filters.dateFrom || (date && date >= filters.dateFrom)) && (!filters.dateTo || (date && date <= filters.dateTo));
    });
}

export function libraryMetadataOrigin(origin) {
    return ({import_frozen:'采集时归档', legacy_backfill:'历史记录补显'})[origin] || '未知';
}
export function productSourceImages(value) {
    if (Array.isArray(value?.source_images)) return value.source_images.filter(x => x && (x.artifact_id || x.url));
    if (Array.isArray(value?.source_image_artifact_ids)) return value.source_image_artifact_ids.map(artifact_id => ({artifact_id}));
    return [];
}
export function groupImageTasks(tasks) {
    const groups = [], byKey = new Map();
    for (const task of tasks) {
        const plan = task.task_type === 'image.generate' && task.generation_plan_id;
        const key = plan && task.snapshot_id ? `image:${task.snapshot_id}:${plan}` : `task:${task.id}`;
        let group = byKey.get(key);
        if (!group) { group = {key, isImage: Boolean(plan && task.snapshot_id), tasks: []}; byKey.set(key, group); groups.push(group); }
        group.tasks.push(task);
    }
    for (const group of groups) {
        if (group.isImage) group.tasks.sort((a,b) => a.id - b.id);
        const current = new Map();
        for (const task of group.tasks) {
            const key = group.isImage && task.target_artifact_id ? String(task.target_artifact_id) : `task:${task.id}`;
            const old = current.get(key);
            if (!old || task.state === 'succeeded' || old.state !== 'succeeded') current.set(key, task);
        }
        const counted = [...current.values()];
        group.total = counted.length;
        group.completed = counted.filter(task => task.state === 'succeeded').length;
        group.skipped = counted.filter(task => task.state === 'skipped').length;
    }
    return groups;
}
export function imageReviewWaiting(task) {
    return task?.task_type === 'image.generate' && task.state === 'running' && task.status_detail?.code === 'host_review_required';
}
export function workbenchLocation(value) {
    value = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    const section = ['cases','batches','collections','library'].includes(value.section) ? value.section : 'cases';
    const caseId = ['cases','collections'].includes(section) && Number.isSafeInteger(value.caseId) && value.caseId > 0 ? value.caseId : null;
    const tabs = ['snapshot','facts','tasks','titles','research','gallery','images','proposals','activity','effects','audit'];
    const tab = caseId && tabs.includes(value.tab) ? value.tab : 'snapshot';
    const scrollTop = Number.isFinite(value.scrollTop) && value.scrollTop >= 0 ? Math.min(value.scrollTop, 10000000) : 0;
    return {section,caseId,tab,scrollTop};
}
export function workbenchListPosition(value) {
    value = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    const text = key => typeof value[key] === 'string' ? value[key].slice(0, 200) : '';
    const batch = typeof value.batch === 'string' && /^[1-9][0-9]*$/.test(value.batch) ? Number(value.batch) : value.batch;
    return {query:text('query'),page:Number.isSafeInteger(value.page) && value.page >= 0 ? Math.min(value.page,100000) : 0,
        batch:Number.isSafeInteger(batch) && batch > 0 ? batch : '',
        status:text('status'),shop:text('shop'),site:text('site')};
}

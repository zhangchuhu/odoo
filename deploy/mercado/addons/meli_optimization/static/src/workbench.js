/** @odoo-module **/
import { Component, onWillStart, useState, useEffect, onWillUnmount } from '@odoo/owl';
import { registry } from '@web/core/registry';
import { useService } from '@web/core/utils/hooks';
import { candidateRows, displayValue, moveSelection, productFactsView } from './workbench_helpers';

export class OptimizationWorkbench extends Component {
    static template = 'meli_optimization.Workbench';
    static props = ['*'];
    setup() {
        this.navigationVersion = 0;
        this.disposed = false;
        onWillUnmount(() => { this.disposed = true; this.navigationVersion++; });
        this.orm = useService('orm');
        this.notification = useService('notification');
        this.state = useState({ loading: false, error: '', data: null, detail: null, tab: 'snapshot', query: '', page: 0,
            batch: '', status: '', shop: '', site: '', section: 'cases', modal: '', form: {}, taskType: 'product.facts',
            taskValues: {}, selections: {}, preview: null, collections: [], library: [], librarySelection: null, libraryContext: null, libraryIndices: [], garmentArtifact: '', libraryQuery: '', librarySite: '' });
        this.tabs = [['snapshot','商品快照'],['facts','商品事实'],['tasks','任务队列'],['titles','标题候选'],['research','爆款研究'],
            ['gallery','目标图库'],['images','图片与质检'],['proposals','变更确认'],['activity','活动与广告'],['effects','效果观察'],['audit','审计记录']];
        this.taskLabels = {'product.facts':'整理商品事实','research.keywords':'关键词研究','research.bestseller':'爆款研究',
            'gallery.collect':'采集 SKU 图库','title.generate':'生成标题候选','image.generate':'逐图生成','image.qc':'图片质检'};
        this.labels = {cases:'商品流程', batches:'优化批次', waiting:'等待条件', failed:'失败任务', pending:'待确认方案'};
        useEffect(() => {
            if (!this.state.modal) return;
            const dialog = document.querySelector('.mw-modal');
            const previous = document.activeElement;
            const controls = () => [...dialog.querySelectorAll('button,input,select,a[href],summary')].filter(el => !el.disabled);
            controls()[0]?.focus();
            const trap = (event) => {
                if (event.key !== 'Tab') return;
                const items = controls(), first = items[0], last = items[items.length - 1];
                if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
                if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
            };
            dialog.addEventListener('keydown', trap);
            return () => {dialog.removeEventListener('keydown', trap); previous?.focus();};
        }, () => [this.state.modal]);
        onWillStart(() => this.refresh());
    }
    modalKeydown(event) { if (event.key === 'Escape') this.state.modal = ''; }
    async rpc(method, args) { return this.orm.call('meli.optimization.case', method, args); }
    async perform(action, message) {
        if (this.state.loading) return;
        this.state.loading = true; this.state.error = '';
        try { const result = await action(); if (message) this.notification.add(message, { type:'success' }); return result; }
        catch (error) { this.state.error = error.data?.message || error.message || '操作失败，请重试'; this.notification.add(this.state.error, { type:'danger' }); }
        finally {
            this.state.loading = false;
            if (this.refreshPending) {
                this.refreshPending = false;
                const waiters = this.refreshWaiters || [];
                this.refreshWaiters = [];
                try { if (!this.disposed) await this.refresh(); }
                finally { waiters.forEach(resolve => resolve()); }
            }
        }
    }
    loadScope() {
        return JSON.stringify([this.navigationVersion, this.state.query, this.state.page, this.state.batch,
            this.state.status, this.state.shop, this.state.site, this.state.section, this.state.detail?.id || false]);
    }
    async load() {
        const scope = this.loadScope();
        const generation = this.loadGeneration = (this.loadGeneration || 0) + 1;
        const current = () => !this.disposed && this.loadGeneration === generation && this.loadScope() === scope;
        const section = this.state.section, detailId = this.state.detail?.id;
        try {
            const data = await this.rpc('workbench_overview', [this.state.query,this.state.page,this.state.batch || false,this.state.status,this.state.shop,this.state.site]);
            if (!current()) return;
            const collections = section === 'collections' ? await this.rpc('workbench_collections', []) : null;
            const library = section === 'library' ? (await this.rpc('workbench_library',[this.state.libraryQuery,this.state.librarySite])).rows : null;
            const detail = detailId ? await this.rpc('workbench_detail',[detailId]) : null;
            if (!current()) return;
            this.state.data = data;
            if (collections) this.state.collections = collections;
            if (library) this.state.library = library;
            if (detail) {
                this.state.detail = detail;
                this.state.selections = {}; this.state.preview = null;
            }
        } catch (error) { if (current()) throw error; }
    }
    async refresh() {
        if (this.state.loading) {
            this.refreshPending = true;
            return new Promise(resolve => {
                (this.refreshWaiters ||= []).push(resolve);
            });
        }
        return this.perform(() => this.load());
    }
    async filter() { this.state.page = 0; return this.refresh(); }
    openBatch(batchId) {
        this.back();
        this.state.batch = Number(batchId);
        this.state.section = 'cases';
        return this.filter();
    }
    back() { this.navigationVersion++; this.state.detail=null; this.state.preview=null; this.state.modal=''; }
    async open(id) {
        this.navigationVersion++;
        return this.perform(async () => { this.state.detail = await this.rpc('workbench_detail',[id]); this.state.tab=this.state.detail.collection ? 'tasks' : 'snapshot'; this.state.selections={}; this.state.preview=null; });
    }
    async navigate(section) { this.back(); this.state.section=section; this.state.status=''; this.state.page=0; return this.refresh(); }
    async page(delta) { this.state.page += delta; return this.refresh(); }
    createForm() { this.state.form = {shop:'',item_id:'',title:'',site_id:'MLM',seller_sku:'',available_quantity:'',image_url:'',batch_name:''}; this.state.modal='create'; }
    snapshotForm() { this.state.form = {...this.state.detail.snapshot.payload}; this.state.form.available_quantity ??= ''; this.state.modal='snapshot'; }
    async saveProduct() {
        await this.perform(async () => {
            const form = {...this.state.form};
            if (form.available_quantity !== '') form.available_quantity = Number(form.available_quantity);
            this.state.detail = await this.rpc(this.state.modal === 'create' ? 'workbench_create' : 'workbench_snapshot', this.state.modal === 'create' ? [form] : [this.state.detail.id, form]);
            this.state.modal=''; this.state.tab='snapshot'; await this.load();
        }, '商品快照已保存');
    }
    collectionForm() { this.state.form={name:'',garment:'',source_url:'',site_id:'MLM'}; this.state.modal='collection'; }
    async readGarment(event) {
        this.state.form.image_data='';
        const file=event.target.files[0]; if (!file) return;
        if(file.size > 8*1024*1024) { this.state.error='图片不得超过 8 MiB'; return; }
        const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
        this.state.form.image_data=data;this.state.form.image_mime=file.type;
    }
    async saveCollection() {
        await this.perform(async()=>{
            this.state.detail=await this.rpc('workbench_collection_create',[{...this.state.form}]);
            this.state.modal='';this.state.tab='tasks';await this.load();
        }, '独立采集已建立；外部研究由具备能力的 Worker 执行');
    }
    async loadLibrary() { this.state.library=(await this.rpc('workbench_library',[this.state.libraryQuery,this.state.librarySite])).rows; }
    async filterLibrary() { return this.perform(()=>this.loadLibrary()); }
    async importGallery(task) {
        return this.perform(async()=>{await this.rpc('workbench_library_import',[this.state.detail.id,task.id,task.result_hash]);}, '已验证图片已保存到公司图库');
    }
    libraryImage(image) { return `/web/image/meli.optimization.artifact/${image.artifact_id}/data`; }
    async selectLibrary() {
        const context={version:this.navigationVersion,case_id:this.state.detail.id,revision:this.state.detail.revision};
        return this.perform(async()=>{await this.loadLibrary();if(!this.contextMatches(context)) return;this.state.libraryContext=context;this.state.librarySelection=null;this.state.libraryIndices=[];this.state.garmentArtifact='';this.state.modal='library-select';});
    }
    chooseLibrary(entry) { this.state.librarySelection=entry;this.state.libraryIndices=[]; }
    toggleLibrary(index) { const list=this.state.libraryIndices;this.state.libraryIndices=list.includes(index)?list.filter(i=>i!==index):[...list,index]; }
    get garmentArtifacts() { return (this.state.detail?.artifacts || []).filter(a=>a.mime_type?.startsWith('image/') && !a.library_origin_id); }
    async uploadGarment(event) {
        await this.readGarment(event);
        if (!this.state.form.image_data) return;
        await this.perform(async()=>{const id=await this.rpc('workbench_upload_garment',[this.state.detail.id,this.state.form.image_data,this.state.form.image_mime]);await this.load();this.state.garmentArtifact=String(id);},'服装参考图已归档');
    }
    async saveLibrarySelection() {
        const context=this.state.libraryContext;
        if(!context || this.disposed || this.navigationVersion !== context.version || this.state.detail?.id !== context.case_id) return;
        return this.perform(async()=>{
            await this.rpc('workbench_library_generate',[context.case_id,this.state.librarySelection.id,[...this.state.libraryIndices],[Number(this.state.garmentArtifact)],context.revision]);
            if(!this.contextMatches(context)) return;
            this.state.modal='';this.state.tab='tasks';await this.load();
        }, '所选目标图已冻结计划并逐图加入队列');
    }
    taskForm() { this.state.taskType='product.facts'; this.state.taskValues={site_id:'MLM',language:'es-MX'}; this.state.modal='task'; }
    get taskFields() { return this.state.data?.task_inputs[this.state.taskType] || []; }
    fieldLabel(field) { return ({facts_artifact_id:'商品事实产物',keywords_artifact_id:'关键词研究产物',site_id:'目标站点',reference_item_id:'已选参考商品 ID',selected_variant:'准确的参考 SKU',language:'标题语言',source_artifact_ids:'服装参考图产物',target_artifact_id:'目标构图图产物',generation_plan_id:'生成计划产物',image_artifact_id:'待质检图片产物'})[field] || field; }
    isArtifact(field) { return field.endsWith('_artifact_id') || ['generation_plan_id','source_artifact_ids'].includes(field); }
    async saveTask() {
        await this.perform(async () => {
            const values = {...this.state.taskValues};
            if (this.state.taskType === 'gallery.collect') values.selected_variant = {seller_sku: values.selected_variant};
            if (values.source_artifact_ids) values.source_artifact_ids = [values.source_artifact_ids];
            await this.rpc('workbench_task',[this.state.detail.id,this.state.taskType,values]); this.state.modal=''; this.state.tab='tasks'; await this.load();
        }, '任务已加入队列，等待具备能力的 Worker');
    }
    async control(task, operation) { return this.perform(async () => {await this.rpc('workbench_control',[this.state.detail.id,task.id,operation]); await this.load();}, '任务状态已更新'); }
    get visibleTasks() {
        const kinds={titles:['title.generate'],research:['research.bestseller','research.keywords'],gallery:['gallery.collect'],images:['image.generate','image.qc']};
        return (this.state.detail?.tasks || []).filter(t => (kinds[this.state.tab] || []).includes(t.task_type));
    }
    get factsView() { return productFactsView(this.state.detail); }
    rows(result) { return candidateRows(result); }
    value(value) { return displayValue(value); }
    json(value) { return JSON.stringify(value,null,2); }
    label(state) { return ({queued:'排队中',running:'执行中',waiting:'等待条件',succeeded:'已完成',failed:'失败',cancelled:'已取消'})[state] || state; }
    safeUrl(value) { return typeof value === 'string' && /^https?:\/\//i.test(value) ? value : ''; }
    imageUrl(row) {
        const artifact = (this.state.detail?.artifacts || []).find(a => String(a.id) === String(row.artifact_id));
        return artifact?.mime_type?.startsWith('image/') ? `/web/image/meli.optimization.artifact/${artifact.id}/data` : this.safeUrl(row.image_url);
    }
    selectable(task) { return task.state==='succeeded' && task.snapshot_id === this.state.detail.snapshot.id && ['title.generate','research.bestseller','gallery.collect','image.generate'].includes(task.task_type); }
    selected(task, index) { return (this.state.selections[task.id] || []).includes(index); }
    toggle(task,index) {
        const multi=['gallery.collect','image.generate'].includes(task.task_type);
        const current=this.state.selections[task.id] || [];
        this.state.selections[task.id] = current.includes(index) ? current.filter(i=>i!==index) : multi ? [...current,index] : [index];
    }
    move(task,index,delta) { this.state.selections[task.id]=moveSelection(this.state.selections[task.id], index, delta); }
    contextMatches(context) {
        return !this.disposed && this.navigationVersion === context.version && this.state.detail?.id === context.case_id && this.state.detail?.revision === context.revision;
    }
    async archive(task) {
        const context = {version:this.navigationVersion,case_id:this.state.detail.id,revision:this.state.detail.revision};
        await this.perform(async () => {
            await this.rpc('workbench_archive',[context.case_id,task.id,task.result_hash]);
            if (this.contextMatches(context)) await this.load();
        }, '真实任务结果已归档，可用于后续任务');
    }
    async propose(task) {
        const context = {version:this.navigationVersion,case_id:this.state.detail.id,revision:this.state.detail.revision};
        await this.perform(async () => {
            const kind=task.task_type==='title.generate'?'title':task.task_type==='research.bestseller'?'reference':'images';
            const proposal=await this.rpc('workbench_propose',[context.case_id,task.id,task.result_hash,kind,this.state.selections[task.id] || []]);
            if (!this.contextMatches(context)) return;
            this.state.preview={...proposal,case_id:context.case_id,revision:context.revision};
            this.state.modal='preview';
        });
    }
    preview(proposal) { this.state.preview={...proposal,case_id:this.state.detail.id,revision:this.state.detail.revision}; this.state.modal='preview'; }
    async confirm() {
        const p=this.state.preview;
        if (!p || this.disposed || this.state.detail?.id !== p.case_id || this.state.detail?.revision !== p.revision) return;
        const context={version:this.navigationVersion,case_id:p.case_id,revision:p.revision};
        await this.perform(async () => {
            await this.rpc('workbench_confirm',[p.case_id,p.id,p.digest]);
            if (!this.contextMatches(context)) return;
            this.state.modal=''; this.state.tab='proposals'; await this.load();
        }, '已确认确切方案；平台发布尚未接入');
    }
}
registry.category('actions').add('meli_optimization.workbench', OptimizationWorkbench);

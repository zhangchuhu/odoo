/** @odoo-module **/
import { Component, onWillStart, useState, useEffect, onWillUnmount } from '@odoo/owl';
import { registry } from '@web/core/registry';
import { useService } from '@web/core/utils/hooks';
import { session } from '@web/session';
import { candidateRows, displayValue, moveSelection, productFactsView, libraryMetadataOrigin, productSourceImages, groupResearchRetries, visibleCollectionTasks, groupImageTasks, imagePlanGroups, imageReviewWaiting, workbenchLocation, workbenchListPosition } from './workbench_helpers';

export class OptimizationWorkbench extends Component {
    static template = 'meli_optimization.Workbench';
    static props = ['*'];
    setup() {
        this.navigationVersion = 0;
        this.disposed = false;
        onWillUnmount(() => { this.disposed = true; this.navigationVersion++; });
        this.orm = useService('orm');
        this.notification = useService('notification');
        this.locationKey = `meli.optimization.workbench.location.v1:${session.uid || 'anonymous'}`;
        this.imagePlanKey = `meli.optimization.image-plans.v1:${session.uid || 'anonymous'}`;
        let storedLocation;
        try { storedLocation = JSON.parse(sessionStorage.getItem(this.locationKey) || 'null'); }
        catch { storedLocation = null; }
        const initialLocation = workbenchLocation(storedLocation);
        const initialList = workbenchListPosition(storedLocation?.list);
        this.initialScrollTop = initialLocation.scrollTop;
        this.locationReady = false;
        this.state = useState({ loading: false, error: '', data: null, detail: null, tab: 'snapshot', query: '', page: 0,
            batch: '', status: '', shop: '', site: '', section: 'cases', modal: '', form: {}, variantShops: [], variantForm: {}, variantPreview: null, variantSelection: [], variantResults: null, taskType: 'product.facts',
            taskValues: {}, selections: {}, imagePlanSelections: {}, preview: null, imagePublishPreview: null, imagePublishVariant: '', imagePublishSkuConfirmed: false, imageDecision: null, collections: [], collectionStartStatus: null, collectionFilters: {query:'',platform:''}, collectionApplied: {}, library: [], librarySelection: null, libraryContext: null, libraryIndices: [], garmentArtifact: '', garmentUploadBusy: false, libraryQuery: '', librarySite: '', libraryFilters: {query:'', platform:'', dateFrom:'', dateTo:''}, libraryApplied: {}, libraryPage: 0, libraryPageSize: 50, libraryTotal: 0, libraryResults: [] });
        Object.assign(this.state, initialList);
        try { const saved = JSON.parse(sessionStorage.getItem(this.imagePlanKey) || '{}'); if (saved && typeof saved === 'object' && !Array.isArray(saved)) this.state.imagePlanSelections = saved; } catch {}
        this.tabs = [['snapshot','商品快照'],['facts','商品事实'],['tasks','任务队列'],['titles','标题候选'],['research','爆款研究'],
            ['gallery','类似爆款'],['images','图片与质检'],['proposals','变更确认'],['activity','活动与广告'],['effects','效果观察'],['audit','审计记录']];
        this.taskLabels = {'product.facts':'整理商品事实','research.keywords':'关键词研究','research.bestseller':'爆款研究',
            'gallery.collect':'采集 SKU 图库','title.generate':'生成标题候选','image.generate':'逐图生成','image.qc':'图片质检'};
        this.labels = {cases:'商品流程', batches:'优化批次', waiting:'等待条件', failed:'失败任务', pending:'待确认方案'};
        useEffect(() => { if (this.locationReady) this.saveWorkbenchLocation(); },
            () => [this.state.section, this.state.detail?.id || null, this.state.tab, this.state.query, this.state.page,
                   this.state.batch, this.state.status, this.state.shop, this.state.site]);
        useEffect(() => {
            const main = document.querySelector('.meli-workbench .mw-main');
            if (!main) return;
            const save = () => this.saveWorkbenchLocation(main.scrollTop);
            main.addEventListener('scroll', save, {passive:true});
            const frame = requestAnimationFrame(() => { main.scrollTop = this.initialScrollTop; });
            return () => { cancelAnimationFrame(frame); main.removeEventListener('scroll', save); };
        }, () => []);
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
        onWillStart(() => this.restoreWorkbenchLocation(initialLocation));
    }
    saveWorkbenchLocation(scrollTop = null) {
        if (!this.locationReady) return;
        const main = document.querySelector('.meli-workbench .mw-main');
        const value = workbenchLocation({section:this.state.section,caseId:this.state.detail?.id || null,
            tab:this.state.tab,scrollTop:scrollTop ?? main?.scrollTop ?? this.initialScrollTop});
        value.list = workbenchListPosition(this.state);
        try { sessionStorage.setItem(this.locationKey, JSON.stringify(value)); } catch { /* Storage may be disabled. */ }
    }
    async restoreWorkbenchLocation(location) {
        this.state.section = location.section;
        if (location.caseId) {
            try {
                const detail = await this.rpc('workbench_detail',[location.caseId]);
                const validTabs = detail.collection ? ['tasks','research','gallery','audit'] : this.tabs.map(([key]) => key);
                this.state.detail = detail;
                this.state.tab = validTabs.includes(location.tab) ? location.tab : (detail.collection ? 'tasks' : 'snapshot');
            } catch {
                this.state.detail = null;
                this.state.section = 'cases';
                this.state.tab = 'snapshot';
                this.initialScrollTop = 0;
                this.notification.add('原商品不可访问或暂时无法加载，已返回商品列表。', {type:'warning'});
            }
        }
        this.locationReady = true;
        return this.refresh();
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
            const library = section === 'library' ? await this.rpc('workbench_library',['','',this.state.libraryApplied,this.state.libraryPage*this.state.libraryPageSize]) : null;
            const detail = detailId ? await this.rpc('workbench_detail',[detailId]) : null;
            if (!current()) return;
            this.state.data = data;
            if (collections) this.state.collections = collections;
            if (library) { this.state.libraryResults = library.rows; this.state.libraryTotal = library.total; this.state.libraryPageSize = library.limit; }
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
        if (this.state.loading) return;
        const version = ++this.navigationVersion;
        const current = () => !this.disposed && this.navigationVersion === version;
        return this.perform(async () => {
            let detail;
            try { detail = await this.rpc('workbench_detail',[id]); } catch (error) { if (current()) throw error; return; }
            if (!current()) return;
            this.state.detail=detail; this.state.tab=detail.collection ? 'tasks' : 'snapshot'; this.state.selections={}; this.state.preview=null;
        });
    }
    async navigate(section) { this.back(); this.state.section=section; this.state.status=''; this.state.page=0; return this.refresh(); }
    async page(delta) { this.state.page += delta; return this.refresh(); }
    async createForm() {
        this.state.variantForm={shop_id:'',item_id:'',batch_name:''};this.state.variantShops=[];this.state.variantPreview=null;this.state.variantSelection=[];this.state.variantResults=null;this.state.modal='variant-create';
        return this.perform(async()=>{const shops=await this.rpc('workbench_variant_shops',[]);if(this.state.modal==='variant-create')this.state.variantShops=shops;});
    }
    manualCreateForm() { this.state.form = {shop:'',item_id:'',title:'',site_id:'MLM',seller_sku:'',available_quantity:'',image_url:'',batch_name:''}; this.state.modal='create'; }
    variantItemId(value) { const id=String(value || '').trim().toUpperCase();return /^[0-9]+$/.test(id) ? 'MLM'+id : id; }
    async readVariants() {
        const f=this.state.variantForm, shop=Number(f.shop_id), id=this.variantItemId(f.item_id);
        if (!shop || !id) return;
        this.state.variantPreview=null;this.state.variantSelection=[];
        return this.perform(async()=>{const p=await this.rpc('workbench_variant_preview',[shop,id]);if(this.state.modal==='variant-create' && this.state.variantForm===f && Number(f.shop_id)===shop && this.variantItemId(f.item_id)===id){this.state.variantPreview=p;this.state.variantSelection=p.item.groups.filter(g=>g.selectable).map(g=>g.key);}});
    }
    toggleVariant(key) { this.state.variantSelection=this.state.variantSelection.includes(key) ? this.state.variantSelection.filter(k=>k!==key) : [...this.state.variantSelection,key]; }
    toggleAllVariants() { const keys=(this.state.variantPreview?.item.groups || []).filter(g=>g.selectable).map(g=>g.key);this.state.variantSelection=keys.every(k=>this.state.variantSelection.includes(k)) ? [] : keys; }
    async syncVariantResult(row) {
        try { const detail=await this.rpc('workbench_detail',[row.case_id]);await this.rpc('workbench_archive_snapshot_gallery',[row.case_id,detail.revision]);row.synced=true;row.error=''; }
        catch (error) { row.synced=false;const message=error?.data?.message || error?.message;row.error='原图同步失败：' + (typeof message === 'string' ? message.slice(0,300) : '请查看记录并重试'); }
    }
    async createVariants() {
        const p=this.state.variantPreview, f=this.state.variantForm, keys=[...this.state.variantSelection];if(!p || !keys.length)return;
        return this.perform(async()=>{const rows=await this.rpc('workbench_variant_create',[p.shop_id,p.item.item_id,p.digest,keys,f.batch_name || '']);let results=rows.map(r=>({...r,synced:false,error:''}));if(this.state.modal==='variant-create' && this.state.variantForm===f){this.state.variantResults=results;results=this.state.variantResults;}for(const row of results)await this.syncVariantResult(row);await this.load();});
    }
    async retryVariantSync(row) { return this.perform(async()=>{await this.syncVariantResult(row);}); }
    async openVariantCase(id) { this.state.modal='';await this.open(id); }
    async splitVariantForm() {
        const detail=this.state.detail;
        if(!detail?.snapshot?.payload?.item_id)return;
        await this.createForm();
        if(this.state.modal==='variant-create') {
            this.state.variantForm.shop_id=detail.shop_id ? String(detail.shop_id) : '';
            this.state.variantForm.item_id=detail.snapshot.payload.item_id;
            if(detail.shop_id)await this.readVariants();
        }
    }
    snapshotForm() { this.state.form = {...this.state.detail.snapshot.payload}; this.state.form.available_quantity ??= ''; this.state.modal='snapshot'; }
    snapshotImageForm() {
        this.snapshotImageReadGeneration = (this.snapshotImageReadGeneration || 0) + 1;
        this.state.sourceImageContext = {version:this.navigationVersion,case_id:this.state.detail.id,revision:this.state.detail.revision};
        this.state.form = {}; this.state.modal='snapshot-image';
    }
    async saveSnapshotImage() {
        const context=this.state.sourceImageContext;
        if (!context || !this.contextMatches(context) || !this.state.form.image_data) return;
        const data=this.state.form.image_data, mime=this.state.form.image_mime;
        return this.perform(async()=>{
            const detail=await this.rpc('workbench_snapshot_source_image',[context.case_id,data,mime,context.revision]);
            if (!this.contextMatches(context)) return;
            this.state.detail=detail; this.state.modal=''; this.state.selections={}; this.state.preview=null;
        }, '源图已绑定到新快照');
    }
    async readSnapshotImage(event) {
        const context=this.state.sourceImageContext, file=event.target.files[0];
        const generation=this.snapshotImageReadGeneration = (this.snapshotImageReadGeneration || 0) + 1;
        this.state.form.image_data='';
        if (!context || !file) return;
        if(file.size > 8*1024*1024) { this.state.error='图片不得超过 8 MiB'; return; }
        const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
        if (this.snapshotImageReadGeneration !== generation || this.state.sourceImageContext !== context || !this.contextMatches(context) || this.state.modal !== 'snapshot-image') return;
        this.state.form.image_data=data; this.state.form.image_mime=file.type;
    }
    async saveProduct() {
        await this.perform(async () => {
            const form = {...this.state.form};
            if (form.available_quantity !== '') form.available_quantity = Number(form.available_quantity);
            this.state.detail = await this.rpc(this.state.modal === 'create' ? 'workbench_create' : 'workbench_snapshot', this.state.modal === 'create' ? [form] : [this.state.detail.id, form]);
            this.state.modal=''; this.state.tab='snapshot'; await this.load();
        }, '商品快照已保存');
    }
    async syncSnapshotGallery() {
        const detail = this.state.detail;
        if (!detail?.snapshot || detail.collection) return;
        const context = {version:this.navigationVersion, case_id:detail.id, revision:detail.revision};
        return this.perform(async () => {
            await this.rpc('workbench_archive_snapshot_gallery', [context.case_id, context.revision]);
            if (this.contextMatches(context)) await this.load();
        });
    }
    collectionForm() { this.state.form={name:'',garment:'',source_sku:'',source_url:'',site_id:'MLM',platform:'mercado-libre-mx',min_sold:500,min_reviews:50,min_rating:4.1,result_limit:15}; this.state.modal='collection'; }
    applyCollectionFilters() { this.state.collectionApplied = {...this.state.collectionFilters}; }
    resetCollectionFilters() { this.state.collectionFilters = {query:'',platform:''}; this.state.collectionApplied = {}; }
    productImages(value) { return productSourceImages(value); }
    productImage(image) { return image?.artifact_id ? this.libraryImage(image) : this.safeUrl(image?.url); }
    validImageArtifactId(id) { return Number.isSafeInteger(id) && id > 0; }
    collectionSourceImage(row) {
        const id = row.snapshot?.source_image_artifact_ids?.[0];
        if (!/^[1-9][0-9]*$/.test(String(id || '')) || !Number.isSafeInteger(Number(id))) return '';
        return this.libraryImage({artifact_id:Number(id)});
    }
    collectionPlatform(row) { return ({'shein-us':'SHEIN US','mercado-libre-mx':'Mercado Libre México'})[row.snapshot?.collection_config?.platform] || '未记录'; }
    get collectionGroups() {
        const groups = new Map();
        const filters = this.state.collectionApplied || {};
        const query = String(filters.query || '').trim().toLowerCase();
        for (const row of this.state.collections || []) {
            const snapshot = row.snapshot || {};
            if (query && ![row.id, row.name, snapshot.source_sku, snapshot.garment].some(v => String(v || '').toLowerCase().includes(query))) continue;
            if (filters.platform && snapshot.collection_config?.platform !== filters.platform) continue;
            const source = String(row.snapshot?.source_sku || '').trim();
            const company = Array.isArray(row.company_id) ? row.company_id[0] : row.company_id;
            const key = source && company ? `company:${company}:source:${source}` : `case:${row.id}`;
            if (!groups.has(key)) groups.set(key, {key, source:source || '未知', company:row.company_name || (company ? `公司 #${company}` : '公司未知'), records:[]});
            groups.get(key).records.push(row);
        }
        return [...groups.values()];
    }
    async readGarment(event) {
        const generation = this.garmentReadGeneration = (this.garmentReadGeneration || 0) + 1;
        const form = this.state.form, modal = this.state.modal, version = this.navigationVersion, context = this.state.libraryContext;
        form.image_data='';
        const file=event.target.files[0]; if (!file) return;
        if(file.size > 8*1024*1024) { this.state.error='图片不得超过 8 MiB'; return; }
        const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
        if (this.disposed || this.garmentReadGeneration !== generation || this.state.modal !== modal || this.state.form !== form || this.navigationVersion !== version || (modal === 'library-select' && (this.state.libraryContext !== context || !context || !this.contextMatches(context)))) return;
        form.image_data=data;form.image_mime=file.type;
        return {data, mime:file.type};
    }
    async saveCollection() {
        await this.perform(async()=>{
            const form={...this.state.form};
            for (const key of ['min_sold','min_reviews','min_rating','result_limit']) form[key]=Number(form[key]);
            this.state.detail=await this.rpc('workbench_collection_create',[form]);
            this.state.modal='';this.state.tab='tasks';await this.load();
        }, '独立采集已建立；外部研究由具备能力的 Worker 执行');
    }
    async loadLibrary() { this.state.library=(await this.rpc('workbench_library',[this.state.libraryQuery,this.state.librarySite])).rows; }
    get filteredLibrary() { return this.state.libraryResults; }
    async fetchLibraryPage() {
        const result = await this.rpc('workbench_library',['','',this.state.libraryApplied,this.state.libraryPage*this.state.libraryPageSize]);
        this.state.libraryResults=result.rows; this.state.libraryTotal=result.total; this.state.libraryPageSize=result.limit;
    }
    async applyLibraryFilters() { return this.perform(()=>{ this.state.libraryApplied = {...this.state.libraryFilters}; this.state.libraryPage=0; return this.fetchLibraryPage(); }); }
    async resetLibraryFilters() { if (this.state.loading) return; this.state.libraryFilters = {query:'', platform:'', dateFrom:'', dateTo:''}; return this.applyLibraryFilters(); }
    async libraryPageBy(delta) { return this.perform(()=>{ this.state.libraryPage += delta; return this.fetchLibraryPage(); }); }
    async filterLibrary() { return this.perform(()=>this.loadLibrary()); }
    async recollectLibrary(entry) {
        return this.perform(async()=>{
            const result = await this.rpc('workbench_library_recollect',[entry.id]);
            this.notification.add(result.reused ? '已复用图片补采任务，等待人工采集' : '已创建图片补采任务，等待人工采集', {type:'success'});
            await this.fetchLibraryPage();
        });
    }
    async importGallery(task) {
        return this.perform(async()=>{await this.rpc('workbench_library_import',[this.state.detail.id,task.id,task.result_hash]);}, '已验证图片已保存到公司图库');
    }
    libraryMetadataOrigin(origin) { return libraryMetadataOrigin(origin); }
    libraryImage(image) { return `/web/image/meli.optimization.artifact/${image.artifact_id}/data`; }
    async selectLibrary() {
        const context={version:this.navigationVersion,case_id:this.state.detail.id,revision:this.state.detail.revision};
        return this.perform(async()=>{await this.loadLibrary();if(!this.contextMatches(context)) return;this.state.libraryContext=context;this.state.librarySelection=null;this.state.libraryIndices=[];this.state.garmentArtifact='';this.state.modal='library-select';});
    }
    chooseLibrary(entry) { this.state.librarySelection=entry;this.state.libraryIndices=[]; }
    toggleLibrary(index) { const list=this.state.libraryIndices;this.state.libraryIndices=list.includes(index)?list.filter(i=>i!==index):[...list,index]; }
    get garmentArtifacts() { return (this.state.detail?.artifacts || []).filter(a=>a.mime_type?.startsWith('image/') && !a.library_origin_id); }
    async uploadGarment(event) {
        if (this.state.garmentUploadBusy || this.state.loading) return;
        this.state.garmentUploadBusy = true;
        try {
        const context=this.state.libraryContext, form=this.state.form;
        if (!context || this.state.modal !== 'library-select' || !this.contextMatches(context)) return;
        const image=await this.readGarment(event);
        if (!image || this.state.modal !== 'library-select' || this.state.libraryContext !== context || this.state.form !== form || !this.contextMatches(context)) return;
        await this.perform(async()=>{
            const id=await this.rpc('workbench_upload_garment',[context.case_id,image.data,image.mime]);
            if (this.state.modal !== 'library-select' || this.state.libraryContext !== context || !this.contextMatches(context)) return;
            await this.load();
            if (this.state.modal !== 'library-select' || this.state.libraryContext !== context || !this.contextMatches(context)) return;
            this.state.garmentArtifact=String(id);
            this.notification.add('服装参考图已归档', {type:'success'});
        });
        } finally { this.state.garmentUploadBusy = false; }
    }
    async saveLibrarySelection() {
        if (this.state.garmentUploadBusy) return;
        const context=this.state.libraryContext;
        if(!context || this.disposed || this.navigationVersion !== context.version || this.state.detail?.id !== context.case_id) return;
        return this.perform(async()=>{
            await this.rpc('workbench_library_generate',[context.case_id,this.state.librarySelection.id,[...this.state.libraryIndices],[],context.revision]);
            if(!this.contextMatches(context)) return;
            this.state.modal='';this.state.tab='tasks';await this.load();
        }, '所选目标图已冻结计划并逐图加入队列');
    }
    async startCollection() {
        if (this.state.loading || !this.state.detail?.collection || !this.state.detail.snapshot) return;
        const context={version:this.navigationVersion,case_id:this.state.detail.id,revision:this.state.detail.revision,snapshot_id:this.state.detail.snapshot.id};
        const current=()=>this.contextMatches(context) && this.state.detail?.snapshot?.id === context.snapshot_id;
        return this.perform(async()=>{
            let result;
            try { result=await this.rpc('workbench_collection_start',[context.case_id,context.snapshot_id,context.revision]); }
            catch (error) { if (current()) throw error; return; }
            if (!current()) return;
            this.state.tab='tasks';
            try { await this.load(); } catch (error) { if (current()) throw error; return; }
            if (!current()) return;
            this.state.collectionStartStatus={case_id:context.case_id,snapshot_id:context.snapshot_id,message:result?.message || ''};
        });
    }
    async startTitleFlow() {
        const context = {version:this.navigationVersion,case_id:this.state.detail.id,revision:this.state.detail.revision};
        return this.perform(async () => {
            await this.rpc('workbench_title_flow', [context.case_id]);
            if (!this.contextMatches(context)) return;
            this.state.tab='tasks'; await this.load();
        }, '标题优化流程已启动');
    }
    taskForm() { this.state.taskType='product.facts'; this.state.taskValues={site_id:'MLM',language:'es-MX'}; this.state.modal='task'; }
    get taskFields() { return this.state.data?.task_inputs[this.state.taskType] || []; }
    fieldLabel(field) { return ({facts_artifact_id:'商品事实产物',keywords_artifact_id:'关键词研究产物',site_id:'目标站点',reference_item_id:'已选参考商品 ID',selected_variant:'准确的参考 SKU',language:'标题语言',source_artifact_ids:'服装参考图产物',target_artifact_id:'目标构图图产物',generation_plan_id:'生成计划产物',image_artifact_id:'待质检图片产物'})[field] || field; }
    isArtifact(field) { return field.endsWith('_artifact_id') || ['generation_plan_id','source_artifact_ids'].includes(field); }
    artifactsFor(field) {
        const expected = {facts_artifact_id:'product.facts',keywords_artifact_id:'research.keywords'}[field];
        const artifacts = this.state.detail?.artifacts || [];
        if (!expected) return artifacts;
        const idOf = value => Array.isArray(value) ? value[0] : value;
        const current = this.state.detail?.snapshot?.id;
        return artifacts.filter(a => idOf(a.result_snapshot_id) === current && (this.state.detail?.tasks || []).some(t => t.id === idOf(a.result_task_id) && t.task_type === expected && t.state === 'succeeded' && (expected !== 'research.keywords' || t.result?.status !== 'empty')));
    }
    async saveTask() {
        await this.perform(async () => {
            const values = {...this.state.taskValues};
            if (this.state.taskType === 'gallery.collect') values.selected_variant = {seller_sku: values.selected_variant};
            if (values.source_artifact_ids) values.source_artifact_ids = [values.source_artifact_ids];
            await this.rpc('workbench_task',[this.state.detail.id,this.state.taskType,values]); this.state.modal=''; this.state.tab='tasks'; await this.load();
        }, '任务已加入队列，等待具备能力的 Worker');
    }
    async control(task, operation) { return this.perform(async () => {await this.rpc('workbench_control',[this.state.detail.id,task.id,operation]); await this.load();}, '任务状态已更新'); }
    expiredImageAvailable(task) {
        return task?.task_type === 'image.generate' && task.state === 'running' && task.lease_expired && Number.isSafeInteger(task.lease_epoch) && task.lease_epoch > 0 && task.snapshot_id === this.state.detail?.snapshot?.id;
    }
    openExpiredImage(task) {
        if (!this.expiredImageAvailable(task)) return;
        this.state.expiredImageTask = task;
        this.state.modal = 'expired-image';
    }
    async regenerateExpiredImage() {
        const task = this.state.expiredImageTask;
        if (!this.expiredImageAvailable(task)) return;
        const context={version:this.navigationVersion,case_id:this.state.detail.id,revision:this.state.detail.revision};
        return this.perform(async()=>{
            await this.rpc('workbench_regenerate_expired_image',[context.case_id,task.id,task.lease_epoch]);
            if (this.contextMatches(context)) {this.state.modal='';await this.load();}
        }, '已重新加入生成队列');
    }
    imageDecisionAvailable(task) {
        const detail = task?.status_detail;
        return imageReviewWaiting(task) && !task.lease_expired && task.snapshot_id === this.state.detail?.snapshot?.id && !!detail?.review_evidence && !!detail?.review_report_hash;
    }
    reviewCheckLabel(name) { return ({garment_construction:'服装结构',color_material:'颜色与材质',garment_details:'服装细节',identity_pose:'人物与姿势',accessories:'配饰',scene_lighting_framing:'场景、光线与构图',text_layout:'文字与排版'})[name] || name; }
    reviewCheckStatus(status) { return ({failed:'未通过',unknown:'无法确定',passed:'通过'})[status] || status; }
    openImageDecision(task, action) {
        if (!this.imageDecisionAvailable(task) || !['regenerate','manual_accept'].includes(action)) return;
        this.state.imageDecision = {task, action, note:'', intent_id:crypto.randomUUID().replaceAll('-', '')};
        this.state.modal = 'image-decision';
    }
    async submitImageDecision() {
        const item=this.state.imageDecision, task=item?.task, status=task?.status_detail, evidence=status?.review_evidence;
        if (!item || !this.imageDecisionAvailable(task) || !evidence) return;
        const context={version:this.navigationVersion,case_id:this.state.detail.id,revision:this.state.detail.revision};
        return this.perform(async()=>{
            await this.rpc('workbench_image_review_decide',[context.case_id,task.id,item.action,evidence.review_seq,evidence.candidate_sha256,status.review_report_hash,item.note,item.intent_id]);
            if (this.contextMatches(context)) {this.state.modal='';await this.load();}
        }, '已记录处理决定');
    }
    async resumeImageReview(task) {
        if (task?.status_detail?.code !== 'host_review_required' || !this.state.detail || task.snapshot_id !== this.state.detail.snapshot?.id) return;
        const context={version:this.navigationVersion,case_id:this.state.detail.id,revision:this.state.detail.revision};
        const review=task.status_detail;
        const best=review.review_kind === 'best_of_budget';
        const args=best ? [context.case_id,task.id,review.candidate_artifact_id,review.candidate_sha256,review.candidate_attempt_id,review.candidate_attempt_number,review.review_seq ?? null] : [context.case_id,task.id,review.review_seq ?? null];
        return this.perform(async()=>{
            const requested=await this.rpc(best ? 'workbench_request_best_review_resume' : 'workbench_request_host_review_resume',args);
            if (this.contextMatches(context)) {
                await this.load();
                this.notification.add(requested === false ? '旧复核状态已恢复，请再次点击继续复核' : '已请求继续复核原图片', {type: 'success'});
            }
        });
    }
    async retryDerived(task) { return this.perform(async () => { const result = await this.rpc('workbench_retry_derived',[this.state.detail.id,task.id]); await this.load(); if (!result.ok) throw new Error('后续操作仍未完成，请检查任务详情'); }, '后续操作已完成'); }
    get groupedTasks() { return visibleCollectionTasks(groupResearchRetries(this.state.detail?.tasks || [], this.state.detail?.retry_events || []), this.state.detail); }
    get taskQueueGroups() { return groupImageTasks(this.groupedTasks); }
    get imagePlans() { return imagePlanGroups(this.state.detail?.tasks, this.state.detail?.artifacts, this.state.detail?.snapshot?.id); }
    imagePlanSelection(plan) {
        const available = (plan.rows || []).filter(row => row.task?.state === 'succeeded');
        const saved = this.state.imagePlanSelections[plan.planId];
        return Array.isArray(saved) ? saved.map(target => available.find(row => row.targetId === target)?.task.id).filter(id => id !== undefined) : available.map(row => row.task.id);
    }
    saveImagePlanSelection(plan, ids) {
        this.state.imagePlanSelections[plan.planId] = ids.map(id => plan.rows.find(row => row.task?.id === id)?.targetId).filter(Boolean);
        try { sessionStorage.setItem(this.imagePlanKey, JSON.stringify(this.state.imagePlanSelections)); } catch {}
    }
    toggleImagePlan(plan, id) {
        const selected = this.imagePlanSelection(plan);
        this.saveImagePlanSelection(plan, selected.includes(id) ? selected.filter(i => i !== id) : [...selected, id]);
    }
    moveImagePlan(plan, index, delta) {
        const selected = [...this.imagePlanSelection(plan)], next = index + delta;
        if (next >= 0 && next < selected.length) [selected[index], selected[next]] = [selected[next], selected[index]];
        this.saveImagePlanSelection(plan, selected);
    }
    resetImagePlan(plan) { delete this.state.imagePlanSelections[plan.planId]; try { sessionStorage.setItem(this.imagePlanKey, JSON.stringify(this.state.imagePlanSelections)); } catch {} }
    async proposeImagePlan(plan) {
        const context={version:this.navigationVersion,case_id:this.state.detail.id,revision:this.state.detail.revision};
        const selected=this.imagePlanSelection(plan);
        await this.perform(async () => {
            const proposal=await this.rpc('workbench_image_plan_propose',[context.case_id,Number(plan.planId),selected]);
            if (this.contextMatches(context)) { this.state.preview={...proposal,case_id:context.case_id,revision:context.revision}; this.state.modal='preview'; }
        });
    }
    get visibleTasks() {
        const kinds={titles:['title.generate'],research:['research.bestseller','research.keywords'],gallery:['gallery.collect'],images:['image.generate','image.qc']};
        return this.groupedTasks.filter(t => (kinds[this.state.tab] || []).includes(t.task_type));
    }
    get unmappedResultTasks() {
        if (this.state.tab !== 'images') return this.visibleTasks;
        const shown = new Set(this.imagePlans.flatMap(plan => (plan.rows || []).map(row => row.task?.id).filter(id => id != null)));
        return this.visibleTasks.filter(task => !shown.has(task.id));
    }
    get hostOfflineHint() {
        const detail = this.state.detail;
        return detail?.host_status?.online === false && (detail.tasks || []).some(task => task.state === 'running' && ['research.keywords','title.generate'].includes(task.task_type));
    }
    get factsView() { return productFactsView(this.state.detail); }
    rows(result) { return candidateRows(result); }
    value(value) { return displayValue(value); }
    json(value) { return JSON.stringify(value,null,2); }
    collectionImportFailed(task) { return !!this.state.detail?.collection && task.task_type === 'research.bestseller' && task.state === 'succeeded' && task.status_detail?.code === 'derived_action_failed'; }
    taskStatusClass(task) { return this.collectionImportFailed(task) ? 'failed' : (imageReviewWaiting(task) || (task.state === 'running' && task.lease_expired) ? 'waiting' : task.state); }
    taskStatusLabel(task) {
        if (this.collectionImportFailed(task)) return '入库失败／待重试';
        if (task.state === 'running' && task.lease_expired) return '租约已过期';
        if (imageReviewWaiting(task)) return '待处理';
        if (task.task_type === 'image.generate' && task.state === 'succeeded' && task.result?.visual_qc?.status === 'manual_accepted') return '已完成 · 人工确认使用';
        if (task.task_type === 'research.bestseller') {
            if (task.status_detail?.code === 'research_images_pending') return '结果待入库';
            if (task.state === 'succeeded' && Array.isArray(task.result?.candidates) && !task.result.candidates.length) return '未找到候选商品';
        }
        return this.label(task.state);
    }
    label(state) { return ({queued:'排队中',running:'执行中',waiting:'等待条件',succeeded:'已完成',failed:'失败',cancelled:'已取消',skipped:'已跳过'})[state] || state; }
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
    async previewImagePublish(proposal, variantId=null) {
        const context={version:this.navigationVersion,case_id:this.state.detail.id,revision:this.state.detail.revision};
        await this.perform(async () => {
            const args=[context.case_id,proposal.id,proposal.digest];
            if (variantId) args.push(variantId);
            const preview=await this.rpc('workbench_image_publish_preview',args);
            if (!this.contextMatches(context)) return;
            this.state.imagePublishPreview={...preview,proposal_id:proposal.id};
            this.state.imagePublishVariant=variantId || '';
            this.state.imagePublishSkuConfirmed=false;
            this.state.modal='image-publish-preview';
        });
    }
    async startImagePublish() {
        const p=this.state.imagePublishPreview;
        if (!p || this.state.detail?.id !== p.case_id) return;
        if (p.target?.selected_variant_id && !this.state.imagePublishSkuConfirmed) return;
        const context={version:this.navigationVersion,case_id:p.case_id,revision:this.state.detail.revision};
        await this.perform(async () => {
            const args=[p.case_id,p.proposal_id,p.proposal_digest,p.digest];
            if (p.target?.selected_variant_id) args.push(p.target.selected_variant_id, true);
            const job=await this.rpc('workbench_image_publish_start',args);
            if (!this.contextMatches(context)) return;
            await this.runImagePublication(context,job.id);
        });
    }
    async resumeImagePublication(job) {
        const context={version:this.navigationVersion,case_id:this.state.detail.id,revision:this.state.detail.revision};
        await this.perform(async () => this.runImagePublication(context,job.id));
    }
    async reconcileImagePublication(job) {
        const context={version:this.navigationVersion,case_id:this.state.detail.id,revision:this.state.detail.revision};
        await this.perform(async () => {
            const result=await this.rpc('workbench_image_publish_reconcile',[context.case_id,job.id]);
            if (!this.contextMatches(context)) return;
            await this.load();
            const message=result.state === 'published' ? '平台图片已核验' : result.code === 'link_verified' ? '图片绑定已核验，可继续发布' : result.message || '平台结果仍需核对';
            this.notification.add(message, {type:['published','linking'].includes(result.state) ? 'success' : 'warning'});
        });
    }
    async abandonImagePublication(job) {
        if (!globalThis.confirm('确认放弃本次未知上传？该图片未绑定到商品，已上传文件可能仍留在平台。')) return;
        const context={version:this.navigationVersion,case_id:this.state.detail.id,revision:this.state.detail.revision};
        await this.perform(async () => {
            await this.rpc('workbench_image_publish_abandon',[context.case_id,job.id]);
            if (!this.contextMatches(context)) return;
            await this.load();
            this.notification.add('已放弃本次上传，可以重新核验并发布', {type:'success'});
        });
    }
    async runImagePublication(context, jobId) {
        let result={id:jobId,state:'queued'};
        for (let i=0;i<200 && this.contextMatches(context);i++) {
            const step=await this.rpc('workbench_image_publish_reserve',[context.case_id,jobId]);
            if (!this.contextMatches(context)) return;
            if (!step.nonce) { result=step; break; }
            result=await this.rpc('workbench_image_publish_dispatch',[context.case_id,jobId,step.nonce]);
            if (['published','failed','reconciliation_required'].includes(result.state)) break;
        }
        if (!this.contextMatches(context)) return;
        this.state.imagePublishResult=result;
        this.state.modal=''; this.state.tab='proposals';
        await this.load();
        const message=result.state === 'published' ? '图片已发布并核验' : result.state === 'reconciliation_required' ? '发布结果待核对，请勿重复提交' : result.code === 'dispatch_in_progress' ? '平台请求处理中，请稍后查看' : '图片发布尚未完成，请查看记录';
        this.notification.add(message,{type:result.state === 'published' ? 'success' : 'warning'});
    }
    async confirm() {
        const p=this.state.preview;
        if (!p || this.disposed || this.state.detail?.id !== p.case_id || this.state.detail?.revision !== p.revision) return;
        const context={version:this.navigationVersion,case_id:p.case_id,revision:p.revision};
        await this.perform(async () => {
            await this.rpc('workbench_confirm',[p.case_id,p.id,p.digest]);
            if (!this.contextMatches(context)) return;
            this.state.modal=''; this.state.tab='proposals'; await this.load();
        }, '已确认确切方案；图片方案可继续核验平台发布范围');
    }
}
registry.category('actions').add('meli_optimization.workbench', OptimizationWorkbench);

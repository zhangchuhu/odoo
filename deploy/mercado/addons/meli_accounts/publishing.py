import hashlib
import json
import re
from urllib.parse import urlparse
import requests
from odoo import api, fields, models
from odoo.exceptions import UserError
from .store_api import API

STATES = [('draft','待检查'),('blocked','需补资料'),('ready','检查通过'),('queued','等待发布'),('sending','发布中'),('done','已创建刊登'),('failed','发布失败'),('uncertain','待核对结果')]
LOCKED = {'queued','sending','done','uncertain'}

def attr_value(a):
    return a.get('value_name') or next((v.get('name') for v in a.get('values',[]) if v.get('name')), '')

def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def read_source_item(product, item_id=None):
    product.ensure_one()
    product.check_access('read')
    source = product.meli_source_store_id
    if not source:
        raise UserError('请先核对商品的来源店铺归属，再准备发布。')
    source.check_access('read')
    if source.state != 'connected':
        raise UserError('来源店铺需要重新授权。')
    identifier = item_id or product.meli_id
    if not identifier or identifier != product.meli_id:
        raise UserError('商品来源刊登编号已变化，请重新准备发布。')
    data = source._get('/items/' + identifier, {'include_attributes': 'all'})
    if not isinstance(data, dict) or data.get('id') != identifier or str(data.get('seller_id')) != source.seller_id:
        raise UserError('无法核实来源店铺商品归属。')
    if data.get('currency_id') != 'USD':
        raise UserError('原商品不是美元价格，需先确认换算规则。')
    return source, data

def source_snapshot(source, data, variant):
    pictures = data.get('pictures', [])
    if variant.get('picture_ids'):
        pictures = [p for p in pictures if p['id'] in variant['picture_ids']]
    terms = []
    raw_terms = variant.get('sale_terms') or data.get('sale_terms') or []
    if not isinstance(raw_terms, list):
        raise UserError('来源商品的销售条款格式无效。')
    for term in raw_terms:
        if not isinstance(term, dict) or not term.get('id'):
            raise UserError('来源商品的销售条款格式无效。')
        value = {'id': term['id']}
        first = next(iter(term.get('values') or []), {})
        value_id = term.get('value_id') or first.get('id')
        value_name = term.get('value_name') or first.get('name')
        if value_id:
            value['value_id'] = value_id
        if value_name:
            value['value_name'] = value_name
        if len(value) > 1:
            terms.append(value)
    return {'category_id': data['category_id'], 'pictures': pictures, 'sale_terms': terms,
            'source_store_id': source.id, 'source_seller_id': source.seller_id}

class Publication(models.Model):
    _name = 'meli.store.publication'
    _description = '店铺商品发布'
    _rec_name = 'sku'
    _order = 'id desc'
    _unique_source = models.Constraint('UNIQUE(store_id, source_item, source_variation)', '此商品规格已存在目标店铺发布记录，请打开原记录。')

    store_id = fields.Many2one('meli.independent.store', required=True, ondelete='restrict', string='目标店铺', readonly=True)
    company_id = fields.Many2one(related='store_id.company_id', store=True)
    product_id = fields.Many2one('product.product', required=True, ondelete='restrict', string='原商品', readonly=True)
    source_item = fields.Char(string='原店商品编号', readonly=True)
    source_variation = fields.Char(required=True, default='0', readonly=True, string='原规格编号')
    sku = fields.Char(string='SKU', required=True, readonly=True)
    family_name = fields.Char(string='英文商品名称', required=True, help='同款所有尺码使用相同英文名称。')
    english_confirmed = fields.Boolean(string='已确认英文名称', help='请确认商品名称使用英语；同款所有尺码保持一致。')
    description = fields.Text(string='英文描述')
    price = fields.Float(string='售价（USD）', digits=(16,2), required=True, readonly=True)
    gtin = fields.Char(string='商品条码（GTIN）')
    listing_type = fields.Selection([('gold_special','标准 Gold Special'),('gold_pro','高级 Gold Pro')], default='gold_special', required=True, string='刊登类型')
    state = fields.Selection(STATES, default='draft', required=True, readonly=True, copy=False, string='状态')
    check_result = fields.Text(string='检查结果', readonly=True)
    result = fields.Text(string='发布结果', readonly=True)
    target_item = fields.Char(string='新店商品编号', readonly=True, copy=False)
    marketplace_items = fields.Text(string='站点刊登编号', readonly=True, copy=False)
    reconcile_id = fields.Char(string='核对商品编号', help='仅在结果不确定时填写新店后台查到的 CBT 商品编号。')
    checked_at = fields.Datetime(readonly=True, string='检查时间')
    submitted_at = fields.Datetime(readonly=True, string='提交时间')
    source_data = fields.Json(readonly=True, groups='base.group_system')
    payload = fields.Json(readonly=True, groups='base.group_system')
    target_snapshot = fields.Json(readonly=True, groups='base.group_system')
    checked_hash = fields.Char(readonly=True, groups='base.group_system')
    picture_ids = fields.Json(readonly=True, groups='base.group_system', copy=False)
    attribute_ids = fields.One2many('meli.store.publication.attribute','publication_id',string='商品属性')

    @api.model_create_multi
    def create(self, vals_list):
        raise UserError('请通过“选择店铺和商品”建立发布记录。')

    def write(self, vals):
        allowed={'english_confirmed','family_name','description','gtin','listing_type','reconcile_id','attribute_ids'}
        if set(vals)-allowed:
            raise UserError('发布状态和商品关联由系统维护。')
        if set(vals)-{'reconcile_id'}:
            if any(r.state in LOCKED for r in self):
                raise UserError('已提交的刊登不可修改，请先查看发布结果。')
            vals=dict(vals,state='draft',checked_hash=False,check_result='资料已修改，请重新检查。')
        return super().write(vals)

    def _set(self, vals):
        return super(Publication,self).write(vals)

    def _lock(self):
        self.ensure_one()
        self.check_access('write')
        self.env.cr.execute('SELECT id FROM meli_store_publication WHERE id=%s FOR UPDATE',[self.id])
        self.invalidate_recordset()

    def _build(self, target):
        self.ensure_one()
        source=self.sudo().source_data
        attrs=[]
        for a in self.attribute_ids:
            if a.code in ('GTIN','SELLER_SKU'):
                continue
            if a.value:
                item={'id':a.code,'value_name':a.value}
                if a.value_id and a.value==a.original_value:
                    item['value_id']=a.value_id
                attrs.append(item)
        attrs.append({'id':'SELLER_SKU','value_name':self.sku})
        if self.gtin:
            attrs.append({'id':'GTIN','value_name':self.gtin.strip()})
        if not any(a['id']=='ITEM_CONDITION' for a in attrs):
            attrs.append({'id':'ITEM_CONDITION','value_id':'2230284','value_name':'New'})
        site={'site_id':'MLM','logistic_type':'fulfillment','listing_type_id':self.listing_type,'price':self.price}
        body={'sites_to_sell':[site],'category_id':source['category_id'],
              'currency_id':'USD','price':self.price,'attributes':attrs,
              'sale_terms':source.get('sale_terms') or []}
        if self.description:
            body['description']={'plain_text':self.description}
        if target['user_products']:
            body['family_name']=self.family_name
            body['pictures']=[{'id':x} for x in (self.sudo().picture_ids or [])]
        else:
            body['title']=self.family_name
            body['catalog_listing']=False
            site['pictures']=[{'id':x} for x in (self.sudo().picture_ids or [])]
        # Full inventory is controlled by Mercado Libre; source stock is never copied.
        return body

    def action_check(self):
        targets={}
        categories={}
        sources={}
        for job in self:
            job._lock()
            if job.state in LOCKED:
                continue
            try:
                if job.product_id.meli_id_variation and job.product_id.meli_id_variation != job.source_variation:
                    raise UserError('发布记录与商品规格不一致，请从正确规格重新准备发布。')
                key = (job.product_id.id, job.source_item)
                if key not in sources:
                    sources[key] = read_source_item(job.product_id, job.source_item)
                source, data = sources[key]
                if source == job.store_id:
                    raise UserError('来源店铺与目标店铺相同，请选择另一家店铺。')
                variants = data.get('variations') or [{}]
                variant = next((v for v in variants if str(v.get('id') or '0') == job.source_variation), None)
                if variant is None:
                    raise UserError('来源商品规格已变化，请重新准备发布。')
                snapshot = source_snapshot(source, data, variant)
                changes = {'source_data': snapshot}
                if snapshot['pictures'] != (job.sudo().source_data or {}).get('pictures'):
                    changes['picture_ids'] = False
                job.sudo()._set(changes)
                store=job.store_id
                if store.id not in targets:
                    targets[store.id]=store._publishing_target()
                target=targets[store.id]
                body=job._build(target)
                cat=body['category_id']
                if (store.id,cat) not in categories:
                    categories[(store.id,cat)]=store._get('/categories/'+cat+'/attributes')
                definitions=categories[(store.id,cat)]
                if not isinstance(definitions,list):
                    raise UserError('无法获取类目必填属性。')
                errors=[]
                if not any(t.get('id') == 'WARRANTY_TYPE' for t in body['sale_terms']):
                    errors.append('来源商品缺少保修销售条款（WARRANTY_TYPE），请先补全真实销售条款。')
                if not job.english_confirmed:
                    errors.append('请确认商品名称已填写为英文，并勾选确认。')
                attrs={a['id']:a for a in body['attributes']}
                if not job.family_name or len(job.family_name)>60:
                    errors.append('英文商品名称需为 1–60 个字符。')
                if job.price<=0:
                    errors.append('售价必须大于 0。')
                if not job.sudo().source_data.get('pictures'):
                    errors.append('商品缺少图片。')
                if not job.gtin and 'EMPTY_GTIN_REASON' not in attrs:
                    reason = next((d for d in definitions if d.get('id') == 'EMPTY_GTIN_REASON'), None)
                    if reason and not job.attribute_ids.filtered(lambda a: a.code == 'EMPTY_GTIN_REASON'):
                        self.env['meli.store.publication.attribute']._add(job, 'EMPTY_GTIN_REASON', reason.get('name') or '无商品条码原因', '')
                    errors.append('缺少 GTIN，请填写真实条码；如符合平台豁免条件，请补充无条码原因。')
                if job.gtin and (not job.gtin.isdigit() or len(job.gtin) not in (8,12,13,14)):
                    errors.append('GTIN 应为 8、12、13 或 14 位数字。')
                for d in definitions:
                    tags=d.get('tags',{})
                    if (tags.get('required') or tags.get('new_required')) and d['id'] not in attrs:
                        errors.append('缺少必填属性：'+d.get('name',d['id']))
                        if not job.attribute_ids.filtered(lambda a:a.code==d['id']):
                            self.env['meli.store.publication.attribute']._add(job,d['id'],d.get('name',d['id']), '')
                for code in ['PACKAGE_HEIGHT','PACKAGE_LENGTH','PACKAGE_WIDTH','PACKAGE_WEIGHT']:
                    if code not in attrs:
                        errors.append('缺少包装属性：'+code)
                job._set({'payload':body,'target_snapshot':target,'checked_at':fields.Datetime.now(),
                    'checked_hash':fingerprint(body),'state':'blocked' if errors else 'ready',
                    'check_result':'\n'.join(errors) if errors else '基础资料检查通过。售价沿用原店美元价格。Full 可售库存以实际入仓为准。平台最终审核以发布返回为准。'})
            except UserError as e:
                job._set({'state':'blocked','check_result':str(e),'checked_hash':False})
        return True

    def action_publish(self):
        for job in self:
            job._lock()
            if job.state!='ready':
                raise UserError('只有“检查通过”的记录可发布。请先检查商品。')
            target=job.sudo().target_snapshot
            if fingerprint(job._build(target))!=job.sudo().checked_hash:
                raise UserError('商品资料发生变化，请重新检查。')
            job._set({'state':'queued','result':'已加入发布队列，等待处理。'})
        self.env.ref('meli_accounts.cron_publish')._trigger()
        return True

    def action_cancel_queue(self):
        for job in self:
            job._lock()
            if job.state=='queued':
                job._set({'state':'draft','result':'已取消排队，尚未提交美客多。'})
        return True

    def _upload_images(self, token):
        ids=[]
        for image in self.sudo().source_data.get('pictures',[])[:12]:
            url=image.get('secure_url') or image.get('url','')
            parsed=urlparse(url)
            if parsed.hostname!='http2.mlstatic.com':
                raise UserError('商品图片来源无效。')
            url='https://'+parsed.netloc+parsed.path
            with requests.get(url,timeout=30,stream=True,allow_redirects=False) as r:
                if r.status_code!=200:
                    raise UserError('下载商品图片失败。')
                parts=[];size=0
                for chunk in r.iter_content(65536):
                    size+=len(chunk)
                    if size>15*1024*1024:
                        raise UserError('商品图片超过 15 MB。')
                    parts.append(chunk)
            r=requests.post(API+'/pictures/items/upload',headers={'Authorization':'Bearer '+token},
                files={'file':('product.jpg',b''.join(parts),'image/jpeg')},timeout=45)
            if not r.ok or not r.json().get('id'):
                raise UserError('上传商品图片失败，请稍后重新检查并发布。')
            ids.append(r.json()['id'])
        return ids

    def _publish_one(self):
        self.ensure_one()
        self._lock()
        if self.state!='queued':
            return
        self._set({'state':'sending','submitted_at':fields.Datetime.now(),'result':'正在准备图片和核对目标店铺。'})
        self.env.cr.commit()  # Claim before token refresh can release row locks.
        try:
            target=self.store_id._publishing_target()
            if target!=self.sudo().target_snapshot or fingerprint(self._build(target))!=self.sudo().checked_hash:
                raise UserError('店铺或商品资料变化，请重新检查。')
            token=self.store_id._token()
            if not self.sudo().picture_ids:
                self._set({'picture_ids':self._upload_images(token)})
            body=self._build(target)
        except (UserError,requests.RequestException,ValueError) as e:
            message=str(e) if isinstance(e,UserError) else '准备发布时网络异常，请重新检查后重试。'
            self._set({'state':'failed','result':message})
            self.env.cr.commit()
            return
        self._lock()
        if self.state != 'sending':
            return
        self._set({'state':'sending','submitted_at':fields.Datetime.now(),'payload':body,'result':'已开始提交；结果不确定时不会自动重发。'})
        self.env.cr.commit() # Durable intent prevents duplicate POST after worker crash.
        try:
            r=requests.post(API+'/global/items',headers={'Authorization':'Bearer '+token},json=body,timeout=60)
            d=r.json()
        except (requests.RequestException,ValueError):
            self._set({'state':'uncertain','result':'网络中断或响应无法解析。请先在新店后台核对，勿重复发布。'})
            self.env.cr.commit()
            return
        identifier=d.get('id') if isinstance(d,dict) else None
        summary=json.dumps({k:d.get(k) for k in ['id','status','error','message','cause','errors','site_id'] if k in d},ensure_ascii=False) if isinstance(d,dict) else '平台返回格式异常。'
        # Never persist credentials even if a remote error echoes them.
        summary=summary.replace(token,'[REDACTED]')[:16000]
        if r.ok and identifier and re.fullmatch(r'(CBT\d+|U\d+|CBTU\d+)',str(identifier)):
            self._set({'state':'done','target_item':str(identifier),'result':summary})
        elif r.status_code>=500 or r.status_code in (408,409) or r.ok:
            self._set({'state':'uncertain','result':'需要核对是否已创建刊登。\n'+summary})
        else:
            self._set({'state':'failed','result':'HTTP %s\n%s' % (r.status_code,summary)})
        self.env.cr.commit()

    @api.model
    def _cron_publish(self):
        # A crashed request must be reconciled, never automatically repeated.
        self.env.cr.execute("SELECT id FROM meli_store_publication WHERE state='sending' AND submitted_at < (NOW() AT TIME ZONE 'UTC') - interval '10 minutes' FOR UPDATE SKIP LOCKED")
        stale=self.browse([r[0] for r in self.env.cr.fetchall()])
        stale._set({'state':'uncertain','result':'上次提交未保存最终响应，请在新店后台核对刊登。'})
        self.env.cr.commit()
        self.env.cr.execute("SELECT id FROM meli_store_publication WHERE state='queued' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED")
        row=self.env.cr.fetchone()
        if row:
            self.browse(row[0])._publish_one()
        if self.search_count([('state','=','queued')]):
            self.env.ref('meli_accounts.cron_publish')._trigger()

    def action_refresh_result(self):
        for job in self:
            job._lock()
            if job.state not in ('done','uncertain'):
                raise UserError('只有已提交的商品才能核对发布结果。')
            identifier=job.target_item or job.reconcile_id
            if not identifier or not re.fullmatch(r'(CBT\d+|U\d+|CBTU\d+)',identifier):
                raise UserError('请填写有效的新店 CBT 商品编号。')
            path='/items/'+identifier if identifier.startswith('CBT') and not identifier.startswith('CBTU') else '/user-products/'+identifier
            d=job.store_id._get(path)
            if str(d.get('seller_id') or d.get('user_id'))!=job.store_id.seller_id:
                raise UserError('该商品不属于目标店铺，拒绝关联。')
            attrs={a['id']:attr_value(a) for a in d.get('attributes',[])}
            if attrs.get('SELLER_SKU')!=job.sku:
                raise UserError('该商品 SKU 不匹配，请人工核对。')
            if identifier.startswith('CBT') and not identifier.startswith('CBTU'):
                mapped=job.store_id._get('/items/'+identifier+'/marketplace_items')
            else:
                mapped=job.store_id._get('/marketplace/user-products/'+identifier+'/mapping')
            job._set({'target_item':identifier,'state':'done',
                'marketplace_items':json.dumps(mapped,ensure_ascii=False,indent=2)[:16000],
                'result':'已核对目标店铺及 SKU。平台状态：'+str(d.get('status','请查看站点刊登'))})
        return True

class PublicationAttribute(models.Model):
    _name='meli.store.publication.attribute'
    _description='刊登商品属性'
    publication_id=fields.Many2one('meli.store.publication',required=True,ondelete='cascade')
    company_id=fields.Many2one(related='publication_id.company_id',store=True)
    code=fields.Char(required=True,readonly=True)
    name=fields.Char(string='属性',required=True,readonly=True)
    value=fields.Char(string='值')
    original_value=fields.Char(readonly=True)
    value_id=fields.Char(readonly=True)

    @api.model_create_multi
    def create(self,vals_list):
        raise UserError('属性由商品检查自动生成。')

    @api.model
    def _add(self,job,code,name,value,value_id=False):
        return super(PublicationAttribute,self).create({'publication_id':job.id,'code':code,'name':name,'value':value,'original_value':value,'value_id':value_id})

    def write(self,vals):
        if set(vals)-{'value'}:
            raise UserError('只能编辑属性值。')
        for job in self.mapped('publication_id'):
            job._lock()
            if job.state in LOCKED:
                raise UserError('已提交的刊登不可修改。')
            job._set({'state':'draft','checked_hash':False})
        return super().write(vals)

class PreparePublication(models.TransientModel):
    _name='meli.store.prepare'
    _description='选择店铺和商品'
    store_id=fields.Many2one('meli.independent.store',string='目标店铺',required=True,domain=[('state','=','connected')])
    product_ids=fields.Many2many('product.product',string='商品',required=True,domain=[('meli_id','!=',False)])

    def action_prepare(self):
        self.ensure_one()
        self.store_id._publishing_target()
        Job=self.env['meli.store.publication']
        ids=[]
        for product in self.product_ids:
            if product.company_id and product.company_id!=self.store_id.company_id:
                raise UserError('商品与目标店铺不属于同一公司。')
            source_store, d = read_source_item(product)
            if source_store == self.store_id:
                raise UserError('来源店铺与目标店铺相同，请选择另一家店铺。')
            variants = d.get('variations') or [{}]
            if product.meli_id_variation:
                variants = [v for v in variants if str(v.get('id')) == product.meli_id_variation]
                if not variants:
                    raise UserError('来源商品规格已变化，请重新导入核对。')
            for v in variants:
                variation=str(v.get('id') or '0')
                existing=Job.search([('store_id','=',self.store_id.id),('source_item','=',product.meli_id),('source_variation','=',variation)],limit=1)
                if existing:
                    existing._lock()
                    if product.meli_id_variation and existing.product_id != product:
                        if existing.state in LOCKED:
                            raise UserError('此规格已有锁定的发布记录，请先核对原记录的商品关联。')
                        existing.sudo()._set({'product_id': product.id, 'state': 'draft',
                            'checked_hash': False, 'check_result': '商品规格关联已更新，请重新检查。'})
                    ids.append(existing.id);continue
                attrs={a['id']:a for a in d.get('attributes',[]) if a.get('id')}
                attrs.update({a['id']:a for a in v.get('attributes',[]) if a.get('id')})
                attrs.update({a['id']:a for a in v.get('attribute_combinations',[]) if a.get('id')})
                sku=attr_value(attrs.get('SELLER_SKU',{})) or v.get('seller_custom_field') or d.get('seller_custom_field')
                if not sku:
                    raise UserError('商品 %s 缺少 SKU。' % product.display_name)
                source=source_snapshot(source_store, d, v)
                job=super(Publication,Job).create({'store_id':self.store_id.id,'product_id':product.id,
                    'source_item':product.meli_id,'source_variation':variation,'sku':sku,
                    'family_name':d['title'][:60], 'price':v.get('price',d['price']),
                    'gtin':attr_value(attrs.get('GTIN',{})), 'source_data':source,
                    'check_result':'请确认英文名称及商品属性，再点击检查。'})
                for code,a in attrs.items():
                    if code in ('GTIN','SELLER_SKU'):
                        continue
                    # Size charts belong to the source seller; never copy their identifiers.
                    value='' if code in ('SIZE_GRID_ID','SIZE_GRID_ROW_ID') else attr_value(a)
                    self.env['meli.store.publication.attribute']._add(job,code,a.get('name',code),value,False if not value else a.get('value_id'))
                ids.append(job.id)
        return {'type':'ir.actions.act_window','name':'商品发布','res_model':'meli.store.publication',
                'view_mode':'list,form','domain':[('id','in',ids)]}

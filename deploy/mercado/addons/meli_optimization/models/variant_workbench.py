"""Platform-verified picture variation previews and case creation."""
import re
from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from ..domain import canonical_hash
from ..domain.image_publish import collect_preflight
from ..domain.variant_groups import traditional_groups, bind_site_variations
from .optimization import PREFIX, ExactJson

_VARIANT_SNAPSHOT_GUARD = object()

class VariantSnapshot(models.Model):
    _inherit = PREFIX + 'snapshot'
    @api.model_create_multi
    def create(self, values_list):
        for vals in values_list:
            case=self.env[PREFIX+'case'].browse(vals.get('case_id',self.env.context.get('default_case_id'))).exists()
            if case and case.variant_identity and self.env.context.get('_variant_snapshot_guard') is not _VARIANT_SNAPSHOT_GUARD:
                raise AccessError('变体快照必须由平台核验后创建')
        return super().create(values_list)

class VariantCreationFence(models.Model):
    _name = PREFIX + 'variant.creation.fence'
    _description = 'Serialize creation of variants of the same platform item'
    target_key = fields.Char(required=True, index=True)
    version = fields.Integer(required=True, default=0)
    _unique_target = models.Constraint('UNIQUE(target_key)', '商品变体创建锁已存在')

class VariantWorkbench(models.Model):
    _inherit = PREFIX + 'case'
    variant_identity = fields.Char(readonly=True, index=True)
    variant_item_id = fields.Char(readonly=True, index=True)
    variant_members = ExactJson(readonly=True)
    _variant_identity_unique = models.Constraint('UNIQUE(company_id, variant_identity)', '该变体已有优化记录')

    @api.model_create_multi
    def create(self, values_list):
        protected=('variant_identity','variant_item_id','variant_members')
        if any('default_'+k in self.env.context for k in protected) or any(any(k in v for k in protected) for v in values_list):
            raise AccessError('变体身份由平台核验后设置')
        return super().create(values_list)

    @api.model
    def workbench_variant_shops(self):
        self._authorize('editor')
        result=[]
        if not self.env['meli.independent.store'].has_access('read'):
            return result
        for shop in self.env['meli.independent.store'].search([('company_id','=',self.env.company.id),('state','=','connected')]):
            try:self._screen_shop(shop.id)
            except (AccessError,ValidationError):continue
            result.append({'id':shop.id,'name':shop.name,'site':shop.site})
        return result

    def _variant_read_product(self, shop, item_id):
        cache={}
        def get(path):
            if path not in cache:cache[path]=self._image_publish_read(shop,path)
            return cache[path]
        try:
            target=collect_preflight(get,item_id,shop.site,shop.seller_id,shop.marketplace_seller_id,shop.logistic_type)
            item=get('/marketplace/items/'+item_id);parent=get('/marketplace/items/'+target['parent_id'])
            if target['model']!='traditional':
                raise ValueError('该商品使用 User Product，暂不能安全识别全部图片变体')
            groups=traditional_groups(parent)
            if any(not g['selectable'] for g in groups):
                for group in groups:
                    group['selectable']=False;group['reason']=group['reason'] or '其他变体图片关系不完整，请核对'
                    group['stock']=None
            elif item.get('variations') or parent.get('variations'):
                groups=bind_site_variations(groups,item.get('variations'))
            else:
                quantity=item.get('available_quantity')
                groups[0]['stock']=quantity if type(quantity) is int and quantity>=0 else None
        except (ValueError,KeyError,TypeError) as exc:
            raise ValidationError(str(exc)) from exc
        title=item.get('title') or parent.get('title')
        if not isinstance(title,str) or not title.strip():raise ValidationError('平台商品标题不可用')
        return {'item_id':item_id,'parent_id':target['parent_id'],'site':shop.site,'title':title[:2000],
                'model':target['model'],'groups':groups,
                'site_variation_ids':sorted(str(v['id']) for v in (item.get('variations') or []) if isinstance(v,dict) and v.get('id') is not None)}

    @api.model
    def workbench_variant_preview(self, shop_id, item_id):
        self._authorize('editor')
        if type(shop_id) is not int or not isinstance(item_id,str) or not re.fullmatch(r'MLM[0-9]+',item_id):
            raise ValidationError('请选择店铺并填写有效的墨西哥站商品 ID')
        shop=self._screen_shop(shop_id)
        if shop.company_id != self.env.company:raise AccessError('店铺公司不匹配')
        data=self._variant_read_product(shop,item_id)
        existing=self.sudo().search([('company_id','=',shop.company_id.id),('shop_id','=',shop.id),('variant_item_id','=',item_id)])
        for group in data['groups']:
            ids=set(group['variant_ids']); matching=existing.filtered(lambda c: bool(ids.intersection(set(c.variant_members or []))) or (not ids and c.variant_members==[]))
            group['existing_id']=matching[:1].id or None
            if matching:group.update(selectable=False,reason='该变体或部分规格已有优化记录')
        payload={'shop_id':shop.id,'shop_name':shop.name,'item':data}
        return dict(payload,digest=canonical_hash(payload))

    @api.model
    def workbench_variant_create(self, shop_id, item_id, expected_digest, group_keys, batch_name=''):
        self._authorize('editor')
        if not isinstance(group_keys,list) or not group_keys or any(not isinstance(x,str) for x in group_keys) or len(set(group_keys))!=len(group_keys):
            raise ValidationError('请选择有效的变体')
        if not isinstance(batch_name,str) or len(batch_name)>200:raise ValidationError('批次名称无效')
        preview=self.workbench_variant_preview(shop_id,item_id)
        if not isinstance(expected_digest,str) or preview['digest']!=expected_digest:raise ValidationError('平台商品或变体已变化，请重新读取')
        selected=[g for g in preview['item']['groups'] if g['key'] in group_keys]
        if len(selected)!=len(group_keys) or any(not g['selectable'] for g in selected):raise ValidationError('选择的变体不可创建')
        shop=self._screen_shop(shop_id)
        fence=f'{shop.company_id.id}:{shop.id}:{item_id}'
        self.env.cr.execute('''INSERT INTO meli_optimization_variant_creation_fence (target_key,version) VALUES (%s,1)
            ON CONFLICT (target_key) DO UPDATE SET version=meli_optimization_variant_creation_fence.version+1''',[fence])
        existing=self.sudo().search([('company_id','=',shop.company_id.id),('shop_id','=',shop.id),('variant_item_id','=',item_id)])
        for group in selected:
            ids=set(group['variant_ids'])
            if existing.filtered(lambda c: bool(ids.intersection(set(c.variant_members or []))) or (not ids and c.variant_members==[])):
                raise ValidationError('变体已创建，请重新读取')
        batch=self.env[PREFIX+'batch'].create({'name':batch_name.strip() or '手动优化批次'})
        result=[]
        for g in selected:
            identity=canonical_hash({'company_id':shop.company_id.id,'shop_id':shop.id,'item_id':item_id,'members':g['variant_ids']})
            case=self.create({'name':item_id+' · '+g['label']+' · '+preview['item']['title'],'batch_id':batch.id,'shop_id':shop.id})
            case._set({'variant_identity':identity,'variant_item_id':item_id,'variant_members':g['variant_ids']})
            scope={'model':'traditional','parent_id':preview['item']['parent_id'],'key':g['key'],'label':g['label'],
                   'variant_ids':g['variant_ids'],'site_variant_ids':g.get('site_variant_ids',[]),'picture_ids':g['picture_ids'],'sizes':g['sizes'], 'attribute_signature':g.get('attribute_signature')}
            payload={'shop':shop.name,'shop_id':shop.id,'item_id':item_id,'title':preview['item']['title'],'site_id':shop.site,
                     'available_quantity':g['stock'],'value_status':'known' if g['stock'] is not None else 'unknown',
                     'source':'platform_variant','variant_scope':scope}
            snapshot=self.env[PREFIX+'snapshot'].with_context(_variant_snapshot_guard=_VARIANT_SNAPSHOT_GUARD).create({'case_id':case.id,'payload':payload})
            result.append({'case_id':case.id,'snapshot_id':snapshot.id,'revision':case.revision,'label':g['label']})
        return result

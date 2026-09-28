"""Read-only Mercado Libre imports, scoped to an independently authorized seller."""
import base64
import logging
import re
from urllib.parse import urlparse

import requests
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .models import MANAGER
from .store_api import API

_logger = logging.getLogger(__name__)
ITEM = re.compile(r'(CBT|MLM)\d+\Z')


def attribute_value(attributes, code):
    for attribute in attributes or []:
        if attribute.get('id') == code:
            return attribute.get('value_name') or next(
                (v.get('name') for v in attribute.get('values', []) if v.get('name')), False)
    return False


class StoreImportEntry(models.Model):
    _inherit = 'meli.independent.store'

    def action_import_products(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group(MANAGER):
            raise AccessError('需要美客多管理权限。')
        if self.state != 'connected' or not self.seller_id:
            raise UserError('请先授权此店铺。')
        self.env.cr.execute('SELECT id FROM meli_independent_store WHERE id=%s FOR UPDATE', [self.id])
        self.write({'write_date': fields.Datetime.now()})
        Job = self.env['meli.store.import']
        job = Job.search([('store_id', '=', self.id), ('state', 'in', ['queued', 'running'])], limit=1)
        if not job:
            job = super(StoreImport, Job).create({'store_id': self.id, 'requested_by': self.env.uid})
        self.env.ref('meli_accounts.cron_store_import')._trigger()
        return {'type': 'ir.actions.act_window', 'name': '店铺商品导入',
                'res_model': Job._name, 'view_mode': 'form', 'res_id': job.id}

    def action_view_imports(self):
        self.ensure_one()
        self.check_access('read')
        return {'type': 'ir.actions.act_window', 'name': '商品导入记录',
                'res_model': 'meli.store.import', 'view_mode': 'list,form',
                'domain': [('store_id', '=', self.id)]}

    def _import_get(self, path, token, params=None):
        # A token is acquired before the batch transaction. Never rotate tokens
        # (which commits) inside an item's savepoint.
        try:
            response = requests.get(API + path, headers={'Authorization': 'Bearer ' + token},
                                    params=params, timeout=30)
            if response.status_code == 404 and path.endswith('/description'):
                return {}
            if not response.ok:
                raise UserError('美客多读取失败（HTTP %s）。请检查授权或稍后重试。' % response.status_code)
            data = response.json()
        except (requests.RequestException, ValueError):
            raise UserError('美客多连接失败或响应无效，请稍后重试。')
        if not isinstance(data, dict) or data.get('error'):
            raise UserError('美客多返回了无效数据。')
        return data

    def _import_image(self, url):
        parsed = urlparse(url or '')
        if parsed.scheme not in ('http', 'https') or parsed.hostname != 'http2.mlstatic.com' or parsed.port:
            raise UserError('商品图片地址无效。')
        try:
            with requests.get('https://http2.mlstatic.com' + parsed.path, timeout=20,
                              stream=True, allow_redirects=False) as response:
                if response.status_code != 200:
                    raise UserError('商品图片下载失败，请重试。')
                content = bytearray()
                for chunk in response.iter_content(65536):
                    content.extend(chunk)
                    if len(content) > 15 * 1024 * 1024:
                        raise UserError('商品图片超过 15 MB。')
                return base64.b64encode(content)
        except requests.RequestException:
            raise UserError('商品图片下载超时或失败，请重试。')


class StoreImport(models.Model):
    _name = 'meli.store.import'
    _description = '店铺商品导入'
    _order = 'id desc'
    _rec_name = 'store_id'

    store_id = fields.Many2one('meli.independent.store', required=True, readonly=True, ondelete='restrict')
    company_id = fields.Many2one(related='store_id.company_id', store=True)
    requested_by = fields.Many2one('res.users', required=True, readonly=True)
    state = fields.Selection([('queued', '排队中'), ('running', '导入中'), ('done', '已完成'),
                              ('partial', '部分失败'), ('failed', '失败'), ('cancelled', '已取消')],
                             default='queued', required=True, readonly=True, index=True)
    line_ids = fields.One2many('meli.store.import.line', 'import_id', readonly=True)
    total_count = fields.Integer(string='已发现刊登', compute='_compute_counts')
    created_count = fields.Integer(string='新增刊登', compute='_compute_counts')
    updated_count = fields.Integer(string='更新刊登', compute='_compute_counts')
    failed_count = fields.Integer(string='失败刊登', compute='_compute_counts')
    message = fields.Text(string='进度 / 结果', readonly=True)
    scroll_id = fields.Char(readonly=True, groups='base.group_system')
    discovered = fields.Boolean(readonly=True)
    finished_at = fields.Datetime(string='完成时间', readonly=True)
    scan_seen = fields.Json(readonly=True, groups='base.group_system')
    _one_active_store = models.UniqueIndex("(store_id) WHERE state IN ('queued', 'running')",
                                           '此店铺已有正在运行的导入任务。')

    @api.depends('line_ids.state')
    def _compute_counts(self):
        for job in self:
            job.total_count = len(job.line_ids)
            job.created_count = len(job.line_ids.filtered(lambda line: line.state == 'created'))
            job.updated_count = len(job.line_ids.filtered(lambda line: line.state == 'updated'))
            job.failed_count = len(job.line_ids.filtered(lambda line: line.state == 'failed'))

    @api.model_create_multi
    def create(self, vals_list):
        raise UserError('请从独立店铺页面点击“导入商品”。')

    def write(self, vals):
        raise UserError('导入记录由后台维护。')

    def _set(self, vals):
        return super(StoreImport, self.sudo()).write(vals)

    def _lock(self):
        self.ensure_one()
        self.check_access('write')
        self.env.cr.execute('SELECT id FROM meli_store_import WHERE id=%s FOR UPDATE', [self.id])
        self.invalidate_recordset()

    def action_retry(self):
        for job in self:
            # Serialize against creation of another import for the same store.
            job.store_id.check_access('write')
            self.env.cr.execute('SELECT id FROM meli_independent_store WHERE id=%s FOR UPDATE', [job.store_id.id])
            job._lock()
            if job.state not in ('failed', 'partial', 'cancelled'):
                raise UserError('只能重试失败或已取消的导入。')
            if self.search_count([('store_id', '=', job.store_id.id), ('state', 'in', ['queued', 'running']), ('id', '!=', job.id)]):
                raise UserError('此店铺已有导入任务正在运行，请等待完成。')
            job.line_ids.filtered(lambda line: line.state == 'failed')._set({'state': 'pending', 'message': False})
            job._set({'state': 'queued', 'requested_by': self.env.uid, 'finished_at': False,
                      'scroll_id': False, 'scan_seen': [], 'discovered': False, 'message': '已重新排队，将保留已成功的记录。'})
        self.env.ref('meli_accounts.cron_store_import')._trigger()
        return True

    def action_cancel(self):
        for job in self:
            job._lock()
            if job.state in ('queued', 'running'):
                job._set({'state': 'cancelled', 'message': '已取消，已导入商品保留。', 'finished_at': fields.Datetime.now()})
        return True

    def action_refresh(self):
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def _discover_page(self, token):
        store = self.store_id
        params = {'search_type': 'scan', 'limit': 100}
        if self.sudo().scroll_id:
            params['scroll_id'] = self.sudo().scroll_id
        data = store._import_get('/marketplace/users/' + store.seller_id + '/items/search', token, params)
        if data.get('seller_id') and str(data['seller_id']) != store.seller_id:
            raise UserError('商品列表的卖家与此店铺不匹配。')
        identifiers = data.get('results')
        if not isinstance(identifiers, list) or any(not isinstance(i, str) or not ITEM.fullmatch(i) for i in identifiers):
            raise UserError('商品列表格式无效，无法确认导入范围。')
        total = data.get('paging', {}).get('total')
        if not isinstance(total, int) or total < 0:
            raise UserError('平台未返回有效的商品总数。')
        known = set(self.line_ids.mapped('item_id'))
        new = list(dict.fromkeys(i for i in identifiers if i not in known))
        if new:
            self.env['meli.store.import.line'].sudo()._add(self, new)
        seen = set(self.sudo().scan_seen or [])
        advanced = set(identifiers) - seen
        seen.update(identifiers)
        complete = len(seen) >= total
        if not complete and (not advanced or not data.get('scroll_id')):
            raise UserError('平台分页未返回剩余商品，请重试导入。')
        self._set({'scroll_id': data.get('scroll_id') or False, 'scan_seen': sorted(seen), 'discovered': complete})

    def _run_batch(self):
        self.ensure_one()
        if self.state not in ('queued', 'running'):
            return
        store = self.store_id
        try:
            store.check_access('write')
            token = store._token()
            self._lock()
            if self.state not in ('queued', 'running'):
                return
            identity = store._import_get('/users/me', token)
            if str(identity.get('id')) != store.seller_id:
                raise UserError('此店铺的授权身份不匹配，请重新授权。')
            self._set({'state': 'running'})
            if not self.discovered:
                self._discover_page(token)
            for line in self.line_ids.filtered(lambda line: line.state == 'pending')[:5]:
                try:
                    with self.env.cr.savepoint():
                        data = store._import_get('/items/' + line.item_id, token, {'include_attributes': 'all'})
                        products, created = self._import_item(data, line.item_id, token)
                        line._set({'state': 'created' if created else 'updated',
                                   'product_ids': [(6, 0, products.ids)], 'message': '导入成功'})
                except (UserError, ValidationError) as error:
                    line._set({'state': 'failed', 'message': str(error)[:2000]})
                except Exception:
                    _logger.exception('Store import %s item %s failed', self.id, line.item_id)
                    line._set({'state': 'failed', 'message': '写入商品失败，请联系管理员查看日志后重试。'})
            pending = self.line_ids.filtered(lambda line: line.state == 'pending')
            values = {'message': '已发现 %s 个刊登，新增 %s，更新 %s，失败 %s。' % (
                self.total_count, self.created_count, self.updated_count, self.failed_count)}
            if self.discovered and not pending:
                values.update(state='partial' if self.failed_count else 'done', finished_at=fields.Datetime.now())
            self._set(values)
        except (UserError, AccessError, ValidationError) as error:
            self._set({'state': 'failed', 'message': str(error)[:2000], 'finished_at': fields.Datetime.now()})

    def _import_item(self, data, item_id, token):
        store = self.store_id
        if data.get('id') != item_id or str(data.get('seller_id')) != store.seller_id:
            raise UserError('该刊登不属于此店铺，未导入。')
        if not data.get('title') or data.get('price') is None or not data.get('currency_id'):
            raise UserError('商品缺少标题、价格或币种，未导入。')
        # Item identity is global; protect duplicate creation even across stores.
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtext('meli-store-import'), hashtext(%s))", [item_id])
        Product = self.env['product.product'].with_context(active_test=False)
        identities = Product.sudo().search([('meli_id', '=', item_id)])
        if any(p.company_id and p.company_id != store.company_id for p in identities):
            raise UserError('此刊登已存在于其他公司，请管理员核对，未创建重复商品。')
        products = Product.browse(identities.ids)
        products.check_access('write')
        if any(p.meli_source_store_id and p.meli_source_store_id != store for p in products):
            raise UserError('此刊登已经关联其他店铺，请核对账号。')
        if any(p.company_id and p.company_id != store.company_id for p in products):
            raise UserError('此商品属于其他公司，未修改。')
        if len(products.product_tmpl_id) > 1:
            raise UserError('此刊登在本地有重复商品，请先合并或核对。')
        created = not products
        description = store._import_get('/marketplace/items/' + item_id + '/description', token,
                                       {'site_id': store.site, 'logistic_type': store.logistic_type or 'fulfillment'}).get('plain_text')
        common = {'name': data['title'], 'meli_title': data['title'],
                  'meli_price': str(data['price']),
                  'meli_currency': data['currency_id']}
        if description is not None:
            common['meli_description'] = description
        for field, key in [('meli_condition', 'condition'), ('meli_listing_type', 'listing_type_id'),
                           ('meli_buying_mode', 'buying_mode'), ('meli_family_name', 'family_name')]:
            if data.get(key):
                common[field] = data[key]
        template = products.product_tmpl_id
        if not template:
            template = self.env['product.template'].create(dict(common, company_id=store.company_id.id,
                                                                type='consu', list_price=0))
        else:
            template.write(common)
        variations = data.get('variations') or []
        if any(not v.get('id') for v in variations) or len({str(v['id']) for v in variations}) != len(variations):
            raise UserError('商品变体编号无效。')
        if len(variations) > 1:
            products = self._variant_products(template, variations, products)
        else:
            products = products or template.product_variant_id
            if len(products) != 1:
                raise UserError('平台变体结构与本地不一致，请人工核对。')
            products.meli_id_variation = str(variations[0]['id']) if variations else False
        by_id = {str(v['id']): v for v in variations}
        for product in products:
            variant = by_id.get(product.meli_id_variation, {})
            sku = (attribute_value(variant.get('attributes'), 'SELLER_SKU') or variant.get('seller_custom_field')
                   or attribute_value(data.get('attributes'), 'SELLER_SKU') or data.get('seller_custom_field'))
            values = dict(common, meli_id=item_id, meli_source_store_id=store.id,
                          meli_ownership_checked_at=fields.Datetime.now(),
                          meli_price=str(variant.get('price', data['price'])),
                          meli_available_quantity=variant.get('available_quantity', data.get('available_quantity', 0)),
                          meli_import_status=data.get('status'), meli_import_data=data)
            if sku:
                values['default_code'] = sku
            product.write(values)
        pictures = data.get('pictures') or []
        if pictures:
            url = pictures[0].get('secure_url') or pictures[0].get('url')
            template.image_1920 = store._import_image(url)
        return products, created

    def _variant_products(self, template, variations, existing):
        ids = {str(v['id']) for v in variations}
        if existing and set(existing.mapped('meli_id_variation')) == ids:
            return existing
        if existing:
            if len(existing) != 1 or existing.meli_id_variation or template.attribute_line_ids:
                raise UserError('平台变体结构与本地不一致，请人工核对，未覆盖已有变体。')
            matches = [v for v in variations if existing.default_code and existing.default_code == (
                attribute_value(v.get('attributes'), 'SELLER_SKU') or v.get('seller_custom_field'))]
            if matches:
                variations = matches[:1] + [v for v in variations if v != matches[0]]
            elif (existing.default_code or
                  self.env['stock.move'].sudo().search_count([('product_id', '=', existing.id)], limit=1) or
                  self.env['sale.order.line'].sudo().search_count([('product_id', '=', existing.id)], limit=1)):
                raise UserError('已有商品有业务记录或 SKU，无法自动确定对应变体，请人工核对。')
        attribute = self.env.ref('meli_accounts.import_variation_attribute')
        values = self.env['product.attribute.value'].browse()
        value_map = {}
        for variant in variations:
            name = '%s / %s' % (template.id, variant['id'])
            labels = [str(a.get('value_name') or '') for a in variant.get('attribute_combinations', [])]
            if labels:
                name += ' · ' + ' / '.join(labels)
            value = self.env['product.attribute.value'].create({'attribute_id': attribute.id, 'name': name})
            values |= value
            value_map[value.id] = str(variant['id'])
        # One value first lets Odoo retain the existing product ID; only then
        # add other values so history/publication relations are not deleted.
        line = self.env['product.template.attribute.line'].create({'product_tmpl_id': template.id,
            'attribute_id': attribute.id, 'value_ids': [(6, 0, values[:1].ids)]})
        line.value_ids = values
        products = template.product_variant_ids
        for product in products:
            value = product.product_template_attribute_value_ids.product_attribute_value_id
            product.meli_id_variation = value_map[value.id]
        return products

    @api.model
    def _cron_import(self):
        job = self.search([('state', 'in', ['queued', 'running'])], order='write_date, id', limit=1)
        if job:
            user = job.requested_by
            if not user.active or not user.has_group(MANAGER) or job.company_id not in user.company_ids:
                job._set({'state': 'failed', 'message': '发起人的店铺权限已失效，请由有权限的用户重试。'})
            else:
                job.with_user(user).with_context(allowed_company_ids=[job.company_id.id])._run_batch()
        if self.search_count([('state', 'in', ['queued', 'running'])]):
            self.env.ref('meli_accounts.cron_store_import')._trigger()


class StoreImportLine(models.Model):
    _name = 'meli.store.import.line'
    _description = '商品导入结果'
    _order = 'id'

    import_id = fields.Many2one('meli.store.import', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='import_id.company_id', store=True)
    item_id = fields.Char(string='刊登 ID', required=True)
    state = fields.Selection([('pending', '待导入'), ('created', '已新增'),
                              ('updated', '已更新'), ('failed', '失败')], default='pending')
    product_ids = fields.Many2many('product.product', string='商品 / 变体')
    message = fields.Text(string='结果')
    _unique_item = models.Constraint('UNIQUE(import_id, item_id)', '导入任务中的刊登不能重复。')

    @api.model_create_multi
    def create(self, vals_list):
        raise UserError('导入明细由后台生成。')

    def write(self, vals):
        raise UserError('导入明细由后台维护。')

    def _set(self, vals):
        return super().write(vals)

    @api.model
    def _add(self, job, identifiers):
        return super().create([{'import_id': job.id, 'item_id': i} for i in identifiers])


class ImportedProduct(models.Model):
    _inherit = 'product.product'

    meli_import_status = fields.Char(string='导入时平台状态', readonly=True)
    meli_import_data = fields.Json(readonly=True, groups=MANAGER)

"""Authenticated, idempotent automatic screening. External reads precede DB locks."""
import re
from datetime import datetime, timezone
from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.addons.meli_bff.service import authorize
from odoo.addons.meli_bff.protocol import Operation
from odoo.addons.meli_platform_adapter import transport
from .optimization import PREFIX, ExactJson, bounded
from ..domain.screening import parse_criteria, screen_products, collect_shop, timestamp


class Screening(models.Model):
    _name = PREFIX + 'screening'
    _inherit = PREFIX + 'protected'
    _description = 'Immutable shop screening request and outcome'
    _internal_only = True
    shop_id = fields.Many2one('meli.independent.store', required=True, ondelete='restrict', index=True)
    request_key = fields.Char(required=True)
    seller_id = fields.Char(required=True)
    criteria = ExactJson(required=True)
    digest = fields.Char(required=True)
    batch_id = fields.Many2one(PREFIX + 'batch', ondelete='restrict')
    outcome = ExactJson()
    _identity = models.Constraint('UNIQUE(company_id, shop_id, request_key)', 'Screening request already exists')


class ScreeningWorkbench(models.Model):
    _inherit = PREFIX + 'case'
    shop_id = fields.Many2one('meli.independent.store', readonly=True, ondelete='restrict', index=True)

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            shop_id = values.get('shop_id', self.env.context.get('default_shop_id'))
            if shop_id:
                shop = self.env['meli.independent.store'].browse(shop_id).exists()
                if not shop or shop.company_id != self.env.company:
                    raise AccessError('商品的店铺归属必须与优化公司一致')
                shop.check_access('read')
        return super().create(vals_list)

    def _screen_shop(self, shop_id):
        self._authorize()
        shop = authorize(self.env, Operation('shop', 'GET', int(shop_id)))
        if not re.fullmatch(r'[1-9][0-9]{0,19}', shop.marketplace_seller_id or ''):
            raise ValidationError('店铺缺少已验证的站点 Seller ID')
        return shop

    @api.model
    def screening_preview(self, shop_id, request_key, criteria):
        shop = self._screen_shop(shop_id)
        try:
            criteria = parse_criteria(criteria)
            if criteria['site_id'] != shop.site or timestamp(criteria['end']) > datetime.now(timezone.utc):
                raise ValueError('Shop site mismatch or future sales window')
        except (ValueError, TypeError, KeyError) as exc:
            raise ValidationError(str(exc)) from exc
        if not isinstance(request_key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_key):
            raise ValidationError('稳定 request_key 必须为 1–128 位字母、数字或 _.:-')
        digest = bounded(dict(criteria=criteria, shop_id=shop.id, seller_id=shop.marketplace_seller_id, company_id=shop.company_id.id))
        # Serialize only the short local preview operation, never remote calls.
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', [f'screen:{shop.company_id.id}:{shop.id}:{request_key}'])
        model = self.env[PREFIX + 'screening'].with_company(shop.company_id)
        existing = model.search([('shop_id', '=', shop.id), ('request_key', '=', request_key)], limit=1)
        if existing and existing.digest != digest:
            raise ValidationError('同一请求身份不可改变筛选条件，请使用新的 request_key')
        row = existing or model._append(dict(company_id=shop.company_id.id, shop_id=shop.id, seller_id=shop.marketplace_seller_id, request_key=request_key, criteria=criteria, digest=digest))
        return dict(id=row.id, digest=row.digest, shop_id=shop.id, company_id=shop.company_id.id, seller_id=row.seller_id, criteria=row.criteria,
            semantics='sales_units <= sales_max AND sum(all SKU available_quantity) >= stock_min; paid orders created in [start,end); cancellations excluded, returns/refunds not deducted; unknown excluded',
            execute_tool='optimization_screen_execute', batch_id=row.batch_id.id or None)

    def _screen_collect(self, shop, criteria):
        token = shop._token()
        def get(path, query=()):
            response = transport.send('GET', path, token=token, query=query, timeout=30)
            if not 200 <= response.status < 300:
                status = response.status
                response.close()
                raise ValidationError(f'店铺完整数据不可用 (HTTP {status})；未创建优化批次，请核验账户接口权限')
            return response.json()
        return collect_shop(get, shop.marketplace_seller_id, criteria)

    @api.model
    def screening_execute(self, preview_id, digest):
        self._authorize()
        row = self.env[PREFIX + 'screening'].browse(int(preview_id)).exists()
        if not row:
            raise AccessError('筛选预览不存在或无权访问')
        row._authorize()
        shop = self._screen_shop(row.shop_id.id)
        if row.company_id != shop.company_id or row.seller_id != shop.marketplace_seller_id or row.criteria['site_id'] != shop.site:
            raise AccessError('店铺归属或授权已改变，请重新预览')
        if digest != row.digest:
            raise ValidationError('请先展示解析后的预览，并传入原始 digest')
        if row.batch_id:
            return row.outcome
        # No row/advisory lock or batch writes while waiting for Mercado Libre.
        try:
            products, orders, source = self._screen_collect(shop, row.criteria)
            result = screen_products(products, orders, row.criteria, row.seller_id, source.get('orders_complete'))
        except (ValueError, TypeError, KeyError) as exc:
            raise ValidationError('完整销售/库存证据不可验证：' + str(exc)) from exc
        observed_at = datetime.now(timezone.utc).isoformat()
        with self.env.cr.savepoint():
            row._lock()
            if row.batch_id:
                return row.outcome
            # Authorization is rechecked at the persistence boundary.
            self._screen_shop(shop.id)
            scoped = self.with_company(shop.company_id)
            batch = self.env[PREFIX + 'batch'].with_company(shop.company_id).create({'name': f'自动筛选 · {shop.name} · {row.request_key}'})
            cases = []
            for product in result['matches']:
                case = scoped.create({'name': product['id'] + ' · ' + str(product.get('title') or ''), 'batch_id': batch.id, 'shop_id': shop.id})
                payload = dict(product, item_id=product['id'], shop=shop.name, shop_id=shop.id, company_id=shop.company_id.id,
                    seller_id=row.seller_id, source=source, observed_at=observed_at, screening_criteria=row.criteria,
                    screening_id=row.id, screening_digest=row.digest, value_status='known')
                self.env[PREFIX + 'snapshot'].with_company(shop.company_id).create({'case_id': case.id, 'payload': payload})
                cases.append(case.id)
            outcome = dict(preview_id=row.id, batch_id=batch.id, case_ids=cases, matched=len(cases), unknown=result['unknown'], evaluated=result['evaluated'], source=source, observed_at=observed_at)
            bounded(outcome)
            row._set({'batch_id': batch.id, 'outcome': outcome})
            return outcome

    @api.model
    def screening_history(self):
        self._authorize('worker')
        return [{'id': r.id, 'shop_id': r.shop_id.id, 'criteria': r.criteria, 'digest': r.digest, 'batch_id': r.batch_id.id or None, 'outcome': r.outcome}
            for r in self.env[PREFIX + 'screening'].search([], order='id desc', limit=100)]

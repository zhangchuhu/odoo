"""Store membership and product-menu filtering; company access rules still apply."""
import re

from odoo import api, fields, models
from odoo.exceptions import UserError


class ProductProduct(models.Model):
    _inherit = 'product.product'

    meli_source_store_id = fields.Many2one(
        'meli.independent.store', string='原刊登店铺', readonly=True, copy=False, index=True)
    meli_ownership_checked_at = fields.Datetime(readonly=True, copy=False)
    meli_store_publication_ids = fields.One2many('meli.store.publication', 'product_id')
    meli_store_ids = fields.Many2many(
        'meli.independent.store', 'meli_product_store_rel', 'product_id', 'store_id',
        string='已关联店铺', compute='_compute_meli_store_ids', store=True, copy=False)

    @api.depends('meli_source_store_id', 'meli_store_publication_ids.state',
                 'meli_store_publication_ids.target_item', 'meli_store_publication_ids.store_id')
    def _compute_meli_store_ids(self):
        for product in self:
            published = product.meli_store_publication_ids.filtered(
                lambda job: job.state == 'done' and job.target_item)
            product.meli_store_ids = product.meli_source_store_id | published.store_id

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)
        if any(vals.get('meli_id') for vals in vals_list):
            products._trigger_meli_ownership_sync()
        return products

    def write(self, vals):
        if 'meli_id' in vals:
            changed = self.filtered(lambda product: product.meli_id != vals['meli_id'])
            if changed:
                super(ProductProduct, changed).write({
                    'meli_source_store_id': False, 'meli_ownership_checked_at': False})
        result = super().write(vals)
        if vals.get('meli_id'):
            self._trigger_meli_ownership_sync()
        return result

    def _trigger_meli_ownership_sync(self):
        cron = self.env.ref('meli_accounts.cron_store_ownership', raise_if_not_found=False)
        if cron:
            cron._trigger()

    def _apply_meli_identity(self, store, item):
        self.ensure_one()
        store.ensure_one()
        sellers = {sid for sid in (store.seller_id, store.marketplace_seller_id) if sid}
        if (not self.meli_id or item.get('id') != self.meli_id
                or str(item.get('seller_id') or '') not in sellers):
            return False
        self.meli_source_store_id = store
        return True

    def _sync_meli_store_ownership(self, stores=None):
        if stores is None:
            stores = self.env['meli.independent.store'].search([
                ('state', '=', 'connected'), ('seller_id', '!=', False)])
        responses = {}
        for product in self:
            if not product.meli_source_store_id and re.fullmatch(r'(CBT|MLM)\d+', product.meli_id or ''):
                for store in stores:
                    key = (store.id, product.meli_id)
                    if key not in responses:
                        try:
                            responses[key] = store._get('/items/' + product.meli_id)
                        except UserError:
                            responses[key] = {}
                    item = responses[key]
                    if isinstance(item, dict) and product._apply_meli_identity(store, item):
                        break
            product.meli_ownership_checked_at = fields.Datetime.now()

    @api.model
    def _cron_sync_meli_store_ownership(self):
        products = self.with_context(active_test=False).search([
            ('meli_id', '!=', False), ('meli_source_store_id', '=', False)],
            order='meli_ownership_checked_at ASC NULLS FIRST, id', limit=100)
        products._sync_meli_store_ownership()


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    meli_store_ids = fields.Many2many(
        'meli.independent.store', 'meli_template_store_rel', 'template_id', 'store_id',
        string='已关联店铺', compute='_compute_meli_store_ids', store=True, copy=False)

    @api.depends('product_variant_ids.meli_store_ids')
    def _compute_meli_store_ids(self):
        for template in self:
            template.meli_store_ids = template.with_context(active_test=False).product_variant_ids.meli_store_ids


class ProductWindowAction(models.Model):
    _inherit = 'ir.actions.act_window'

    def _get_action_dict(self):
        action = super()._get_action_dict()
        selection = self.env.context.get('meli_store_selection')
        if action.get('res_model') not in ('product.template', 'product.product') or not selection:
            return action
        # Materialize pending computed membership before M2M domain optimization.
        self.env[action['res_model']].flush_model(['meli_store_ids'])
        if selection == 'unassigned':
            extra = [('meli_store_ids', '=', False)]
            label = '待归属商品'
        else:
            # /web/action/load reads actions with sudo; restore user access for stores.
            stores = self.env['meli.independent.store'].sudo(False)
            store = stores.search([('id', '=', selection), ('state', '=', 'connected')], limit=1) if isinstance(selection, int) else stores.browse()
            extra = [('meli_store_ids', 'in', store.ids)]
            label = store.name if store else '请选择店铺'
        original = action.get('domain') or '[]'
        if not isinstance(original, str):
            original = repr(original)
        action['domain'] = '(%s) + %r' % (original, extra)
        action['name'] = '%s · %s' % (action['name'], label)
        action['display_name'] = action['name']
        return action

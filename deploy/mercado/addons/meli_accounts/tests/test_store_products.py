from unittest.mock import patch
from odoo.tests import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval
from odoo.addons.meli_accounts.publishing import Publication


@tagged('post_install', '-at_install')
class TestStoreProducts(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.store = cls.env['meli.independent.store'].create({
            'name': 'Empty test store', 'seller_id': '900000001',
            'state': 'connected', 'logistic_type': 'fulfillment',
        })
        cls.product = cls.env['product.product'].create({'name': 'Unassigned test product'})
        cls.action = cls.env['ir.actions.act_window'].create({
            'name': 'Test products', 'res_model': 'product.product',
            'view_mode': 'list,form',
            'domain': repr([('id', '=', cls.product.id)]),
        })

    def test_empty_store_does_not_show_unassigned_products(self):
        action = self.action.with_context(meli_store_selection=self.store.id)._get_action_dict()
        products = self.env['product.product'].search(safe_eval(action['domain']))
        self.assertFalse(products, 'An empty store must not display the shared product library')

    def _visible(self, selection, model='product.product', domain=None):
        action = self.action.copy({'res_model': model, 'domain': repr(domain or [])})
        data = action.with_context(meli_store_selection=selection)._get_action_dict()
        return self.env[model].search(safe_eval(data['domain']))

    def _own(self, product=None, store=None, item='CBT900000001'):
        product = product or self.product
        store = store or self.store
        product.write({'meli_id': item})
        return product._apply_meli_identity(store, {'id': item, 'seller_id': int(store.seller_id)})

    def test_switch_filters_templates_variants_and_keeps_existing_domain(self):
        other = self.store.copy({'name': 'Other store', 'seller_id': '900000002', 'state': 'connected'})
        other_product = self.env['product.product'].create({'name': 'Other product'})
        self._own()
        self._own(other_product, other, 'CBT900000002')
        ids = (self.product | other_product).ids
        self.assertEqual(self._visible(self.store.id, domain=[('id', 'in', ids)]), self.product)
        self.assertEqual(self._visible(other.id, domain=[('id', 'in', ids)]), other_product)
        self.assertFalse(self._visible(self.store.id, domain=[('id', '=', other_product.id)]))
        templates = (self.product | other_product).product_tmpl_id
        self.assertEqual(self._visible(other.id, 'product.template', [('id', 'in', templates.ids)]), other_product.product_tmpl_id)

    def test_unassigned_is_separate_and_unknown_store_is_empty(self):
        self.assertIn(self.product, self._visible('unassigned'))
        self._own()
        self.assertNotIn(self.product, self._visible('unassigned'))
        self.assertFalse(self._visible(999999999))

    def test_platform_identity_must_match_item_and_seller(self):
        self.product.write({'meli_id': 'CBT900000001'})
        self.assertFalse(self.product._apply_meli_identity(self.store, {'id': 'CBT900000001', 'seller_id': 999}))
        self.assertFalse(self.product._apply_meli_identity(self.store, {'id': 'CBT999', 'seller_id': 900000001}))
        self.assertFalse(self.product.meli_store_ids)
        self.assertTrue(self._own())
        self.product.write({'meli_id': 'CBT900000003'})
        self.assertFalse(self.product.meli_store_ids, 'Changing the source listing must clear stale ownership')

    def test_only_successful_publications_add_target_store(self):
        self._own()
        target = self.store.copy({'name': 'Target store', 'seller_id': '900000002', 'state': 'connected'})
        job = super(Publication, self.env['meli.store.publication']).create({
            'store_id': target.id, 'product_id': self.product.id,
            'sku': 'STORE-TEST', 'family_name': 'Test', 'price': 10,
        })
        job._set({'state': 'ready'})
        self.assertNotIn(self.product, self._visible(target.id))
        job._set({'state': 'failed'})
        self.assertNotIn(self.product, self._visible(target.id))
        job._set({'state': 'done', 'target_item': 'CBT900000004'})
        self.assertIn(self.product, self._visible(target.id))
        self.assertIn(self.product, self._visible(self.store.id))
        self.assertIn(self.product.product_tmpl_id, self._visible(target.id, 'product.template'))

    def test_sync_verifies_imported_item_using_platform_response(self):
        self.product.write({'meli_id': 'CBT900000001'})
        with patch.object(type(self.store), '_get', return_value={'id': 'CBT900000001', 'seller_id': 900000001}):
            self.product._sync_meli_store_ownership(stores=self.store)
        self.assertEqual(self.product.meli_source_store_id, self.store)

    def test_no_selection_does_not_change_backend_product_search(self):
        self.assertIn(self.product, self.env['product.product'].search([('id', '=', self.product.id)]))
        data = self.action._get_action_dict()
        self.assertEqual(self.env['product.product'].search(safe_eval(data['domain'])), self.product)

    def test_cron_associates_new_imports(self):
        self.product.write({'meli_id': 'CBT900000001'})
        with patch.object(type(self.store), '_get', return_value={'id': 'CBT900000001', 'seller_id': 900000001}):
            self.env['product.product']._cron_sync_meli_store_ownership()
        self.assertEqual(self.product.meli_source_store_id, self.store)

    def test_company_rules_still_limit_store_selection(self):
        self.env.ref('base.user_admin').group_ids |= self.env.ref('meli_oerp.group_mercadolibre_manager')
        company = self.env['res.company'].create({'name': 'Outside active company'})
        self.store.company_id = company
        self._own()
        action = self.action.with_user(self.env.ref('base.user_admin')).sudo().with_context(
            meli_store_selection=self.store.id,
            allowed_company_ids=[self.env.company.id],
        )._get_action_dict()
        self.assertFalse(self.env['product.product'].search(safe_eval(action['domain'])))

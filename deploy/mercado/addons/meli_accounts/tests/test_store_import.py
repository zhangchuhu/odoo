from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch
from psycopg2 import IntegrityError
import requests

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged, new_test_user


@tagged('post_install', '-at_install')
class TestStoreImport(TransactionCase):
    def setUp(self):
        super().setUp()
        # Import fixtures are intentionally uncommitted. Credential persistence
        # has separate real-cursor tests in meli_bff; keep this suite on imports.
        token = patch.object(type(self.env['meli.independent.store']), '_token', return_value='test-import-token')
        token.start()
        self.addCleanup(token.stop)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref('meli_oerp.group_mercadolibre_manager')
        cls.stores = cls.env['meli.independent.store'].create([
            {'name': name, 'seller_id': seller, 'state': 'connected',
             'access_token': 'test-' + seller, 'expires_at': fields.Datetime.now() + timedelta(days=1)}
            for name, seller in [('Import A', '900001'), ('Import B', '900002')]
        ])

    def item(self, item_id='CBT900001', seller='900001', **extra):
        return dict(id=item_id, seller_id=int(seller), title='Imported lamp',
                    price=12.50, currency_id='USD', status='active',
                    condition='new', attributes=[{'id': 'SELLER_SKU', 'value_name': 'SAME-SKU'}],
                    variations=[], pictures=[], available_quantity=7, **extra)

    def run_import(self, store, items):
        self.assertTrue(hasattr(store, 'action_import_products'), 'Independent store import entry is missing')
        action = store.action_import_products()
        job = store.env['meli.store.import'].browse(action['res_id'])
        def get(record, path, token, params=None):
            if path == '/users/me':
                return {'id': int(record.seller_id)}
            if path == '/marketplace/users/' + record.seller_id + '/items/search':
                return {'seller_id': record.seller_id, 'results': list(items), 'paging': {'total': len(items)}}
            if path.endswith('/description'):
                return {'plain_text': 'Lamp description'}
            key = path.removeprefix('/items/')
            if key in items:
                return deepcopy(items[key])
            raise AssertionError('Unexpected endpoint: ' + path)
        with patch.object(type(store), '_import_get', get):
            for _ in range(10):
                if job.state not in ('queued', 'running'):
                    break
                job._run_batch()
        return job

    def test_separate_sellers_same_sku_and_repeat_import(self):
        company_seller = self.env.company.mercadolibre_seller_id
        for store, item_id in zip(self.stores, ['CBT900001', 'CBT900002']):
            job = self.run_import(store, {item_id: self.item(item_id, store.seller_id)})
            self.assertEqual(job.state, 'done')
            self.assertEqual(job.created_count, 1)
        products = self.env['product.product'].search([('meli_id', 'in', ['CBT900001', 'CBT900002'])])
        self.assertEqual(len(products), 2)
        self.assertEqual(products.filtered(lambda p: p.meli_id == 'CBT900001').meli_source_store_id, self.stores[0])
        self.assertEqual(products.filtered(lambda p: p.meli_id == 'CBT900002').meli_source_store_id, self.stores[1])
        job = self.run_import(self.stores[0], {'CBT900001': self.item()})
        self.assertEqual(job.updated_count, 1)
        self.assertEqual(self.env['product.product'].search_count([('meli_id', '=', 'CBT900001')]), 1)
        self.assertEqual(self.env.company.mercadolibre_seller_id, company_seller)
        self.assertFalse(products.mapped('website_published') and any(products.mapped('website_published')))
        self.assertEqual(products.mapped('qty_available'), [0.0, 0.0])

    def test_wrong_seller_fails_without_placeholder_and_other_items_continue(self):
        job = self.run_import(self.stores[0], {'CBT900001': self.item(), 'CBT900002': self.item('CBT900002', '900002')})
        self.assertEqual(job.created_count, 1)
        self.assertEqual(job.failed_count, 1)
        self.assertEqual(job.state, 'partial')
        self.assertFalse(self.env['product.product'].search([('meli_id', '=', 'CBT900002')]))

    def test_existing_import_retains_product_identity(self):
        product = self.env['product.product'].create({'name': 'Old lamp', 'meli_id': 'CBT900001'})
        job = self.run_import(self.stores[0], {'CBT900001': self.item()})
        self.assertEqual(job.updated_count, 1)
        self.assertEqual(job.line_ids.product_ids, product)
        self.assertEqual(product.meli_source_store_id, self.stores[0])
        self.assertEqual(product.meli_price, '12.5')
        self.assertEqual(product.meli_currency, 'USD')
        self.assertEqual(product.meli_title, 'Imported lamp')

    def test_variations_keep_identifiers_on_repeat(self):
        data = self.item()
        data['variations'] = [
            {'id': 1001, 'price': 12.5, 'available_quantity': 3, 'seller_custom_field': 'RED',
             'attribute_combinations': [{'name': 'Color', 'value_name': 'Red'}]},
            {'id': 1002, 'price': 13, 'available_quantity': 4, 'seller_custom_field': 'BLUE',
             'attribute_combinations': [{'name': 'Color', 'value_name': 'Blue'}]},
        ]
        job = self.run_import(self.stores[0], {'CBT900001': data})
        self.assertEqual(job.state, 'done', (job.message, job.line_ids.mapped('message')))
        products = job.line_ids.product_ids
        self.assertEqual(len(products), 2)
        self.assertEqual(len(products.product_tmpl_id), 1)
        self.assertEqual(set(products.mapped('meli_id_variation')), {'1001', '1002'})
        self.assertEqual(products.filtered(lambda p: p.meli_id_variation == '1001').default_code, 'RED')
        self.assertEqual(products.filtered(lambda p: p.meli_id_variation == '1002').default_code, 'BLUE')
        again = self.run_import(self.stores[0], {'CBT900001': data})
        self.assertEqual(again.line_ids.product_ids, products)

    def test_empty_store_and_pending_store(self):
        job = self.run_import(self.stores[0], {})
        self.assertEqual(job.state, 'done')
        self.assertFalse(job.line_ids)
        self.stores[0].state = 'pending'
        with self.assertRaises(UserError):
            self.stores[0].action_import_products()

    def test_duplicate_click_returns_same_active_job(self):
        self.assertTrue(hasattr(self.stores, 'action_import_products'), 'Independent store import entry is missing')
        first = self.stores[0].action_import_products()
        second = self.stores[0].action_import_products()
        self.assertEqual(first['res_id'], second['res_id'])
        other = self.stores[1].action_import_products()
        self.assertNotEqual(first['res_id'], other['res_id'])

    def test_pagination_and_failed_page_cannot_look_complete(self):
        self.assertTrue(hasattr(self.stores, 'action_import_products'), 'Independent store import entry is missing')
        job = self.env['meli.store.import'].browse(self.stores[0].action_import_products()['res_id'])
        def get(record, path, token, params=None):
            if path == '/users/me':
                return {'id': 900001}
            if path.endswith('/items/search'):
                if params.get('scroll_id'):
                    raise UserError('Simulated page failure')
                return {'results': ['CBT900001'], 'scroll_id': 'next-page', 'paging': {'total': 2}}
            if path.endswith('/description'):
                return {'plain_text': ''}
            return self.item()
        with patch.object(type(self.stores), '_import_get', get):
            job._run_batch()
            job._run_batch()
        self.assertEqual(job.state, 'failed')
        self.assertIn('Simulated page failure', job.message)

        # Restarting the scan must walk past the already imported first page.
        def recovered(record, path, token, params=None):
            if path.endswith('/items/search') and params.get('scroll_id'):
                return {'results': ['CBT900003'], 'paging': {'total': 2}}
            if path == '/items/CBT900003':
                return self.item('CBT900003')
            return get(record, path, token, params)
        job.action_retry()
        with patch.object(type(self.stores), '_import_get', recovered):
            job._run_batch()
            job._run_batch()
        self.assertEqual(job.state, 'done', (job.message, job.line_ids.mapped('message')))
        self.assertEqual(job.created_count, 2)

    def test_non_system_manager_can_import_and_cannot_edit_results(self):
        user = new_test_user(self.env, login='store_import_manager',
            groups='base.group_user,product.group_product_manager,meli_oerp.group_mercadolibre_manager')
        self.assertFalse(user.has_group('base.group_system'))
        job = self.run_import(self.stores[0].with_user(user), {'CBT900001': self.item()})
        self.assertEqual(job.state, 'done', job.message)
        self.assertEqual(job.created_count, 1)
        with self.assertRaises(UserError):
            job.write({'store_id': self.stores[1].id})
        with self.assertRaises(UserError):
            job.line_ids.write({'state': 'updated'})

    def test_manager_cannot_import_other_company(self):
        user = new_test_user(self.env, login='store_import_limited',
            groups='base.group_user,meli_oerp.group_mercadolibre_manager')
        other = self.env['res.company'].create({'name': 'Other import company'})
        self.stores[0].company_id = other
        with self.assertRaises(AccessError):
            self.stores[0].with_user(user).action_import_products()

    def test_hidden_company_item_is_not_duplicated(self):
        user = new_test_user(self.env, login='store_import_collision',
            groups='base.group_user,product.group_product_manager,meli_oerp.group_mercadolibre_manager')
        other = self.env['res.company'].create({'name': 'Product owner company'})
        original = self.env['product.product'].create({'name': 'Other company lamp', 'company_id': other.id, 'meli_id': 'CBT900001'})
        job = self.run_import(self.stores[0].with_user(user), {'CBT900001': self.item()})
        self.assertEqual(job.state, 'partial', job.message)
        self.assertEqual(job.failed_count, 1)
        self.assertEqual(self.env['product.product'].search([('meli_id', '=', 'CBT900001')]), original)

    def test_database_rejects_two_active_jobs_for_one_store(self):
        from ..importing import StoreImport
        self.stores[0].action_import_products()
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            super(StoreImport, self.env['meli.store.import']).create({
                'store_id': self.stores[0].id, 'requested_by': self.env.uid})

    def test_absent_description_is_optional_but_auth_error_is_not(self):
        response = requests.Response()
        response.status_code = 404
        with patch('odoo.addons.meli_accounts.importing.requests.get', return_value=response):
            self.assertEqual(self.stores[0]._import_get('/marketplace/items/CBT900001/description', 'test'), {})
            with self.assertRaises(UserError):
                self.stores[0]._import_get('/items/CBT900001', 'test')
        response.status_code = 403
        with patch('odoo.addons.meli_accounts.importing.requests.get', return_value=response):
            with self.assertRaises(UserError):
                self.stores[0]._import_get('/marketplace/items/CBT900001/description', 'test')

    def test_placeholder_expands_to_variants_without_replacing_product(self):
        original = self.env['product.product'].create({'name': 'Imported placeholder', 'meli_id': 'CBT900001'})
        data = self.item()
        data['variations'] = [
            {'id': 1001, 'price': 12.5, 'available_quantity': 3, 'seller_custom_field': 'RED'},
            {'id': 1002, 'price': 13, 'available_quantity': 4, 'seller_custom_field': 'BLUE'},
        ]
        job = self.run_import(self.stores[0], {'CBT900001': data})
        self.assertEqual(job.state, 'done', job.line_ids.mapped('message'))
        self.assertEqual(len(job.line_ids.product_ids), 2)
        self.assertIn(original, job.line_ids.product_ids)
        self.assertEqual(original.meli_id_variation, '1001')

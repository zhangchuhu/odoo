import json
from copy import deepcopy
from contextlib import contextmanager
from unittest.mock import patch
import requests
from psycopg2 import IntegrityError

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged
from ..publishing import Publication


@tagged('post_install', '-at_install')
class TestPublishingSource(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source, cls.target = cls.env['meli.independent.store'].create([
            {'name': 'Source publishing store', 'seller_id': '800001', 'state': 'connected'},
            {'name': 'Target publishing store', 'seller_id': '800002', 'marketplace_seller_id': '800003',
             'state': 'connected', 'site': 'MLM', 'logistic_type': 'fulfillment'},
        ])
        cls.env.company.mercadolibre_seller_id = cls.target.seller_id
        cls.product = cls.env['product.product'].create({
            'name': 'Lamp', 'meli_id': 'CBT800001', 'meli_source_store_id': cls.source.id})
        cls.target_data = {'seller': '800002', 'site_seller': '800003', 'site': 'MLM',
                           'logistics': 'fulfillment', 'user_products': True}

    def item(self):
        return {'id': 'CBT800001', 'seller_id': 800001, 'currency_id': 'USD', 'price': 20,
                'title': 'Desk Lamp', 'category_id': 'CBT123',
                'pictures': [{'id': 'source-picture', 'secure_url': 'https://http2.mlstatic.com/lamp.jpg'}],
                'sale_terms': [{'id': 'WARRANTY_TYPE', 'value_id': '6150835', 'value_name': 'No warranty'}],
                'attributes': [{'id': 'SELLER_SKU', 'value_name': 'LAMP'},
                    {'id': 'GTIN', 'value_name': '1234567890128'},
                    *[{'id': code, 'value_name': value} for code, value in [
                        ('PACKAGE_HEIGHT', '10 cm'), ('PACKAGE_LENGTH', '20 cm'),
                        ('PACKAGE_WIDTH', '10 cm'), ('PACKAGE_WEIGHT', '500 g')]]]}

    @contextmanager
    def network(self, data=None):
        item = data if data is not None else self.item()
        def get(store, path, params=None):
            if path.startswith('/categories/'):
                return []
            if path == '/items/CBT800001':
                if store.id != self.source.id:
                    raise UserError('Wrong source authorization')
                return deepcopy(item)
            raise AssertionError('Unexpected path ' + path)
        with patch.object(type(self.source), '_get', get), patch.object(
                type(self.env['meli.util']), 'get_new_instance', side_effect=UserError('错误的公司来源授权')):
            yield

    def prepare(self, product=None):
        wizard = self.env['meli.store.prepare'].create({
            'store_id': self.target.id, 'product_ids': [(6, 0, (product or self.product).ids)]})
        action = wizard.action_prepare()
        return self.env['meli.store.publication'].search(action['domain'])

    def old_job(self):
        item = self.item()
        job = super(Publication, self.env['meli.store.publication']).create({
            'store_id': self.target.id, 'product_id': self.product.id, 'source_item': 'CBT800001',
            'source_variation': '0', 'sku': 'LAMP', 'family_name': 'Approved English Lamp',
            'price': 20, 'gtin': '1234567890128', 'english_confirmed': True,
            'source_data': {'category_id': 'CBT123', 'pictures': item['pictures']}})
        for a in item['attributes']:
            if a['id'].startswith('PACKAGE_'):
                self.env['meli.store.publication.attribute']._add(job, a['id'], a['id'], a['value_name'])
        return job

    def test_prepare_uses_actual_source_and_preserves_sale_terms(self):
        with self.network(), patch.object(type(self.target), '_publishing_target', return_value=self.target_data):
            jobs = self.prepare()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs.source_data['source_store_id'], self.source.id)
        self.assertEqual(jobs._build(self.target_data)['sale_terms'], self.item()['sale_terms'])
        self.assertEqual(self.env.company.mercadolibre_seller_id, '800002')

    def test_only_selected_variation_is_prepared(self):
        data = self.item()
        data['variations'] = [
            {'id': 11, 'price': 20, 'attributes': [{'id': 'SELLER_SKU', 'value_name': 'RED'}]},
            {'id': 12, 'price': 21, 'attributes': [{'id': 'SELLER_SKU', 'value_name': 'BLUE'}]},
        ]
        self.product.meli_id_variation = '12'
        with self.network(data), patch.object(type(self.target), '_publishing_target', return_value=self.target_data):
            jobs = self.prepare()
        self.assertEqual(jobs.mapped('source_variation'), ['12'])
        self.assertEqual(jobs.sku, 'BLUE')

    def test_recheck_repairs_old_snapshot_without_overwriting_approved_name(self):
        job = self.old_job()
        with self.network(), patch.object(type(self.target), '_publishing_target', return_value=self.target_data):
            job.action_check()
        self.assertEqual(job.state, 'ready', job.check_result)
        self.assertEqual(job.payload['sale_terms'], self.item()['sale_terms'])
        self.assertEqual(job.family_name, 'Approved English Lamp')

    def test_missing_source_membership_is_blocked(self):
        self.product.meli_source_store_id = False
        with self.network(), patch.object(type(self.target), '_publishing_target', return_value=self.target_data):
            with self.assertRaisesRegex(UserError, '归属|来源'):
                self.prepare()

    def test_wrong_remote_seller_is_blocked(self):
        data = self.item()
        data['seller_id'] = 800002
        with self.network(data), patch.object(type(self.target), '_publishing_target', return_value=self.target_data):
            with self.assertRaisesRegex(UserError, '归属|来源'):
                self.prepare()

    def test_missing_warranty_is_not_silently_invented(self):
        data = self.item()
        data['sale_terms'] = []
        job = self.old_job()
        with self.network(data), patch.object(type(self.target), '_publishing_target', return_value=self.target_data):
            job.action_check()
        self.assertEqual(job.state, 'blocked')
        self.assertIn('销售条款', job.check_result)

    def test_actual_post_body_contains_sale_terms(self):
        job = self.old_job()
        response = requests.Response()
        response.status_code = 201
        response._content = json.dumps({'id': 'CBT899999'}).encode()
        submitted = []
        def post(url, **kwargs):
            self.assertEqual(url, 'https://api.mercadolibre.com/global/items')
            submitted.append(kwargs['json'])
            return response
        with self.network(), patch.object(type(self.target), '_publishing_target', return_value=self.target_data), \
             patch.object(type(self.target), '_token', return_value='test-only-token'), \
             patch.object(type(job), '_upload_images', return_value=['uploaded-picture']), \
             patch.object(self.env.cr, 'commit', side_effect=self.env.flush_all), \
             patch('odoo.addons.meli_accounts.publishing.requests.post', side_effect=post):
            job.action_check()
            job.action_publish()
            job._publish_one()
        self.assertEqual(job.state, 'done', job.result)
        self.assertEqual(submitted[0]['sale_terms'], self.item()['sale_terms'])
        self.assertEqual(submitted[0]['pictures'], [{'id': 'uploaded-picture'}])

    def test_old_draft_relinks_to_selected_variant(self):
        data = self.item()
        data['variations'] = [{'id': 12, 'price': 20, 'attributes': [{'id': 'SELLER_SKU', 'value_name': 'LAMP'}]}]
        old = self.old_job()
        old._set({'source_variation': '12'})
        selected = self.env['product.product'].create({'name': 'Blue Lamp', 'meli_id': 'CBT800001',
            'meli_id_variation': '12', 'meli_source_store_id': self.source.id})
        with self.network(data), patch.object(type(self.target), '_publishing_target', return_value=self.target_data):
            jobs = self.prepare(selected)
        self.assertEqual(jobs, old)
        self.assertEqual(jobs.product_id, selected)
        self.assertEqual(jobs.state, 'draft')

    def test_locked_old_draft_is_not_relinked_or_duplicated(self):
        data = self.item()
        data['variations'] = [{'id': 12, 'price': 20}]
        old = self.old_job()
        old._set({'source_variation': '12', 'state': 'queued'})
        selected = self.env['product.product'].create({'name': 'Blue Lamp', 'meli_id': 'CBT800001',
            'meli_id_variation': '12', 'meli_source_store_id': self.source.id})
        with self.network(data), patch.object(type(self.target), '_publishing_target', return_value=self.target_data):
            with self.assertRaises(UserError):
                self.prepare(selected)
        self.assertEqual(old.product_id, self.product)

    def test_duplicate_remote_variant_rejected_even_for_different_local_products(self):
        old = self.old_job()
        selected = self.env['product.product'].create({'name': 'Duplicate local record', 'meli_id': 'CBT800001'})
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            super(Publication, self.env['meli.store.publication']).create({
                'store_id': self.target.id, 'product_id': selected.id, 'source_item': old.source_item,
                'source_variation': '0', 'sku': 'LAMP', 'family_name': 'Lamp', 'price': 20})

    def test_sale_terms_values_are_normalized(self):
        data = self.item()
        data['sale_terms'] = [{'id': 'WARRANTY_TYPE', 'values': [{'id': '6150835', 'name': 'No warranty'}]}]
        with self.network(data), patch.object(type(self.target), '_publishing_target', return_value=self.target_data):
            job = self.prepare()
        self.assertEqual(job._build(self.target_data)['sale_terms'], self.item()['sale_terms'])

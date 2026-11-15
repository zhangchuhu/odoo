from unittest.mock import patch
from datetime import datetime, timezone
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import AccessError, ValidationError


@tagged('post_install', '-at_install')
class ScreeningCase(TransactionCase):
    def setUp(self):
        super().setUp()
        operator = self.env['res.users'].create({'name': 'Screen operator', 'login': 'screen-operator', 'group_ids': [(6, 0, [self.env.ref(g).id for g in ('meli_bff.group_reader', 'meli_optimization.group_editor', 'meli_oerp.group_mercadolibre_manager')])]})
        self.ui = self.env['meli.optimization.case'].with_user(operator)
        self.shop = self.env['meli.independent.store'].create({'name': 'Screen shop', 'seller_id': '8', 'marketplace_seller_id': '9', 'site': 'MLM'})
        self.criteria = dict(site_id='MLM', timezone='America/Mexico_City', start='2026-11-01T00:00:00-06:00', end='2026-11-08T00:00:00-06:00', sales_max=2, stock_min=10, sales_policy='paid_gross_units')

    def preview(self):
        self.assertTrue(hasattr(self.ui, 'screening_preview'), 'preview operation missing')
        return self.ui.screening_preview(self.shop.id, 'stable-request', self.criteria)

    def collected(self, *args):
        return ([dict(id='MLM1', seller_id='9', site_id='MLM', title='Dress', available_quantity=20, variations=[])], [], dict(orders_complete=True, provider='isolated-test'))

    def test_preview_execution_idempotency_and_visibility(self):
        preview = self.preview()
        self.assertEqual(preview['criteria'], self.criteria)
        with patch.object(type(self.ui), '_screen_collect', self.collected):
            result = self.ui.screening_execute(preview['id'], preview['digest'])
            again = self.ui.screening_execute(preview['id'], preview['digest'])
        self.assertEqual(result, again)
        case = self.ui.browse(result['case_ids'])
        self.assertEqual(case.shop_id, self.shop)
        self.assertEqual(self.ui.workbench_overview(batch_id=result['batch_id'])['total'], 1)
        with self.assertRaises(ValidationError):
            self.ui.screening_preview(self.shop.id, 'stable-request', dict(self.criteria, sales_max=3))

    def test_all_sizes_criteria_and_evidence_persisted(self):
        self.criteria.update(require_all_skus_in_stock=True, sales_max=1, stock_min=0)
        preview = self.preview()
        self.assertIn('every listed SKU', preview['semantics'])
        def collect(*args):
            return ([dict(id='MLM1', seller_id='9', site_id='MLM', title='Dress', variations=[dict(id='small', available_quantity=2, attribute_combinations=[dict(id='SIZE', value_name='S')]), dict(id='medium', available_quantity=1, attribute_combinations=[dict(id='SIZE', value_name='M')])])], [], dict(orders_complete=True, provider='isolated-test'))
        with patch.object(type(self.ui), '_screen_collect', collect):
            outcome = self.ui.screening_execute(preview['id'], preview['digest'])
        payload = self.ui.workbench_detail(outcome['case_ids'][0])['snapshot']['payload']
        self.assertTrue(payload['screening_criteria']['require_all_skus_in_stock'])
        self.assertEqual([row['size']['value_name'] for row in payload['sku_size_evidence']], ['S', 'M'])
        self.assertEqual(payload['sales_units'], 0)

    def test_execute_checkpoints_progress_without_exposing_private_cache(self):
        from ..domain.screening_resume import CollectionPending
        preview = self.preview()
        def collect(model, shop, criteria, checkpoint=None):
            if not checkpoint.get('responses'):
                checkpoint['responses'] = [{'path': '/users/me', 'query': [], 'data': {'id': 8}, 'read_started_at': datetime.now(timezone.utc).isoformat(), 'observed_at': datetime.now(timezone.utc).isoformat()}]
                raise CollectionPending()
            return self.collected()
        with patch.object(type(self.ui), '_screen_collect', collect):
            running = self.ui.screening_execute(preview['id'], preview['digest'])
            self.assertEqual(running['job_state'], 'running')
            self.assertNotIn('job_checkpoint', running)
            self.assertNotIn('job_checkpoint', self.ui.screening_history()[0]['outcome'])
            result = self.ui.screening_execute(preview['id'], preview['digest'])
            self.assertEqual(result['matched'], 1)
            self.assertEqual(self.ui.screening_execute(preview['id'], preview['digest']), result)

    def test_concurrent_step_returns_progress_without_remote_call(self):
        from odoo import sql_db
        preview = self.preview()
        with sql_db.db_connect(self.env.cr.dbname).cursor() as other:
            other.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', [f"screen-step:{preview['id']}"])
            with patch.object(type(self.ui), '_screen_collect', side_effect=AssertionError('duplicate remote fetch')):
                state = self.ui.screening_execute(preview['id'], preview['digest'])
            self.assertEqual(state['code'], 'step_in_progress')
        with patch.object(type(self.ui), '_screen_collect', self.collected):
            self.assertEqual(self.ui.screening_execute(preview['id'], preview['digest'])['matched'], 1)

    def test_failed_collection_retries_fresh_without_duplicate_batch(self):
        preview = self.preview()
        checkpoints = []
        def collect(model, shop, criteria, checkpoint):
            checkpoints.append(dict(checkpoint))
            if len(checkpoints) == 1:
                checkpoint['responses'] = [{'unverified': True}]
                raise ValueError('source changed')
            return self.collected()
        with patch.object(type(self.ui), '_screen_collect', collect):
            failed = self.ui.screening_execute(preview['id'], preview['digest'])
            self.assertEqual(failed['job_state'], 'failed')
            result = self.ui.screening_execute(preview['id'], preview['digest'])
        self.assertEqual(checkpoints[1], {})
        self.assertEqual(result['matched'], 1)

    def test_expired_checkpoint_fails_without_recollection_or_batch(self):
        from datetime import datetime, timedelta, timezone
        preview = self.preview()
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        row = self.env['meli.optimization.screening'].browse(preview['id'])
        row._set({'outcome': {'job_state': 'running', 'job_checkpoint': {'responses': [{'path': '/users/me', 'query': [], 'data': {'id': 8}, 'read_started_at': old, 'observed_at': old}]}}})
        with patch.object(type(self.ui), '_screen_collect', side_effect=AssertionError('expired checkpoint must not fetch')):
            failed = self.ui.screening_execute(preview['id'], preview['digest'])
        self.assertEqual(failed['code'], 'checkpoint_expired')
        self.assertFalse(row.batch_id)
        with patch.object(type(self.ui), '_screen_collect', self.collected):
            self.assertEqual(self.ui.screening_execute(preview['id'], preview['digest'])['matched'], 1)

    def test_final_restart_uses_last_actual_read_time(self):
        from datetime import datetime, timedelta, timezone
        preview = self.preview()
        observed = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
        row = self.env['meli.optimization.screening'].browse(preview['id'])
        row._set({'outcome': {'job_state': 'running', 'job_checkpoint': {'responses': [{'path': '/users/me', 'query': [], 'data': {'id': 8}, 'read_started_at': observed, 'observed_at': observed}]}}})
        with patch.object(type(self.ui), '_screen_collect', self.collected):
            result = self.ui.screening_execute(preview['id'], preview['digest'])
        self.assertEqual(result['observed_at'], observed)
        self.assertGreater(result['committed_at'], observed)

    def test_atomic_batch_rollback(self):
        preview = self.preview()
        count = self.env['meli.optimization.batch'].search_count([])
        with patch.object(type(self.ui), '_screen_collect', self.collected), patch.object(type(self.env['meli.optimization.snapshot']), 'create', side_effect=ValidationError('forced snapshot failure')):
            with self.assertRaises(ValidationError): self.ui.screening_execute(preview['id'], preview['digest'])
        self.assertEqual(self.env['meli.optimization.batch'].search_count([]), count)
        self.assertFalse(self.env['meli.optimization.screening'].browse(preview['id']).batch_id)

    def test_digest_and_company_boundary(self):
        preview = self.preview()
        with self.assertRaises(ValidationError): self.ui.screening_execute(preview['id'], 'wrong')
        company = self.env['res.company'].create({'name': 'Other screen company'})
        worker = self.env['res.users'].create({'name': 'Worker', 'login': 'screen-worker', 'company_id': company.id, 'company_ids': [(6, 0, company.ids)], 'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_editor').id, self.env.ref('meli_bff.group_reader').id])]})
        with self.assertRaises(AccessError): self.ui.with_user(worker).with_context(allowed_company_ids=company.ids).screening_execute(preview['id'], preview['digest'])

    def test_live_provider_rejects_wrong_parent_token_before_search(self):
        from types import SimpleNamespace
        from ..models import screening
        with patch.object(type(self.shop), '_token', return_value='isolated-token'), patch.object(screening.transport, 'send', return_value=SimpleNamespace(status=200, json=lambda: {'id': 'wrong'})) as send:
            with self.assertRaises(ValidationError):
                self.ui._screen_collect(self.shop, self.criteria)
            self.assertEqual(send.call_args.args[1], '/users/me')

    def test_case_shop_link_cannot_cross_company(self):
        company = self.env['res.company'].create({'name': 'Foreign screening shop'})
        shop = self.env['meli.independent.store'].create({'name': 'Foreign', 'company_id': company.id})
        batch = self.env['meli.optimization.batch'].create({'name': 'Current company'})
        with self.assertRaises(AccessError):
            self.ui.create({'name': 'Forged ownership', 'batch_id': batch.id, 'shop_id': shop.id})


from datetime import timedelta
from odoo import fields
from odoo.tests import HttpCase


@tagged('post_install', '-at_install', 'optimization_http')
class ScreeningHTTPCase(HttpCase):
    def test_preview_execute_history_authenticated_transport(self):
        user = self.env['res.users'].create({'name': 'Screen HTTP', 'login': 'screen-http', 'group_ids': [(6, 0, [self.env.ref(g).id for g in ('meli_bff.group_reader', 'meli_optimization.group_editor', 'meli_oerp.group_mercadolibre_manager')])]})
        key = self.env['res.users.apikeys'].with_user(user).sudo()._generate('rpc', 'test only', fields.Datetime.now() + timedelta(hours=1))
        headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
        shop = self.env['meli.independent.store'].create({'name': 'Screen HTTP shop', 'seller_id': '8', 'marketplace_seller_id': '9', 'site': 'MLM'})
        criteria = dict(site_id='MLM', timezone='America/Mexico_City', start='2026-11-01T00:00:00-06:00', end='2026-11-08T00:00:00-06:00', sales_max=2, stock_min=10, sales_policy='paid_gross_units')
        preview = self.url_open('/api/meli/optimization/screening/preview', json=dict(shop_id=shop.id, request_key='http-stable', criteria=criteria), headers=headers)
        self.assertEqual(preview.status_code, 200, preview.text)
        row = preview.json()['data']
        path = f"/api/meli/optimization/screening/{row['id']}/execute"
        wrong = self.url_open(path, json={'digest': 'changed'}, headers=headers)
        self.assertEqual(wrong.status_code, 409, wrong.text)
        def collect(*args):
            from ..domain.screening_resume import CollectionPending
            if not args[-1].get('responses'):
                args[-1]['responses'] = [{'path': '/users/me', 'query': [], 'data': {'id': 8}, 'read_started_at': datetime.now(timezone.utc).isoformat(), 'observed_at': datetime.now(timezone.utc).isoformat()}]
                raise CollectionPending()
            return ([dict(id='MLM1', seller_id='9', site_id='MLM', title='Dress', available_quantity=20, variations=[])], [], dict(orders_complete=True, provider='isolated-test'))
        with patch.object(type(self.env['meli.optimization.case']), '_screen_collect', collect):
            pending = self.url_open(path, json={'digest': row['digest']}, headers=headers)
            self.assertEqual(pending.json()['data']['job_state'], 'running')
            self.assertNotIn('job_checkpoint', pending.text)
            executed = self.url_open(path, json={'digest': row['digest']}, headers=headers)
        self.assertEqual(executed.status_code, 200, executed.text)
        self.assertEqual(executed.json()['data']['matched'], 1)
        history = self.url_open('/api/meli/optimization/screening/history', data='{}', headers=headers)
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()['data'][0]['batch_id'], executed.json()['data']['batch_id'])

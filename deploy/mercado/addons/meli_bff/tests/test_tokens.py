from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading
import uuid
from unittest.mock import patch

import requests
from odoo import api, fields, sql_db, SUPERUSER_ID
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo.orm.registry import Registry, DummyRLock


@tagged('post_install', '-at_install')
class TestTokens(TransactionCase):
    def setUp(self):
        super().setUp()
        self.db = sql_db.db_connect(self.env.cr.dbname)
        with self.db.cursor() as cr:
            cr.execute("DELETE FROM ir_config_parameter WHERE key='meli_bff.test_uncommitted'")
            env = api.Environment(cr, SUPERUSER_ID, {})
            user = env['res.users'].with_context(no_reset_password=True).create({
                'name': 'BFF token test', 'login': 'bff-token-' + uuid.uuid4().hex,
                'group_ids': [(6, 0, [env.ref('meli_oerp.group_mercadolibre_manager').id])],
            })
            self.token_uid = user.id
            store = env['meli.independent.store'].create({
                'name': 'BFF credential test', 'seller_id': '987650001', 'state': 'connected',
                'access_token': 'old-access', 'refresh_token': 'old-refresh',
                'expires_at': fields.Datetime.now() - timedelta(seconds=5),
            })
            self.store_id = store.id
            cr.commit()

    def tearDown(self):
        with self.db.cursor() as cr:
            api.Environment(cr, SUPERUSER_ID, {})['meli.independent.store'].browse(self.store_id).unlink()
            api.Environment(cr, SUPERUSER_ID, {})['res.users'].browse(self.token_uid).unlink()
            cr.commit()
        super().tearDown()

    def response(self, **overrides):
        import json
        r = requests.Response()
        r.status_code = 200
        r._content = json.dumps(dict(dict(access_token='rotated-access', refresh_token='rotated-refresh',
                                     expires_in=3600, user_id=987650001), **overrides)).encode()
        return r

    def get_token(self):
        with self.db.cursor() as cr:
            return api.Environment(cr, self.token_uid, {})['meli.independent.store'].browse(self.store_id)._token()

    def test_refresh_survives_caller_rollback_without_committing_other_changes(self):
        with patch('requests.sessions.Session.request', return_value=self.response()):
            with self.db.cursor() as caller:
                env = api.Environment(caller, self.token_uid, {})
                # An unrelated SQL change must not be committed by refreshing credentials.
                caller.execute("INSERT INTO ir_config_parameter(key,value) VALUES ('meli_bff.test_uncommitted','x')")
                token = env['meli.independent.store'].browse(self.store_id)._token()
                self.assertEqual(token, 'rotated-access')
                caller.rollback()
        with self.db.cursor() as cr:
            cr.execute('SELECT access_token,refresh_token FROM meli_independent_store WHERE id=%s', [self.store_id])
            self.assertEqual(cr.fetchone(), ('rotated-access', 'rotated-refresh'))
            cr.execute("SELECT count(*) FROM ir_config_parameter WHERE key='meli_bff.test_uncommitted'")
            self.assertEqual(cr.fetchone()[0], 0, 'Token refresh committed unrelated business data')
            cr.execute("DELETE FROM ir_config_parameter WHERE key='meli_bff.test_uncommitted'")
            cr.commit()

    def test_concurrent_refresh_rotates_once(self):
        barrier = threading.Barrier(2)
        count = []
        def exchange(*args, **kwargs):
            count.append(1)
            return self.response()
        def worker():
            barrier.wait(timeout=10)
            return self.get_token()
        # Server initialization holds Registry._lock on the runner thread.
        # Registry is fully loaded here; allow workers to enter their own real cursors.
        with patch.object(Registry, '_lock', DummyRLock()), patch('requests.sessions.Session.request', side_effect=exchange), ThreadPoolExecutor(2) as pool:
            jobs = [pool.submit(worker) for _ in range(2)]
            self.assertEqual([job.result(timeout=20) for job in jobs], ['rotated-access'] * 2)
        self.assertEqual(len(count), 1)

    def test_invalid_refresh_preserves_previous_credentials(self):
        invalid = requests.Response()
        invalid.status_code = 400
        invalid._content = b'{"error":"invalid_grant"}'
        for result in [invalid, requests.Timeout('secret'), self.response_bad_expiry()]:
            with self.subTest(result=type(result).__name__):
                kwargs = {'side_effect': result} if isinstance(result, Exception) else {'return_value': result}
                with patch('requests.sessions.Session.request', **kwargs), self.assertRaises(UserError):
                    self.get_token()
        with self.db.cursor() as cr:
            cr.execute('SELECT access_token,refresh_token FROM meli_independent_store WHERE id=%s', [self.store_id])
            self.assertEqual(cr.fetchone(), ('old-access', 'old-refresh'))

    def response_bad_expiry(self):
        r = self.response()
        r._content = b'{"access_token":"bad","refresh_token":"bad","user_id":987650001,"expires_in":-1}'
        return r

    def test_incomplete_wrong_seller_and_non_json_preserve_credentials(self):
        malformed = requests.Response()
        malformed.status_code = 200
        malformed._content = b'not json'
        for result in [malformed, self.response(user_id=123), self.response(refresh_token=''),
                       self.response(expires_in=True), self.response(expires_in='3600')]:
            with self.subTest(body=result.content), patch('requests.sessions.Session.request', return_value=result):
                with self.assertRaises(UserError):
                    self.get_token()
        with self.db.cursor() as cr:
            cr.execute('SELECT access_token,refresh_token FROM meli_independent_store WHERE id=%s', [self.store_id])
            self.assertEqual(cr.fetchone(), ('old-access', 'old-refresh'))

    def test_caller_holding_store_lock_gets_bounded_error(self):
        with self.db.cursor() as cr:
            cr.execute('SELECT id FROM meli_independent_store WHERE id=%s FOR UPDATE', [self.store_id])
            with patch('requests.sessions.Session.request', side_effect=AssertionError('must not refresh')):
                with self.assertRaises(UserError):
                    api.Environment(cr, self.token_uid, {})['meli.independent.store'].browse(self.store_id)._token()

    def test_inactive_user_and_missing_refresh_do_not_call_platform(self):
        with self.db.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            env['meli.independent.store'].browse(self.store_id).write({'refresh_token': False})
            cr.commit()
        with patch('requests.sessions.Session.request', side_effect=AssertionError('must not refresh')):
            with self.assertRaises(UserError):
                self.get_token()

    def test_internal_superuser_can_refresh_for_scheduled_jobs(self):
        with patch('requests.sessions.Session.request', return_value=self.response()):
            with self.db.cursor() as cr:
                token = api.Environment(cr, SUPERUSER_ID, {})['meli.independent.store'].browse(self.store_id)._token()
                self.assertEqual(token, 'rotated-access')
        with self.db.cursor() as cr:
            api.Environment(cr, SUPERUSER_ID, {})['res.users'].browse(self.token_uid).write({'active': False})
            cr.commit()
        with patch('requests.sessions.Session.request', side_effect=AssertionError('must not refresh')):
            with self.assertRaises(UserError):
                self.get_token()

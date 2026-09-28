import io
import http.client
from contextlib import closing
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import HttpCase, tagged
from .. import client


@tagged('post_install', '-at_install')
class TestHttp(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.store = cls.env['meli.independent.store'].create({
            'name': 'HTTP shop', 'seller_id': '900001', 'marketplace_seller_id': '900002',
            'state': 'connected', 'logistic_type': 'fulfillment',
        })
        cls.other_company = cls.env['res.company'].create({'name': 'Other BFF company'})
        cls.other = cls.env['meli.independent.store'].create({
            'name': 'Other shop', 'company_id': cls.other_company.id,
        })
        cls.writer = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'BFF writer', 'login': 'bff-http-writer',
            'group_ids': [(6, 0, [cls.env.ref('meli_oerp.group_mercadolibre_manager').id])],
        })
        reader_group = cls.env.ref('meli_bff.group_reader', raise_if_not_found=False) or cls.env.ref('base.group_user')
        cls.reader = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'BFF reader', 'login': 'bff-http-reader',
            'group_ids': [(6, 0, [reader_group.id])],
        })
        cls.writer_key = cls.env['res.users.apikeys'].with_user(cls.writer).sudo()._generate(None, 'test', fields.Datetime.now() + timedelta(days=1))
        cls.reader_key = cls.env['res.users.apikeys'].with_user(cls.reader).sudo()._generate(None, 'test', fields.Datetime.now() + timedelta(days=1))

    def call(self, suffix, method='GET', body=None, key=None, content_type='application/json'):
        headers = {'Content-Type': content_type}
        if key is not False:
            headers['Authorization'] = 'Bearer ' + (key or self.writer_key)
        return self.url_open('/api/meli' + suffix, data=body, headers=headers, method=method)

    def test_authentication_and_status(self):
        for key in [False, 'invalid']:
            response = self.call('/shops', key=key)
            self.assertEqual(response.status_code, 401)
            self.assertFalse(response.json()['success'])
        response = self.call('/shops', key=self.reader_key)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('access_token', response.text)
        self.assertNotIn('refresh_token', response.text)
        self.assertIn(self.store.id, [s['id'] for s in response.json()['shops']])
        self.assertNotIn(self.other.id, [s['id'] for s in response.json()['shops']])

    def test_permissions_and_path_identity_before_upstream(self):
        with patch.object(client, 'send', side_effect=AssertionError('unauthorized upstream call')):
            self.assertEqual(self.call('/shops/%s/items' % self.store.id, 'POST', b'{}', self.reader_key).status_code, 403)
            self.assertEqual(self.call('/shops/%s' % self.other.id).status_code, 403)
            self.assertEqual(self.call('/shops/%s/items?shopId=%s' % (self.store.id, self.other.id)).status_code, 403)
            self.assertEqual(self.call('/unimplemented').status_code, 404)

    def fake_send(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        data = {'seller_id': self.seller, 'id': 'CBT123'}
        if path == '/users/me':
            data = {'id': '900001', 'tags': self.tags}
        if path == '/pictures/items/upload':
            return client.PlatformResponse(201, [('Content-Type', 'application/json')], io.BytesIO(b'{"id":"pic"}'))
        return client.PlatformResponse(200, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(data).encode()))

    def test_multipart_and_description_keep_bound_shop(self):
        self.calls, self.seller, self.tags = [], 900001, []
        body = b'--BFF\r\nContent-Disposition: form-data; name="file"; filename="a.jpg"\r\nContent-Type: image/jpeg\r\n\r\n\xff\x00\xfe\r\n--BFF--\r\n'
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=self.fake_send):
            response = self.call('/shops/%s/pictures' % self.store.id, 'POST', body, content_type='multipart/form-data; boundary=BFF')
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(self.calls[-1][2]['body'], body)
            self.assertEqual(self.calls[-1][2]['content_type'], 'multipart/form-data; boundary=BFF')
            response = self.call('/shops/%s/items/CBT123/description' % self.store.id, 'PUT', b'{"plain_text":"Lamp"}')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.calls[-1][1], '/global/items/CBT123')
            self.assertEqual(json.loads(self.calls[-1][2]['body']), {'site_id': 'MLM', 'logistic_type': 'fulfillment', 'description': {'plain_text': 'Lamp'}})

    def test_foreign_seller_and_user_products_are_rejected(self):
        self.calls, self.seller, self.tags = [], 999999, ['user_products_seller']
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', side_effect=self.fake_send):
            response = self.call('/shops/%s/items/CBT123' % self.store.id, 'PUT', b'{"price":12}')
            self.assertEqual(response.status_code, 403)
            self.assertEqual(len(self.calls), 1)
            response = self.call('/shops/%s/items' % self.store.id, 'POST', b'{"sites_to_sell":[{"site_id":"MLM","logistic_type":"fulfillment"}]}')
            self.assertEqual(response.status_code, 409)
            self.assertEqual(len(self.calls), 2)

    def test_revoked_inactive_and_cookie_only_authentication(self):
        self.reader.active = False
        self.assertEqual(self.call('/shops', key=self.reader_key).status_code, 401)
        self.env['res.users.apikeys'].search([('user_id', '=', self.writer.id)]).unlink()
        self.assertEqual(self.call('/shops').status_code, 401)
        self.authenticate('admin', 'admin')
        self.assertEqual(self.call('/shops', key=False).status_code, 401)

    def test_body_and_image_limits_and_form_identity(self):
        from .. import ir_http, controllers
        body = b'--BFF\r\nContent-Disposition: form-data; name="file"; filename="a.jpg"\r\n\r\n1234\r\n--BFF--\r\n'
        with patch.object(client, 'send', side_effect=AssertionError('invalid upload reached platform')):
            with patch.object(ir_http, 'MAX_BODY', 16):
                self.assertEqual(self.call('/shops/%s/items' % self.store.id, 'POST', b'x' * 17).status_code, 413)
            with patch.object(controllers, 'MAX_PICTURE', 3):
                self.assertEqual(self.call('/shops/%s/pictures' % self.store.id, 'POST', body, content_type='multipart/form-data; boundary=BFF').status_code, 413)
            form = b'--BFF\r\nContent-Disposition: form-data; name="shopId"\r\n\r\n999\r\n' + body
            self.assertEqual(self.call('/shops/%s/pictures' % self.store.id, 'POST', form, content_type='multipart/form-data; boundary=BFF').status_code, 400)

    def test_upstream_status_and_network_errors_are_safe(self):
        from ..protocol import BffError
        with patch.object(type(self.store), '_token', return_value='test-token'):
            with patch.object(client, 'send', return_value=client.PlatformResponse(429, [('Content-Type', 'application/json'), ('Retry-After', '10')], io.BytesIO(b'{"error":"rate_limit"}'))):
                response = self.call('/shops/%s/items' % self.store.id)
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.content, b'{"error":"rate_limit"}')
                self.assertEqual(response.headers['Retry-After'], '10')
            with patch.object(client, 'send', side_effect=BffError(502, 'Mercado Libre request failed or timed out')):
                response = self.call('/shops/%s/items' % self.store.id)
                self.assertEqual(response.status_code, 502)
                self.assertNotIn('test-token', response.text)

    def test_access_log_omits_query_values(self):
        with self.assertLogs('werkzeug', level='INFO') as logs:
            response = self.call('/shops?access_token=should-never-be-logged')
            self.assertEqual(response.status_code, 400)
        self.assertNotIn('should-never-be-logged', '\n'.join(logs.output))

    def test_encoded_separator_is_rejected_before_platform(self):
        with patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'send', return_value=client.PlatformResponse(200, [], io.BytesIO(b'{"seller_id":900001}'))):
            response = self.call('/shops/%s/items/CBT123%%2fdescription' % self.store.id)
            self.assertEqual(response.status_code, 404)

    def test_raw_query_survives_http_and_bff_selector_removal(self):
        from .test_client import Handler
        from http.server import ThreadingHTTPServer
        import threading
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.received = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.cr.flush()
        self.cr.clear()
        with self.allow_requests(), patch.object(type(self.store), '_token', return_value='test-token'), patch.object(client, 'API', 'http://127.0.0.1:%s' % server.server_port):
            with closing(http.client.HTTPConnection('127.0.0.1', self.http_port(), timeout=10)) as connection:
                cookies = '; '.join('%s=%s' % pair for pair in self.opener.cookies.items())
                connection.request('GET', '/api/meli/shops/%s/items?scope=global&q=one%%20two&status=active&q=%%2f%%41' % self.store.id,
                                   headers={'Authorization': 'Bearer ' + self.writer_key, 'Cookie': cookies})
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 422)
        self.assertEqual(server.received[-1][0], '/marketplace/users/900001/items/search?q=one%20two&status=active&q=%2f%41')

    def test_secondary_company_is_correctly_attributed_in_audit(self):
        self.writer.company_ids |= self.other_company
        with self.assertLogs('odoo.addons.meli_bff.ir_http', level='INFO') as logs:
            response = self.call('/shops/%s' % self.other.id)
            self.assertEqual(response.status_code, 200)
        self.assertIn('company=%s shop=%s' % (self.other_company.id, self.other.id), '\n'.join(logs.output))

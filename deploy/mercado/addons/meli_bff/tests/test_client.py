import gzip
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from .. import client
from ..protocol import BffError


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.do_POST()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.server.received.append((self.path, self.headers, body))
        if self.path == '/truncated':
            self.send_response(200)
            self.send_header('Content-Length', '100')
            self.end_headers()
            self.wfile.write(b'short')
            self.close_connection = True
            return
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', '/secret')
            self.end_headers()
            return
        data = gzip.compress(b'{"error":"platform validation"}')
        self.send_response(422)
        self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Connection', 'x-private')
        self.send_header('X-Private', 'internal')
        self.send_header('Link', '<a>')
        self.send_header('Link', '<b>')
        self.end_headers()
        self.wfile.write(data)


class TestClient(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(client, 'send', None)), 'BFF transport is missing')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.received = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.patch = patch.object(client, 'API', 'http://127.0.0.1:%s' % self.server.server_port)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_binary_query_auth_and_compressed_response(self):
        payload = b'--x\r\n\x00\xff\r\n--x--'
        result = client.send('POST', '/pictures/items/upload', token='test-token', body=payload,
                             query=[('a', 'one two'), ('a', '+')], content_type='multipart/form-data; boundary=x')
        self.addCleanup(result.close)
        path, headers, body = self.server.received[0]
        self.assertEqual(path, '/pictures/items/upload?a=one+two&a=%2B')
        self.assertEqual(headers['Authorization'], 'Bearer test-token')
        self.assertEqual(headers['Content-Type'], 'multipart/form-data; boundary=x')
        self.assertEqual(body, payload)
        self.assertEqual(result.status, 422)
        self.assertEqual(gzip.decompress(b''.join(result.iter_body())), b'{"error":"platform validation"}')
        self.assertNotIn('x-private', {k.lower() for k, v in result.headers})
        self.assertEqual([v for k, v in result.headers if k.lower() == 'link'], ['<a>', '<b>'])

    def test_truncated_response_returns_502_before_headers_escape(self):
        with self.assertRaises(BffError) as raised:
            client.send('GET', '/truncated', token='test-token')
        self.assertEqual(raised.exception.status, 502)

    def test_encoded_query_and_interleaving_are_preserved(self):
        result = client.send('GET', '/items', token='test-token',
                             query='q=one%20two&status=active&q=%2f%41')
        result.close()
        self.assertEqual(self.server.received[-1][0], '/items?q=one%20two&status=active&q=%2f%41')

    def test_redirect_is_not_followed(self):
        result = client.send('GET', '/redirect', token='test-token')
        self.addCleanup(result.close)
        self.assertEqual(result.status, 302)
        self.assertEqual(len(self.server.received), 1)

    def test_unsafe_path_and_network_failure(self):
        for path in ['https://evil.test', '//evil.test', '/items/../oauth/token', '/items?access_token=x']:
            with self.subTest(path=path), self.assertRaises(BffError):
                client.send('GET', path, token='test-token')
        with patch.object(client.http.client.HTTPConnection, 'connect', side_effect=TimeoutError('secret-token')):
            with self.assertRaises(BffError) as raised:
                client.send('POST', '/global/items', token='test-token')
            self.assertEqual(raised.exception.status, 502)
            self.assertNotIn('secret-token', str(raised.exception))

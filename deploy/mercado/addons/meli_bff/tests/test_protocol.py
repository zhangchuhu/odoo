import json
import unittest
from types import SimpleNamespace

from .. import protocol


class TestProtocol(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(protocol, 'resolve_route', None)), 'BFF route resolver is missing')
        self.store = SimpleNamespace(id=3, seller_id='900001', marketplace_seller_id='900002',
                                     site='MLM', logistic_type='fulfillment')

    def call(self, method, suffix, body=b'', query=()):
        return protocol.prepare_call(protocol.resolve_route(method, suffix), self.store, query, body)

    def test_description_uses_global_update_and_bound_site(self):
        call = self.call('PUT', '/shops/3/items/CBT123/description', b'{"plain_text":"Lamp"}')
        self.assertEqual(call.path, '/global/items/CBT123')
        self.assertEqual(json.loads(call.body), {'site_id': 'MLM', 'logistic_type': 'fulfillment',
                                               'description': {'plain_text': 'Lamp'}})

    def test_listing_query_keeps_repeated_values_and_selects_bound_seller(self):
        call = self.call('GET', '/shops/3/items', query=[('scope', 'marketplace'), ('status', 'active'), ('status', 'paused')])
        self.assertEqual(call.path, '/marketplace/users/900002/items/search')
        self.assertEqual(call.query, [('status', 'active'), ('status', 'paused')])

    def test_native_json_and_binary_are_preserved(self):
        body = b'{ "title" : "Lamp", "sites_to_sell": [{"site_id":"MLM", "logistic_type":"fulfillment"}] }'
        call = self.call('POST', '/shops/3/items', body)
        self.assertEqual(call.path, '/global/items')
        self.assertEqual(call.body, body)
        binary = b'--boundary\r\n\x00\xff\r\n--boundary--'
        self.assertEqual(self.call('POST', '/shops/3/pictures', binary).body, binary)

    def test_unknown_paths_and_methods_are_rejected(self):
        for method, path in [('GET', '/amazon/shops/3'), ('PATCH', '/shops/3/items/CBT123'),
                             ('GET', '/shops/0'), ('GET', '/shops/03'), ('GET', '/shops/3/../4'),
                             ('GET', '/shops/3/items/CBT123%2fdescription'), ('GET', '/shops/3//items')]:
            with self.subTest(path=path), self.assertRaises(protocol.BffError):
                protocol.resolve_route(method, path)

    def test_query_cannot_override_identity_or_target(self):
        for query in [[('shopId', '4')], [('shopId', '3'), ('shopId', '4')],
                      [('access_token', 'evil')], [('caller.id', '4')], [('url', 'https://evil.test')],
                      [('site_id', 'MLB')], [('scope', 'invalid')], [('scope', 'global'), ('scope', 'marketplace')]]:
            with self.subTest(query=query), self.assertRaises(protocol.BffError):
                self.call('GET', '/shops/3/items', query=query)

    def test_description_rejects_invalid_payload_and_scope_override(self):
        for body in [b'[]', b'null', b'bad', b'{"plain_text":42}', b'{"plain_text":"x","site_id":"MLB"}']:
            with self.subTest(body=body), self.assertRaises(protocol.BffError):
                self.call('PUT', '/shops/3/items/CBT123/description', body)

    def test_create_and_update_cannot_target_another_site(self):
        for body in [b'{"site_id":"MLB"}', b'{"sites_to_sell":[{"site_id":"MLB"}]}',
                     b'{"seller_id":42}', b'null', b'{}']:
            with self.subTest(body=body), self.assertRaises(protocol.BffError):
                self.call('POST', '/shops/3/items', body)

    def test_headers_remove_connection_named_headers_and_keep_duplicates(self):
        headers = [('Content-Type', 'application/json'), ('Connection', 'x-private'),
                   ('X-Private', 'secret'), ('Set-Cookie', 'session=x'), ('Content-Length', '3'),
                   ('Content-Encoding', 'gzip'), ('Link', 'a'), ('Link', 'b')]
        self.assertEqual(protocol.filtered_response_headers(headers),
                         [('Content-Type', 'application/json'), ('Content-Encoding', 'gzip'), ('Link', 'a'), ('Link', 'b')])

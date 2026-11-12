import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from odoo.addons.meli_bff import service
from odoo.addons.meli_platform_adapter import transport
from odoo.addons.meli_platform_adapter.errors import PlatformError


def response(value, status=200):
    return transport.PlatformResponse(status, [('Content-Type', 'application/json')], io.BytesIO(json.dumps(value).encode()))


class TestAdsRead(unittest.TestCase):
    def setUp(self):
        self.store = SimpleNamespace(site='MLM', seller_id='900001', marketplace_seller_id='900002', _token=lambda: 'token')
        self.calls = []
        self.seller = 900002
        self.advertisers = [{'advertiser_id': 10, 'site_id': 'MLM'}]
        self.ads = [{'advertiser_id': 10, 'id': 20, 'campaign_id': 30}]

    def send(self, method, path, **kwargs):
        self.calls.append((path, kwargs))
        if path == '/marketplace/items/MLM123':
            return response({'id': 'MLM123', 'seller_id': self.seller, 'site_id': 'MLM'})
        if path == '/advertising/advertisers':
            return response({'advertisers': self.advertisers})
        if path.endswith('/ad_groups/search'):
            return response({'paging': {'total': 1}, 'results': self.ads})
        if path.startswith('/marketplace/seller-promotions/items/'):
            return response([])
        return response({}, 404)

    def test_ads_derive_advertiser_and_reject_foreign_item(self):
        with patch.object(transport, 'send', side_effect=self.send):
            result = service.read_product_ads(None, self.store, {'item_id': 'MLM123', 'limit': 10, 'offset': 0})
            self.assertEqual(result.status, 200)
            result.close()
            self.assertEqual(self.calls[-1][0], '/marketplace/advertising/MLM/advertisers/10/product_ads/ad_groups/search')
            self.assertIn(('filters[item_ids]', 'MLM123'), self.calls[-1][1]['query'])
            self.seller = 999999
            self.calls.clear()
            with self.assertRaises(PlatformError):
                service.read_product_ads(None, self.store, {'item_id': 'MLM123'})
            self.assertEqual(len(self.calls), 1)

    def test_ads_reject_ambiguous_advertiser_and_foreign_results(self):
        with patch.object(transport, 'send', side_effect=self.send):
            self.advertisers.append({'advertiser_id': 11, 'site_id': 'MLM'})
            with self.assertRaises(PlatformError):
                service.read_product_ads(None, self.store, {'item_id': 'MLM123'})
            self.advertisers.pop()
            self.ads[0]['advertiser_id'] = 11
            with self.assertRaises(PlatformError):
                service.read_product_ads(None, self.store, {'item_id': 'MLM123'})

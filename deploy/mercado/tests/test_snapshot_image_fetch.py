import importlib.util
from pathlib import Path
import unittest
import requests

path = Path(__file__).parents[1] / 'addons/meli_optimization/domain/snapshot_image_fetch.py'
spec = importlib.util.spec_from_file_location('snapshot_image_fetch', path)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

class FetchTest(unittest.TestCase):
    def test_transient_tls_eof_retries_with_same_restricted_request(self):
        class Response:
            status_code=200
            headers={'Content-Type':'image/jpeg'}
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def iter_content(self, size): yield b'image'
        calls=[]
        def get(url, **kw):
            calls.append((url,kw))
            if len(calls)==1: raise requests.exceptions.SSLError('EOF occurred in violation of protocol')
            return Response()
        self.assertEqual(mod.fetch('https://http2.mlstatic.com/a.jpg',get),(b'image','image/jpeg'))
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0],calls[1])
        self.assertFalse(calls[0][1]['allow_redirects'])

    def test_retries_are_bounded_and_certificate_errors_are_not_retried(self):
        calls=[]
        def get(url, **kw):
            calls.append(url)
            raise requests.exceptions.SSLError('EOF occurred in violation of protocol')
        with self.assertRaises(requests.exceptions.SSLError): mod.fetch('https://http2.mlstatic.com/a.jpg',get)
        self.assertEqual(len(calls),3)
        calls.clear()
        def cert(url, **kw):
            calls.append(url)
            raise requests.exceptions.SSLError('CERTIFICATE_VERIFY_FAILED')
        with self.assertRaises(requests.exceptions.SSLError): mod.fetch('https://http2.mlstatic.com/a.jpg',cert)
        self.assertEqual(len(calls),1)

    def test_only_platform_gallery_secure_url_and_order(self):
        self.assertEqual(mod.picture_urls([{'secure_url':'https://http2.mlstatic.com/a.jpg'}, {'secure_url':'https://http2.mlstatic.com/b.jpg'}]), ['https://http2.mlstatic.com/a.jpg','https://http2.mlstatic.com/b.jpg'])
    def test_refuses_unsafe_and_missing_gallery(self):
        for p in ([], [{'url':'https://http2.mlstatic.com/a'}], [{'secure_url':'https://evil.test/a'}], [{'secure_url':'https://http2.mlstatic.com:444/a'}], [{'secure_url':'https://http2.mlstatic.com/a?x=1'}], [{'secure_url':'https://http2.mlstatic.com/a'}, {'secure_url':'https://http2.mlstatic.com/a'}]):
            with self.assertRaises(ValueError): mod.picture_urls(p)
    def test_redirect_is_never_followed(self):
        class Response:
            status_code=302
            headers={}
            def __enter__(self): return self
            def __exit__(self,*a): pass
        calls=[]
        def get(*a, **kw): calls.append(kw); return Response()
        with self.assertRaises(ValueError): mod.fetch('https://http2.mlstatic.com/a.jpg',get)
        self.assertEqual(len(calls),1)
        self.assertFalse(calls[0]['allow_redirects'])
    def test_stream_caps_and_media_types(self):
        class Response:
            status_code=200
            headers={'Content-Type':'image/jpeg'}
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def iter_content(self, size): yield b'a' * (mod.MAX_BYTES + 1)
        with self.assertRaises(ValueError): mod.fetch('https://http2.mlstatic.com/a.jpg',lambda *a, **kw: Response())
        Response.headers={'Content-Type':'text/html'}
        with self.assertRaises(ValueError): mod.fetch('https://http2.mlstatic.com/a.jpg',lambda *a, **kw: Response())
    def test_platform_ownership_and_site_are_mandatory(self):
        good={'id':'MLM123','site_id':'MLM','seller_id':42,'pictures':[{'secure_url':'https://http2.mlstatic.com/a.jpg'}]}
        self.assertEqual(mod.platform_pictures(good, 'MLM123', 'MLM', '42'), good['pictures'])
        for changes in ({'id':'MLM124'}, {'site_id':'MLB'}, {'seller_id':43}, {'pictures':[]}):
            with self.assertRaises(ValueError): mod.platform_pictures({**good,**changes},'MLM123','MLM','42')

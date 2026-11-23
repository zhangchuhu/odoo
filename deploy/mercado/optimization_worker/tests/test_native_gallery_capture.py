"""A qualified candidate's native gallery receipt uses its original leased binding."""
import base64
import hashlib
import io
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from optimization_worker.candidate_image_upload import capture_native_gallery
from optimization_worker.collector_progress import CollectorProgressError, handle_progress
from optimization_worker.domain import canonical_hash
from optimization_worker.host_session import SessionStore
from optimization_worker.native_collector_host import forward, read_message
from optimization_worker.tab_receipt_broker import Broker


class NativeGalleryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = SessionStore(self.root / 'host', 'fixture')
        self.claim = dict(company_id=3, snapshot_id=4, input_hash='h', lease_epoch=5)
        self.rid = '12345678-1234-4123-8123-123456789012'
        self.identity = 'mercado-libre-mx:MLM123'
        self.url = 'https://www.mercadolibre.com.mx/vestido/p/MLM123'
        self.redirect = 'https://articulo.mercadolibre.com.mx/MLM-123-vestido-_JM'
        self.images = ['https://http2.mlstatic.com/front.webp','https://http2.mlstatic.com/back.webp']
        self.platform = 'mercado-libre-mx'
        plan = {'platform': self.platform, 'queries': ['a','b','c'], 'category':'dress'}
        signed = dict(request_id=self.rid, company_id=3, snapshot_id=4, input_hash='h', plan=plan)
        signed['digest'] = canonical_hash(signed)
        self.candidate = dict(platform=self.platform, item_id='MLM123', url=self.url)
        record = dict(request_id=self.rid, state='candidate_images_pending', collector_plan=signed,
                      collector_artifact_id='12', collector_evidence={'data':'checked'},
                      collector_pending={'artifact_id':'12','result': {'candidates':[self.candidate,
                          dict(self.candidate,item_id='MLM456',url=self.url.replace('123','456'))]}})
        with self.store._db() as db:
            self.store._put(db, record)
        def capture(event, guard):
            return capture_native_gallery(self.store, self.rid, self.claim, event, guard)
        self.broker = Broker(self.root / 'socket', self.root / 'ledger',
                             dict(version=1, request_id=self.rid,platform=self.platform,plan_digest=signed['digest'],lease_epoch=5,
                                  query_index=2, query='c', nonce='a'*32), 4, lambda:True,
                             expected_window_id=7, expected_session='current', watch_only=True,
                             anchor_origin='chrome-extension://'+'a'*32+'/', progress_plan=(plan,{}),
                             gallery_capture=capture)
        self.broker.anchor_guard_alive = lambda: True
        self.broker.guard_alive = lambda session: session == 'current'
        self.raw=self.jpeg('blue')
        self.event=dict(operation='gallery_capture',request_id=self.rid,window_id=7,session_id='current',tab_id=4,
                        native_origin=self.broker.anchor_origin,identity=self.identity,detail_url=self.redirect,
                        manifest=dict(detail_url=self.redirect,images=self.images),
                        screenshot=base64.b64encode(self.raw).decode())

    @staticmethod
    def jpeg(color):
        output=io.BytesIO()
        Image.new('RGB',(10,12),color).save(output,format='JPEG')
        return output.getvalue()

    def test_real_native_socket_returns_first_receipt_after_lost_response(self):
        import threading
        native = self.event.copy()
        origin = native.pop('native_origin')
        with self.broker:
            with self.assertRaisesRegex(ValueError, 'Native origin required'):
                forward(self.broker.path, self.event, origin=origin)
            def call(event):
                thread=threading.Thread(target=self.broker.serve_once)
                thread.start()
                try:
                    return forward(self.broker.path,event,origin=origin)
                finally:
                    thread.join(1)
                    self.assertFalse(thread.is_alive())
            receipt = call(native)
            self.assertEqual(receipt,dict(ok=True,identity=self.identity,image_count=2,
                                          screenshot_sha256=hashlib.sha256(self.raw).hexdigest()))
            self.assertEqual(call(dict(native,screenshot=base64.b64encode(self.jpeg('red')).decode())),receipt)
            conflict = call(dict(native,manifest=dict(detail_url=self.url,images=list(reversed(self.images)))))
            self.assertEqual(conflict['error'],'gallery_manifest_conflict')
        stored=self.store.inspect(self.rid)['collector_pending']['gallery_manifests'][self.identity]
        self.assertEqual(stored['detail_url'],self.url)
        self.assertEqual(Path(stored['screenshot_path']).read_bytes(),self.raw)

    def test_fences_target_origin_candidate_order_image_hosts_and_frame(self):
        cases = [
            ({'request_id':'other'},'checkpoint_binding_invalid'),
            ({'tab_id':5},'checkpoint_binding_invalid'),
            ({'session_id':'old'},'checkpoint_binding_invalid'),
            ({'native_origin':'chrome-extension://'+'b'*32+'/'},'checkpoint_binding_invalid'),
            ({'identity':self.platform+':MLM456','detail_url':self.redirect.replace('123','456'),
              'manifest':dict(images=self.images,detail_url=self.url.replace('123','456'))},'gallery_phase_invalid'),
            ({'detail_url':self.url.replace('123','456')},'gallery_candidate_invalid'),
            ({'detail_url':'https://listado.mercadolibre.com.mx/MLM123'},'gallery_candidate_invalid'),
            ({'manifest':dict(detail_url=self.url,images=['https://mlstatic.com.evil.example/a'])},'gallery_image_invalid'),
            ({'manifest':dict(detail_url=self.url,images=['https://user@http2.mlstatic.com/a'])},'gallery_image_invalid'),
            ({'screenshot':'AA=='},'gallery_screenshot_invalid'),
            ({'screenshot':base64.b64encode(b'\xff\xd8\xffbogus').decode()},'gallery_screenshot_invalid'),
        ]
        for change,code in cases:
            with self.subTest(change=tuple(change)), self.assertRaises(CollectorProgressError) as err:
                handle_progress(self.broker,dict(self.event,**change))
            self.assertEqual(err.exception.code,code)
        self.broker.fence = lambda: False
        with self.assertRaises(CollectorProgressError):
            handle_progress(self.broker, self.event)
        self.broker.fence = lambda: True
        self.broker.guard_alive=lambda session:False
        with self.assertRaises(CollectorProgressError) as err:
            handle_progress(self.broker,self.event)
        self.assertEqual(err.exception.code,'checkpoint_guard_pending')
        self.assertNotIn('gallery_manifests',self.store.inspect(self.rid)['collector_pending'])
        import json, struct
        frame=json.dumps(dict(self.event,screenshot='A'*900000)).encode()
        with self.assertRaises(ValueError):
            read_message(io.BytesIO(struct.pack('=I',len(frame))+frame))


if __name__ == '__main__':
    unittest.main()

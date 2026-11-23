import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from optimization_worker.host_session import SessionStore
from optimization_worker.research_bridge_socket import Server, send
from optimization_worker.research_return_bridge import ReturnBridge
from optimization_worker.tests.test_collector_import import fixture
from optimization_worker.visual_candidate import VisualCandidate


class VisualCandidateTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.ledger = self.root / 'ledger.sqlite'
        data, queries = fixture()
        request = '6fda5929-4ad3-4cde-894b-8c0089b7e334'
        self.key = request, 'a'*64, 3
        self.plan = dict(platform='mercado-libre-mx',category='dress',queries=queries,result_limit=5)
        self.config = dict(result_limit=5,min_sold=100,min_reviews=0,min_rating=0)
        self.claim = dict(id=235, snapshot_id=254, company_id=1, input_hash='test',task_type='research.bestseller')
        self.review = VisualCandidate(self.ledger,self.key,self.plan,self.config,self.claim)
        self.store = SessionStore(self.root/'host.sqlite','visual-test')
        self.store.files.mkdir(exist_ok=True)
        self.downloads = self.root/'Downloads'
        self.downloads.mkdir()
        with sqlite3.connect(self.ledger) as db:
            from optimization_worker.collector_progress import initialize_details
            initialize_details(db)
            db.execute('CREATE TABLE query_cards (request_id TEXT, plan_digest TEXT, lease_epoch INTEGER, query_index INTEGER, body TEXT)')
            for i, block in enumerate(data['queries']):
                for card in block['observations']:
                    card['product_id'] = 'MLM'+card['product_id']
                    card['url'] = 'https://articulo.mercadolibre.com.mx/'+card['product_id']
                db.execute('INSERT INTO query_cards VALUES (?,?,?,?,?)',(*self.key,i,json.dumps(block['observations'])))
        self.browser_live = True
        self.broker = SimpleNamespace(watch_only=True,gallery_binding=None,conflict=False,rebind_requested=False,
            fence=lambda:True,anchor_guard_alive=lambda:self.browser_live,guard_alive=lambda _session:self.browser_live,
            anchor_origin='anchor',tab=3,window=4,session='session')
        self.event = dict(request_id=request,tab_id=3,window_id=4,session_id='session',native_origin='anchor')
        self.detail = dict(data['details'][0],identity='mercado-libre-mx:MLM1',product_id='MLM1',
            detail_url='https://articulo.mercadolibre.com.mx/MLM1',status='rejected',
            reason='imagery_ambiguous_or_inaccessible',sold_display='+100 vendidos',visual_category_code=None,
            match_level=None,visual_features=None)
        self.source = 'https://http2.mlstatic.com/source.jpg'

    def test_download_vision_and_socket_write_survive_browser_switch(self):
        start = dict(self.event,operation='visual_photo_start',detail=self.detail,photo=1,source=self.source,variant='selected-pink')
        ticket = self.review.native(self.broker,start,self.store,self.downloads)
        self.assertEqual(ticket,self.review.native(self.broker,start,self.store,self.downloads))
        target = self.downloads/ticket['filename']
        target.parent.mkdir(parents=True)
        Image.new('RGB',(600,800),'pink').save(target)
        raw=target.read_bytes()
        photo=self.review.native(self.broker,dict(self.event,operation='visual_photo_saved',receipt=ticket['receipt'],
            download_id=12,final_url=self.source,size=len(raw)),self.store,self.downloads)
        self.assertEqual(photo['sha256'],hashlib.sha256(raw).hexdigest())
        self.browser_live = False
        bridge = ReturnBridge(self.key[0],lambda:self.claim,lambda:None, {
            'visual_candidate_status':lambda *_:self.review.status(self.store,self.root),
            'submit_visual_verdict':lambda _c,d:self.review.submit(d,self.store,lambda:None),
        },write_guard=lambda:self.fail('visual submit must not depend on Chrome focus'))
        request=dict(operation='submit_visual_verdict',data=dict(submission_id='original-visual-01',receipts=[photo['receipt']],
            status='qualified',reason=None,category='dress',match_level='同款',features=['lace bodice']))
        with Server(self.root/'bridge.sock',bridge) as server:
            def call(message):
                worker=threading.Thread(target=server.serve_once)
                worker.start()
                try:
                    return send(server.path,message)
                finally:
                    worker.join()
            current=call(dict(operation='visual_candidate_status', data={}))['visual']
            self.assertEqual(Path(current['images'][0]['path']).read_bytes(),raw)
            receipt=call(request)
            self.assertTrue(receipt['accepted'])
            self.assertEqual(receipt,call(request))
            request['data']['features']=['different cut']
            self.assertEqual(call(request),{'accepted':False})
        with sqlite3.connect(self.ledger) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM collector_details').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM visual_verdicts').fetchone()[0],1)
        self.assertEqual(self.review.current(),(1,'mercado-libre-mx:MLM2'))
        self.assertEqual(self.review.reuse_first_photo(self.store,self.detail['identity'],self.source,'selected-pink'),raw)
        self.assertIsNone(self.review.reuse_first_photo(self.store,self.detail['identity'],self.source,'wrong-variant'))

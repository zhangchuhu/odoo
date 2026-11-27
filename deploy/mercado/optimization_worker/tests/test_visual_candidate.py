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
            db.execute('CREATE TABLE accepted_receipts (id INTEGER PRIMARY KEY, request_id TEXT, body TEXT)')
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

    def test_card_v3_visual_photo_uses_detail_qualified_order_and_supplemental_sales(self, strategy='card-visual-v3'):
        from optimization_worker.card_sales_v3_checkpoints import initialize,seal_query,append_page_batch,finish_page
        from optimization_worker.card_sales_v3_pipeline import initialize_pipeline,freeze_queue,record_detail
        self.plan['strategy']=strategy;self.config['min_sold']=500
        self.review=VisualCandidate(self.ledger,self.key,self.plan,self.config,self.claim)
        with sqlite3.connect(self.ledger) as db:
            initialize(db);initialize_pipeline(db)
            for i,q in enumerate(self.plan['queries']):
                seal_query(db,self.key,i,q,fence=lambda:True)
                card=dict(query=q,product_id='MLM'+str(i+1),url='https://articulo.mercadolibre.com.mx/MLM'+str(i+1),rank=1,first_seen=1,bbox=[1,1,30,40],sales_text='+150 vendidos')
                append_page_batch(db,self.key,i,1,0,[card],fence=lambda:True)
                if strategy=='card-home-v4':db.execute('INSERT INTO card_v3_pages VALUES (?,?,?,?,?,?)',(*self.key,i,1,'home_complete'))
                else:finish_page(db,self.key,i,1,'end_of_results',fence=lambda:True)
            queue=freeze_queue(db,self.key,plan_exhausted=True,fence=lambda:True,homepage_only=strategy=='card-home-v4')
            record_detail(db,self.key,0,queue[0],'+150 vendidos',500,fence=lambda:True)
        self.assertEqual(self.review.current(),(0,'mercado-libre-mx:'+queue[0]))
        detail=dict(self.detail,identity='mercado-libre-mx:'+queue[0],product_id=queue[0],detail_url='https://articulo.mercadolibre.com.mx/'+queue[0],sold_display='+150 vendidos')
        with sqlite3.connect(self.ledger) as db:db.execute('INSERT INTO card_v3_detail_observations VALUES (?,?,?,?,?)',(*self.key,0,json.dumps(detail)))
        self.assertTrue(self.review.valid_detail(detail,detail['identity']))
        self.assertTrue(self.review.valid_detail(dict(detail,sold_display='+1.000 vendidos'),detail['identity']))
        self.assertEqual(self.review.valid_detail(dict(detail,sold_display='+100 vendidos'),detail['identity']),strategy=='card-home-v4')
        self.assertFalse(self.review.valid_detail(dict(detail,sold_display='100 vendidos'),detail['identity']))
        primary=dict(category='dress',components='one_piece',length='long',shoulders='regular',structure='one_piece',silhouette='a_line',fit='loose',sleeve_length='short')
        self.plan['design_profile']=dict(primary=primary,secondary=['lace'],styles=['boho'],occasions=[])
        self.review=VisualCandidate(self.ledger,self.key,self.plan,self.config,self.claim)
        ticket=self.review.native(self.broker,dict(self.event,operation='visual_photo_start',detail=detail,photo=1,source=self.source,variant='pink'),self.store,self.downloads)
        target=self.downloads/ticket['filename'];target.parent.mkdir(parents=True)
        Image.new('RGB',(600,800),'pink').save(target);raw=target.read_bytes()
        self.review.native(self.broker,dict(self.event,operation='visual_photo_saved',receipt=ticket['receipt'],download_id=12,final_url=self.source,size=len(raw)),self.store,self.downloads)
        from unittest.mock import patch
        verdict=dict(submission_id='card-v3-test-01',receipts=[ticket['receipt']],candidate_primary=primary,secondary=['lace'],styles=['boho'],occasions=[],features=['lace'])
        with patch('optimization_worker.collector_cli.validate_source'),patch.object(self.store,'inspect',return_value={}):
            result=self.review.submit(verdict,self.store,lambda:True)
            self.assertEqual(result['classification'],'highly_similar')
            self.assertEqual(result['images'][0]['sha256'],hashlib.sha256(raw).hexdigest())
            self.assertEqual(self.review.submit(verdict,self.store,lambda:True),result)
            self.assertEqual(self.review.submit(verdict,self.store,lambda:None),result)
            resumed=VisualCandidate(self.ledger,(self.key[0],self.key[1],self.key[2]+1),self.plan,self.config,self.claim)
            with self.assertRaises(ValueError):resumed.submit(verdict,self.store,lambda:True)
        with sqlite3.connect(self.ledger) as db:
            for i in (1,2):record_detail(db,self.key,i,queue[i],'+100 vendidos',500,fence=lambda:True)
            from optimization_worker.visual_v3_finalize import build_result
            with self.assertRaises(ValueError):
                build_result(db,self.key,self.review.scope,self.plan,self.config,'2026-11-26T00:00:00+00:00',lambda filename,sha:self.review.photo(self.store.files,filename,sha))
            import io
            screenshot=io.BytesIO();Image.new('RGB',(120,120),'white').save(screenshot,format='JPEG');shot=screenshot.getvalue();shot_sha=hashlib.sha256(shot).hexdigest();shot_file=Path(self.store._file(shot)).name
            for i in range(3):db.execute('INSERT INTO card_v3_batch_evidence VALUES (?,?,?,?,?,?,?,?,?,?)',(*self.key,i,1,0,shot_sha,shot_file,120,120))
            outcome=build_result(db,self.key,self.review.scope,self.plan,self.config,'2026-11-26T00:00:00+00:00',lambda filename,sha:self.review.photo(self.store.files,filename,sha))
            self.assertEqual(outcome['candidates'],[])
            self.assertEqual(outcome['supplemental_candidates'][0]['item_id'],queue[0])
            from optimization_worker.domain import validate_result,DomainError
            import copy
            bad=copy.deepcopy(outcome);bad['supplemental_candidates'].append(copy.deepcopy(bad['supplemental_candidates'][0]))
            with self.assertRaises(DomainError):validate_result('research.bestseller',bad)
            from optimization_worker.visual_v3_finalize import snapshot_evidence
            evidence=snapshot_evidence(db,self.key,self.review.scope,self.plan,outcome,'rec-test')
            self.assertEqual(evidence['details'][0]['identity'],'mercado-libre-mx:'+queue[0])
            self.assertEqual(evidence['details'][0]['status'],'qualified')
            from optimization_worker.visual_v3_finalize import prepare_stage
            self.assertEqual(prepare_stage(db,self.key,self.review.scope,self.plan,self.config,'rec-test','2026-11-26T00:00:00+00:00',lambda filename,sha:self.review.photo(self.store.files,filename,sha)),evidence)

    def test_home_visual_photo_uses_detail_qualified_order_and_supplemental_sales(self):
        self.test_card_v3_visual_photo_uses_detail_qualified_order_and_supplemental_sales('card-home-v4')

    def test_visual_v2_decision_uses_verified_photo_and_bound_source_design(self):
        from optimization_worker.visual_v2_checkpoints import initialize,seal_queries,append_batch,finish_query
        primary={'category':'dress','components':{'status':'not_applicable','value':None},'length':'long','shoulders':'regular','structure':'one_piece',
                 'silhouette':'a_line','fit':None,'sleeve_length':None}
        self.plan.update(strategy='visual-v2',design_profile={'primary':primary,'secondary':['lace'],'styles':['boho'],'occasions':[]})
        self.review=VisualCandidate(self.ledger,self.key,self.plan,self.config,self.claim)
        with sqlite3.connect(self.ledger) as db:
            initialize(db);seal_queries(db,self.key,self.plan['queries'],fence=lambda:True)
            for i in range(3):
                card={'product_id':'MLM'+str(i+1),'url':'https://articulo.mercadolibre.com.mx/MLM'+str(i+1)}
                append_batch(db,(*self.key,str(i)),0,[card],fence=lambda:True)
                finish_query(db,(*self.key,str(i)),'home_complete',fence=lambda:True)
        ticket=self.review.native(self.broker,dict(self.event,operation='visual_photo_start',detail=self.detail,photo=1,source=self.source,variant='pink'),self.store,self.downloads)
        target=self.downloads/ticket['filename'];target.parent.mkdir(parents=True)
        Image.new('RGB',(600,800),'pink').save(target);raw=target.read_bytes()
        photo=self.review.native(self.broker,dict(self.event,operation='visual_photo_saved',receipt=ticket['receipt'],download_id=12,final_url=self.source,size=len(raw)),self.store,self.downloads)
        verdict=dict(submission_id='visual-v2-test-01',receipts=[photo['receipt']],candidate_primary=primary,
                     secondary=['lace'],styles=[],occasions=[],critical_conflict=False,features=['lace bodice'])
        receipt=self.review.submit(verdict,self.store,lambda:None)
        self.assertEqual(receipt['status'],'qualified')
        self.assertEqual(receipt['classification'],'highly_similar')
        self.assertEqual(self.review.submit(verdict,self.store,lambda:None),receipt)
        with sqlite3.connect(self.ledger) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM visual_v2_decisions').fetchone()[0],1)

    def test_staged_singleton_visual_verdict_uses_staged_order(self):
        self.plan['strategy']='staged-v1'
        with sqlite3.connect(self.ledger) as db:
            for i,body in db.execute('SELECT query_index,body FROM query_cards ORDER BY query_index').fetchall():
                cards=json.loads(body)
                for card in cards:
                    pid='MLM'+str(i*1000+card['rank'])
                    card.update(product_id=pid,url='https://articulo.mercadolibre.com.mx/'+pid)
                db.execute('UPDATE query_cards SET body=? WHERE query_index=?',(json.dumps(cards),i))
        ticket=self.review.native(self.broker,dict(self.event,operation='visual_photo_start',detail=self.detail,photo=1,source=self.source,variant='pink'),self.store,self.downloads)
        target=self.downloads/ticket['filename'];target.parent.mkdir(parents=True)
        Image.new('RGB',(600,800),'pink').save(target);raw=target.read_bytes()
        photo=self.review.native(self.broker,dict(self.event,operation='visual_photo_saved',receipt=ticket['receipt'],download_id=12,final_url=self.source,size=len(raw)),self.store,self.downloads)
        verdict=dict(submission_id='staged-visual-01',receipts=[photo['receipt']],status='qualified',reason=None,category='dress',match_level='同款',features=['lace bodice'])
        self.assertEqual(self.review.submit(verdict,self.store,lambda:None)['identity'],'mercado-libre-mx:MLM1')

    def test_staged_expansion_visual_verdict_uses_sealed_queries(self):
        from optimization_worker.collector_staged_policy import initialize_expansions
        data,_=fixture();self.plan['strategy']='staged-v1';extra='extra dress'
        cards=[dict(c,query=extra,product_id='MLM'+str(30+c['rank']),url='https://articulo.mercadolibre.com.mx/MLM'+str(30+c['rank'])) for c in data['queries'][0]['observations']]
        with sqlite3.connect(self.ledger) as db:
            initialize_expansions(db);body=json.dumps({'queries':[extra]},sort_keys=True,separators=(',',':'))
            db.execute('INSERT INTO staged_expansions VALUES (?,?,?,?,?)',(*self.key,body,hashlib.sha256(body.encode()).hexdigest()))
            db.execute('INSERT INTO query_cards VALUES (?,?,?,?,?)',(*self.key,3,json.dumps(cards)))
            for i,detail in enumerate(data['details'][:20]):
                detail.update(identity='mercado-libre-mx:MLM'+str(i+1),product_id='MLM'+str(i+1),
                              detail_url='https://articulo.mercadolibre.com.mx/MLM'+str(i+1),sold_display='+100 vendidos' if i==0 else '+10 vendidos')
                db.execute('INSERT INTO collector_details VALUES (?,?,?,?,?)',(*self.key,i,json.dumps(detail)))
        detail=dict(self.detail,identity='mercado-libre-mx:MLM31',product_id='MLM31',detail_url='https://articulo.mercadolibre.com.mx/MLM31')
        ticket=self.review.native(self.broker,dict(self.event,operation='visual_photo_start',detail=detail,photo=1,source=self.source,variant='pink'),self.store,self.downloads)
        target=self.downloads/ticket['filename'];target.parent.mkdir(parents=True)
        Image.new('RGB',(600,800),'pink').save(target);raw=target.read_bytes()
        photo=self.review.native(self.broker,dict(self.event,operation='visual_photo_saved',receipt=ticket['receipt'],download_id=12,final_url=self.source,size=len(raw)),self.store,self.downloads)
        verdict=dict(submission_id='expanded-visual-01',receipts=[photo['receipt']],status='qualified',reason=None,category='dress',match_level='同款',features=['lace bodice'])
        self.assertEqual(self.review.submit(verdict,self.store,lambda:None)['identity'],'mercado-libre-mx:MLM31')

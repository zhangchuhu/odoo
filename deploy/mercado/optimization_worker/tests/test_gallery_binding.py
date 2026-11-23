"""After evidence import only a fresh native detail proof can resume gallery writes."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import socket
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from unittest.mock import Mock, patch

from optimization_worker.domain import canonical_hash
from optimization_worker.host_session import SessionStore
from optimization_worker.native_collector_host import forward, read_message, write_message
from optimization_worker.tab_receipt_factory import build_native_broker


class GalleryBindingTests(unittest.TestCase):
    def fixture(self, root, platform):
        store = SessionStore(root/'host','fixture')
        claim = dict(id=240,lease_epoch=6,snapshot_id=4,company_id=3,input_hash='h',task_type='research.bestseller')
        request = '12345678-1234-4123-8123-123456789012'
        signed = dict(request_id=request,snapshot_id=4,company_id=3,input_hash='h',
                      plan=dict(platform=platform,queries=['first','second','third']))
        signed['digest'] = canonical_hash(signed)
        url = 'https://us.shein.com/dress-p-123.html' if platform=='shein-us' else 'https://www.mercadolibre.com.mx/dress/p/MLM123'
        pid = '123' if platform=='shein-us' else 'MLM123'
        evidence = dict(platform=platform,details=[dict(status='qualified',identity=platform+':'+pid,detail_url=url)])
        digest = hashlib.sha256(json.dumps(evidence,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        pending = dict(data=evidence,digest=digest,ledger_v1=True,artifact_id='1611',candidate_uploads={},
                       result=dict(candidates=[dict(item_id=pid,platform=platform,url=url)]))
        with store._db() as db:
            store._put(db,dict(request_id=request,state='candidate_images_pending',task_type=claim['task_type'],
                               collector_evidence=evidence,collector_artifact_id='1611',collector_plan=signed,collector_pending=pending))
        client = Mock(research_ledger_enabled=True)
        client.research_intent.return_value = dict(artifact_id='1611',digest=digest)
        run = dict(claim=claim,phase='host_pending',request_id=request)
        cfg = dict(anchor_extension_id='a'*32,peer_extension_id='b'*32,extension_ids=['a'*32,'b'*32])
        def build():
            return build_native_broker(root/'s',root/'ledger',request,0,store,client,'j',watch_only=True,dedicated_required=True)
        return store,client,request,cfg,run,build,url,platform+':'+pid

    def test_scoped_intent_and_known_images_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            store,client,rid,cfg,run,build,*_ = self.fixture(Path(tmp),'mercado-libre-mx')
            with patch('optimization_worker.native_collector_host.private_config',return_value=cfg), \
                    patch('optimization_worker.tab_receipt_factory.worker_records',return_value=[run]), \
                    patch('optimization_worker.tab_receipt_factory.fence'):
                client.research_intent.return_value['artifact_id']='2000'
                with self.assertRaisesRegex(ValueError,'gallery evidence'):
                    build()
                client.research_intent.return_value['artifact_id']='1611'
                with store._db() as db:
                    record=store._get(db,rid)
                    original=record['collector_pending']['result']['candidates'][0]
                    record['collector_pending']['result']['candidates'][0]=dict(original,item_id='MLM999',
                        url='https://www.mercadolibre.com.mx/dress/p/MLM999')
                    store._put(db,record)
                with self.assertRaisesRegex(ValueError,'candidate identities'):
                    build()
                with store._db() as db:
                    record=store._get(db,rid)
                    record['collector_pending']['result']['candidates'][0]=original
                    store._put(db,record)
                broker = build()
                self.assertTrue(broker.gallery_binding.target())
                with store._db() as db:
                    record=store._get(db,rid)
                    record['collector_pending']['candidate_uploads']['item:0']={'state':'unknown'}
                    store._put(db,record)
                self.assertIsNone(broker.gallery_binding.target())
                with self.assertRaises(ValueError):
                    build()

    def test_fresh_scoped_detail_proof_opens_guard_without_search_or_evidence_writes(self):
        for platform in ('mercado-libre-mx','shein-us'):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                store,client,rid,cfg,run,build,url,identity = self.fixture(root,platform)
                with patch('optimization_worker.native_collector_host.private_config',return_value=cfg), \
                        patch('optimization_worker.tab_receipt_factory.worker_records',return_value=[run]), \
                        patch('optimization_worker.tab_receipt_factory.fence'), build() as broker:
                    def send(msg,origin=None):
                        thread=threading.Thread(target=broker.serve_once);thread.start()
                        try: return forward(broker.path,msg,origin=origin)
                        finally: thread.join(2)
                    def guard(msg):
                        thread=threading.Thread(target=broker.serve_once);thread.start()
                        connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
                        self.addCleanup(connection.close)
                        connection.connect(str(broker.path))
                        stream=connection.makefile('rwb')
                        self.addCleanup(stream.close)
                        write_message(stream,msg);result=read_message(stream)
                        thread.join(2)
                        return result
                    anchor = broker.anchor_origin
                    origin = broker.peer_origin if platform=='shein-us' else anchor
                    base = dict(request_id=rid,session_id='c'*32)
                    token = send(dict(base,operation='anchor_challenge'),anchor)['token']
                    self.assertTrue(send(dict(base,operation='anchor_confirm',epoch=6,window_id=7,marker='d'*32,token=token),anchor)['ok'])
                    broker.open_anchor_guard(dict(base,operation='anchor_guard_open',window_id=7,marker='d'*32,native_origin=anchor))
                    binding = dict(base,window_id=7,tab_id=44)
                    challenge = dict(binding,operation='gallery_binding_challenge')
                    self.assertFalse(send(challenge,origin)['ok'])
                    issue = dict(base,window_id=7,operation='task_tab_challenge')
                    token = send(issue,anchor)['token']
                    self.assertTrue(send(dict(binding,operation='task_tab_confirm',token=token,epoch=6),anchor)['ok'])
                    if platform=='shein-us':
                        binding['session_id']='e'*32
                        peer = send(dict(binding,operation='peer_challenge'),origin)
                        self.assertTrue(send(dict(binding,operation='peer_confirm',epoch=6,token=peer['token'],peer_id='b'*32),anchor)['ok'])
                        self.assertTrue(send(dict(binding,operation='select_window',observed_at=datetime.now(timezone.utc).isoformat()))['ok'])
                    query = dict(binding,operation='gallery_binding_challenge')
                    self.assertFalse(send(query,'chrome-extension://'+'z'*32+'/')['ok'])
                    for changed in (dict(query,window_id=88),dict(query,tab_id=45),dict(query,request_id='other'),dict(query,session_id='x'*32)):
                        self.assertFalse(send(changed,origin)['ok'])
                    status = {k:binding[k] for k in ('tab_id','window_id','session_id')}
                    self.assertTrue(send(dict(status,operation='binding_status'))['gallery_binding_required'])
                    self.assertFalse(guard(dict(binding,operation='guard_open'))['ok'])
                    offer = send(query,origin)
                    self.assertEqual((offer['identity'],offer['detail_url'],offer['epoch']),(identity,url,6))
                    commit = dict(binding,operation='gallery_binding_confirm',nonce=offer['nonce'],epoch=6,
                                  identity=identity,detail_url=url,observed_at=datetime.now(timezone.utc).isoformat())
                    for changed in (dict(commit,epoch=5),dict(commit,nonce='stale'),dict(commit,detail_url=url.replace('123','124')),
                                    dict(commit,observed_at='2020-01-01T00:00:00Z')):
                        self.assertFalse(send(changed,origin)['ok'])
                    self.assertTrue(send(commit,origin)['ok'])
                    self.assertFalse(send(commit,origin)['ok'])
                    self.assertTrue(send(dict(status,operation='binding_status'))['guard_required'])
                    self.assertTrue(guard(dict(binding,operation='guard_open'))['ok'])
                    self.assertTrue(broker.guard_alive(binding['session_id']))
                    for op in ('poll','recover_receipt','stage_evidence','submit','query_cards_complete'):
                        self.assertEqual(send(dict(operation=op,request_id=rid))['error'],'gallery_phase_invalid')
                    with closing(sqlite3.connect(root/'ledger')) as db:
                        for table in ('challenges','accepted_receipts','staged_evidence'):
                            self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
                    client.create_research_artifact.assert_not_called()
                    with store._db() as db:
                        record=store._get(db,rid)
                        record['collector_pending']['candidate_uploads']['i:0']={'state':'unknown'}
                        store._put(db,record)
                    self.assertFalse(broker.guard_alive(binding['session_id']))

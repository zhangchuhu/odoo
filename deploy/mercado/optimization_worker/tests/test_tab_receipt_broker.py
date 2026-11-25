import tempfile
import threading
import unittest
import socket
import time
from unittest.mock import patch
import subprocess
import sys
from datetime import datetime,timezone
from pathlib import Path
from optimization_worker.native_collector_host import forward, write_message
from optimization_worker.tab_receipt_broker import Broker

class BrokerTest(unittest.TestCase):
    def test_client_disconnect_during_status_does_not_stop_broker(self):
        challenge = dict(version=1, request_id='r', nonce='a'*32, lease_epoch=3,
                         plan_digest='b'*64, platform='mercado-libre-mx', query_index=0, query='dress')
        status = dict(operation='binding_status', tab_id=123, window_id=4, session_id='c'*32)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'broker'
            with Broker(path, Path(tmp)/'ledger', challenge, 123, lambda: True,
                        expected_window_id=4, expected_session='c'*32, watch_only=True) as broker:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as abandoned:
                    abandoned.connect(str(path))
                    with abandoned.makefile('wb') as stream:
                        write_message(stream, status)
                    abandoned.shutdown(socket.SHUT_RDWR)
                self.assertTrue(broker.serve_once())
                reader = threading.Thread(target=broker.serve_once)
                reader.start()
                try:
                    self.assertTrue(forward(path, status)['matches'])
                finally:
                    reader.join(2)

    def test_watch_status_reports_live_anchor_and_controlled_session_rebind(self):
        anchor = 'chrome-extension://' + 'a'*32 + '/'
        peer = 'chrome-extension://' + 'b'*32 + '/'
        session, marker = 'c'*32, 'd'*32
        challenge = dict(version=1,request_id='r',nonce='a'*32,lease_epoch=3,plan_digest='b'*64,
                         platform='mercado-libre-mx',query_index=0,query='dress')
        reconciled=False
        live=True
        with tempfile.TemporaryDirectory() as tmp:
            sock=Path(tmp)/'s'
            with Broker(sock,Path(tmp)/'ledger',challenge,123,lambda:live,expected_window_id=4,
                        expected_session=session,watch_only=True,anchor_origin=anchor,peer_origin=peer,
                        rebind_check=lambda:reconciled) as broker:
                old=broker.anchor_registry.challenge('r',3,session,lambda:True)
                self.assertTrue(broker.anchor_registry.confirm(dict(request='r',epoch=3,session=session,
                        window=4,marker=marker,token=old),lambda _:True,lambda:True))
                def call(message, origin=None):
                    reader=threading.Thread(target=broker.serve_once)
                    reader.start()
                    try: return forward(sock,message,origin=origin)
                    finally: reader.join(2)
                def confirm(identity, window):
                    token=call(dict(operation='anchor_challenge',request_id='r',session_id=identity),anchor)['token']
                    return call(dict(operation='anchor_confirm',request_id='r',epoch=3,session_id=identity,
                                     window_id=window,marker=marker,token=token),anchor)
                status=dict(operation='binding_status',tab_id=123,window_id=4,session_id=session)
                initial=call(status)
                self.assertTrue(initial['anchor_required'] and initial['matches'])
                self.assertFalse(initial['anchor_active'])
                self.assertEqual((initial['anchor_extension_id'],initial['peer_extension_id'],initial['platform']),
                                 ('a'*32,'b'*32,'mercado-libre-mx'))
                self.assertEqual(confirm(session,4),{'ok':True,'rebind_required':False})
                guard=dict(operation='anchor_guard_open',request_id='r',session_id=session,
                           window_id=4,marker=marker,native_origin=anchor)
                self.assertIsNotNone(broker.open_anchor_guard(guard))
                self.assertTrue(call(status)['anchor_active'])
                self.assertFalse(call(dict(status,window_id=7))['matches'])
                self.assertEqual(confirm('e'*32,4),{'ok':False,'rebind_required':False})
                self.assertTrue(call(status)['anchor_active'])
                reconciled=True
                self.assertEqual(confirm('e'*32,4),{'ok':True,'rebind_required':True})
                self.assertFalse(call(status)['anchor_active'])
                self.assertFalse(call(status)['matches'])
                self.assertIsNone(broker.open_anchor_guard(dict(guard,session_id='e'*32)))
                live=False
                lost=call(status)
                self.assertIsNone(lost['request_id'])
                self.assertNotIn('anchor_extension_id',lost)

    def test_browser_restart_reidentifies_anchor_before_rebinding(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); origin='chrome-extension://'+'a'*32+'/'
            r=dict(version=1,request_id='r',nonce='a'*32,lease_epoch=1,plan_digest='b'*64,platform='mercado-libre-mx',query_index=1,query='dress')
            first=Broker(p/'s',p/'l',r,123,lambda:True,expected_window_id=4,expected_session='c'*32,anchor_origin=origin,rebind_check=lambda:True)
            old=first.anchor_registry.challenge('r',1,'c'*32,lambda:True)
            self.assertTrue(first.anchor_registry.confirm(dict(request='r',epoch=1,session='c'*32,window=4,marker='d'*32,token=old),lambda _:True,lambda:True))
            token=first.anchor_challenge(dict(operation='anchor_challenge',request_id='r',session_id='e'*32,native_origin=origin))
            self.assertTrue(first.anchor_confirm(dict(operation='anchor_confirm',request_id='r',epoch=1,session_id='e'*32,window_id=5,marker='d'*32,token=token,native_origin=origin)))
            self.assertTrue(first.rebind_requested)
            self.assertEqual(first.anchor_registry.current(),(5,'e'*32,'d'*32))

    def test_anchor_guard_reopens_after_query_broker_turnover(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); origin='chrome-extension://'+'a'*32+'/'
            base=dict(version=1,request_id='r',nonce='a'*32,lease_epoch=1,plan_digest='b'*64,platform='mercado-libre-mx',query_index=0,query='one')
            first=Broker(p/'s',p/'l',base,None,lambda:True,anchor_origin=origin)
            def confirm(broker):
                token=broker.anchor_challenge(dict(operation='anchor_challenge',request_id='r',session_id='c'*32,native_origin=origin))
                return broker.anchor_confirm(dict(operation='anchor_confirm',request_id='r',epoch=1,session_id='c'*32,window_id=4,marker='d'*32,token=token,native_origin=origin))
            self.assertTrue(confirm(first))
            guard=dict(operation='anchor_guard_open',request_id='r',session_id='c'*32,window_id=4,marker='d'*32,native_origin=origin)
            token=first.open_anchor_guard(guard);self.assertIsNotNone(token)
            first.close_anchor_guard(token);self.assertFalse(first.anchor_guard_alive())
            second=Broker(p/'s',p/'l',dict(base,nonce='e'*32,query_index=1,query='two'),123,lambda:True,expected_window_id=4,expected_session='c'*32,anchor_origin=origin,rebind_check=lambda:True)
            self.assertTrue(confirm(second));self.assertFalse(second.rebind_requested)
            self.assertIsNotNone(second.open_anchor_guard(guard));self.assertTrue(second.anchor_guard_alive())

    def test_existing_binding_allows_anchor_reconfirmation_after_broker_turnover(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); origin='chrome-extension://'+'a'*32+'/'
            r=dict(version=1,request_id='r',nonce='a'*32,lease_epoch=1,plan_digest='b'*64,platform='mercado-libre-mx',query_index=1,query='dress')
            broker=Broker(p/'s',p/'l',r,123,lambda:True,expected_window_id=4,expected_session='c'*32,anchor_origin=origin,rebind_check=lambda:True)
            old=broker.anchor_registry.challenge('r',1,'c'*32,lambda:True)
            self.assertTrue(broker.anchor_registry.confirm(dict(request='r',epoch=1,session='c'*32,window=4,marker='d'*32,token=old),lambda _:True,lambda:True))
            event=dict(operation='anchor_challenge',request_id='r',session_id='c'*32,native_origin=origin)
            token=broker.anchor_challenge(event)
            confirm=dict(operation='anchor_confirm',request_id='r',epoch=1,session_id='c'*32,window_id=4,marker='d'*32,token=token,native_origin=origin)
            self.assertTrue(broker.anchor_confirm(confirm))
            self.assertFalse(broker.rebind_requested)

    def test_peer_proof_requires_live_anchor_and_exact_tab(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);req=dict(version=1,request_id='peer',nonce='a'*32,lease_epoch=3,plan_digest='b'*64,platform='shein-us',query_index=0,query='dress')
            origin='chrome-extension://'+'a'*32+'/';peer='chrome-extension://'+'b'*32+'/'
            b=Broker(p/'s',p/'l',req,None,lambda:True,anchor_origin=origin,peer_origin=peer)
            a={'operation':'anchor_challenge','request_id':'peer','session_id':'c'*32,'native_origin':origin};token=b.anchor_challenge(a)
            self.assertTrue(b.anchor_confirm({'operation':'anchor_confirm','request_id':'peer','epoch':3,'session_id':'c'*32,'window_id':4,'marker':'d'*32,'token':token,'native_origin':origin}))
            ge={'operation':'anchor_guard_open','request_id':'peer','session_id':'c'*32,'window_id':4,'marker':'d'*32,'native_origin':origin};guard=b.open_anchor_guard(ge)
            ti={'operation':'task_tab_challenge','request_id':'peer','session_id':'c'*32,'window_id':4,'native_origin':origin};tt=b.task_tab_challenge(ti)
            self.assertTrue(b.task_tab_confirm(dict(ti,operation='task_tab_confirm',epoch=3,token=tt,tab_id=77)))
            i={'operation':'peer_challenge','request_id':'peer','session_id':'e'*32,'window_id':4,'tab_id':77,'native_origin':peer}
            t=b.peer_challenge(i);self.assertTrue(t)
            f={'operation':'peer_confirm','request_id':'peer','epoch':3,'session_id':'e'*32,'window_id':4,'tab_id':77,'token':t,'peer_id':'b'*32,'native_origin':origin}
            self.assertFalse(b.peer_confirm(dict(f,tab_id=78)))
            self.assertTrue(b.peer_confirm(f))
            self.assertFalse(b.peer_confirm(f))
            b.close_anchor_guard(guard)
            self.assertFalse(b.peer_session_valid('e'*32,4,77))
    def test_anchor_restart_requests_rebind_without_adopting_old_tab(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); request=dict(version=1,request_id='restart',nonce='a'*32,lease_epoch=3,plan_digest='b'*64,platform='mercado-libre-mx',query_index=1,query='dress')
            origin='chrome-extension://'+'a'*32+'/'
            broker=Broker(p/'s',p/'l',request,123,lambda:True,expected_window_id=4,expected_session='c'*32,rebind_check=lambda:True,anchor_origin=origin)
            import sqlite3
            with sqlite3.connect(p/'l') as db: db.execute('INSERT INTO verified_anchor VALUES (1,?,?,?)',(4,'c'*32,'d'*32))
            issue={'operation':'anchor_challenge','request_id':'restart','session_id':'e'*32,'native_origin':origin}
            token=broker.anchor_challenge(issue)
            proof={'operation':'anchor_confirm','request_id':'restart','epoch':3,'session_id':'e'*32,'window_id':8,'marker':'d'*32,'token':token,'native_origin':origin}
            self.assertTrue(broker.anchor_confirm(proof))
            self.assertTrue(broker.rebind_requested)
            self.assertEqual(broker.tab,123)
    def test_anchor_confirmation_requires_expected_extension_and_one_time_challenge(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); request=dict(version=1,request_id='anchor',nonce='a'*32,lease_epoch=3,plan_digest='b'*64,platform='mercado-libre-mx',query_index=0,query='dress')
            broker=Broker(p/'s',p/'l',request,None,lambda:True,anchor_origin='chrome-extension://'+'a'*32+'/')
            issue={'operation':'anchor_challenge','request_id':'anchor','session_id':'c'*32,'native_origin':'chrome-extension://'+'a'*32+'/'}
            token=broker.anchor_challenge(issue)
            self.assertTrue(token)
            proof={'operation':'anchor_confirm','request_id':'anchor','epoch':3,'session_id':'c'*32,'window_id':4,'marker':'d'*32,'token':token,'native_origin':issue['native_origin']}
            self.assertFalse(broker.anchor_confirm(dict(proof,native_origin='chrome-extension://'+'b'*32+'/')))
            self.assertTrue(broker.anchor_confirm(proof))
            self.assertFalse(broker.anchor_confirm(proof))
            guard_event={'operation':'anchor_guard_open','request_id':'anchor','session_id':'c'*32,'window_id':4,'marker':'d'*32,'native_origin':issue['native_origin']}
            self.assertFalse(broker.anchor_guard_alive())
            guard=broker.open_anchor_guard(guard_event)
            self.assertTrue(guard)
            self.assertTrue(broker.anchor_guard_alive())
            broker.close_anchor_guard(guard)
            self.assertFalse(broker.anchor_guard_alive())
            guard=broker.open_anchor_guard(guard_event)
            event={'operation':'select_window','request_id':'anchor','tab_id':22,'window_id':4,'session_id':'c'*32,'observed_at':datetime.now(timezone.utc).isoformat()}
            self.assertFalse(broker.select_window(dict(event,window_id=5)))
            self.assertFalse(broker.select_window(dict(event,session_id='e'*32)))
            self.assertTrue(broker.select_window(event))
            tab_issue={'operation':'task_tab_challenge','request_id':'anchor','session_id':'c'*32,'window_id':4,'native_origin':issue['native_origin']}
            tab_token=broker.task_tab_challenge(tab_issue)
            self.assertTrue(tab_token)
            tab_proof=dict(tab_issue,operation='task_tab_confirm',epoch=3,token=tab_token,tab_id=99)
            self.assertFalse(broker.task_tab_confirm(dict(tab_proof,window_id=5)))
            self.assertTrue(broker.task_tab_confirm(tab_proof))
            self.assertFalse(broker.task_tab_confirm(tab_proof))
            self.assertEqual(broker.tab,99)

    def test_task_tab_retry_restores_only_confirmed_current_browser_and_lease(self):
        for platform in ('mercado-libre-mx','shein-us'):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp)
                origin = 'chrome-extension://' + 'a'*32 + '/'
                request = dict(version=1,request_id='resume',nonce='a'*32,lease_epoch=3,
                               plan_digest='b'*64,platform=platform,query_index=0,query='dress')
                enabled = [True]
                def attach(challenge, session='c'*32):
                    broker = Broker(p/'s',p/'ledger',challenge,None,lambda:enabled[0],anchor_origin=origin)
                    issue = dict(operation='anchor_challenge',request_id=challenge['request_id'],
                                 session_id=session,native_origin=origin)
                    grant = broker.anchor_challenge(issue)
                    assertion = dict(issue,operation='anchor_confirm',epoch=challenge['lease_epoch'],
                                     window_id=4,marker='d'*32,token=grant)
                    self.assertTrue(broker.anchor_confirm(assertion))
                    anchor_guard = broker.open_anchor_guard(dict(issue,operation='anchor_guard_open',window_id=4,marker='d'*32))
                    self.assertTrue(anchor_guard)
                    return broker,dict(issue,operation='task_tab_challenge',window_id=4),anchor_guard

                broker,issue,_ = attach(request)
                token = broker.task_tab_challenge(issue)
                self.assertIsInstance(token,str)
                self.assertEqual(broker.task_tab_challenge(issue),{'ok':False,'error':'task_tab_pending'})
                self.assertTrue(broker.task_tab_confirm(dict(issue,operation='task_tab_confirm',epoch=3,tab_id=99,token=token)))
                expected = dict(ok=True,request_id='resume',epoch=3,platform=platform,window_id=4,
                                session_id='c'*32,bound_tab_id=99)
                self.assertEqual(broker.task_tab_challenge(issue),expected)
                successor,issue,guard = attach(dict(request,nonce='e'*32))
                self.assertEqual(successor.task_tab_challenge(issue),expected)
                self.assertEqual(successor.tab,99)
                for wrong in (dict(issue,request_id='other'),dict(issue,window_id=9),dict(issue,session_id='f'*32),
                              dict(issue,native_origin='chrome-extension://'+'b'*32+'/')):
                    self.assertIsNone(successor.task_tab_challenge(wrong))
                enabled[0] = False
                self.assertIsNone(successor.task_tab_challenge(issue))
                enabled[0] = True
                successor.rebind_requested = True
                self.assertIsNone(successor.task_tab_challenge(issue))
                successor.rebind_requested = False
                successor.close_anchor_guard(guard)
                self.assertIsNone(successor.task_tab_challenge(issue))
                for challenge,session in ((dict(request,lease_epoch=4),'c'*32),
                                          (dict(request,request_id='other'),'c'*32),
                                          (dict(request,plan_digest='9'*64),'c'*32),
                                          (request,'e'*32)):
                    other,other_issue,_ = attach(challenge,session)
                    self.assertIsInstance(other.task_tab_challenge(other_issue),str)

    def test_upgraded_broker_requires_accepted_receipt_on_exact_current_binding(self):
        import json,sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            origin = 'chrome-extension://' + 'a'*32 + '/'
            request = dict(version=1,request_id='upgrade',nonce='a'*32,lease_epoch=3,
                           plan_digest='b'*64,platform='mercado-libre-mx',query_index=1,query='dress')
            broker = Broker(p/'s',p/'ledger',request,None,lambda:True,anchor_origin=origin)
            issue = dict(operation='anchor_challenge',request_id='upgrade',session_id='c'*32,native_origin=origin)
            grant = broker.anchor_challenge(issue)
            self.assertTrue(broker.anchor_confirm(dict(issue,operation='anchor_confirm',epoch=3,window_id=4,
                                                       marker='d'*32,token=grant)))
            self.assertTrue(broker.open_anchor_guard(dict(issue,operation='anchor_guard_open',window_id=4,marker='d'*32)))
            issue = dict(issue,operation='task_tab_challenge',window_id=4)
            broker.tab = 99
            self.assertIsNone(broker.task_tab_challenge(issue))
            identity = dict(tab_id=99,window_id=4,session_id='c'*32,plan_digest='b'*64,platform='mercado-libre-mx')
            receipt = dict(request,collector_tab_id=99,tab_id=99,session_id='c'*32,window_id=4)
            from contextlib import closing
            with closing(sqlite3.connect(p/'ledger')) as db, db:
                db.execute('INSERT INTO bindings VALUES (?,?)',('upgrade',json.dumps(identity)))
                for changed in (dict(receipt,lease_epoch=2),dict(receipt,tab_id=98),dict(receipt,session_id='f'*32)):
                    db.execute('INSERT INTO accepted_receipts(request_id,body) VALUES (?,?)',('upgrade',json.dumps(changed)))
                    db.commit()
                    self.assertIsNone(broker.task_tab_challenge(issue))
                db.execute('INSERT INTO accepted_receipts(request_id,body) VALUES (?,?)',('upgrade',json.dumps(receipt)))
            self.assertEqual(broker.task_tab_challenge(issue)['bound_tab_id'],99)

    def test_duplicate_task_tab_request_over_native_socket_returns_only_existing_identity(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            origin = 'chrome-extension://' + 'a'*32 + '/'
            request = dict(version=1,request_id='same-task',nonce='a'*32,lease_epoch=3,
                           plan_digest='b'*64,platform='mercado-libre-mx',query_index=0,query='dress')
            with Broker(p/'s',p/'ledger',request,None,lambda:True,anchor_origin=origin) as broker:
                def deliver(message):
                    reader = threading.Thread(target=broker.serve_once)
                    reader.start()
                    try:
                        return forward(p/'s',message,origin=origin)
                    finally:
                        reader.join(2)
                base = dict(request_id='same-task',session_id='c'*32)
                anchor = deliver(dict(base,operation='anchor_challenge'))
                self.assertTrue(deliver(dict(base,operation='anchor_confirm',epoch=3,window_id=4,marker='d'*32,
                                             token=anchor['token']))['ok'])
                self.assertTrue(broker.open_anchor_guard(dict(base,operation='anchor_guard_open',window_id=4,
                                                              marker='d'*32,native_origin=origin)))
                issue = dict(base,operation='task_tab_challenge',window_id=4)
                token = deliver(issue)['token']
                self.assertEqual(deliver(issue),{'ok':False,'error':'task_tab_pending'})
                self.assertTrue(deliver(dict(issue,operation='task_tab_confirm',epoch=3,tab_id=99,token=token))['ok'])
                self.assertEqual(deliver(issue),dict(base,ok=True,epoch=3,platform='mercado-libre-mx',
                                                    window_id=4,bound_tab_id=99))
            rows = [json.loads(line) for line in (p/'collector-window-trace.jsonl').read_text().splitlines()]
            results = [r['code'] for r in rows if r['operation']=='task_tab_challenge' and r['stage']=='broker.result']
            self.assertEqual(results,['ok','task_tab_pending','task_tab_reused'])

    def test_staged_evidence_requires_matching_live_watch_and_is_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); rid='12345678-1234-4234-8234-123456789abc'
            r=dict(version=1,request_id=rid,nonce='a'*32,lease_epoch=1,plan_digest='b'*64,platform='mercado-libre-mx',query_index=2,query='dress')
            broker=Broker(p/'s',p/'l',r,123,lambda:True,expected_window_id=4,expected_session='c'*32,watch_only=True)
            msg={'operation':'stage_evidence','request_id':rid,'tab_id':123,'window_id':4,'session_id':'c'*32,'evidence':{'task_record_id':'rec'+rid.replace('-',''),'details':[]}}
            self.assertFalse(broker.stage_evidence(msg))
            broker.guard_alive=lambda *args:True
            self.assertTrue(broker.stage_evidence(msg))
            self.assertFalse(broker.stage_evidence(dict(msg,tab_id=124)))
            self.assertFalse(broker.stage_evidence(dict(msg,request_id='other')))
            self.assertFalse(broker.stage_evidence(dict(msg,evidence={'task_record_id':'recwrong'})))
            self.assertFalse(broker.stage_evidence(dict(msg,evidence=dict(msg['evidence'],details=[1]))))
    def test_recover_accepted_receipt_only_for_original_request_lease_and_tab(self):
        import json, sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); challenge=dict(version=1,request_id='r',nonce='a'*32,lease_epoch=4,plan_digest='b'*64,platform='mercado-libre-mx',query_index=1,query='other')
            with Broker(p/'s',p/'l',challenge,123,lambda:True,expected_window_id=4,expected_session='c'*32) as broker:
                receipt={**challenge,'query_index':0,'query':'dress','tab_id':123,'collector_tab_id':123,'window_id':4,'session_id':'c'*32}
                with sqlite3.connect(p/'l') as db:db.execute('insert into accepted_receipts(request_id,body) values (?,?)',('r',json.dumps(receipt)))
                msg=dict(operation='recover_receipt',request_id='r',tab_id=123,window_id=4,session_id='c'*32,query_index=0,query='dress')
                for bad in (dict(msg,request_id='other'),dict(msg,tab_id=124),dict(msg,session_id='d'*32),dict(msg,query='wrong')):
                    self.assertIsNone(broker.recover_receipt(bad))
                broker.challenge['lease_epoch']=5
                self.assertIsNone(broker.recover_receipt(msg), 'a new lease cannot reuse an old receipt')
                broker.challenge['lease_epoch']=4
                self.assertEqual(broker.recover_receipt(msg),receipt)
                self.assertEqual(broker.recover_receipt(msg),receipt, 'same fenced browser session can recover after another restart')
    def test_recover_receipt_after_first_tab_is_bound_in_current_broker(self):
        import sqlite3, json
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); challenge=dict(version=1,request_id='r',nonce='a'*32,lease_epoch=4,plan_digest='b'*64,platform='mercado-libre-mx',query_index=0,query='dress')
            with Broker(p/'s',p/'l',challenge,None,lambda:True,expected_window_id=4,expected_session='c'*32) as broker:
                broker.tab=123
                receipt={**challenge,'tab_id':123,'collector_tab_id':123,'window_id':4,'session_id':'c'*32}
                with sqlite3.connect(p/'l') as db: db.execute('insert into accepted_receipts(request_id,body) values (?,?)',('r',json.dumps(receipt)))
                msg=dict(operation='recover_receipt',request_id='r',tab_id=123,window_id=4,session_id='c'*32,query_index=0,query='dress')
                self.assertEqual(broker.recover_receipt(msg),receipt)
                self.assertIsNone(broker.recover_receipt(dict(msg,tab_id=124)))

    def test_binding_status_only_for_matching_session_and_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);r=dict(version=1,request_id='status',nonce='a'*32,lease_epoch=1,plan_digest='b'*64,platform='mercado-libre-mx',query_index=1,query='dress')
            with Broker(p/'s',p/'l',r,123,lambda:True,expected_window_id=4,expected_session='c'*32) as broker:
                stop=threading.Event()
                def serve():
                    while not stop.is_set(): broker.serve_once()
                t=threading.Thread(target=serve);t.start()
                try:
                    msg=dict(operation='binding_status',tab_id=123,window_id=4,session_id='c'*32)
                    self.assertTrue(forward(p/'s',msg)['matches'])
                    self.assertEqual(forward(p/'s',msg)['request_id'], 'status')
                    self.assertFalse(forward(p/'s',msg)['guard_required'])
                    self.assertFalse(forward(p/'s',dict(msg,tab_id=124))['matches'])
                    self.assertIsNone(forward(p/'s',dict(msg,session_id='d'*32))['bound_tab_id'])
                finally: stop.set();t.join()
    def test_guard_reconnect_only_for_matching_watch_broker(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); r=dict(version=1,request_id='watch',nonce='a'*32,lease_epoch=1,plan_digest='b'*64,platform='mercado-libre-mx',query_index=2,query='dress')
            with Broker(p/'s',p/'l',r,123,lambda:True,expected_window_id=4,expected_session='c'*32,watch_only=True) as broker:
                stop=threading.Event()
                def serve():
                    while not stop.is_set(): broker.serve_once()
                t=threading.Thread(target=serve);t.start()
                try:
                    msg=dict(operation='binding_status',tab_id=123,window_id=4,session_id='c'*32)
                    self.assertTrue(forward(p/'s',msg)['guard_required'])
                    for change in (dict(tab_id=124),dict(window_id=5),dict(session_id='d'*32)):
                        self.assertFalse(forward(p/'s',dict(msg,**change))['guard_required'])
                finally: stop.set();t.join()
    def test_sigkill_releases_lock_and_stale_socket_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            script='''
import sys, time
from pathlib import Path
from optimization_worker.tab_receipt_broker import Broker
p=Path(sys.argv[1])
r=dict(version=1,request_id='killed',nonce='a'*32,lease_epoch=1,plan_digest='b'*64,platform='mercado-libre-mx',query_index=0,query='dress')
with Broker(p/'s',p/'l',r,123,lambda:True):
 print('ready',flush=True)
 time.sleep(300)
'''
            child=subprocess.Popen([sys.executable,'-c',script,tmp],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(),'ready')
                child.kill();child.wait(timeout=5)
                self.assertTrue((p/'s').exists())
                r=dict(version=1,request_id='recovered',nonce='d'*32,lease_epoch=2,plan_digest='b'*64,platform='mercado-libre-mx',query_index=0,query='dress')
                with Broker(p/'s',p/'l',r,123,lambda:True) as broker:
                    self.assertIsNotNone(broker.sock)
                self.assertFalse((p/'s').exists())
            finally:
                if child.poll() is None: child.kill();child.wait(timeout=5)
                child.stdout.close();child.stderr.close()
    def test_stale_owned_socket_recovered_but_live_socket_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); request=dict(version=1,request_id='stale',nonce='a'*32,lease_epoch=1,plan_digest='b'*64,platform='mercado-libre-mx',query_index=0,query='dress')
            s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.bind(str(p/'s'));s.close();(p/'s').chmod(0o600)
            with Broker(p/'s',p/'l',request,123,lambda:True) as broker:
                with self.assertRaises(ValueError):
                    with Broker(p/'s',p/'other',dict(request,request_id='other'),123,lambda:True): pass
                self.assertTrue(broker.sock)
    def test_guard_heartbeat_expiry_fails_closed(self):
        from optimization_worker.native_collector_host import read_message, write_message
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); request=dict(version=1,request_id='expiry',nonce='a'*32,lease_epoch=1,plan_digest='b'*64,platform='mercado-libre-mx',query_index=0,query='dress')
            with Broker(p/'s',p/'l',request,123,lambda:True,expected_window_id=1,expected_session='c'*32) as broker:
                stop=threading.Event()
                def serve():
                    while not stop.is_set(): broker.serve_once()
                thread=threading.Thread(target=serve);thread.start()
                try:
                    sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);sock.connect(str(p/'s'));stream=sock.makefile('rwb')
                    with patch('optimization_worker.tab_receipt_broker.time.monotonic', return_value=100):
                        write_message(stream,dict(operation='guard_open',tab_id=123,window_id=1,session_id='c'*32))
                        self.assertTrue(read_message(stream)['ok']);self.assertTrue(broker.guard_alive())
                    with patch('optimization_worker.tab_receipt_broker.time.monotonic', return_value=116):
                        self.assertFalse(broker.guard_alive())
                        second=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);second.connect(str(p/'s'));second_stream=second.makefile('rwb')
                        write_message(second_stream,dict(operation='guard_open',tab_id=123,window_id=1,session_id='c'*32))
                        self.assertTrue(read_message(second_stream)['ok'])
                        self.assertTrue(broker.guard_alive())
                        stream.close();sock.close()
                        time.sleep(.02)
                        self.assertTrue(broker.guard_alive(), 'Old connection must not clear new guard')
                        second_stream.close();second.close()
                finally: stop.set();thread.join()
    def test_guard_disconnect_and_session_change_fail_closed(self):
        from optimization_worker.native_collector_host import read_message, write_message
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); request=dict(version=1,request_id='guard',nonce='a'*32,lease_epoch=1,plan_digest='b'*64,platform='mercado-libre-mx',query_index=0,query='dress')
            with Broker(p/'s',p/'l',request,123,lambda:True,expected_window_id=1,expected_session='c'*32) as broker:
                stop=threading.Event()
                def serve():
                    while not stop.is_set(): broker.serve_once()
                thread=threading.Thread(target=serve);thread.start()
                try:
                    sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);sock.connect(str(p/'s'))
                    stream=sock.makefile('rwb')
                    write_message(stream,dict(operation='guard_open',tab_id=123,window_id=1,session_id='c'*32))
                    self.assertTrue(read_message(stream)['ok'])
                    self.assertTrue(broker.guard_alive())
                    self.assertFalse(broker.guard_alive(session_id='d'*32))
                    stream.close();sock.close()
                    for _ in range(100):
                        if not broker.guard_alive(): break
                        time.sleep(.01)
                    self.assertFalse(broker.guard_alive())
                finally:
                    stop.set();thread.join()
    def test_existing_target_can_confirm_same_window_and_session_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);request=dict(version=1,request_id='r4',nonce='a'*32,lease_epoch=1,plan_digest='b'*64,platform='mercado-libre-mx',query_index=1,query='dress')
            with Broker(p/'s',p/'l',request,123,lambda:True,expected_window_id=1,expected_session='c'*32) as broker:
                event=dict(operation='select_window',request_id='r4',tab_id=123,window_id=1,session_id='c'*32,observed_at=datetime.now(timezone.utc).isoformat())
                self.assertTrue(broker.select_window(event))
                self.assertFalse(broker.select_window(dict(event,session_id='d'*32)))
                self.assertFalse(broker.rebind_requested)

    def test_new_session_requests_safe_rebind_only_after_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);request=dict(version=1,request_id='r5',nonce='a'*32,lease_epoch=1,plan_digest='b'*64,platform='mercado-libre-mx',query_index=1,query='dress')
            with Broker(p/'s',p/'l',request,123,lambda:True,expected_window_id=1,expected_session='c'*32,rebind_check=lambda:True) as broker:
                event=dict(operation='select_window',request_id='r5',tab_id=777,window_id=1,session_id='d'*32,observed_at=datetime.now(timezone.utc).isoformat())
                self.assertFalse(broker.select_window(event))
                self.assertTrue(broker.rebind_requested)
    def test_select_window_before_native_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); request=dict(version=1,request_id='r3',nonce='a'*32,lease_epoch=1,plan_digest='b'*64,platform='mercado-libre-mx',query_index=0,query='dress')
            with Broker(p/'s',p/'l',request,None,lambda:True) as broker:
                selected=dict(operation='select_window',request_id='r3',tab_id=99,window_id=1,session_id='c'*32,observed_at=datetime.now(timezone.utc).isoformat())
                self.assertFalse(broker.select_window(dict(selected,request_id='old-task')))
                self.assertTrue(broker.select_window(selected))
                self.assertFalse(broker.select_window(dict(selected,window_id=2)))
    def test_observed_native_tab_must_be_unique(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); request=dict(version=1,request_id='r2',nonce='a'*32,lease_epoch=1,plan_digest='b'*64,platform='mercado-libre-mx',query_index=0,query='dress')
            with Broker(p/'s',p/'l',request,None,lambda:True,expected_window_id=1) as broker:
                event=dict(operation='observe_tab',request_id='r2',nonce='a'*32,lease_epoch=1,plan_digest='b'*64,tab_id=123,window_id=1,session_id='c'*32,observed_at=datetime.now(timezone.utc).isoformat())
                self.assertTrue(broker.observe(event))
                self.assertIsNone(broker.poll(123), 'No selection before the entire observation window closes')
                self.assertFalse(broker.observe(dict(event,tab_id=124)))
                broker.started-=16
                self.assertIsNone(broker.poll(123))
    def test_poll_and_submit_fenced_bound_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); request=dict(version=1,request_id='r1',nonce='a'*32,lease_epoch=1,plan_digest='b'*64,platform='mercado-libre-mx',query_index=0,query='dress')
            with Broker(p/'s',p/'l',request,123,lambda:True) as broker:
                stop=threading.Event()
                def serve():
                    while not stop.is_set(): broker.serve_once()
                thread=threading.Thread(target=serve);thread.start()
                try:
                    self.assertIsNone(forward(p/'s',{'operation':'poll','request_id':'r1','tab_id':124})['challenge'])
                    self.assertEqual(forward(p/'s',{'operation':'poll','request_id':'old-task','tab_id':123})['error'],'request_changed')
                    self.assertEqual(forward(p/'s',{'operation':'poll','request_id':'r1','tab_id':123})['challenge'],request)
                    receipt=dict(request,session_id='c'*32,tab_id=123,window_id=1,collector_tab_id=123,url='https://listado.mercadolibre.com.mx/dress',observed_at=datetime.now(timezone.utc).isoformat())
                    self.assertTrue(forward(p/'s',{'operation':'submit','receipt':receipt})['ok'])
                    self.assertFalse(forward(p/'s',{'operation':'submit','receipt':receipt})['ok'])
                finally: stop.set();thread.join()

class ActivationSafetyTest(unittest.TestCase):
    def test_activation_nonce_is_bound_fresh_and_single_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); challenge=dict(version=1,request_id='r',nonce='a'*32,lease_epoch=1,plan_digest='b'*64,platform='mercado-libre-mx',query_index=0,query='dress')
            broker=Broker(p/'s',p/'l',challenge,123,lambda:True,expected_window_id=4,expected_session='c'*32)
            event=dict(operation='activation_challenge',request_id='r',tab_id=123,window_id=4,session_id='c'*32)
            issued=broker.issue_activation(event);self.assertIsNotNone(issued)
            observed=dict(operation='activation_confirm',request_id='r',tab_id=123,window_id=4,session_id='c'*32,nonce=issued['nonce'],url='https://listado.mercadolibre.com.mx/dress',query='dress',observed_at=datetime.now(timezone.utc).isoformat())
            self.assertFalse(broker.confirm_activation(dict(observed,query='different')))
            self.assertTrue(broker.confirm_activation(observed))
            self.assertFalse(broker.confirm_activation(observed))
            self.assertIsNone(broker.issue_activation(dict(event,tab_id=124)))
            self.assertIsNone(broker.issue_activation(dict(event,session_id='d'*32)))
            issued=broker.issue_activation(event)
            self.assertFalse(broker.confirm_activation(dict(observed,nonce=issued['nonce'],url='https://evil.example/dress')))
            broker._activation=(issued['nonce'],time.monotonic()-11)
            self.assertFalse(broker.confirm_activation(dict(observed,nonce=issued['nonce'])))
            issued=broker.issue_activation(event);broker.fence=lambda:False
            self.assertFalse(broker.confirm_activation(dict(observed,nonce=issued['nonce'])))

class CardsGateTest(unittest.TestCase):
    def test_watch_activation_uses_third_completed_query_and_fails_closed(self):
        import sqlite3,json
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);ch=dict(version=1,request_id='r',nonce='a'*32,lease_epoch=1,plan_digest='b'*64,platform='mercado-libre-mx',query_index=0,query='first')
            b=Broker(p/'s',p/'l',ch,1,lambda:True,expected_window_id=2,expected_session='c'*32,watch_only=True)
            event=dict(operation='activation_challenge',request_id='r',tab_id=1,window_id=2,session_id='c'*32)
            self.assertIsNone(b.issue_activation(event))
            third={**ch,'query_index':2,'query':'third','tab_id':1,'window_id':2,'session_id':'c'*32}
            with sqlite3.connect(p/'l') as db:
                db.execute('insert into accepted_receipts(request_id,body) values (?,?)',('r',json.dumps(third)))
                db.execute('insert into query_cards(request_id,plan_digest,lease_epoch,query_index,body) values (?,?,?,?,?)',('r','b'*64,1,2,json.dumps([{'query':'third'}]*30)))
            issued=b.issue_activation(event);self.assertEqual((issued['query_index'],issued['query']),(2,'third'))
            self.assertIsNone(b.issue_activation(dict(event,tab_id=9)))
            b.fence=lambda:False;self.assertIsNone(b.issue_activation(event))

    def test_receipt_does_not_complete_until_cards_and_restart_resumes(self):
        from optimization_worker.tab_receipt_store import TabReceiptStore
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);s=TabReceiptStore(str(p/'l'))
            req=dict(version=1,request_id='r',nonce='a'*32,lease_epoch=1,plan_digest='b'*64,platform='mercado-libre-mx',query_index=0,query='dress')
            receipt={**req,'session_id':'c'*32,'tab_id':1,'window_id':2,'collector_tab_id':1,'url':'https://listado.mercadolibre.com.mx/dress','observed_at':datetime.now(timezone.utc).isoformat()}
            s.issue(req);s.accept(receipt,lambda:True)
            b=Broker(p/'s',p/'l',dict(req,nonce='d'*32),1,lambda:True,expected_window_id=2,expected_session='c'*32)
            self.assertTrue(b.query_verified);self.assertFalse(b.done);self.assertIsNone(b.poll(1));self.assertEqual(s.completed_card_index('r','b'*64,1),0)
            cards=[{'query':'dress','rank':i+1,'is_ad':False,'title':'x','url':f'https://articulo.mercadolibre.com.mx/MLM-{100000+i}-item','product_id':f'MLM{100000+i}','thumbnail_url':None} for i in range(30)]
            event={'operation':'query_cards_complete','request_id':'r','tab_id':1,'window_id':2,'session_id':'c'*32,'query_index':0,'cards':cards}
            self.assertFalse(b.complete_query_cards(dict(event,tab_id=3)));self.assertTrue(b.complete_query_cards(event));self.assertTrue(b.done)
            self.assertEqual(s.completed_card_index('r','b'*64,1),1)
            nxt=Broker(p/'s',p/'l',dict(req,nonce='e'*32,query_index=1,query='other'),1,lambda:True,expected_window_id=2,expected_session='c'*32)
            self.assertTrue(nxt.complete_query_cards(event));self.assertFalse(nxt.done)
            act=nxt.issue_activation(dict(operation='activation_challenge',request_id='r',tab_id=1,window_id=2,session_id='c'*32))
            self.assertEqual(act['query'],'dress')
            observed=dict(operation='activation_confirm',request_id='r',tab_id=1,window_id=2,session_id='c'*32,nonce=act['nonce'],url=receipt['url'],query='dress',observed_at=datetime.now(timezone.utc).isoformat())
            self.assertTrue(nxt.confirm_activation(observed))

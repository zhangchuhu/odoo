import threading
import unittest
import tempfile
from pathlib import Path
from optimization_worker.research_service import ResearchService

class Tests(unittest.TestCase):
    def test_capability_diagnostics_emit_only_failed_kinds_on_change(self):
        from optimization_worker.research_service import CapabilityDiagnostics
        from optimization_worker.research_capability_probe import ProbeResult
        logs=[]; reporter=CapabilityDiagnostics(logs.append)
        for result in (ProbeResult(False,('chrome',)),ProbeResult(False,('chrome',)),ProbeResult(True,())):
            self.assertIs(reporter.report(result),result)
        self.assertEqual(logs,['Research readiness unavailable: chrome','Research readiness restored'])
    def test_expired_retry_retires_only_confirmed_original(self):
        from optimization_worker.research_service import recover_expired_original
        record={'claim':{'id':150,'lease_token':'token','lease_epoch':1},'request_id':'req','phase':'host_pending'}
        saved=[]
        class Client:
            def retry_expired_research(self,claim,request): return {'source_task_id':150,'task_id':150,'state':'queued'}
        self.assertTrue(recover_expired_original(Client(),record,saved.append))
        self.assertTrue(saved[0]['done'])
        self.assertEqual(saved[0]['outcome'],'expired_unknown_retried')
        self.assertEqual(saved[0]['replacement_task_id'],150)
        self.assertEqual(record.get('done'),None)
        class Wrong:
            def retry_expired_research(self,claim,request): return {'source_task_id':149,'task_id':151,'state':'queued'}
        with self.assertRaises(ValueError): recover_expired_original(Wrong(),record,saved.append)
        class NewTask:
            def retry_expired_research(self,claim,request): return {'source_task_id':150,'task_id':151,'state':'queued'}
        with self.assertRaises(ValueError): recover_expired_original(NewTask(),record,saved.append)
    def test_lock_rejects_second_instance(self):
        from optimization_worker.cooperative_research_host import ClaimGate,CooperativeResearchHost
        class Worker:
            def once(self,**kwargs): return 'idle'
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'lock'
            first=ResearchService.from_host(CooperativeResearchHost(ClaimGate(lambda:False),Worker(),lambda:None),path)
            second=ResearchService.from_host(CooperativeResearchHost(ClaimGate(lambda:False),Worker(),lambda:None),path)
            with first:
                with self.assertRaises(BlockingIOError): second.__enter__()

    def test_retried_task_keeps_distinct_lease_journal_entries(self):
        from optimization_worker.journal import Journal
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(Path(tmp) / 'journal.sqlite')
            try:
                old = {'claim': {'id': 150, 'input_hash': 'same', 'lease_epoch': 1},
                       'request_id': 'old', 'done': True}
                new = {'claim': {'id': 150, 'input_hash': 'same', 'lease_epoch': 2},
                       'request_id': 'new', 'done': False}
                journal.save(old)
                journal.save(new)
                self.assertEqual(journal.active(), [new])
                self.assertEqual(journal.db.execute('SELECT count(*) FROM runs').fetchone()[0], 2)
            finally:
                journal.close()

class RestartTests(unittest.TestCase):
    def test_timeout_remains_unknown_across_service_restart(self):
        import subprocess
        from optimization_worker.interactive_dispatcher import Dispatcher
        from optimization_worker.cooperative_research_host import ClaimGate,CooperativeResearchHost
        class Store:
            def next(self,**kwargs): return {'request_id':'r','task_type':'research.bestseller','action':'browse_research','state':'prepared'}
            def inspect(self,request): return {'state':'prepared'}
        class Worker:
            def once(self,**kwargs): return 'idle'
        calls=[]
        def runner(*args,**kwargs):
            calls.append(1)
            raise subprocess.TimeoutExpired(args[0],1)
        with tempfile.TemporaryDirectory() as tmp:
            journal=Path(tmp)/'j.sqlite'
            for iteration in range(2):
                dispatcher=Dispatcher(journal,Store(),runner=runner,host_probe=lambda:True)
                results=[]
                host=CooperativeResearchHost(ClaimGate(lambda:True),Worker(),lambda:results.append(dispatcher.tick()))
                with ResearchService.from_host(host,Path(tmp)/'lock') as service:
                    service.tick()
                self.assertEqual(results,['unknown'])
            self.assertEqual(len(calls),1)

class ProcessTests(unittest.TestCase):
    def test_cli_starts_waiting_and_stops_without_live_probes(self):
        import os, subprocess, sys, select, time
        with tempfile.TemporaryDirectory() as tmp:
            env={k:os.environ[k] for k in ('PATH','HOME','PYTHONPATH') if k in os.environ}
            env.update({'OPTIMIZATION_JOURNAL':tmp+'/worker.sqlite','OPTIMIZATION_HOST_SESSION':tmp+'/host.sqlite',
                'OPTIMIZATION_DISPATCH_JOURNAL':tmp+'/dispatch.sqlite','OPTIMIZATION_RESEARCH_LOCK':tmp+'/lock',
                'OPTIMIZATION_INTERACTIVE_CODEX_HOME':tmp,'OPTIMIZATION_WORKSPACE':tmp,
                'OPTIMIZATION_COMPUTER_USE_CWD':tmp,'ODOO_OPTIMIZATION_URL':'http://127.0.0.1:1',
                'ODOO_API_KEY':'dummy-not-a-real-token'})
            proc=subprocess.Popen([sys.executable,'-m','optimization_worker.research_service','loop'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
            try:
                deadline=time.monotonic()+12;states=[]
                while time.monotonic()<deadline:
                    readable,_,_=select.select([proc.stdout],[],[],max(0,deadline-time.monotonic()))
                    if not readable: break
                    states.append(proc.stdout.readline().strip())
                    if states[-1]=='queue_unavailable': break
                self.assertIn('queue_unavailable',states)
                self.assertNotIn('waiting_for_host',states)
                proc.terminate()
                self.assertEqual(proc.wait(timeout=5),0)
            finally:
                if proc.poll() is None: proc.kill();proc.wait()
                proc.stdout.close();proc.stderr.close()

class SlowProbeLeaseTests(unittest.TestCase):
    def test_real_session_worker_heartbeats_while_probe_blocks(self):
        from optimization_worker.cooperative_research_host import ClaimGate,GatedClient,CooperativeResearchHost
        from optimization_worker.host_session import SessionProvider
        from optimization_worker.runtime import Worker
        from optimization_worker.journal import Journal
        probe_started=threading.Event();probe_release=threading.Event();heartbeat=threading.Event();terminal=threading.Event()
        class Client:
            identity='fixture';timeout=1
            def heartbeat(self,claim): heartbeat.set()
            def submit(self,claim,operation,envelope): return {'state':'failed'}
            def claim(self,*a,**k): self.fail('unexpected claim')
        class Store:
            def synchronize(self,records): pass
            def enqueue(self,*args): pass
            def inspect(self,request): return {'state':'terminal','reason':'test_done'} if terminal.is_set() else {'state':'prepared'}
            def result(self,request): return None
        def slow_probe(): probe_started.set();probe_release.wait(3);return False
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'j.sqlite';client=Client();gate=ClaimGate(slow_probe)
            claim={'id':1,'task_type':'research.bestseller','lease_epoch':1,'lease_token':'fake','heartbeat_seconds':0.02,'payload':{}}
            journal=Journal(path,client.identity)
            journal.save({'claim':claim,'phase':'host_pending','request_id':'r','sequence':0,'context':{}});journal.close()
            worker=Worker(GatedClient(client,gate),path,providers={'research.bestseller':SessionProvider(Store())},capabilities=('research.bestseller',))
            host=CooperativeResearchHost(gate,worker,lambda:None,poll_seconds=0.02)
            service=ResearchService.from_host(host,lock_path=Path(tmp)/'service.lock')
            with service:
                tick=threading.Thread(target=service.tick);tick.start()
                self.assertTrue(probe_started.wait(1));self.assertTrue(heartbeat.wait(1))
                heartbeat.clear();self.assertTrue(heartbeat.wait(1))
                terminal.set();probe_release.set();tick.join(2)

class ShutdownLockTests(unittest.TestCase):
    def test_lock_held_until_dispatch_and_worker_terminate(self):
        from optimization_worker.cooperative_research_host import ClaimGate,CooperativeResearchHost
        entered=threading.Event();release=threading.Event();closed=threading.Event()
        class Worker:
            def once(self,**kwargs): return 'idle'
        def dispatch(): entered.set();release.wait(2)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'lock';host=CooperativeResearchHost(ClaimGate(lambda:True),Worker(),dispatch,poll_seconds=.02)
            service=ResearchService.from_host(host,path);service.__enter__()
            tick=threading.Thread(target=service.tick);tick.start();self.assertTrue(entered.wait(1))
            def shutdown(): service.__exit__(None,None,None);closed.set()
            closer=threading.Thread(target=shutdown);closer.start()
            self.assertFalse(closed.wait(.1))
            contender=ResearchService.from_host(CooperativeResearchHost(ClaimGate(lambda:False),Worker(),lambda:None),path)
            with self.assertRaises(BlockingIOError): contender.__enter__()
            release.set();tick.join(2);closer.join(2);self.assertTrue(closed.is_set())

class AdapterTimeoutTests(unittest.TestCase):
    def test_adapter_timeout_is_independently_configurable_and_bounded(self):
        from optimization_worker.research_service import adapter
        with tempfile.TemporaryDirectory() as tmp:
            executable=Path(tmp)/'probe';executable.write_text('#!/bin/sh\nexit 0\n');executable.chmod(0o700)
            self.assertTrue(callable(adapter(str(executable),timeout=120)))
            for timeout in (0,301,float('inf')):
                with self.assertRaises(ValueError): adapter(str(executable),timeout=timeout)

    def test_adapter_passes_selected_timeout_and_excludes_credentials(self):
        import subprocess
        from unittest.mock import patch
        from optimization_worker.research_service import adapter
        with tempfile.TemporaryDirectory() as tmp:
            executable=Path(tmp)/'probe';executable.write_text('#!/bin/sh\nexit 0\n');executable.chmod(0o700)
            def runner(args,**kwargs):
                self.assertEqual(kwargs['timeout'],115)
                self.assertNotIn('ODOO_API_KEY',kwargs['env'])
                return subprocess.CompletedProcess(args,0,b'{"ok":true}')
            with patch('optimization_worker.research_service.subprocess.run',side_effect=runner),patch.dict('os.environ',{'ODOO_API_KEY':'secret'}):
                self.assertEqual(adapter(str(executable),timeout=115)('nonce'),{'ok':True})

class LiveAdaptersTests(unittest.TestCase):
    def test_actual_browser_and_vision_probe_adapters_are_called(self):
        from unittest.mock import patch
        from optimization_worker.research_service import build_live_adapters
        cfg={'home':'/h','cwd':'/w','computer_use_cwd':'/c','proxy':'http://127.0.0.1:7897'}
        with patch('optimization_worker.chrome_live_probe.probe_chrome',return_value=True) as chrome, patch('optimization_worker.multimodal_probe.probe_multimodal',return_value=True) as vision:
            probes=build_live_adapters(cfg,object(),{'chrome':90,'vision':90,'callback':120},now=lambda:123)
            self.assertTrue(probes['chrome']('nonce')['ok']);self.assertTrue(probes['vision']('nonce')['ok'])
            chrome.assert_called_once();vision.assert_called_once()

class CaseBoundCallbackTests(unittest.TestCase):
    def test_both_transport_and_authorized_orm_are_required(self):
        from unittest.mock import patch
        from optimization_worker.research_service import build_live_adapters
        from optimization_worker.callback_transport_probe import CallbackDiagnostics
        cfg={'home':'h','cwd':'w','computer_use_cwd':'c','proxy':''}
        settings={'chrome':90,'vision':90,'callback':120}
        for transport,orm,expected in ((True,True,True),(True,False,False),(False,True,False)):
            with patch('optimization_worker.callback_transport_probe.probe_diagnostics',return_value=CallbackDiagnostics(transport,orm)) as probe:
                evidence=build_live_adapters(cfg,object(),settings,case_id=42,now=lambda:123)['callback']('nonce')
                self.assertEqual(evidence['ok'],expected)
                self.assertFalse(evidence['task_completion_verified'])
                self.assertEqual(probe.call_args.args[2],42)
    def test_missing_case_never_authorizes_callback(self):
        from optimization_worker.research_service import build_live_adapters
        cfg={'home':'h','cwd':'w','computer_use_cwd':'c','proxy':''}
        evidence=build_live_adapters(cfg,object(),{'chrome':90,'vision':90,'callback':120},now=lambda:123)['callback']('nonce')
        self.assertFalse(evidence['ok'])
        self.assertFalse(evidence['task_completion_verified'])

class TimingConfigTests(unittest.TestCase):
    def test_default_and_configured_idle_and_readiness_intervals(self):
        from optimization_worker.research_service import timing_config
        self.assertEqual(timing_config({}),(30.0,60.0))
        self.assertEqual(timing_config({'OPTIMIZATION_PROBE_CACHE_TTL':'7','OPTIMIZATION_IDLE_POLL_SECONDS':'45'}),(7.0,45.0))
        for setting,value in (('OPTIMIZATION_PROBE_CACHE_TTL','31'),('OPTIMIZATION_IDLE_POLL_SECONDS','2'),('OPTIMIZATION_IDLE_POLL_SECONDS','nan')):
            with self.assertRaises(ValueError): timing_config({setting:value})

class PreparedPlanIsolationTests(unittest.TestCase):
    def test_intact_plan_is_preserved_and_only_its_request_is_isolated(self):
        from optimization_worker.research_service import isolate_prepared_research
        from optimization_worker.domain import canonical_hash
        from contextlib import nullcontext
        class Store:
            def __init__(self):
                import re,json
                doc=(Path(__file__).resolve().parents[1]/'RESEARCH_BRIDGE.md').read_text();plan=json.loads(re.findall(r'```json\n(.*?)\n```',doc,re.S)[-1])
                payload={'site_id':'MLM'}; snap={'case_id':9,'snapshot_id':8,'company_id':1}
                binding={'request_id':'r','company_id':1,'case_id':9,'snapshot_id':8,'input_hash':canonical_hash(payload),'plan':plan}
                binding['digest']=canonical_hash(binding)
                self.record={'request_id':'r','task_type':'research.bestseller','state':'prepared','payload':payload,'context':{'snapshot':snap},'collector_plan':binding}
            def _db(self):return nullcontext(None)
            def _get(self,db,request):return self.record.copy()
            def _put(self,db,record):self.record=record
        store=Store();plan=store.record['collector_plan'].copy()
        self.assertTrue(isolate_prepared_research(store,'r'))
        self.assertEqual(store.record['state'],'unknown');self.assertEqual(store.record['collector_plan'],plan)
    def test_pending_or_tampered_plan_does_not_isolate(self):
        from optimization_worker.research_service import isolate_prepared_research
        from contextlib import nullcontext
        class Store:
            def __init__(self,record):self.record=record
            def _db(self):return nullcontext(None)
            def _get(self,db,request):return self.record.copy()
            def _put(self,db,record):raise AssertionError('must not mutate')
        for r in ({'task_type':'research.bestseller','state':'prepared','collector_pending':{'artifact_id':1}}, {'task_type':'research.bestseller','state':'prepared','collector_plan':{'digest':'forged'}}):
            self.assertFalse(isolate_prepared_research(Store(r),'r'))
class ReceiptRecoveryTests(unittest.TestCase):
    def test_interrupted_image_lookup_uses_original_identity_and_order(self):
        from optimization_worker.research_service import settle_interrupted_upload
        from unittest.mock import Mock, patch
        store = Mock()
        store.inspect.return_value = {'state': 'candidate_images_pending', 'collector_pending': {
            'result': {'candidates': [{'platform': 'mercado-libre-mx', 'item_id': 'MLM123', 'url': 'url'}]},
            'gallery_manifests': {'mercado-libre-mx:MLM123': {'images': ['image']}},
            'candidate_uploads': {'mercado-libre-mx:MLM123:0': {'state': 'unknown'}},
        }}
        client, claim = Mock(), {'id': 3}
        with patch('optimization_worker.host_session.worker_records', return_value=[{'request_id': 'r', 'claim': claim}]), \
             patch('optimization_worker.host_session.fence'), patch('optimization_worker.candidate_image_upload.reconcile') as lookup:
            self.assertTrue(settle_interrupted_upload(store, client, 'journal', 'r'))
            self.assertEqual(lookup.call_args.args[:5], (store, 'r', claim, 'mercado-libre-mx:MLM123', None))
            self.assertEqual(lookup.call_args.kwargs, {'order': 0})


class TargetedStateTests(unittest.TestCase):
    def test_rejects_foreign_journal_or_orphan_host_requests(self):
        import json, sqlite3
        from contextlib import closing
        from optimization_worker.research_service import validate_targeted_state
        with tempfile.TemporaryDirectory() as tmp:
            worker, host = Path(tmp)/'worker', Path(tmp)/'host'
            with closing(sqlite3.connect(worker)) as db, db:
                db.execute('CREATE TABLE runs (id TEXT, record TEXT)')
                db.execute('INSERT INTO runs VALUES (?,?)', ('a',json.dumps({'claim':{'id':55},'request_id':'r'})))
            with closing(sqlite3.connect(host)) as db, db:
                db.execute('CREATE TABLE requests (id TEXT, record TEXT)')
                db.execute('INSERT INTO requests VALUES (?,?)', ('r','{}'))
            validate_targeted_state(worker,host,55)
            with closing(sqlite3.connect(host)) as db, db: db.execute("INSERT INTO requests VALUES ('other','{}')")
            with self.assertRaises(ValueError): validate_targeted_state(worker,host,55)
            with closing(sqlite3.connect(host)) as db, db: db.execute("DELETE FROM requests WHERE id='other'")
            with self.assertRaises(ValueError): validate_targeted_state(worker,host,56)

class TargetedLockTests(unittest.TestCase):
    def test_validation_runs_after_exclusive_lock(self):
        import fcntl, os
        with tempfile.TemporaryDirectory() as tmp:
            lock=Path(tmp)/'lock'
            def verify():
                fd=os.open(lock,os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError): fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                finally: os.close(fd)
                raise ValueError('foreign task')
            service=ResearchService.from_host(object(),lock,validate=verify)
            with self.assertRaises(ValueError): service.__enter__()
            fd=os.open(lock,os.O_RDWR)
            try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            finally: os.close(fd)
class ScopedResumePreflightTest(unittest.TestCase):
    def test_targeted_restart_recovers_same_registered_request_after_lost_ack(self):
        from optimization_worker.client import APIError
        from optimization_worker.host_session import SessionStore
        from optimization_worker.journal import Journal
        from optimization_worker.research_service import continue_scoped_startup
        from optimization_worker.runtime import Worker
        from optimization_worker.host_session import SessionProvider
        from unittest.mock import Mock

        class Client:
            identity = 'test'
            timeout = 1
            coordinated = []
            fail_once = True
            fresh_token = 'rotated'
            research_ledger_enabled = True

            def request(self, method, path, body):
                self.asserted = (path, body)
                return dict(task_id=240, scoped_worker=True, non_admin=True, legacy_artifact_denied=True)

            def continue_registered_research(self, claim, request, nonce):
                self.coordinated.append((request, claim['lease_token'], nonce))
                if self.fail_once:
                    self.fail_once = False
                    raise APIError()
                return dict(request_id=request, claim=dict(claim,lease_token=self.fresh_token))

            def heartbeat(self, claim):
                if claim['lease_token'] != self.fresh_token:
                    raise APIError(409)

        with tempfile.TemporaryDirectory() as directory:
            client = Client()
            journal_path = Path(directory) / 'worker.sqlite'
            store = SessionStore(Path(directory) / 'host.sqlite',client.identity)
            j = Journal(journal_path,client.identity)
            dispatcher = Mock()
            claim = dict(id=240,task_type='research.bestseller',company_id=1,input_hash='hash',
                         snapshot_id=257,payload={},lease_epoch=12,lease_token='old',heartbeat_seconds=30)
            context = {'snapshot': {'id':257}, 'artifacts':{}}
            store.enqueue('original', claim['task_type'], claim['payload'], context)
            j.save(dict(claim=claim,phase='host_pending',context=context,request_id='original',sequence=1))
            with self.assertRaises(ValueError):
                continue_scoped_startup(client,242,store,j,dispatcher)
            with self.assertRaises(APIError):
                continue_scoped_startup(client,240,store,j,dispatcher)
            dispatcher.acknowledge_reconciliation.assert_not_called()
            j.close()
            j = Journal(journal_path,client.identity)
            try:
                self.assertTrue(continue_scoped_startup(client,240,store,j,dispatcher))
                dispatcher.acknowledge_reconciliation.assert_called_once_with('original')
                self.assertEqual(client.coordinated[0],client.coordinated[1])
                recovered = j.active()[0]
                self.assertEqual(recovered['claim']['lease_epoch'],12)
                self.assertEqual(recovered['claim']['lease_token'],'rotated')
                self.assertNotIn('scoped_continue_intent',recovered)
            finally:
                j.close()
            worker = Worker(client,journal_path,providers={'research.bestseller': SessionProvider(store)},
                            capabilities=('research.bestseller',),task_id=240)
            self.assertEqual(worker.once(allow_claim=False),'host_pending')
            self.assertEqual(store.request_ids(),{'original'})
            with store._db() as db:
                row=store._get(db,'original')
                row.update(state='collector_upload_unknown',collector_pending={'ledger_v1':True})
                store._put(db,row)
            j=Journal(journal_path,client.identity)
            try:
                dispatcher.reset_mock()
                self.assertTrue(continue_scoped_startup(client,240,store,j,dispatcher))
                dispatcher.acknowledge_reconciliation.assert_not_called()
            finally:
                j.close()

    def test_automatic_queue_uses_original_active_task_for_resume(self):
        from optimization_worker.research_service import validate_scoped_preflight
        rows=[{'request_id':'r1','claim':{'id':84,'lease_token':'token','lease_epoch':2}}]
        calls=[]
        def preflight(client, task_id, **kw): calls.append((task_id,kw)); return True
        validate_scoped_preflight(object(),None,229,rows,preflight)
        self.assertEqual(calls[0][0],84)
        self.assertEqual(calls[0][1]['resume']['request_id'],'r1')

    def test_ambiguous_and_wrong_target_fail_closed(self):
        from optimization_worker.research_service import validate_scoped_preflight
        row={'request_id':'r','claim':{'id':84,'lease_token':'t','lease_epoch':2}}
        with self.assertRaises(ValueError): validate_scoped_preflight(object(),None,229,[row,row],lambda *a,**k:True)
        with self.assertRaises(ValueError): validate_scoped_preflight(object(),85,229,[row],lambda *a,**k:True)
        with self.assertRaises(ValueError): validate_scoped_preflight(object(),None,229,[row],lambda *a,**k:False)

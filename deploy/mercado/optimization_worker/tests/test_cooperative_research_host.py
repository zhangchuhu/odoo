import threading
import unittest
from optimization_worker.cooperative_research_host import ClaimGate, GatedClient, CooperativeResearchHost

class Client:
    identity='fixture'
    def __init__(self): self.calls=[]
    def claim(self, capabilities, task_id=None): self.calls.append((capabilities,task_id)); return False

class CooperativeTest(unittest.TestCase):
    def test_transport_fault_recovers_and_shutdown_keeps_pending_checkpoint(self):
        from optimization_worker.client import APIError
        recovered = threading.Event()
        class Worker:
            attempts = 0
            def once(self, **kwargs):
                self.attempts += 1
                if self.attempts == 1:
                    raise APIError(503)
                recovered.set()
                return 'host_pending'
        gate = ClaimGate(lambda: True)
        host = CooperativeResearchHost(gate, Worker(), lambda: None, poll_seconds=.01)
        try:
            host.tick()
            self.assertTrue(recovered.wait(1))
            self.assertTrue(gate.ready())
            self.assertFalse(host.worker_failed.is_set())
        finally:
            host.stop_new_claims()
            host.worker_thread.join(1)
        self.assertFalse(host.worker_thread.is_alive())

    def test_dispatch_preserves_original_on_transient_reconciliation_transport(self):
        from optimization_worker.client import APIError
        class Worker:
            def once(self, **kwargs): return 'idle'
        calls = []
        def reconcile():
            calls.append(True)
            if len(calls) == 1:
                raise APIError(503)
            return True
        gate = ClaimGate(lambda: True)
        host = CooperativeResearchHost(gate, Worker(), lambda: 'unknown', isolate_unknown=reconcile)
        try:
            self.assertEqual(host.tick(), 'waiting')
            self.assertTrue(gate.ready())
            self.assertEqual(host.tick(), 'running')
            self.assertEqual(len(calls), 2)
        finally:
            host.stop_new_claims()
            host.worker_thread.join(1)

    def test_dispatch_failure_logs_stage_and_exception_type_without_sensitive_message(self):
        finish = threading.Event()
        class Worker:
            def once(self, **kwargs): finish.wait(1); return 'idle'
        def fail(): raise ValueError('secret token')
        host = CooperativeResearchHost(ClaimGate(lambda: True), Worker(), fail)
        try:
            with self.assertLogs('optimization_worker.cooperative_research_host', level='ERROR') as captured:
                self.assertEqual(host.tick(), 'reconciliation_required')
            self.assertIn('dispatch', captured.output[0])
            self.assertIn('ValueError', captured.output[0])
            self.assertNotIn('secret token', captured.output[0])
        finally:
            host.stop_new_claims(); finish.set(); host.worker_thread.join(1)

    def test_each_claim_checks_probe_and_failure_blocks_claim(self):
        state=[True]; raw=Client(); gate=ClaimGate(lambda:state[0]); client=GatedClient(raw,gate)
        self.assertIs(client.claim(['research.bestseller'],task_id=9),False)
        state[0]=False
        self.assertIs(client.claim(['research.bestseller'],task_id=9),False)
        self.assertEqual(len(raw.calls),1)
        state[0]=True
        client.claim(['research.bestseller'],task_id=9)
        self.assertEqual(len(raw.calls),2)

    def test_recovery_preserves_inflight_worker_when_probe_fails(self):
        state=[True]; started=threading.Event(); finish=threading.Event(); dispatched=[]
        class Worker:
            def once(self, **kwargs): started.set(); finish.wait(2); return 'idle'
        host=CooperativeResearchHost(ClaimGate(lambda:state[0]),Worker(),lambda:dispatched.append(True))
        try:
            host.tick(); self.assertTrue(started.wait(1)); self.assertEqual(dispatched,[True])
            state[0]=False; host.tick()
            self.assertTrue(host.worker_thread.is_alive())
            self.assertEqual(dispatched,[True])
            host.stop_new_claims(); finish.set(); host.worker_thread.join(1)
        finally: host.stop_new_claims(); finish.set()

    def test_process_restart_rechecks_probe_and_does_not_assume_ready(self):
        state=[False]; called=[]; raw=Client(); gate=ClaimGate(lambda:state[0]); client=GatedClient(raw,gate)
        class Worker:
            def once(self, **kwargs):
                if kwargs['allow_claim']: client.claim(['research.bestseller'],task_id=9)
                return 'idle'
        host=CooperativeResearchHost(gate,Worker(),lambda:called.append('dispatch'))
        self.assertEqual(host.tick(),'waiting'); host.stop_new_claims(); host.worker_thread.join(1)
        self.assertEqual(raw.calls,[]); self.assertNotIn('dispatch',called)

    def test_worker_failure_requires_reconciliation_and_no_new_dispatch(self):
        failed=threading.Event(); calls=[]
        class Worker:
            def once(self, **kwargs): failed.set(); raise RuntimeError('sensitive')
        host=CooperativeResearchHost(ClaimGate(lambda:True),Worker(),lambda:calls.append('dispatch'))
        host.tick(); self.assertTrue(failed.wait(1)); host.worker_thread.join(1)
        before=len(calls)
        self.assertEqual(host.tick(),'reconciliation_required')
        self.assertEqual(len(calls),before)

    def test_slow_dispatch_and_shutdown_keep_inflight_worker_heartbeat_alive(self):
        heartbeat=threading.Event(); finish=threading.Event(); dispatch_started=threading.Event(); release=threading.Event()
        raw=Client(); gate=ClaimGate(lambda:True); client=GatedClient(raw,gate)
        class Worker:
            def once(self, **kwargs):
                while not finish.wait(0.01): heartbeat.set()
                return 'idle'
        def dispatch(): dispatch_started.set(); release.wait(2)
        host=CooperativeResearchHost(gate,Worker(),dispatch)
        outer=threading.Thread(target=host.tick)
        try:
            outer.start(); self.assertTrue(dispatch_started.wait(1)); self.assertTrue(heartbeat.wait(1))
            self.assertFalse(host.stop_new_claims())
            client.claim(['research.bestseller'],task_id=9)
            self.assertEqual(raw.calls,[])
            self.assertTrue(host.worker_thread.is_alive())
        finally:
            finish.set(); release.set(); outer.join(1)
            if host.worker_thread: host.worker_thread.join(1)

    def test_dispatch_failure_revokes_claims_and_preserves_worker(self):
        started=threading.Event(); finish=threading.Event(); raw=Client(); gate=ClaimGate(lambda:True)
        class Worker:
            def once(self, **kwargs): started.set(); finish.wait(1); return 'idle'
        host=CooperativeResearchHost(gate,Worker(),lambda: (_ for _ in ()).throw(RuntimeError('sensitive')))
        try:
            self.assertEqual(host.tick(),'reconciliation_required')
            self.assertTrue(started.wait(1))
            GatedClient(raw,gate).claim(['research.bestseller'],task_id=9)
            self.assertEqual(raw.calls,[])
        finally:
            host.stop_new_claims(); finish.set(); host.worker_thread.join(1)

class IdleProbeTests(unittest.TestCase):
    def test_idle_ticks_do_not_probe_or_dispatch_when_no_host_request(self):
        counts=[]
        class Worker:
            def once(self,**kwargs): return 'idle'
        host=CooperativeResearchHost(ClaimGate(lambda:counts.append('probe') or True),Worker(),lambda:counts.append('dispatch'),dispatch_needed=lambda:False,idle_poll_seconds=30)
        try:
            self.assertEqual(host.tick(),'waiting')
            self.assertEqual(host.tick(),'waiting')
            self.assertEqual(counts,[])
        finally:
            host.stop_new_claims();host.worker_thread.join(1)

    def test_idle_worker_polls_less_frequently_and_stop_wakes_it(self):
        import time
        calls=[];entered=threading.Event()
        class Worker:
            def once(self,**kwargs): calls.append(1);entered.set();return 'idle'
        host=CooperativeResearchHost(ClaimGate(lambda:True),Worker(),lambda:None,poll_seconds=.02,idle_poll_seconds=.3,dispatch_needed=lambda:False)
        try:
            host.tick();self.assertTrue(entered.wait(1));time.sleep(.1)
            self.assertEqual(len(calls),1)
        finally:
            host.stop_new_claims();host.worker_thread.join(1)
        self.assertFalse(host.worker_thread.is_alive())

    def test_new_host_request_triggers_probe_and_dispatch(self):
        state=[False];calls=[]
        class Worker:
            def once(self,**kwargs): return 'idle'
        host=CooperativeResearchHost(ClaimGate(lambda:calls.append('probe') or True),Worker(),lambda:calls.append('dispatch'),dispatch_needed=lambda:state[0])
        try:
            self.assertEqual(host.tick(),'waiting');state[0]=True
            self.assertEqual(host.tick(),'running');self.assertEqual(calls,['probe','dispatch'])
        finally:
            host.stop_new_claims();host.worker_thread.join(1)

class QueuePrecheckTests(unittest.TestCase):
    def test_absent_queue_skips_probe_and_claim(self):
        calls=[]
        class Client:
            def claim(self,*args,**kwargs): calls.append('claim');return False
        gate=ClaimGate(lambda:calls.append('probe') or True)
        client=GatedClient(Client(),gate,queue_check=lambda:False)
        self.assertFalse(client.claim(['research.bestseller']))
        self.assertEqual(calls,[])
    def test_queue_failure_fails_closed_and_non_boolean_rejected(self):
        calls=[]
        class Client:
            def claim(self,*args,**kwargs): calls.append('claim');return False
        gate=ClaimGate(lambda:calls.append('probe') or True)
        for check in (lambda: 1, lambda: (_ for _ in ()).throw(RuntimeError())):
            client=GatedClient(Client(),gate,queue_check=check)
            self.assertFalse(client.claim(['research.bestseller']))
        self.assertEqual(calls,[])
    def test_queued_rechecks_probe_then_claim(self):
        calls=[]
        class Client:
            def claim(self,*args,**kwargs):calls.append('claim');return False
        gate=ClaimGate(lambda:calls.append('probe') or True)
        GatedClient(Client(),gate,queue_check=lambda:True).claim(['research.bestseller'])
        self.assertEqual(calls,['probe','claim'])

    def test_client_requires_exact_boolean_response(self):
        from optimization_worker.client import Client
        client=Client('http://127.0.0.1:1','test')
        client.request=lambda *args:{'claimable':True}
        self.assertTrue(client.has_claimable_research())
        client.request=lambda *args:{'claimable':1}
        with self.assertRaises(ValueError):client.has_claimable_research()

class DispatchUnknownTests(unittest.TestCase):
    def test_unknown_stops_new_claims_and_reports_reconciliation(self):
        calls=[]
        class Worker:
            def once(self,**kwargs): return 'idle'
        gate=ClaimGate(lambda:True)
        host=CooperativeResearchHost(gate,Worker(),lambda:'unknown')
        try:
            self.assertEqual(host.tick(),'reconciliation_required')
            self.assertFalse(gate.ready())
        finally:
            host.stop_new_claims();host.worker_thread.join(1)

class QueueStatusTests(unittest.TestCase):
    def test_empty_failed_and_unavailable_are_distinct(self):
        class Client:
            def claim(self,*args,**kwargs): return False
        gate=ClaimGate(lambda:False)
        no_work=GatedClient(Client(),gate,queue_check=lambda:False)
        self.assertFalse(no_work.claim(['research.bestseller']))
        self.assertEqual(no_work.queue_status(),'idle')
        no_host=GatedClient(Client(),gate,queue_check=lambda:True)
        self.assertFalse(no_host.claim(['research.bestseller']))
        self.assertEqual(no_host.queue_status(),'waiting_for_host')
        def broken():raise OSError('redact')
        unavailable=GatedClient(Client(),gate,queue_check=broken)
        self.assertFalse(unavailable.claim(['research.bestseller']))
        self.assertEqual(unavailable.queue_status(),'queue_unavailable')

    def test_idle_status_does_not_trigger_probe(self):
        calls=[]
        class Worker:
            def once(self,**kwargs):return 'idle'
        host=CooperativeResearchHost(ClaimGate(lambda:calls.append(1) or False),Worker(),lambda:None,dispatch_needed=lambda:False,status_provider=lambda:'idle')
        try:self.assertEqual(host.tick(),'idle');self.assertEqual(calls,[])
        finally:host.stop_new_claims();host.worker_thread.join(1)

    def test_existing_pending_task_is_not_reported_as_empty_queue(self):
        started=threading.Event()
        class Worker:
            def once(self,**kwargs):started.set();return 'host_pending'
        host=CooperativeResearchHost(ClaimGate(lambda:False),Worker(),lambda:None,dispatch_needed=lambda:False,status_provider=lambda:'checking_queue',poll_seconds=.02)
        try:
            host.tick();self.assertTrue(started.wait(1));self.assertEqual(host.tick(),'running')
        finally:
            # The fake pending task must complete before a cooperative stop can finish.
            host.worker.once=lambda **kwargs:'idle'
            host.stop_new_claims();host.worker_thread.join(1)

class IsolatedUnknownTests(unittest.TestCase):
    def test_unknown_isolated_callback_keeps_other_claims_enabled(self):
        events=[]
        class Worker:
            def once(self,**kwargs): return 'idle'
        gate=ClaimGate(lambda:True)
        host=CooperativeResearchHost(gate,Worker(),lambda:'unknown',isolate_unknown=lambda: events.append('isolated') or True)
        try:
            self.assertEqual(host.tick(),'running')
            self.assertEqual(events,['isolated'])
            self.assertTrue(gate.ready())
        finally:
            host.stop_new_claims();host.worker_thread.join(1)

import threading
import unittest
import time
from optimization_worker.tab_receipt_supervisor import NativeSupervisor

class FakeBroker:
    def __init__(self): self.done=False
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def serve_once(self): self.done=True; time.sleep(.001)

class SupervisorTest(unittest.TestCase):
    def test_broker_exception_records_safe_diagnostic(self):
        events=[]
        class Broken(FakeBroker):
            def serve_once(self): raise RuntimeError('secret credentials')
        s=NativeSupervisor(lambda *a, **k: Broken(), diagnostic=lambda *a: events.append(a))
        s.start();s.thread.join(2)
        self.assertEqual(s.error, 'RuntimeError')
        self.assertIn(('supervisor','broker_exception','RuntimeError'),events)
        self.assertNotIn('secret credentials',str(events))
    def test_window_selection_report_requires_live_fenced_broker(self):
        s=NativeSupervisor(lambda *args, **kwargs: None)
        class Broker:
            window=42; session='a'*32; started=time.monotonic(); fence=lambda self: True
        s.active_broker=Broker()
        result=s.window_selection_status()
        self.assertTrue(result['registered']); self.assertEqual(result['window_id'],42)
        self.assertGreater(result['observation_seconds_remaining'],0)
        s.active_broker.fence=lambda: False
        self.assertFalse(s.window_selection_status()['registered'])
        s.active_broker=None
        self.assertFalse(s.window_selection_status()['registered'])
    def test_confirmation_requires_current_live_guard(self):
        supervisor=NativeSupervisor(lambda *args, **kwargs: None)
        supervisor.live_confirmed=True; supervisor.confirmed_at=time.monotonic()
        self.assertFalse(supervisor.fresh_confirmation())
        class Guard:
            alive=True
            def guard_alive(self): return self.alive
        guard=Guard();supervisor.active_broker=guard
        self.assertTrue(supervisor.fresh_confirmation())
        guard.alive=False
        self.assertFalse(supervisor.fresh_confirmation())
        supervisor.active_broker=None
        self.assertFalse(supervisor.fresh_confirmation())
    def test_completed_receipts_watch_session_then_rebind(self):
        seen=[]
        class Changed(FakeBroker):
            def __init__(self): super().__init__();self.rebind_requested=False
            def serve_once(self): self.rebind_requested=True
        def factory(i, *, rebind=False, watch_only=False):
            seen.append((i,rebind,watch_only));return Changed() if len(seen)==1 else FakeBroker()
        s=NativeSupervisor(factory,start_index=3);s.start()
        for _ in range(200):
            if len(seen)>=5: break
            time.sleep(.01)
        s.close()
        self.assertEqual(seen,[(0,False,True),(0,True,False),(1,False,False),(2,False,False),(0,False,True)])
    def test_rebind_restarts_with_first_query(self):
        seen=[]
        class RebindBroker(FakeBroker):
            def __init__(self): super().__init__();self.rebind_requested=False
            def serve_once(self): self.rebind_requested=True
        def factory(i, *, rebind=False, watch_only=False):
            seen.append((i,rebind)); return RebindBroker() if len(seen)==1 else FakeBroker()
        s=NativeSupervisor(factory,start_index=2);s.start()
        for _ in range(200):
            if len(seen)>=5: break
            time.sleep(.01)
        s.close()
        self.assertEqual(seen,[(2,False),(0,True),(1,False),(2,False),(0,False)])
    def test_starts_three_brokers_in_order(self):
        seen=[]
        def factory(i, *, rebind=False, watch_only=False): seen.append(i);return FakeBroker()
        supervisor=NativeSupervisor(factory);supervisor.start()
        for _ in range(200):
            if len(seen)>=4: break
            time.sleep(.01)
        supervisor.close()
        self.assertEqual(seen,[0,1,2,0]); self.assertFalse(supervisor.thread.is_alive())

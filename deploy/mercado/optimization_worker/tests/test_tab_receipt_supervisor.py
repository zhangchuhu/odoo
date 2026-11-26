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
    def test_page_supervisor_switches_only_after_durable_page_completion(self):
        from optimization_worker.tab_receipt_supervisor import CardPageSupervisor
        seen=[];pending=[(0,0),(1,1),(2,1),(0,-1),(0,2)]
        class Page(FakeBroker):
            def serve_once(self):
                seen.append((self.query,self.requested_page))
                pending.pop(0);self.done=True
        class Watch(FakeBroker):
            def serve_once(self):time.sleep(.001)
        def factory(index, *, rebind=False, watch_only=False):
            if watch_only:return Watch()
            page=Page();page.query=index;return page
        supervisor=CardPageSupervisor(factory,lambda:pending[0] if pending else None);supervisor.start()
        for _ in range(200):
            if not pending:break
            time.sleep(.01)
        supervisor.close()
        self.assertEqual(seen,[(0,0),(1,1),(2,1),(0,-1),(0,2)])

    def test_page_supervisor_rebinds_before_resuming_pages(self):
        from optimization_worker.tab_receipt_supervisor import CardPageSupervisor
        seen=[];state={'pending':(1,2)}
        class Changed(FakeBroker):
            def serve_once(self):self.rebind_requested=True
        class Rebound(FakeBroker):
            def serve_once(self):state['pending']=(1,0);self.done=True
        class Finished(FakeBroker):
            def serve_once(self):state['pending']=None;self.done=True
        def factory(i,*,rebind=False,watch_only=False):
            seen.append((i,rebind,watch_only))
            if watch_only:return type('Watch',(FakeBroker,),{'serve_once':lambda self:time.sleep(.001)})()
            return Changed() if len(seen)==1 else Rebound() if rebind else Finished()
        supervisor=CardPageSupervisor(factory,lambda:state['pending']);supervisor.start()
        for _ in range(200):
            if state['pending'] is None:break
            time.sleep(.01)
        supervisor.close()
        self.assertEqual(seen[:3],[(1,False,False),(0,True,False),(1,False,False)])
    def test_visual_v2_can_resume_after_nine_queries(self):
        s=NativeSupervisor(lambda *args,**kwargs: FakeBroker(),start_index=9,max_queries=9)
        self.assertEqual(s.start_index,9)
        with self.assertRaises(ValueError):NativeSupervisor(lambda *args,**kwargs: FakeBroker(),start_index=9)

    def test_sealed_expansion_switches_from_watch_to_ordered_search_and_back(self):
        seen=[]
        state={'expansion':False,'finished':False}
        class Watch(FakeBroker):
            def serve_once(self):
                if not state['finished']: state['expansion']=True
                time.sleep(.001)
        def factory(i, *, rebind=False, watch_only=False):
            seen.append((i,watch_only))
            if watch_only:return Watch()
            if i==4:state['finished']=True
            return FakeBroker()
        def expansion():
            return (5,5 if state['finished'] else 3) if state['expansion'] else None
        s=NativeSupervisor(factory,start_index=3,expansion_state=expansion);s.start()
        for _ in range(200):
            if len(seen)>=4: break
            time.sleep(.01)
        s.close()
        self.assertEqual(seen[:4],[(0,True),(3,False),(4,False),(0,True)])
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

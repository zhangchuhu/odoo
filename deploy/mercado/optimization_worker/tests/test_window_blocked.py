import unittest
from optimization_worker.host_session import SessionProvider, SessionBlocked
class TestBlocked(unittest.TestCase):
 def test_provider_surfaces_window_blocked(self):
  class Store:
   def enqueue(self,*args): pass
   def inspect(self,*args): return {'state':'waiting','reason':'window_registration_failed'}
  with self.assertRaises(SessionBlocked): SessionProvider(Store()).run('research.bestseller',{}, {}, 'request')
 def test_worker_marks_known_blocker_waiting_and_settles_checkpoint(self):
  import tempfile
  from pathlib import Path
  from optimization_worker.tests.test_runtime import Fixture
  from optimization_worker.client import Client
  from optimization_worker.runtime import Worker
  fixture=Fixture()
  class Provider:
   def run(self,*args): raise SessionBlocked()
  try:
   with tempfile.TemporaryDirectory() as tmp:
    worker=Worker(Client(fixture.url,'fixture-key',timeout=.5),Path(tmp)/'j.sqlite',providers={'research.keywords':Provider()})
    self.assertEqual(worker.once(),'waiting')
    states=[body['body']['state'] for op,body in fixture.events if op=='checkpoint']
    self.assertEqual(states,['unknown','failed'])
    waits=[body['body']['code'] for op,body in fixture.events if op=='wait']
    self.assertEqual(waits,['window_registration_failed'])
  finally: fixture.close()
 def test_expired_registered_window_blocks_only_if_no_tab_observed(self):
  from optimization_worker.research_return_bridge import window_blocker_safe
  class Broker:
   initial_tab=None; candidates=set(); done=False; conflict=False; window=1; session="s"; started=0
   def fence(self): return True
  class Supervisor:
   active_broker=Broker()
   def window_selection_status(self): return {'registered':True,'observation_seconds_remaining':0}
  self.assertTrue(window_blocker_safe(Supervisor()))
  Supervisor.active_broker.candidates={(123,'session')}
  self.assertFalse(window_blocker_safe(Supervisor()))

 def test_late_observation_during_shutdown_prevents_block(self):
  from optimization_worker.research_return_bridge import close_for_window_blocker
  class Broker:
   initial_tab=None; candidates=set(); done=False; conflict=False; window=1; session='s'; started=0
   def fence(self): return True
  class Supervisor:
   active_broker=Broker()
   def close(self): self.active_broker.candidates.add((123,'s'))
  self.assertIsNone(close_for_window_blocker(Supervisor()))

 def test_live_anchor_guard_prevents_closing_registered_window(self):
  from optimization_worker.research_return_bridge import close_for_window_blocker
  class Broker:
   initial_tab=None; candidates=set(); done=False; conflict=False; window=1; session='s'; started=0
   def fence(self): return True
   def anchor_guard_alive(self): return True
  class Supervisor:
   active_broker=Broker(); closed=False
   def close(self): self.closed=True
  supervisor=Supervisor()
  self.assertIsNone(close_for_window_blocker(supervisor))
  self.assertFalse(supervisor.closed)

class BrowserBlockerTest(unittest.TestCase):
    def test_page_block_preserves_partial_receipts_and_waits_for_operator(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from optimization_worker.client import Client
        from optimization_worker.host_session import SessionStore
        from optimization_worker.research_return_bridge import build_bridge
        from optimization_worker.runtime import Worker
        from optimization_worker.tests.test_runtime import Fixture

        request_id = '12345678-1234-4234-8234-123456789abc'
        fixture = Fixture()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store = SessionStore(Path(tmp) / 'host', 'fixture')
                pending = {'candidate_uploads': {'item:0': {'state': 'confirmed', 'artifact_id': '123'}}}
                with store._db() as db:
                    store._put(db, {'request_id': request_id, 'task_type': 'research.bestseller',
                                    'state': 'candidate_images_pending', 'collector_pending': pending})
                claim = {'task_type': 'research.bestseller', 'lease_epoch': 1}
                with patch('optimization_worker.host_session.worker_records', return_value=[{'request_id': request_id, 'claim': claim}]), patch('optimization_worker.host_session.fence'):
                    bridge = build_bridge(request_id, object(), store, 'journal')
                    with self.assertRaises(ValueError):
                        bridge.call(request_id, 'page_blocked', {'code': 'unknown'})
                    self.assertEqual(bridge.call(request_id, 'page_blocked', {'code': 'captcha_required'}), {'accepted': True})
                record = store.inspect(request_id)
                self.assertEqual(record['state'], 'waiting')
                self.assertEqual(record['resume_state'], 'candidate_images_pending')
                self.assertEqual(record['collector_pending'], pending)
                class Provider:
                    def run(self, *args): raise SessionBlocked(record['reason'])
                worker = Worker(Client(fixture.url, 'fixture-key', timeout=.5), Path(tmp) / 'journal',
                                providers={'research.keywords': Provider()})
                self.assertEqual(worker.once(), 'waiting')
                self.assertEqual([b['body']['state'] for op, b in fixture.events if op == 'checkpoint'], ['unknown'])
                self.assertEqual([b['body']['code'] for op, b in fixture.events if op == 'wait'], ['captcha_required'])
        finally:
            fixture.close()

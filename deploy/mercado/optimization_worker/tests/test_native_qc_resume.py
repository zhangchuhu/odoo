import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from optimization_worker.host_session import SessionStore,synchronize_live

class NativeQCResumeTest(unittest.TestCase):
 def test_best_of_budget_reports_selected_artifact_but_never_resumes(self):
  with tempfile.TemporaryDirectory() as d:
   s=SessionStore(Path(d)/'host.sqlite','test');s.enqueue('r','image.generate',{}, {'artifacts': {}})
   with s._db() as db:
    r=s._get(db,'r');r.update(state='waiting',reason='best_of_budget_review_required',review_seq=1,best_of_budget={'alias':'candidate_2','artifact_id':'42','sha256':'a'*64,'attempt_number':2,'attempt_id':'attempt-2'});s._put(db,r)
   class C:
    def __init__(self):self.sent=[]
    def heartbeat(self,c):return {}
    def host_progress(self,c,r,stage,**kwargs):self.sent.append((r,stage,kwargs))
   c=C()
   with patch('optimization_worker.host_session.worker_records',return_value=[{'claim':{'id':1},'request_id':'r'}]):synchronize_live(c,s,'ignored')
   self.assertEqual(s.inspect('r')['state'],'waiting')
   self.assertEqual(c.sent,[('r','review_required',{'review_seq':1,'review_kind':'best_of_budget','candidate_artifact_id':42,'candidate_sha256':'a'*64,'candidate_attempt_id':'attempt-2','candidate_attempt_number':2})])
 def test_best_resume_lost_ack_replays_same_selected_candidate(self):
  with tempfile.TemporaryDirectory() as d:
   s=SessionStore(Path(d)/'host.sqlite','test');s.enqueue('r','image.generate',{}, {'artifacts': {}})
   chosen={'alias':'candidate_2','artifact_id':'42','sha256':'a'*64,'attempt_number':2,'attempt_id':'attempt-2'}
   with s._db() as db:
    r=s._get(db,'r');r.update(state='waiting',reason='best_of_budget_review_required',review_seq=1,best_of_budget=chosen,quality_cycle={'attempts':[{}, {'attempt_id':'attempt-2','digest':'a'*64}]},uploads={'candidate_2':{'artifact_id':'42','sha256':'a'*64,'path':'/candidate'}});s._put(db,r)
   progress={'code':'host_resume_requested','request_id':'r','intent_id':'intent-1','review_seq':1,'review_kind':'best_of_budget','candidate_artifact_id':42,'candidate_sha256':'a'*64,'candidate_attempt_id':'attempt-2','candidate_attempt_number':2}
   class C:
    def __init__(self):self.calls=0
    def heartbeat(self,c):return {'host_progress':progress}
    def host_progress(self,*args):
     self.calls+=1
     if self.calls==1:raise OSError('ack lost')
   c=C();rows=[{'claim':{'id':1},'request_id':'r'}]
   with patch('optimization_worker.host_session.worker_records',return_value=rows):
    with self.assertRaises(OSError):synchronize_live(c,s,'ignored')
    self.assertEqual(s.inspect('r')['state'],'best_qc_pending')
    synchronize_live(c,s,'ignored')
   self.assertEqual(c.calls,2);self.assertEqual(s.inspect('r')['candidate_path'],'/candidate');self.assertFalse(s.inspect('r')['resume_ack_pending'])
 def test_original_request_resumes_review_without_generation(self):
  with tempfile.TemporaryDirectory() as d:
   s=SessionStore(Path(d)/'host.sqlite','test');s.enqueue('r','image.generate',{}, {'artifacts': {}})
   with s._db() as db:
    r=s._get(db,'r');r.update(state='waiting',reason='candidate_qc_unsettled',resume_state='qc_pending');s._put(db,r)
   class C:
    def __init__(self):self.sent=[]
    def heartbeat(self,c):return {'host_progress':{'code':'host_resume_requested','request_id':'r','intent_id':'intent-1','review_seq':1}}
    def host_progress(self,c,r,stage,intent_id=None,review_seq=None):self.sent.append((r,stage,intent_id,review_seq))
   c=C();rows=[{'claim':{'id':1},'request_id':'r'}]
   with patch('optimization_worker.host_session.worker_records',return_value=rows):synchronize_live(c,s,'ignored')
   self.assertEqual(s.inspect('r')['state'],'qc_pending');self.assertEqual(c.sent,[('r','resumed','intent-1',1)])
 def test_lost_ack_replays_resumed_without_touching_candidate(self):
  with tempfile.TemporaryDirectory() as d:
   s=SessionStore(Path(d)/'host.sqlite','test');s.enqueue('r','image.generate',{}, {'artifacts': {}})
   with s._db() as db:
    r=s._get(db,'r');r.update(state='qc_pending',resume_intent_id='intent-1',resume_ack_pending=True,review_seq=1,candidate_path='/immutable',uploads={'candidate':{'artifact_id':'42'}});s._put(db,r)
   class C:
    def __init__(self):self.sent=[]
    def heartbeat(self,c):return {'host_progress':{'code':'host_resume_requested','request_id':'r','intent_id':'intent-1','review_seq':1}}
    def host_progress(self,c,r,stage,intent_id=None,review_seq=None):self.sent.append((r,stage,intent_id,review_seq))
   c=C()
   with patch('optimization_worker.host_session.worker_records',return_value=[{'claim':{'id':1},'request_id':'r'}]):synchronize_live(c,s,'ignored')
   r=s.inspect('r');self.assertEqual(r['state'],'qc_pending');self.assertEqual(r['uploads']['candidate']['artifact_id'],'42');self.assertEqual(c.sent,[('r','resumed','intent-1',1)])
 def test_waiting_reports_review_without_changing_task(self):
  with tempfile.TemporaryDirectory() as d:
   s=SessionStore(Path(d)/'host.sqlite','test');s.enqueue('r','image.generate',{}, {'artifacts': {}})
   with s._db() as db:
    r=s._get(db,'r');r.update(state='waiting',reason='candidate_qc_unsettled',resume_state='qc_pending');s._put(db,r)
   class C:
    def __init__(self):self.sent=[]
    def heartbeat(self,c):return {}
    def host_progress(self,c,r,stage,intent_id=None,review_seq=None):self.sent.append((r,stage,intent_id,review_seq))
   c=C()
   with patch('optimization_worker.host_session.worker_records',return_value=[{'claim':{'id':1},'request_id':'r'}]):synchronize_live(c,s,'ignored')
   self.assertEqual(s.inspect('r')['state'],'waiting');self.assertEqual(c.sent,[('r','review_required',None,1)])
 def test_old_resume_intent_never_consumed_twice(self):
  with tempfile.TemporaryDirectory() as d:
   s=SessionStore(Path(d)/'host.sqlite','test');s.enqueue('r','image.generate',{}, {'artifacts': {}})
   with s._db() as db:
    r=s._get(db,'r');r.update(state='waiting',reason='candidate_qc_unsettled',resume_state='qc_pending',resume_intent_id='intent-1',resume_ack_pending=False,review_seq=2);s._put(db,r)
   class C:
    def heartbeat(self,c):return {'host_progress':{'code':'host_resume_requested','request_id':'r','intent_id':'intent-1','review_seq':1}}
    def host_progress(self,c,r,stage,intent_id=None,review_seq=None):
     assert stage=='review_required' and review_seq==2
   with patch('optimization_worker.host_session.worker_records',return_value=[{'claim':{'id':1},'request_id':'r'}]):synchronize_live(C(),s,'ignored')
   self.assertEqual(s.inspect('r')['state'],'waiting')
 def test_new_intent_after_second_review_resumes_only_new_round(self):
  with tempfile.TemporaryDirectory() as d:
   s=SessionStore(Path(d)/'host.sqlite','test');s.enqueue('r','image.generate',{}, {'artifacts': {}})
   with s._db() as db:
    r=s._get(db,'r');r.update(state='waiting',reason='candidate_qc_unsettled',resume_state='qc_pending',resume_intent_id='intent-1',resume_ack_pending=False,review_seq=2);s._put(db,r)
   class C:
    def heartbeat(self,c):return {'host_progress':{'code':'host_resume_requested','request_id':'r','intent_id':'intent-2','review_seq':2}}
    def host_progress(self,c,r,stage,intent_id=None,review_seq=None):self.intent=intent_id
   c=C()
   with patch('optimization_worker.host_session.worker_records',return_value=[{'claim':{'id':1},'request_id':'r'}]):synchronize_live(c,s,'ignored')
   r=s.inspect('r');self.assertEqual(r['state'],'qc_pending');self.assertEqual(r['resume_intent_id'],'intent-2');self.assertFalse(r['resume_ack_pending']);self.assertEqual(c.intent,'intent-2')
 def test_acknowledged_server_state_clears_lost_local_ack(self):
  with tempfile.TemporaryDirectory() as d:
   s=SessionStore(Path(d)/'host.sqlite','test');s.enqueue('r','image.generate',{}, {'artifacts': {}})
   with s._db() as db:
    r=s._get(db,'r');r.update(state='qc_pending',resume_intent_id='intent-1',resume_ack_pending=True,review_seq=1);s._put(db,r)
   class C:
    def heartbeat(self,c):return {'host_progress':{'code':'host_review_resumed','request_id':'r','intent_id':'intent-1','review_seq':1}}
    def host_progress(self,*a):raise AssertionError('already acknowledged')
   with patch('optimization_worker.host_session.worker_records',return_value=[{'claim':{'id':1},'request_id':'r'}]):synchronize_live(C(),s,'ignored')
   self.assertFalse(s.inspect('r')['resume_ack_pending'])

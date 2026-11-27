import json
import tempfile
import threading
import unittest
from pathlib import Path
from optimization_worker.research_bridge_socket import Server, send

class Tests(unittest.TestCase):
    def test_diagnostics_only_record_operation_and_outcome(self):
        events=[]
        class Bridge:
            request_id='bound'
            def call(self,request,op,data): return {'accepted':True}
        with tempfile.TemporaryDirectory() as tmp:
            with Server(Path(tmp)/'s',Bridge(),on_event=lambda op,accepted: events.append((op,accepted))) as server:
                thread=threading.Thread(target=server.serve_once);thread.start()
                self.assertEqual(send(server.path,{'operation':'page_blocked','data':{'code':'private-value'}}),{'accepted':True})
                thread.join(2)
        self.assertEqual(events,[('page_blocked',True)])
    def test_home_protocol_responses_reach_the_child(self):
        responses = {
            'seal_suggestions': {'accepted': True, 'selection': {'queries': [{'query': 'a'}, {'query': 'b'}, {'query': 'c'}]}},
            'home_expansion_options': {'accepted': True, 'expansion': {'queries': ['d']}},
            'reserve_captcha_attempt': {'accepted': True, 'captcha': {'attempt': 1}},
            'arm_home_verification': {'accepted': True, 'watch': {'stage': 'search'}},
        }
        for operation, expected in responses.items():
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as tmp:
                class Bridge:
                    request_id = 'bound'
                    def call(self, request, op, data): return expected
                with Server(Path(tmp)/'s', Bridge()) as server:
                    thread=threading.Thread(target=server.serve_once); thread.start()
                    self.assertEqual(send(server.path, {'operation': operation, 'data': {}}), expected)
                    thread.join(2)

    def test_prior_lease_status_roundtrip(self):
        class Bridge:
            request_id='bound'
            def call(self,request,op,data): return {'accepted':True,'inactive':False}
        with tempfile.TemporaryDirectory() as tmp:
            with Server(Path(tmp)/'s',Bridge()) as server:
                thread=threading.Thread(target=server.serve_once);thread.start()
                response=send(server.path,{'operation':'prior_lease_status','data':{'request_id':'12345678-1234-4234-8234-123456789abc'}})
                thread.join(2);self.assertEqual(response,{'accepted':True,'inactive':False})
    def test_binding_status_roundtrip(self):
        class Bridge:
            request_id='bound'
            def call(self,request,op,data):
                assert request=='bound' and op=='binding_status' and data=={}
                return {'accepted':True,'binding':{'tab_id':123,'window_id':4,'completed_queries':1,'queries':['a','b','c'],'binding_matches_plan':True,'guard_active':False}}
        with tempfile.TemporaryDirectory() as tmp:
            with Server(Path(tmp)/'s',Bridge()) as server:
                thread=threading.Thread(target=server.serve_once);thread.start()
                response=send(server.path,{'operation':'binding_status','data':{}})
                thread.join(2)
                self.assertEqual(response['binding']['tab_id'],123)
                self.assertEqual(response['binding']['completed_queries'],1)
    def test_roundtrip_is_bound_and_sanitized(self):
        class Bridge:
            request_id='bound'
            def call(self, request, op, data):
                self.request, self.operation=request,op
                return {'accepted': True}
        with tempfile.TemporaryDirectory() as tmp:
            bridge=Bridge(); server=Server(Path(tmp)/'s',bridge)
            with server:
                thread=threading.Thread(target=server.serve_once);thread.start()
                self.assertEqual(send(server.path, {'operation':'finish','data':{}}), {'accepted':True})
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertEqual((bridge.request,bridge.operation),('bound','finish'))
            self.assertFalse(server.path.exists())

    def test_socket_refuses_existing_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'s';path.write_text('preserve')
            with self.assertRaises(FileExistsError):
                with Server(path,object()): pass
            self.assertEqual(path.read_text(),'preserve')

    def test_bad_message_rejected(self):
        class Bridge:
            request_id='bound'
            def call(self,*args): raise AssertionError()
        with tempfile.TemporaryDirectory() as tmp:
            server=Server(Path(tmp)/'s',Bridge())
            with server:
                thread=threading.Thread(target=server.serve_once);thread.start()
                result=send(server.path, {'operation':'finish','data':{},'request_id':'other'})
                thread.join(2)
                self.assertEqual(result,{'accepted':False})

class ChildProcessTests(unittest.TestCase):
    def test_child_cli_uses_socket_without_credentials(self):
        import os
        import subprocess
        import sys
        class Bridge:
            request_id='bound'
            def call(self, request, op, data): return {'accepted':True}
        with tempfile.TemporaryDirectory() as tmp:
            with Server(Path(tmp)/'s',Bridge()) as server:
                thread=threading.Thread(target=server.serve_once);thread.start()
                env={k:os.environ[k] for k in ('PATH','PYTHONPATH') if k in os.environ}
                process=subprocess.run([sys.executable,'-m','optimization_worker.research_bridge_socket','--socket',str(server.path)],input='{"operation":"finish","data":{}}',text=True,capture_output=True,env=env,timeout=5)
                thread.join(2)
                self.assertEqual(process.returncode,0)
                self.assertEqual(json.loads(process.stdout),{'accepted':True})

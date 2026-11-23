import hashlib
import unittest
from optimization_worker.callback_transport_probe import probe_transport

class Tests(unittest.TestCase):
    def test_roundtrip_proves_only_probe_transport(self):
        class Client:
            def request(self,method,path,body):
                self.calls=getattr(self,'calls',[])+[(method,path)]
                if path.endswith('/create'): return {'probe_id':'a'*32,'digest':hashlib.sha256(body['nonce'].encode()).hexdigest()}
                if path.endswith('/read'): return {'probe_id':'a'*32,'digest':hashlib.sha256(body['nonce'].encode()).hexdigest()}
                if path.endswith('/delete'): return {'deleted':True}
        client=Client();result=probe_transport(client,'A'*32)
        self.assertTrue(result.transport_verified)
        self.assertFalse(result.business_write_verified)
        self.assertEqual(len(client.calls),3)
    def test_mismatch_fails_and_deletes_probe(self):
        class Client:
            def request(self,method,path,body):
                self.calls=getattr(self,'calls',[])+[path]
                if path.endswith('/create'): return {'probe_id':'a'*32,'digest':hashlib.sha256(body['nonce'].encode()).hexdigest()}
                if path.endswith('/read'): return {'probe_id':'a'*32,'digest':'wrong'}
                return {'deleted':True}
        client=Client();result=probe_transport(client,'A'*32)
        self.assertFalse(result.transport_verified)
        self.assertTrue(client.calls[-1].endswith('/delete'))

class ArtifactDiagnosticsTest(unittest.TestCase):
    def test_combination_never_claims_task_completion(self):
        from optimization_worker.callback_transport_probe import probe_diagnostics
        class Client:
            def request(self,method,path,body):
                if path.endswith('/artifact-rollback'): return {'artifact_orm_write_read_verified':True,'task_completion_verified':False}
                if path.endswith('/delete'): return {'deleted':True}
                return {'probe_id':'a'*32,'digest':hashlib.sha256(body['nonce'].encode()).hexdigest()}
        result=probe_diagnostics(Client(),'A'*32,42)
        self.assertTrue(result.transport_verified)
        self.assertTrue(result.artifact_orm_write_read_verified)
        self.assertFalse(result.task_completion_verified)
    def test_missing_or_forged_proof_fails_closed(self):
        from optimization_worker.callback_transport_probe import probe_diagnostics
        class Client:
            def request(self,method,path,body):
                if path.endswith('/artifact-rollback'): return {'artifact_orm_write_read_verified':True,'task_completion_verified':True}
                if path.endswith('/delete'): return {'deleted':True}
                return {'probe_id':'a'*32,'digest':hashlib.sha256(body['nonce'].encode()).hexdigest()}
        result=probe_diagnostics(Client(),'A'*32,42)
        self.assertFalse(result.artifact_orm_write_read_verified)
        self.assertFalse(result.task_completion_verified)

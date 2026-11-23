import unittest
import io
from optimization_worker.client import Client


class ResearchArtifactClientTest(unittest.TestCase):
    def test_scoped_cli_cannot_bypass_guarded_bridge(self):
        from optimization_worker.collector_cli import require_cli_operation_allowed
        for command in ('plan','submit','image','gallery-manifest','finish-images'):
            with self.assertRaises(ValueError): require_cli_operation_allowed(command,True)
        for command in ('reconcile','reconcile-image'):
            require_cli_operation_allowed(command,True)
        require_cli_operation_allowed('finish-images',False)
        with self.assertRaises(ValueError): require_cli_operation_allowed('finish-images',False,{'collector_pending':{'ledger_v1':True}})
    def test_seal_rejects_unknown_and_validates_server_receipt(self):
        from optimization_worker.research_upload import seal_manifest
        class C:
            def seal_research_manifest(self,claim,request,intents): return {'request_id':request,'intent_count':len(intents),'coverage':'unknown'}
        evidence={'ledger_v1':True,'artifact_id':'10','digest':'d'}
        with self.assertRaises(ValueError): seal_manifest(C(),{},'r',evidence,{'x':{'ledger_v1':True,'state':'unknown'}})
        self.assertEqual(seal_manifest(C(),{},'r',evidence,{'x':{'ledger_v1':True,'state':'confirmed','artifact_id':'11','digest':'e'}}),2)
    def test_manifest_seal_carries_original_lease_and_intents(self):
        c=Client('http://127.0.0.1:8069','test'); calls=[]
        c.request=lambda *a: calls.append(a) or {'request_id':'r','intent_count':1,'coverage':'unknown'}
        c.seal_research_manifest({'id':9,'lease_token':'t','lease_epoch':2},'r',['evidence:a'])
        self.assertEqual(calls[0],('POST','/internal/meli-optimization/tasks/9/research-intents/seal',{'request_id':'r','intents':['evidence:a'],'lease_token':'t','lease_epoch':2}))
    def test_editor_reconciliation_does_not_require_active_lease(self):
        c=Client('http://127.0.0.1:8069','test'); calls=[]
        c.request=lambda *a: calls.append(a) or {'coverage':'unknown'}
        c.research_intent_reconcile(9,'request-1','evidence')
        self.assertEqual(calls[0],('POST','/api/meli/optimization/tasks/9/research-intents/reconcile',{'request_id':'request-1','intent_id':'evidence'}))
    def test_missing_ledger_receipt_remains_unknown(self):
        from optimization_worker.research_upload import lookup_receipt
        class Fake:
            def research_intent(self, *args): return {'coverage':'unknown','artifact_id':None,'digest':None}
        with self.assertRaisesRegex(ValueError, 'unknown'):
            lookup_receipt(Fake(), {'id':9}, 'r', 'evidence', '', 'abc')
    def test_upload_adapter_uses_scoped_endpoint_only_when_explicitly_enabled(self):
        from optimization_worker.research_upload import upload
        class Fake:
            research_ledger_enabled = True
            def create_research_artifact(self, claim, request_id, intent_id, **kwargs):
                return (request_id, intent_id, kwargs['data'])
            def create_artifact(self, *args, **kwargs):
                raise AssertionError('must not use legacy endpoint')
        result = upload(Fake(), {'id':9}, 'r', 'image', 'sku:1:0', 3, b'abc', 'image/png')
        self.assertEqual(result[0], 'r')
        self.assertTrue(result[1].startswith('image:'))
        self.assertEqual(result[2], b'abc')

    def test_upload_adapter_defaults_to_existing_endpoint(self):
        from optimization_worker.research_upload import upload
        class Fake:
            def create_artifact(self, case_id, **kwargs): return case_id
        self.assertEqual(upload(Fake(), {}, 'r', 'evidence', '', 3, b'a', 'application/json'), 3)
    def test_large_research_image_uses_artifact_payload_limit(self):
        class Response(io.BytesIO):
            status = 200
        class Opener:
            def open(self, request, timeout):
                return Response(b'{"success":true,"data":{"artifact_id":5}}')
        c = Client('http://127.0.0.1:8069', 'test')
        c._opener = Opener()
        result = c.create_research_artifact({'id':9,'lease_token':'x','lease_epoch':1}, 'r', 'i', data=b'x'*300000, mime_type='image/jpeg')
        self.assertEqual(result['artifact_id'], 5)
    def test_request_scoped_write_carries_original_fence_and_intent(self):
        c = Client('http://127.0.0.1:8069', 'test')
        calls = []
        c.request = lambda *a: calls.append(a) or {'artifact_id': '5'}
        claim = {'id': 9, 'lease_token': 'lease', 'lease_epoch': 2}
        c.create_research_artifact(claim, 'request-1', 'evidence', data=b'abc', mime_type='application/json')
        method, path, body = calls[0]
        self.assertEqual((method, path), ('POST', '/internal/meli-optimization/tasks/9/research-artifacts'))
        self.assertEqual((body['request_id'], body['intent_id'], body['lease_token'], body['lease_epoch']), ('request-1', 'evidence', 'lease', 2))
        self.assertEqual(body['artifact']['data'], 'YWJj')

    def test_reconcile_is_request_scoped_and_never_assumes_missing_means_safe(self):
        c = Client('http://127.0.0.1:8069', 'test')
        calls = []
        c.request = lambda *a: calls.append(a) or {'coverage': 'unknown'}
        self.assertEqual(c.research_intent({'id': 9, 'lease_token': 'lease', 'lease_epoch': 2}, 'request-1', 'evidence')['coverage'], 'unknown')
        self.assertEqual(calls[0], ('POST', '/internal/meli-optimization/tasks/9/research-intents/query', {'request_id': 'request-1', 'intent_id': 'evidence', 'lease_token': 'lease', 'lease_epoch': 2}))

import copy
import importlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
try:
    from optimization_worker.client import Client, APIError
    from optimization_worker.runtime import Worker
except ModuleNotFoundError:
    Client = Worker = APIError = None


def result():
    return {'schema_version': '1', 'evidence': [{'id': 'e1', 'source': 'fixture',
        'observed_at': '2026-11-15T00:00:00Z'}], 'status': 'complete',
        'keywords': [{'text': 'vestido', 'evidence_ids': ['e1']}]}


class Provider:
    def __init__(self, output=None, delay=0, error=None):
        self.output = result() if output is None else output
        self.delay, self.error, self.calls = delay, error, 0

    def run(self, task_type, payload, context, request_id):
        self.calls += 1
        time.sleep(self.delay)
        if self.error:
            raise self.error
        return copy.deepcopy(self.output)

class SingleActiveResearchTest(unittest.TestCase):
    def test_resumes_waiting_request_before_claiming_more_work(self):
        from unittest.mock import Mock, patch
        from optimization_worker.journal import Journal

        client = Mock(identity='fixture')
        original = {
            'claim': {'id': 240, 'task_type': 'research.bestseller', 'lease_epoch': 5},
            'request_id': 'original-gallery-request', 'phase': 'host_pending',
        }

        def resume_waiting(journal):
            journal.save(original)
            return True

        with tempfile.TemporaryDirectory() as directory:
            for task_id in (None, 240):
                with self.subTest(task_id=task_id):
                    path = Path(directory) / f'{task_id}.sqlite'
                    worker = Worker(client, path, task_id=task_id, single_active=True,
                                    capabilities=('research.bestseller',), resume_waiting=resume_waiting)
                    with patch.object(worker, '_run', return_value='pending') as run:
                        self.assertEqual(worker.once(), 'pending')
                    self.assertEqual(run.call_args.args[1], original)
                    journal = Journal(path, client.identity)
                    try:
                        self.assertEqual(journal.active(), [original])
                    finally:
                        journal.close()
        client.claim.assert_not_called()

    def test_ambiguous_active_journal_fails_closed_without_claim_or_dispatch(self):
        from unittest.mock import patch, Mock
        client = Mock(identity='fixture')
        journal = Mock()
        journal.active.return_value = [
            {'claim': {'id': 1, 'task_type': 'research.bestseller'}},
            {'claim': {'id': 2, 'task_type': 'research.bestseller'}},
        ]
        worker = Worker(client, 'ignored', capabilities=('research.bestseller',), single_active=True)
        with patch('optimization_worker.runtime.Journal', return_value=journal):
            self.assertEqual(worker.once(), 'reconciliation_required')
        client.claim.assert_not_called()
        journal.close.assert_called_once()


class Fixture:
    def __init__(self):
        self.state, self.events, self.receipts = 'queued', [], {}
        self.drop_complete = False
        self.drop_checkpoint = False
        self.cancel_heartbeat = False
        self.stale = False
        self.artifacts = {}
        self.lookup_errors = {}
        self.claim = {'id': 21, 'snapshot_id': 15, 'company_id': 1, 'task_type': 'research.keywords',
            'schema_version': '1', 'payload': {'schema_version': '1', 'facts_artifact_id': '23', 'site_id': 'MLM'},
            'lease_token': 'secret-lease', 'lease_epoch': 1, 'heartbeat_seconds': .02}
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.respond(None)
            def do_POST(self):
                self.respond(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            def respond(self, body):
                if self.headers.get('Authorization') != 'Bearer fixture-key':
                    return self.send(401, {'success': False})
                path = self.path
                fixture.events.append((path.rsplit('/', 1)[-1], copy.deepcopy(body)))
                if path in fixture.lookup_errors:
                    return self.send(fixture.lookup_errors[path], {'success': False})
                if path.endswith('/claim'):
                    if fixture.state != 'queued':
                        return self.send(200, {'success': True, 'data': False})
                    fixture.state = 'running'
                    return self.send(200, {'success': True, 'data': fixture.claim})
                if '/snapshots/' in path:
                    from optimization_worker.domain import canonical_hash
                    payload = {'item_id': 'MLM1', 'title': 'Dress'}
                    return self.send(200, {'success': True, 'data': {'snapshot_id': 15, 'case_id': 12,
                        'payload': payload, 'digest': canonical_hash(payload)}})
                if '/artifacts/' in path:
                    from optimization_worker.domain import canonical_hash
                    if path.rsplit('/', 1)[-1] in fixture.artifacts:
                        return self.send(200, {'success': True, 'data': fixture.artifacts[path.rsplit('/', 1)[-1]]})
                    payload = {'facts': ['dress']}
                    return self.send(200, {'success': True, 'data': {'artifact_id': 23,
                        'payload': payload, 'data': False, 'digest': canonical_hash(payload), 'mime_type': 'application/json'}})
                op = path.rsplit('/', 1)[-1]
                key = body.get('submission_key')
                if key and key in fixture.receipts:
                    return self.send(200, {'success': True, 'data': fixture.receipts[key]})
                if op == 'heartbeat' and fixture.cancel_heartbeat:
                    fixture.state = 'cancelled'
                if fixture.stale or fixture.state != 'running':
                    return self.send(409, {'success': False, 'error': {'code': 'invalid_operation'}})
                if body['lease_token'] != fixture.claim['lease_token'] or body['lease_epoch'] != 1:
                    return self.send(409, {'success': False})
                if op == 'heartbeat':
                    return self.send(200, {'success': True, 'data': fixture.claim})
                if op == 'complete': fixture.state = 'succeeded'
                if op == 'wait': fixture.state = 'waiting'
                if op == 'fail': fixture.state = 'failed'
                receipt = {'task_id': 21, 'submission_key': key, 'state': fixture.state, 'accepted': True}
                fixture.receipts[key] = receipt
                if (op == 'complete' and fixture.drop_complete) or (op == 'checkpoint' and fixture.drop_checkpoint):
                    fixture.drop_checkpoint = False
                    fixture.drop_complete = False
                    self.close_connection = True
                    self.connection.close()
                    return
                return self.send(200, {'success': True, 'data': receipt})
            def send(self, status, value):
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Worker, 'standalone optimization Worker is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)

    def worker(self, provider=None, **options):
        providers = {} if provider is None else {'research.keywords': provider}
        return Worker(Client(self.fixture.url, 'fixture-key', timeout=.5),
            Path(self.tmp.name) / 'journal.sqlite', providers=providers, **options)

    def test_claim_to_completion_uses_real_http_and_external_lifecycle(self):
        provider = Provider()
        self.assertEqual(self.worker(provider).once(), 'succeeded')
        self.assertEqual(provider.calls, 1)
        operations = [op for op, _ in self.fixture.events]
        self.assertLess(operations.index('checkpoint'), operations.index('complete'))
        states = [body['body']['state'] for op, body in self.fixture.events if op == 'checkpoint']
        self.assertEqual(states, ['unknown', 'succeeded'])
        self.assertEqual(self.fixture.state, 'succeeded')

    def test_output_artifact_lookup_rejection_fails_known_result(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tests'))
        from test_optimization_domain import gallery
        for status in (403, 404, 409):
            with self.subTest(status=status):
                self.fixture.state = 'queued'
                self.fixture.events.clear()
                self.fixture.receipts.clear()
                output = gallery()
                output['images'][0]['artifact_id'] = '999'
                self.fixture.claim.update(task_type='gallery.collect', payload={
                    'schema_version': '1', 'reference_item_id': 'MLM123', 'selected_variant': {'color': 'black'}})
                self.fixture.lookup_errors['/api/meli/optimization/artifacts/999'] = status
                provider = Provider(output=output)
                worker = Worker(Client(self.fixture.url, 'fixture-key'),
                    Path(self.tmp.name) / ('output-%s.sqlite' % status), providers={'gallery.collect': provider})
                self.assertEqual(worker.once(), 'failed')
                self.assertEqual(self.fixture.state, 'failed')
                self.assertEqual([body['body']['state'] for op, body in self.fixture.events if op == 'checkpoint'],
                                 ['unknown', 'failed'])
                self.assertEqual([body['body']['code'] for op, body in self.fixture.events if op == 'fail'], ['invalid_result'])
                self.assertEqual(worker.once(), 'idle')
                self.assertEqual(provider.calls, 1)

    def test_input_lookup_rejection_fails_before_dispatch(self):
        for resource in ('snapshots/15', 'artifacts/23'):
            for status in (403, 404, 409):
                with self.subTest(resource=resource, status=status):
                    self.fixture.state = 'queued'
                    self.fixture.events.clear()
                    self.fixture.receipts.clear()
                    self.fixture.lookup_errors = {'/api/meli/optimization/' + resource: status}
                    provider = Provider()
                    worker = Worker(Client(self.fixture.url, 'fixture-key'),
                        Path(self.tmp.name) / ('input-%s-%s.sqlite' % (resource.replace('/', '-'), status)),
                        providers={'research.keywords': provider})
                    self.assertEqual(worker.once(), 'failed')
                    self.assertEqual(provider.calls, 0)
                    self.assertEqual([body['body']['code'] for op, body in self.fixture.events if op == 'fail'], ['invalid_input'])
                    self.assertFalse(any(op == 'checkpoint' for op, _ in self.fixture.events))

    def test_reference_server_failure_remains_recoverable(self):
        provider = Provider()
        self.fixture.lookup_errors['/api/meli/optimization/artifacts/23'] = 503
        worker = self.worker(provider)
        with self.assertRaises(APIError):
            worker.once()
        self.assertEqual(provider.calls, 0)
        self.assertEqual(self.fixture.state, 'running')
        self.fixture.lookup_errors.clear()
        self.assertEqual(worker.once(), 'succeeded')
        self.assertEqual(provider.calls, 1)

    def test_fenced_endpoint_rejections_remain_lease_loss(self):
        for operation in ('heartbeat', 'checkpoint'):
            for status in (403, 404, 409):
                with self.subTest(operation=operation, status=status):
                    self.fixture.state = 'queued'
                    self.fixture.events.clear()
                    self.fixture.lookup_errors = {'/internal/meli-optimization/tasks/21/' + operation: status}
                    provider = Provider()
                    worker = Worker(Client(self.fixture.url, 'fixture-key'),
                        Path(self.tmp.name) / ('fence-%s-%s.sqlite' % (operation, status)),
                        providers={'research.keywords': provider})
                    self.assertEqual(worker.once(), 'lease_lost')
                    self.assertEqual(provider.calls, 0)
                    self.assertFalse(any(op in ('complete', 'fail', 'wait') for op, _ in self.fixture.events))

    def test_missing_provider_waits_with_exact_status_envelope(self):
        self.assertEqual(self.worker().once(), 'waiting')
        body = next(body for op, body in self.fixture.events if op == 'wait')
        self.assertEqual(body['body']['code'], 'capability_missing')
        self.assertEqual(set(body), {'lease_token', 'lease_epoch', 'submission_key', 'body'})

    def test_invalid_result_fails_instead_of_completing(self):
        self.assertEqual(self.worker(Provider(output={'wrong': True})).once(), 'failed')
        self.assertFalse(any(op == 'complete' for op, _ in self.fixture.events))

    def test_heartbeat_runs_during_slow_provider(self):
        self.assertEqual(self.worker(Provider(delay=.15)).once(), 'succeeded')
        self.assertGreaterEqual(sum(op == 'heartbeat' for op, _ in self.fixture.events), 3)

    def test_unknown_result_is_not_retried_on_restart(self):
        provider = Provider(delay=.15)
        self.assertEqual(self.worker(provider, provider_timeout=.04).once(), 'waiting')
        self.worker(provider).once()
        self.assertEqual(provider.calls, 1)
        states = [body['body']['state'] for op, body in self.fixture.events if op == 'checkpoint']
        self.assertEqual(states, ['unknown'])
        time.sleep(.16)

    def test_accepted_completion_lost_response_replays_without_provider(self):
        provider = Provider()
        self.fixture.drop_complete = True
        with self.assertRaises(APIError):
            self.worker(provider).once()
        self.assertEqual(self.worker(provider).once(), 'succeeded')
        self.assertEqual(provider.calls, 1)
        complete = [body for op, body in self.fixture.events if op == 'complete']
        self.assertEqual(len(complete), 2)
        self.assertEqual(complete[0], complete[1])

    def test_cancel_during_provider_prevents_new_submissions(self):
        self.fixture.cancel_heartbeat = True
        self.assertEqual(self.worker(Provider(delay=.1)).once(), 'lease_lost')
        self.assertFalse(any(op in ('complete', 'fail', 'wait') for op, _ in self.fixture.events))

    def test_stale_lease_prevents_provider_invocation(self):
        self.fixture.stale = True
        provider = Provider()
        self.assertEqual(self.worker(provider).once(), 'lease_lost')
        self.assertEqual(provider.calls, 0)

    def test_builtin_product_facts_resolves_snapshot(self):
        self.fixture.claim.update(task_type='product.facts', payload={'schema_version': '1', 'product_snapshot_id': '15'})
        self.assertEqual(self.worker().once(), 'succeeded')
        output = next(body['body'] for op, body in self.fixture.events if op == 'complete')
        self.assertEqual(output['facts'][0]['name'], 'item_id')
        self.assertEqual(output['facts'][0]['value'], 'MLM1')

    def test_facts_provider_is_invoked_when_configured(self):
        self.fixture.claim.update(task_type='product.facts', payload={'schema_version': '1', 'product_snapshot_id': '15'})
        from optimization_worker.handlers import product_facts
        output = product_facts({'snapshot': {'snapshot_id': 15, 'payload': {'title': 'Dress'}}})
        provider = Provider(output=output)
        worker = Worker(Client(self.fixture.url, 'fixture-key', timeout=.5), Path(self.tmp.name) / 'fact.sqlite', providers={'product.facts': provider})
        self.assertEqual(worker.once(), 'succeeded')
        self.assertEqual(provider.calls, 1)

    def test_offline_provider_requires_explicit_mode(self):
        from optimization_worker.providers import OfflineProvider
        with self.assertRaises(ValueError):
            OfflineProvider(offline=False)

    def test_client_rejects_remote_plaintext_and_secret_urls(self):
        for url in ('http://example.com', 'https://user:pass@example.com', 'https://example.com?key=secret'):
            with self.assertRaises(ValueError):
                Client(url, 'secret')

    def test_provider_exception_waits_without_exposing_exception(self):
        provider = Provider(error=RuntimeError('secret provider URL key=private'))
        self.assertEqual(self.worker(provider).once(), 'waiting')
        self.assertNotIn('private', json.dumps(self.fixture.events))
        self.assertEqual(provider.calls, 1)

    def test_crash_after_dispatch_intent_never_invokes_provider_again(self):
        from optimization_worker.journal import Journal
        journal = Journal(Path(self.tmp.name) / 'journal.sqlite')
        claim = self.fixture.claim
        self.fixture.state = 'running'
        journal.save({'claim': claim, 'phase': 'calling', 'request_id': 'before-crash', 'sequence': 1})
        journal.close()
        provider = Provider()
        self.assertEqual(self.worker(provider).once(), 'waiting')
        self.assertEqual(provider.calls, 0)

    def test_cli_requires_explicit_offline_for_fixture(self):
        import subprocess
        import os
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]), ODOO_API_KEY='private')
        completed = subprocess.run([sys.executable, '-m', 'optimization_worker', 'once',
            '--fixture', '/missing.json'], env=env, capture_output=True, text=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('requires --offline', completed.stderr)
        self.assertNotIn('private', completed.stderr)

    def test_journal_is_bound_to_origin_and_credentials(self):
        provider = Provider()
        self.worker(provider).once()
        other = Worker(Client(self.fixture.url, 'different-key'), Path(self.tmp.name) / 'journal.sqlite')
        with self.assertRaisesRegex(ValueError, 'identity'):
            other.once()

    def test_gallery_rejects_provider_sku_mismatch(self):
        from optimization_worker.handlers import validate_output
        from optimization_worker.domain import DomainError
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tests'))
        from test_optimization_domain import gallery
        claim = dict(self.fixture.claim, task_type='gallery.collect', payload={
            'reference_item_id': 'different', 'selected_variant': {'color': 'black'}})
        with self.assertRaises(DomainError):
            validate_output(self.worker().client, claim, gallery())


    def test_all_provider_handler_types_validate_and_complete(self):
        import base64
        import hashlib
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tests'))
        from test_optimization_domain import evidence, gallery, research, titles
        image = dict(gallery()['images'][0], artifact_id='24', sha256=hashlib.sha256(b'fixture-image').hexdigest())
        self.fixture.artifacts['24'] = {'artifact_id': 24, 'payload': False,
            'data': base64.b64encode(b'fixture-image').decode(), 'digest': image['sha256'], 'mime_type': 'image/jpeg'}
        gallery_result = gallery()
        gallery_result['images'] = [image]
        cases = [
            ('research.bestseller', {'facts_artifact_id': '23', 'keywords_artifact_id': '23', 'site_id': 'MLM'}, research()),
            ('title.generate', {'facts_artifact_id': '23', 'keywords_artifact_id': '23', 'language': 'es-MX'}, titles()),
            ('gallery.collect', {'reference_item_id': 'MLM123', 'selected_variant': {'color': 'black'}}, gallery_result),
            ('image.generate', {'source_artifact_ids': ['24'], 'target_artifact_id': '24', 'generation_plan_id': '23'},
                {'schema_version': '1', 'evidence': evidence(), 'image': image, 'request_id': 'replaced-by-provider'}),
            ('image.qc', {'image_artifact_id': '24', 'generation_plan_id': '23'},
                {'schema_version': '1', 'evidence': evidence(), 'image_artifact_id': '24', 'status': 'review',
                 'checks': [{'name': 'garment', 'status': 'unknown', 'evidence_ids': ['source:any-id']}]}),
        ]
        from optimization_worker.providers import OfflineProvider
        for index, (kind, payload, output) in enumerate(cases):
            with self.subTest(kind=kind):
                self.fixture.state = 'queued'
                self.fixture.claim.update(id=30 + index, task_type=kind, payload=dict(payload, schema_version='1'))
                worker = Worker(Client(self.fixture.url, 'fixture-key'), Path(self.tmp.name) / 'journal.sqlite',
                    providers={kind: OfflineProvider(offline=True, results={kind: output})})
                self.assertEqual(worker.once(), 'succeeded')

    def test_domain_boundary_does_not_import_odoo_or_generic_domain_package(self):
        import subprocess
        import os
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
        code = "import sys, types; sys.modules['domain']=types.ModuleType('domain'); from optimization_worker.domain import validate_result; assert 'odoo' not in sys.modules"
        completed = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)


    def test_lost_checkpoint_receipt_replays_before_dispatch(self):
        provider = Provider()
        self.fixture.drop_checkpoint = True
        with self.assertRaises(APIError):
            self.worker(provider).once()
        self.assertEqual(provider.calls, 0)
        self.assertEqual(self.worker(provider).once(), 'succeeded')
        self.assertEqual(provider.calls, 1)
        checkpoints = [body for op, body in self.fixture.events if op == 'checkpoint']
        self.assertEqual(checkpoints[0], checkpoints[1])

    def test_non_json_provider_result_is_invalid_terminal_result(self):
        self.assertEqual(self.worker(Provider(output={'value': float('nan')})).once(), 'failed')
        failures = [body['body']['code'] for op, body in self.fixture.events if op == 'fail']
        self.assertEqual(failures, ['invalid_result'])
        states = [body['body']['state'] for op, body in self.fixture.events if op == 'checkpoint']
        self.assertEqual(states, ['unknown', 'failed'])

    def test_cli_explicit_offline_runs_actual_http_fixture(self):
        import subprocess
        import os
        path = Path(self.tmp.name) / 'fixture.json'
        path.write_text(json.dumps({'research.keywords': result()}))
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]), ODOO_API_KEY='fixture-key',
            ODOO_OPTIMIZATION_URL=self.fixture.url, OPTIMIZATION_JOURNAL=str(Path(self.tmp.name) / 'cli.sqlite'))
        completed = subprocess.run([sys.executable, '-m', 'optimization_worker', 'once', '--offline', '--fixture', str(path)],
            env=env, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), 'succeeded')
        self.assertNotIn('fixture-key', completed.stdout + completed.stderr)


    def test_restart_after_known_missing_provider_resolves_checkpoint(self):
        from optimization_worker.journal import Journal
        journal = Journal(Path(self.tmp.name) / 'journal.sqlite')
        self.fixture.state = 'running'
        journal.save({'claim': self.fixture.claim, 'phase': 'provider_missing',
            'provider_missing': True, 'request_id': 'known-no-dispatch', 'sequence': 1})
        journal.close()
        self.assertEqual(self.worker().once(), 'waiting')
        states = [body['body']['state'] for op, body in self.fixture.events if op == 'checkpoint']
        self.assertEqual(states, ['failed'])


    def test_facts_preserve_non_object_json_snapshot(self):
        from optimization_worker.handlers import product_facts
        from optimization_worker.domain import validate_result
        output = product_facts({'snapshot': {'snapshot_id': 15, 'payload': ['supplied', None]}})
        validate_result('product.facts', output)
        self.assertEqual(output['facts'][0]['value'], ['supplied', None])


if __name__ == '__main__':
    unittest.main()

class SourceImageContextCase(unittest.TestCase):
    def test_qc_target_binding_requires_matching_artifact_ownership(self):
        from optimization_worker.handlers import prepare
        class Transport:
            def snapshot(self, ref): return {'case_id':12, 'payload':{}}
            def artifact(self, ref):
                return {'case_id':12, 'company_id':1, 'payload':{'snapshot_id':1, 'target_artifact_ids':['3'], 'source_artifact_ids':[]} if ref == '2' else None}
        claim={'task_type':'image.qc', 'snapshot_id':1, 'company_id':1, 'generation_task_id':5,
               'generation_target_artifact_id':'3', 'payload':{'schema_version':'1','image_artifact_id':'1','generation_plan_id':'2'}}
        self.assertEqual(prepare(Transport(),claim)['qc_target_artifact_id'], '3')
        class Foreign(Transport):
            def artifact(self, ref):
                item=super().artifact(ref)
                if ref == '3': item['case_id']=99
                return item
        self.assertNotIn('qc_target_artifact_id', prepare(Foreign(),claim))
    def test_prepare_rejects_foreign_source_image(self):
        from optimization_worker.handlers import prepare
        class Transport:
            def snapshot(self, ref):
                return {'case_id':12, 'payload':{'source_image_artifact_ids':[99]}}
            def artifact(self, ref):
                return {'artifact_id':99, 'case_id':13, 'company_id':1, 'data':'x', 'digest':'x'}
        with self.assertRaises(ValueError):
            prepare(Transport(), {'task_type':'product.facts', 'snapshot_id':1, 'company_id':1,
                'payload':{'schema_version':'1','product_snapshot_id':'1'}})
        class OtherCompany(Transport):
            def artifact(self, ref):
                return {'artifact_id':99, 'case_id':12, 'company_id':2, 'data':'x', 'digest':'x'}
        with self.assertRaises(ValueError):
            prepare(OtherCompany(), {'task_type':'product.facts', 'snapshot_id':1, 'company_id':1,
                'payload':{'schema_version':'1','product_snapshot_id':'1'}})

    def test_prepare_loads_standalone_source_image_into_verified_context(self):
        from optimization_worker.handlers import prepare
        # Narrow in-memory transport fixture: actual handler behavior is under test.
        class Transport:
            def snapshot(self, ref):
                return {'case_id':12, 'payload': {'source_image_artifact_ids': [99]}}
            def artifact(self, ref):
                return {'artifact_id': int(ref), 'case_id':12, 'company_id':1, 'digest': 'verified', 'data': 'test-only'}
        context = prepare(Transport(), {'task_type': 'product.facts', 'snapshot_id': 1,
            'company_id':1, 'payload': {'schema_version': '1', 'product_snapshot_id': '1'}})
        self.assertEqual(context['artifacts']['99']['artifact_id'], 99)

class ResearchUnknownProviderTests(WorkerTests):
    def test_unknown_session_waits_with_original_request_unsettled(self):
        from optimization_worker.host_session import SessionUnknown
        class P:
            def run(self,*args): raise SessionUnknown()
        self.assertEqual(self.worker(P()).once(), 'waiting')
        operations=[(op,body) for op,body in self.fixture.events if op in ('checkpoint','wait')]
        self.assertEqual([body['body'].get('state') for op,body in operations if op=='checkpoint'],['unknown'])
        self.assertEqual([body['body']['code'] for op,body in operations if op=='wait'],['external_result_unknown'])

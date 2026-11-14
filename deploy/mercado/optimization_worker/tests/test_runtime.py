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


class Fixture:
    def __init__(self):
        self.state, self.events, self.receipts = 'queued', [], {}
        self.drop_complete = False
        self.drop_checkpoint = False
        self.cancel_heartbeat = False
        self.stale = False
        self.artifacts = {}
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
                        'payload': payload, 'data': False, 'digest': canonical_hash(payload)}})
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

import base64
import copy
import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from optimization_worker import host_session as hs
from optimization_worker.domain import canonical_hash
from test_runtime import Fixture, result
from optimization_worker.client import Client
from optimization_worker.runtime import Worker


def png(color='red'):
    out = io.BytesIO()
    Image.new('RGB', (3, 4), color).save(out, format='PNG')
    return out.getvalue()


def caps():
    return {'runtime': 'codex', 'vision': 'view_image', 'browser': 'mcp__cua_repl',
            'generation': 'image_gen.imagegen', 'provenance': 'Actual exposed tool schema checked this session',
            'model': None, 'supports_query': False, 'max_input_images': None}


class HostSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = hs.SessionStore(self.root / 'host.sqlite', 'identity')
        self.payload = {'schema_version': '1', 'source_artifact_ids': ['2'],
                        'target_artifact_id': '1', 'generation_plan_id': '3'}
        self.context = {'snapshot': {'case_id': 12, 'payload': {}}, 'artifacts': {
            str(i): {'digest': hashlib.sha256(png()).hexdigest(), 'data': base64.b64encode(png()).decode(),
                     'mime_type': 'image/png'} for i in (1, 2)}}
        self.context['artifacts']['3'] = {'payload': {'operation': 'garment_swap'}, 'digest': canonical_hash({'operation': 'garment_swap'})}

    def prepare(self):
        self.store.enqueue('request-1', 'image.generate', self.payload, self.context)
        action = self.store.next()
        self.assertEqual(action['action'], 'select_references')
        plan = {'selected_reference_ids': ['2'], 'garment_facts': ['red dress'],
                'classification': 'front', 'viewed_digests': action['input_digests']}
        self.store.plan('request-1', plan)
        return self.store.next()

    def test_generation_dispatch_once_across_restart_and_unknown(self):
        action = self.prepare()
        first = self.store.dispatch('request-1', caps(), action['input_digests'])
        self.assertTrue(first['should_invoke'])
        self.assertEqual(first['invocation']['referenced_image_paths'], action['ordered_image_paths'])
        reopened = hs.SessionStore(self.root / 'host.sqlite', 'identity')
        self.assertFalse(reopened.dispatch('request-1', caps(), action['input_digests'])['should_invoke'])
        reopened.error('request-1', 'unknown')
        self.assertEqual(reopened.next()['action'], 'awaiting_result')
        self.assertFalse(reopened.dispatch('request-1', caps(), action['input_digests'])['should_invoke'])

    def test_missing_capability_or_changed_bytes_cannot_dispatch(self):
        action = self.prepare()
        with self.assertRaises(ValueError):
            self.store.dispatch('request-1', {}, action['input_digests'])
        Path(action['ordered_image_paths'][0]).write_bytes(png('blue'))
        with self.assertRaises(ValueError):
            self.store.dispatch('request-1', caps(), action['input_digests'])

    def test_identity_binding_and_input_change_rejected(self):
        self.prepare()
        with self.assertRaises(ValueError):
            hs.SessionStore(self.root / 'host.sqlite', 'other')
        with self.assertRaises(ValueError):
            self.store.enqueue('request-1', 'image.generate', dict(self.payload, target_artifact_id='2'), self.context)

    def test_real_image_import_and_qc_required_before_result(self):
        action = self.prepare()
        self.store.dispatch('request-1', caps(), action['input_digests'])
        path = self.root / 'actual.png'
        path.write_bytes(png('blue'))
        class Upload:
            calls = 0
            def create_artifact(inner, case_id, **kwargs):
                inner.calls += 1
                self.assertEqual(case_id, 12)
                self.assertEqual(kwargs['data'], path.read_bytes())
                return {'artifact_id': 91, 'digest': hashlib.sha256(kwargs['data']).hexdigest()}
        client = Upload()
        self.store.image('request-1', path, client)
        self.assertIsNone(self.store.result('request-1'))
        qc = self.store.next()
        self.assertEqual(qc['action'], 'review_candidate')
        self.store.qc('request-1', {'viewed_digests': qc['input_digests'], 'checks': [
            {'name': name, 'status': 'passed', 'evidence': 'Visible region inspected'} for name in hs.QC_CHECKS]})
        self.assertEqual(self.store.result('request-1')['image']['artifact_id'], '91')
        self.store.image('request-1', path, client)
        self.assertEqual(client.calls, 1)

    def test_unknown_upload_never_retried_and_result_bytes_immutable(self):
        action = self.prepare()
        self.store.dispatch('request-1', caps(), action['input_digests'])
        path = self.root / 'actual.png'
        path.write_bytes(png())
        class Lost:
            calls = 0
            def create_artifact(inner, *args, **kwargs):
                inner.calls += 1
                raise OSError('response lost')
        client = Lost()
        with self.assertRaises(OSError):
            self.store.image('request-1', path, client)
        with self.assertRaises(ValueError):
            self.store.image('request-1', path, client)
        self.assertEqual(client.calls, 1)
        self.assertEqual(self.store.next()['action'], 'reconcile_upload')

    def test_worker_handoff_keeps_lease_and_completes_same_request(self):
        fixture = Fixture()
        self.addCleanup(fixture.close)
        client = Client(fixture.url, 'fixture-key')
        store = hs.SessionStore(self.root / 'worker-host.sqlite', client.identity)
        provider = hs.SessionProvider(store)
        worker = Worker(client, self.root / 'worker.sqlite', providers={'research.keywords': provider})
        self.assertEqual(worker.once(), 'host_pending')
        action = store.next()
        store.submit(action['request_id'], result(), caps())
        self.assertEqual(worker.once(), 'succeeded')
        self.assertEqual(len([e for e in fixture.events if e[0] == 'claim']), 1)

    def test_browser_submission_requires_visible_evidence(self):
        self.store.enqueue('browser-1', 'gallery.collect', {'schema_version': '1', 'reference_item_id': 'https://example.com/product', 'selected_variant': {'color': 'red'}}, {'snapshot': {'case_id': 12, 'payload': {}}, 'artifacts': {}})
        self.assertEqual(self.store.next()['action'], 'browse_gallery')
        with self.assertRaises(ValueError):
            self.store.submit('browser-1', {}, caps())

    def test_candidate_change_after_upload_rejected_by_qc(self):
        action = self.prepare()
        self.store.dispatch('request-1', caps(), action['input_digests'])
        path = self.root / 'candidate.png'
        path.write_bytes(png('blue'))
        class Upload:
            def create_artifact(inner, *args, **kwargs):
                return {'artifact_id': 91, 'digest': hashlib.sha256(kwargs['data']).hexdigest()}
        self.store.image('request-1', path, Upload())
        qc = self.store.next()
        Path(qc['candidate_path']).write_bytes(png('green'))
        forged_views = dict(qc['input_digests'])
        forged_views[qc['candidate_path']] = hashlib.sha256(png('green')).hexdigest()
        with self.assertRaises(ValueError):
            self.store.qc('request-1', {'viewed_digests': forged_views, 'checks': [
                {'name': n, 'status': 'passed', 'evidence': 'Viewed'} for n in hs.QC_CHECKS]})

    def test_json_result_cannot_overwrite_dispatched_generation(self):
        action = self.prepare()
        self.store.dispatch('request-1', caps(), action['input_digests'])
        with self.assertRaises(ValueError):
            self.store.submit('request-1', result(), caps())

    def test_gallery_local_import_binds_real_files_and_visible_sku(self):
        self.store.enqueue('browser-1', 'gallery.collect', {'schema_version': '1', 'reference_item_id': 'https://example.com/product', 'selected_variant': {'color': 'red'}}, {'snapshot': {'case_id': 12, 'payload': {}}, 'artifacts': {}})
        path = self.root / 'export.png'
        path.write_bytes(png())
        observed = {'schema_version': '1', 'evidence': [{'id': 'page', 'source': 'https://example.com/product', 'observed_at': '2026-11-15T00:00:00Z'}],
                    'reference_item_id': 'https://example.com/product', 'selected_variant': {'color': 'red'},
                    'observed_at': '2026-11-15T00:00:00Z', 'completeness_status': 'complete',
                    'images': [{'image_id': 'main', 'order': 0, 'role': 'main', 'source': 'https://cdn.example.com/image.png', 'evidence_ids': ['page']}],
                    'browser_observation': {'selected_variant_verified': True, 'gallery_only_verified': True,
                        'page_url': 'https://example.com/product', 'screenshot_path': str(path), 'observed_gallery_count': 1, 'blocked': False}}
        class Upload:
            def create_artifact(inner, *args, **kwargs):
                return {'artifact_id': 92, 'digest': hashlib.sha256(kwargs['data']).hexdigest()}
        self.store.gallery('browser-1', observed, {'main': str(path)}, Upload(), caps())
        actual = self.store.result('browser-1')
        self.assertEqual(actual['images'][0]['artifact_id'], '92')
        self.assertEqual(actual['images'][0]['sha256'], hashlib.sha256(png()).hexdigest())
        self.assertEqual(actual['gallery_manifest_hash'], canonical_hash({'selected_variant': actual['selected_variant'], 'images': actual['images']}))

    def test_two_independent_hosts_can_only_claim_one_edit(self):
        from concurrent.futures import ThreadPoolExecutor
        action = self.prepare()
        def dispatch(_):
            store = hs.SessionStore(self.root / 'host.sqlite', 'identity')
            return store.dispatch('request-1', caps(), action['input_digests'])['should_invoke']
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(dispatch, range(2))), 1)

    def test_host_cli_fence_requires_original_active_worker_lease(self):
        from optimization_worker.journal import Journal
        journal_path = self.root / 'fence.sqlite'
        journal = Journal(journal_path, 'identity')
        journal.save({'claim': {'id': 21, 'input_hash': 'a', 'lease_epoch': 2, 'lease_token': 'private'}, 'request_id': 'request-1', 'phase': 'host_pending'})
        journal.close()
        class Lease:
            def heartbeat(inner, claim):
                self.assertEqual(claim['lease_epoch'], 2)
                return {'state': 'running'}
        hs.fence(Lease(), journal_path, 'request-1')
        with self.assertRaises(ValueError):
            hs.fence(Lease(), journal_path, 'other-request')

    def test_unavailable_capability_can_resume_without_resetting_dispatch(self):
        action = self.prepare()
        self.store.error('request-1', 'unavailable')
        self.assertEqual(self.store.next()['action'], 'waiting')
        self.store.resume('request-1')
        self.assertTrue(self.store.dispatch('request-1', caps(), action['input_digests'])['should_invoke'])
        self.store.error('request-1', 'unavailable')
        self.store.resume('request-1')
        self.assertFalse(self.store.dispatch('request-1', caps(), action['input_digests'])['should_invoke'])

    def test_standalone_qc_requires_actual_viewed_originals(self):
        payload = {'schema_version': '1', 'image_artifact_id': '1', 'generation_plan_id': '3'}
        self.store.enqueue('qc-1', 'image.qc', payload, self.context)
        body = {'schema_version': '1', 'image_artifact_id': '1', 'status': 'passed',
                'evidence': [{'id': 'v', 'source': 'view_image', 'observed_at': '2026-11-15T00:00:00Z'}],
                'checks': [{'name': 'garment', 'status': 'passed', 'evidence_ids': ['v']}]}
        with self.assertRaises(ValueError):
            self.store.submit('qc-1', body, caps())

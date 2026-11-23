import unittest
from optimization_worker.facts_merge import merge_visual_facts

class FactsMergeTest(unittest.TestCase):
    def test_preserves_known_facts_and_adds_sourced_visual_observation(self):
        snapshot = {'snapshot_id': 7, 'payload': {'title': 'Dress', 'stock': 4}}
        result = merge_visual_facts(snapshot, [{'name': '领型', 'value': '圆领', 'evidence_ids': ['image:12']}], ['材质待确认'], [{'id': 'image:12', 'source': 'artifact:12', 'observed_at': '2026-11-19T00:00:00Z'}])
        self.assertEqual(result['facts'][0]['category'], 'known')
        self.assertEqual(result['facts'][-1]['category'], 'visual')
        self.assertIn('材质待确认', result['uncertainties'])
    def test_rejects_unsourced_or_overlapping_observations(self):
        snapshot = {'snapshot_id': 7, 'payload': {'title': 'Dress'}}
        for row in [{'name':'title','value':'Fake','evidence_ids':['image:1']}, {'name':'领型','value':'圆领','evidence_ids':['bogus']}]:
            with self.assertRaises(ValueError):
                merge_visual_facts(snapshot, [row], [], [{'id':'image:1','source':'artifact:1','observed_at':'2026-11-19T00:00:00Z'}])

class FactsHostTest(unittest.TestCase):
    def test_host_can_claim_product_facts(self):
        import tempfile
        from pathlib import Path
        from optimization_worker.host_session import SessionStore
        with tempfile.TemporaryDirectory() as d:
            store = SessionStore(Path(d) / 'host.sqlite', 'test')
            store.enqueue('fact1', 'product.facts', {}, {'snapshot': {'snapshot_id': 1, 'payload': {}}, 'artifacts': {}})
            self.assertTrue(store.claim_text('fact1'))

    def test_facts_schema_contains_visual_observations(self):
        from optimization_worker.text_host import output_schema
        schema = output_schema('product.facts')
        self.assertIn('observations', schema['properties'])
        self.assertIn('uncertainties', schema['properties'])

    def test_facts_host_without_images_submits_only_known_facts(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from optimization_worker.host_session import SessionStore
        from optimization_worker.text_host import run_one
        with tempfile.TemporaryDirectory() as d:
            store = SessionStore(Path(d) / 'host.sqlite', 'test')
            store.enqueue('fact1', 'product.facts', {}, {'snapshot': {'snapshot_id': 1, 'payload': {'title':'Dress'}}, 'artifacts': {}})
            with patch.dict('os.environ', {'OPTIMIZATION_CODEX_HOME': d}), patch('optimization_worker.text_host.synchronize_live'), patch('optimization_worker.text_host.fence'):
                self.assertEqual(run_one(store, None, 'test', lambda *a, **k: self.fail('no image should not invoke model')), 'submitted')
            result = store.inspect('fact1')['result']
            self.assertEqual(result['facts'][0]['category'], 'known')
            self.assertTrue(any('图片' in item for item in result['uncertainties']))

    def test_interrupted_claim_is_marked_unknown_without_touching_results(self):
        import tempfile
        from pathlib import Path
        from optimization_worker.host_session import SessionStore
        with tempfile.TemporaryDirectory() as d:
            store = SessionStore(Path(d) / 'host.sqlite', 'test')
            store.enqueue('fact1', 'product.facts', {}, {'snapshot': {'snapshot_id': 1, 'payload': {}}, 'artifacts': {}})
            self.assertTrue(store.claim_text('fact1'))
            store.mark_interrupted_text({'product.facts'})
            self.assertEqual(store.inspect('fact1')['state'], 'unknown')

    def test_host_passes_verified_image_and_attaches_visual_evidence(self):
        import tempfile, hashlib, base64, json, io
        from pathlib import Path
        from unittest.mock import patch
        from PIL import Image
        from optimization_worker.host_session import SessionStore
        from optimization_worker.text_host import run_one
        with tempfile.TemporaryDirectory() as d:
            data = io.BytesIO(); Image.new('RGB', (20, 20), 'red').save(data, format='PNG'); raw = data.getvalue()
            artifact = {'data': base64.b64encode(raw).decode(), 'digest': hashlib.sha256(raw).hexdigest(), 'mime_type': 'image/png'}
            store = SessionStore(Path(d) / 'host.sqlite', 'test')
            store.enqueue('fact1', 'product.facts', {}, {'snapshot': {'snapshot_id': 1, 'payload': {'title':'Dress'}}, 'artifacts': {'12': artifact}})
            def runner(args, **kwargs):
                self.assertIn('--image', args)
                self.assertIn('observations.name 必须是以下字段之一', kwargs['input'])
                self.assertIn('领型', kwargs['input'])
                self.assertIn('剪裁', kwargs['input'])
                self.assertTrue(Path(args[args.index('--image') + 1]).is_file())
                Path(args[args.index('-o') + 1]).write_text(json.dumps({'observations':[{'name':'领型','value':'圆领','evidence_ids':['image:12']}], 'uncertainties':['材质待确认']}))
                return type('Done', (), {'returncode': 0})()
            with patch.dict('os.environ', {'OPTIMIZATION_CODEX_HOME': d}), patch('optimization_worker.text_host.synchronize_live'), patch('optimization_worker.text_host.fence'):
                self.assertEqual(run_one(store, None, 'test', runner), 'submitted')
            result = store.inspect('fact1')['result']
            self.assertEqual(result['facts'][-1]['category'], 'visual')
            self.assertEqual(result['facts'][-1]['evidence_ids'], ['image:12'])

    def test_recovery_preserves_ready_result_and_provider_fences_unknown(self):
        import tempfile
        from pathlib import Path
        from optimization_worker.host_session import SessionStore, SessionProvider, SessionUnknown
        with tempfile.TemporaryDirectory() as d:
            store = SessionStore(Path(d) / 'host.sqlite', 'test')
            context = {'snapshot': {'snapshot_id': 1, 'payload': {'title':'Dress'}}, 'artifacts': {}}
            store.enqueue('ready', 'product.facts', {}, context)
            store.claim_text('ready')
            store.save_text_result('ready', merge_visual_facts(context['snapshot'], [], [], []))
            store.enqueue('unknown', 'product.facts', {}, context)
            store.claim_text('unknown')
            store.mark_interrupted_text({'product.facts'})
            self.assertEqual(store.inspect('ready')['state'], 'result_ready')
            with self.assertRaises(SessionUnknown):
                SessionProvider(store).run('product.facts', {}, context, 'unknown')

    def test_prepare_uses_snapshot_source_images_artifact_ids_and_rejects_foreign(self):
        from optimization_worker.handlers import prepare
        class Client:
            def snapshot(self, ref):
                return {'case_id': 12, 'payload': {'source_images': [{'artifact_id': 42}, {'url':'https://example.com/x'}]}}
            def artifact(self, ref):
                return {'artifact_id': 42, 'case_id': 12, 'company_id': 1, 'data':'x', 'digest':'x'}
        claim = {'task_type':'product.facts', 'snapshot_id':1, 'company_id':1, 'payload':{'schema_version':'1','product_snapshot_id':'1'}}
        self.assertEqual(set(prepare(Client(), claim)['artifacts']), {'42'})
        class Foreign(Client):
            def artifact(self, ref):
                return {'artifact_id':42, 'case_id':99, 'company_id':1, 'data':'x'}
        with self.assertRaises(ValueError): prepare(Foreign(), claim)

    def test_expired_lease_stops_facts_before_provider_dispatch(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from optimization_worker.host_session import SessionStore
        from optimization_worker.text_host import safe_run_one
        from optimization_worker.client import APIError
        with tempfile.TemporaryDirectory() as d:
            store = SessionStore(Path(d) / 'host.sqlite', 'test')
            store.enqueue('fact1', 'product.facts', {}, {'snapshot': {'snapshot_id': 1, 'payload': {}}, 'artifacts': {}})
            with patch.dict('os.environ', {'OPTIMIZATION_CODEX_HOME': d}), patch('optimization_worker.text_host.synchronize_live'), patch('optimization_worker.text_host.fence', side_effect=APIError(409)):
                self.assertEqual(safe_run_one(store, None, 'test', kinds={'product.facts'}), 'stale_lease')
            self.assertEqual(store.inspect('fact1')['state'], 'prepared')

    def test_visual_scope_rejects_material_and_sales_in_names_and_values(self):
        snapshot = {'snapshot_id': 7, 'payload': {'title': 'Dress'}}
        image = [{'id':'image:1','source':'artifact:1','observed_at':'2026-11-19T00:00:00Z'}]
        for name, value in [('材质','棉'), ('material','cotton'), ('销量','100'), ('sales','100'), ('剪裁','棉质'), ('领型','100% cotton'), ('category','poliéster')]:
            with self.assertRaises(ValueError):
                merge_visual_facts(snapshot, [{'name': name, 'value': value, 'evidence_ids':['image:1']}], [], image)

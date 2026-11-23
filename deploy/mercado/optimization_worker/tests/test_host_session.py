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
        self.context = {'snapshot': {'case_id': 12, 'company_id': 1, 'payload': {'title': 'Dress red'}}, 'artifacts': {
            str(i): {'digest': hashlib.sha256(png()).hexdigest(), 'data': base64.b64encode(png()).decode(),
                     'mime_type': 'image/png'} for i in (1, 2)}}
        self.context['artifacts']['3'] = {'payload': {'operation': 'garment_swap'}, 'digest': canonical_hash({'operation': 'garment_swap'})}

    def prepare(self):
        self.store.enqueue('request-1', 'image.generate', self.payload, self.context)
        action = self.store.next()
        self.assertEqual(action['action'], 'select_references')
        plan = {'selected_reference_ids': ['2'], 'target_color':'red', 'garment_facts': {'required':['garment_type:dress'], 'forbidden':[]},
                'classification': 'front', 'source_evidence':[{'source_id':'2','angle':'front','roles':['model'],'information_score':90,'observed_color':'red'}],
                'garment_instances':['dress'], 'viewed_digests': action['input_digests']}
        self.store.plan('request-1', plan)
        return self.store.next()

    def test_next_exact_request_skips_other_waiting_record(self):
        self.store.enqueue('request-1', 'image.generate', self.payload, self.context)
        self.store.enqueue('request-2', 'image.generate', self.payload, self.context)
        with self.store._db() as db:
            r = self.store._get(db, 'request-1'); r.update(state='waiting', reason='pending'); self.store._put(db, r)
        self.assertEqual(self.store.next(request_id='request-2')['request_id'], 'request-2')
        self.assertEqual(self.store.next(request_id='missing')['action'], 'idle')

    def test_wrong_color_never_advances_to_dispatch(self):
        self.context['snapshot']['payload'] = {'title': 'Conjunto Caqui'}
        self.store.enqueue('request-1', 'image.generate', self.payload, self.context)
        action = self.store.next()
        plan = {'selected_reference_ids': ['2'], 'target_color':'khaki', 'source_evidence':[{'source_id':'2','observed_color':'black'}], 'viewed_digests':action['input_digests']}
        self.store.plan('request-1', plan)
        self.assertEqual(self.store.inspect('request-1')['state'], 'waiting')
        self.assertEqual(self.store.inspect('request-1')['reason'], 'target_color_unconfirmed')

    def test_dispatch_rechecks_color_of_existing_plan(self):
        action=self.prepare()
        with self.store._db() as db:
            r=self.store._get(db,'request-1'); r['plan']['source_evidence'][0]['observed_color']='black'; self.store._put(db,r)
        result=self.store.dispatch('request-1', caps(), action['input_digests'])
        self.assertFalse(result['should_invoke'])
        self.assertEqual(result['reason'], 'target_color_unconfirmed')

    def test_generation_dispatch_once_across_restart_and_unknown(self):
        action = self.prepare()
        first = self.store.dispatch('request-1', caps(), action['input_digests'])
        self.assertTrue(first['should_invoke'])
        self.assertEqual(first['invocation']['referenced_image_paths'], action['ordered_image_paths'])
        reopened = hs.SessionStore(self.root / 'host.sqlite', 'identity')
        self.assertFalse(reopened.dispatch('request-1', caps(), action['input_digests'])['should_invoke'])

    def test_late_images_are_immutable_per_attempt(self):
        action = self.prepare(); first = self.store.dispatch('request-1', caps(), action['input_digests'])
        with self.store._db() as db:
            r = self.store._get(db,'request-1'); r['quality_cycle']['attempts'].append({'number':2,'attempt_id':'second','status':'dispatched'}); self.store._put(db,r)
        path = self.root / 'late.png'; path.write_bytes(png('blue'))
        self.store.archive_image('request-1',path,first['attempt_id'])
        self.store.archive_image('request-1',path,first['attempt_id'])
        path.write_bytes(png('green')); self.store.archive_image('request-1',path,'second')
        with self.assertRaises(ValueError): self.store.archive_image('request-1',path,first['attempt_id'])
        with self.assertRaises(ValueError): self.store.archive_image('request-1',path,'unknown')
        self.assertEqual(len(self.store.inspect('request-1')['late_results']),2)

    def test_historical_resumed_round_never_falls_back_to_initial(self):
        attempt = {'attempt_id':'first'}
        r = {'resume_intent_id':'intent', 'review_seq':2, 'quality_cycle':{'attempts':[attempt]}}
        self.assertEqual(hs._review_round(r,attempt)['review_seq'],2)
        r.pop('resume_intent_id')
        with self.assertRaises(ValueError): hs._review_round(r,attempt)
        r['resume_intent_id']='intent'; r['quality_cycle']['attempts'].append({'attempt_id':'second'})
        with self.assertRaises(ValueError): hs._review_round(r,attempt,True)

    def test_unverifiable_historical_round_does_not_starve_other_requests(self):
        action=self.prepare(); self.store.dispatch('request-1',caps(),action['input_digests'])
        with self.store._db() as db:
            r=self.store._get(db,'request-1'); r.update(state='qc_pending', candidate_path=r['ordered_image_paths'][0], review_seq=1)
            r.pop('active_review_binding',None); r['uploads']={'candidate':{'sha256':'a'*64}}
            self.store._put(db,r)
        self.store.enqueue('request-2','research.keywords',{}, {'artifacts':{}})
        self.assertEqual(self.store.next()['request_id'],'request-2')
        old=self.store.inspect('request-1')
        self.assertEqual(old['state'],'waiting'); self.assertEqual(old['reason'],'manual_review_binding_required')
        self.assertEqual(old['review_seq'],1)
        self.assertEqual(self.store.next()['request_id'],'request-2')

    def test_dispatch_persists_attempt_and_unknown_never_spends_another(self):
        action = self.prepare()
        first = self.store.dispatch('request-1', caps(), action['input_digests'])
        self.assertEqual(first['attempt_number'], 1)
        self.assertTrue(first['attempt_id'])
        reopened = hs.SessionStore(self.root / 'host.sqlite', 'identity')
        cycle = reopened.inspect('request-1')['quality_cycle']
        self.assertEqual(cycle['attempts'][0]['attempt_id'], first['attempt_id'])
        reopened.error('request-1', 'unknown')
        self.assertEqual(reopened.inspect('request-1')['quality_cycle']['attempts'][0]['status'], 'unknown')
        self.assertFalse(reopened.dispatch('request-1', caps(), action['input_digests'])['should_invoke'])
        self.assertEqual(len(reopened.inspect('request-1')['quality_cycle']['attempts']), 1)
        reopened.error('request-1', 'unknown')
        self.assertEqual(reopened.next()['action'], 'awaiting_result')
        self.assertFalse(reopened.dispatch('request-1', caps(), action['input_digests'])['should_invoke'])

    def test_confirmed_service_retry_survives_restart_and_is_claimed_once(self):
        from unittest.mock import patch
        action = self.prepare()
        with patch.object(hs.time, 'time', return_value=100):
            first = self.store.dispatch('request-1', caps(), action['input_digests'])
        with self.assertRaises(ValueError):
            self.store.report_service_error('request-1', first['invocation_id'], 'ambiguous timeout')
        with patch.object(hs.time, 'time', return_value=101):
            self.store.report_service_error('request-1', first['invocation_id'], 'HTTP 503', 0, confirmed_finished=True)
        pending = self.store.next()
        self.assertEqual(pending['action'], 'retry_generation_service')
        self.assertEqual(pending['expected_unknown_at'], 101)
        reopened = hs.SessionStore(self.root / 'host.sqlite', 'identity')
        with patch.object(hs.time, 'time', return_value=105):
            with self.assertRaises(ValueError): reopened.claim_service_retry('request-1', 101, caps(), action['input_digests'])
        with patch.object(hs.time, 'time', return_value=106):
            second = reopened.claim_service_retry('request-1', 101, caps(), action['input_digests'])
            self.assertTrue(second['should_invoke'])
            self.assertEqual(second['invocation'], first['invocation'])
            self.assertNotEqual(second['invocation_id'], first['invocation_id'])
            self.assertFalse(reopened.claim_service_retry('request-1', 101, caps(), action['input_digests'])['should_invoke'])
            with self.assertRaises(ValueError): reopened.report_service_error('request-1', first['invocation_id'], 'stale 503', 0, confirmed_finished=True)
        state = reopened.inspect('request-1')
        self.assertEqual(len(state['quality_cycle']['attempts']), 1)
        self.assertEqual(len(state['generation_retry']['receipts']), 1)
        self.assertEqual(reopened.next()['action'], 'awaiting_result')

    def test_service_retry_unknown_and_retired_cannot_be_claimed(self):
        from unittest.mock import patch
        action = self.prepare()
        with patch.object(hs.time, 'time', return_value=100):
            self.store.dispatch('request-1', caps(), action['input_digests'])
        self.store.error('request-1', 'unknown')
        with self.assertRaises(ValueError): self.store.claim_service_retry('request-1', 100, caps(), action['input_digests'])
        with self.assertRaises(ValueError): self.store.report_service_error('request-1', 'bad', '503', 0, confirmed_finished=True)

    def test_old_invocation_cannot_import_or_report_after_service_retry(self):
        from unittest.mock import patch
        action = self.prepare()
        with patch.object(hs.time, 'time', return_value=100):
            first = self.store.dispatch('request-1', caps(), action['input_digests'])
        with patch.object(hs.time, 'time', return_value=101):
            self.store.report_service_error('request-1', first['invocation_id'], '503', confirmed_finished=True)
        with patch.object(hs.time, 'time', return_value=106):
            second = self.store.claim_service_retry('request-1', 101, caps(), action['input_digests'])
        path = self.root / 'native.png'; path.write_bytes(png('blue'))
        class Upload:
            def create_artifact(inner, *args, **kwargs):
                return {'artifact_id': 42, 'digest': hashlib.sha256(kwargs['data']).hexdigest()}
        for stale in (None, first['invocation_id']):
            with self.assertRaises(ValueError):
                self.store.image('request-1', path, Upload(), second['attempt_id'], stale)
            with self.assertRaises(ValueError):
                self.store.error('request-1', 'unknown', invocation_id=stale)
        self.assertEqual(self.store.inspect('request-1')['state'], 'dispatched')
        self.store.image('request-1', path, Upload(), second['attempt_id'], second['invocation_id'])
        self.assertEqual(self.store.inspect('request-1')['state'], 'qc_pending')

    def test_direct_saved_result_rejects_stale_invocation(self):
        from unittest.mock import patch
        action = self.prepare()
        with patch.object(hs.time, 'time', return_value=100):
            first = self.store.dispatch('request-1', caps(), action['input_digests'])
        with patch.object(hs.time, 'time', return_value=101):
            self.store.report_service_error('request-1', first['invocation_id'], '503', confirmed_finished=True)
        with patch.object(hs.time, 'time', return_value=106):
            second = self.store.claim_service_retry('request-1', 101, caps(), action['input_digests'])
        raw = png('blue'); path = self.store._file(raw)
        with self.assertRaises(ValueError):
            self.store.save_image_result('request-1', path, hashlib.sha256(raw).hexdigest(), second['attempt_id'], first['invocation_id'])
        self.assertEqual(self.store.inspect('request-1')['state'], 'dispatched')

    def test_legacy_multi_attempt_without_invocation_identity_requires_reconciliation(self):
        record = {'quality_cycle': {'attempts': [{'number': 1}, {'number': 2}]}}
        with self.assertRaises(ValueError): hs._verify_invocation(record, None)
        with self.assertRaises(ValueError): hs._verify_invocation(record, 'unverified')
        hs._verify_invocation({'quality_cycle': {'attempts': [{'number': 1}]}}, None)

    def test_comparison_action_includes_confirmed_infographic_inventory(self):
        action = self.prepare()
        inventory = {'visible_text': ['Size M'], 'panels': ['front'], 'garment_instances': ['dress']}
        with self.store._db() as db:
            r = self.store._get(db, 'request-1')
            r['confirmed_inventory'] = inventory
            r['plan']['classification'] = 'infographic'
            r['state'] = 'comparison_pending'
            r['uploads'] = {key: {'path': r['ordered_image_paths'][0]} for key in ('candidate', 'candidate_2', 'candidate_3')}
            self.store._put(db, r)
        comparison = self.store.next()
        self.assertEqual(comparison['action'], 'compare_candidates')
        self.assertEqual(comparison['inventory'], inventory)
        self.assertEqual(comparison['classification'], 'infographic')

    def test_parallel_service_claim_only_invokes_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        action = self.prepare()
        with patch.object(hs.time, 'time', return_value=100):
            first = self.store.dispatch('request-1', caps(), action['input_digests'])
        with patch.object(hs.time, 'time', return_value=101):
            self.store.report_service_error('request-1', first['invocation_id'], '503', 0, confirmed_finished=True)
        def claim(_):
            return hs.SessionStore(self.root / 'host.sqlite', 'identity').claim_service_retry('request-1', 101, caps(), action['input_digests'])['should_invoke']
        with patch.object(hs.time, 'time', return_value=106):
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(sum(pool.map(claim, range(2))), 1)

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
        self.assertEqual(self.store.inspect('request-1')['quality_cycle']['attempts'][0]['status'], 'review_pending')
        self.assertIsNone(self.store.result('request-1'))
        qc = self.store.next()
        self.assertEqual(qc['action'], 'review_candidate')
        with self.assertRaises(ValueError):
            self.store.qc('request-1', {'viewed_digests': qc['input_digests'], 'checks': [{'name': n, 'status': 'passed', 'evidence': 'synthetic'} for n in hs.QC_CHECKS]})
        self.store.qc('request-1', {'viewed_digests': qc['input_digests'], 'quality_binding':qc['quality_binding'], 'review_binding':qc['review_binding'], 'skill_report': {'schema_version':1, 'candidate':'candidate_1', 'scores':{'garment_construction':90,'color_material':88,'garment_details':88,'target_preservation':90,'text_layout':None}, 'critical_defects':[], 'primary_defect':None, 'evidence':['visible garment'], 'confidence':0.85, 'decision':'accept', 'exact_text':None,'added_text':None,'missing_text':None,'instances_exact':None,'panel_count_exact':None,'panel_layout_exact':None}, 'checks': [
            {'name': name, 'status': 'passed', 'evidence': 'Visible region inspected'} for name in hs.QC_CHECKS]})
        self.assertEqual(self.store.result('request-1')['image']['artifact_id'], '91')
        self.assertEqual(self.store.inspect('request-1')['quality_cycle']['attempts'][0]['status'], 'early_qc_pass')
        self.store.image('request-1', path, client)
        self.assertEqual(client.calls, 1)

    def test_uncertain_skill_qc_waits_without_regeneration(self):
        from test_outfit_quality import report
        action = self.prepare()
        self.store.dispatch('request-1', caps(), action['input_digests'])
        path = self.root / 'candidate-unknown.png'; path.write_bytes(png('blue'))
        class Upload:
            def create_artifact(inner, case_id, **kwargs):
                return {'artifact_id':91, 'digest':hashlib.sha256(kwargs['data']).hexdigest()}
        self.store.image('request-1', path, Upload()); qc=self.store.next()
        skill=report();skill['confidence']=0.5
        self.store.qc('request-1', {'viewed_digests':qc['input_digests'], 'quality_binding':qc['quality_binding'], 'review_binding':qc['review_binding'], 'skill_report':skill,
            'checks':[{'name':n,'status':'passed','evidence':'visible'} for n in hs.QC_CHECKS]})
        self.assertEqual(self.store.inspect('request-1')['state'],'waiting')
        self.assertEqual(self.store.inspect('request-1')['uploads']['candidate']['artifact_id'],'91')
        self.assertFalse(self.store.dispatch('request-1',caps(),qc['input_digests'])['should_invoke'])

    def test_low_score_accept_can_resume_same_candidate(self):
        from test_outfit_quality import report
        action = self.prepare(); self.store.dispatch('request-1', caps(), action['input_digests'])
        path = self.root / 'resume.png'; path.write_bytes(png('blue'))
        class Upload:
            def create_artifact(inner, case_id, **kwargs):
                return {'artifact_id': 91, 'digest':hashlib.sha256(kwargs['data']).hexdigest()}
        self.store.image('request-1', path, Upload()); qc = self.store.next()
        skill = report(); skill['scores']['garment_construction'] = 40; skill['primary_defect'] = 'wrong_collar'
        evidence = {'viewed_digests':qc['input_digests'], 'quality_binding':qc['quality_binding'], 'review_binding':qc['review_binding'], 'skill_report':skill,
            'checks':[{'name':n,'status':'passed','evidence':'visible'} for n in hs.QC_CHECKS]}
        self.store.qc('request-1', evidence)
        self.assertEqual(self.store.inspect('request-1')['quality_cycle']['attempts'][0]['status'], 'review')
        self.store.consume_review_resume('request-1', 'intent', 1); self.store.acknowledge_review_resume('request-1','intent')
        evidence['skill_report'] = report()
        with self.assertRaises(ValueError): self.store.qc('request-1', evidence)
        evidence['review_binding'] = self.store.next()['review_binding']; self.store.qc('request-1', evidence)
        self.assertIsNotNone(self.store.result('request-1'))
        self.assertEqual(len(self.store.inspect('request-1')['quality_cycle']['attempts']), 1)

    def test_rejected_candidate_allows_one_new_fenced_attempt(self):
        from test_outfit_quality import report
        action = self.prepare()
        first = self.store.dispatch('request-1', caps(), action['input_digests'])
        path = self.root / 'candidate.png'; path.write_bytes(png('blue'))
        class Upload:
            def create_artifact(inner, case_id, **kwargs):
                return {'artifact_id': 91, 'digest': hashlib.sha256(kwargs['data']).hexdigest()}
        self.store.image('request-1', path, Upload())
        qc = self.store.next(); skill = report(); skill.update(decision='reject', critical_defects=['wrong_collar'], primary_defect='wrong_collar')
        self.store.qc('request-1', {'viewed_digests': qc['input_digests'], 'quality_binding': qc['quality_binding'], 'review_binding': qc['review_binding'],
            'skill_report': skill, 'checks': [{'name': n, 'status': 'failed' if n == 'garment_construction' else 'passed', 'evidence':'visible'} for n in hs.QC_CHECKS]})
        action = self.store.next(); self.assertEqual(action['action'], 'generate')
        second = self.store.dispatch('request-1', caps(), action['input_digests'])
        self.assertEqual(second['attempt_number'], 2)
        self.assertNotEqual(second['attempt_id'], first['attempt_id'])
        self.assertTrue(second['invocation']['prompt'].startswith(first['invocation']['prompt']))
        self.assertIn('collar', second['invocation']['prompt'].lower())
        path.write_bytes(png('green'))
        with self.assertRaises(ValueError): self.store.error('request-1', 'unknown')
        with self.assertRaises(ValueError): self.store.error('request-1', 'unknown', invocation_id=first['invocation_id'])
        with self.assertRaises(ValueError): self.store.image('request-1', path, Upload(), second['attempt_id'])
        with self.assertRaises(ValueError): self.store.image('request-1', path, Upload(), first['attempt_id'])
        with self.assertRaises(ValueError): self.store.image('request-1', path, Upload())
        self.store.image('request-1', path, Upload(), second['attempt_id'], second['invocation_id'])
        self.assertEqual(self.store.next()['quality_attempt'], 2)

    def test_reject_without_known_primary_defect_waits(self):
        from test_outfit_quality import report
        action = self.prepare(); self.store.dispatch('request-1', caps(), action['input_digests'])
        path = self.root / 'rejected.png'; path.write_bytes(png('blue'))
        class Upload:
            def create_artifact(inner, case_id, **kwargs):
                return {'artifact_id':91, 'digest':hashlib.sha256(kwargs['data']).hexdigest()}
        self.store.image('request-1', path, Upload()); qc=self.store.next()
        skill=report(); skill.update(decision='reject',critical_defects=['wrong_collar'],primary_defect=None)
        self.store.qc('request-1', {'viewed_digests':qc['input_digests'], 'quality_binding':qc['quality_binding'], 'review_binding':qc['review_binding'],
            'skill_report':skill, 'checks':[{'name':n,'status':'failed','evidence':'visible'} for n in hs.QC_CHECKS]})
        self.assertEqual(self.store.inspect('request-1')['state'],'waiting')
        self.assertEqual(len(self.store.inspect('request-1')['quality_cycle']['attempts']),1)

    def test_late_upload_receipt_cannot_rewind_next_attempt(self):
        from test_outfit_quality import report
        action = self.prepare(); self.store.dispatch('request-1', caps(), action['input_digests'])
        path = self.root / 'racing.png'; path.write_bytes(png('blue'))
        store = self.store
        class Racing:
            def artifact(inner, artifact_id):
                return {'case_id':12, 'company_id':1, 'data':base64.b64encode(path.read_bytes()).decode(), 'mime_type':'image/png'}
            def create_artifact(inner, case_id, **kwargs):
                digest = hashlib.sha256(kwargs['data']).hexdigest()
                store.reconcile('request-1', 'candidate', 91, inner)
                store.image('request-1', path, inner)
                qc=store.next(); skill=report(); skill.update(decision='reject', critical_defects=['wrong_collar'], primary_defect='wrong_collar')
                store.qc('request-1', {'viewed_digests':qc['input_digests'], 'quality_binding':qc['quality_binding'], 'review_binding':qc['review_binding'],
                    'skill_report':skill, 'checks':[{'name':n,'status':'failed','evidence':'visible'} for n in hs.QC_CHECKS]})
                store.dispatch('request-1', caps(), store.next()['input_digests'])
                return {'artifact_id':91, 'digest':digest}
        with self.assertRaises(ValueError): store.image('request-1', path, Racing())
        record=store.inspect('request-1')
        self.assertEqual(record['state'],'dispatched')
        self.assertEqual(len(record['quality_cycle']['attempts']),2)

    def test_best_first_candidate_can_be_reviewed(self):
        self._run_three_candidate_review(1)

    def test_best_third_candidate_can_be_reviewed(self):
        self._run_three_candidate_review(3)

    def _run_three_candidate_review(self, best_number):
        from test_outfit_quality import report
        class Upload:
            def create_artifact(inner, case_id, **kwargs):
                return {'artifact_id': int(hashlib.sha256(kwargs['data']).hexdigest()[:8], 16), 'digest': hashlib.sha256(kwargs['data']).hexdigest()}
        action = self.prepare()
        for n, color in enumerate(('blue', 'green', 'yellow'), 1):
            invocation = self.store.dispatch('request-1', caps(), action['input_digests'])
            path = self.root / f'candidate-{n}.png'; path.write_bytes(png(color))
            self.store.image('request-1', path, Upload(), invocation['attempt_id'], invocation['invocation_id'])
            qc = self.store.next(); skill = report(f'candidate_{n}'); skill.update(decision='reject', critical_defects=['wrong_collar'], primary_defect='wrong_collar')
            self.store.qc('request-1', {'viewed_digests': qc['input_digests'], 'quality_binding': qc['quality_binding'], 'review_binding': qc['review_binding'],
                'skill_report':skill, 'checks':[{'name':name,'status':'failed','evidence':'visible'} for name in hs.QC_CHECKS]})
            action = self.store.next()
        self.assertEqual(action['action'], 'compare_candidates')
        self.assertFalse(self.store.dispatch('request-1', caps(), action['input_digests'])['should_invoke'])
        reports = []
        for n in range(1, 4):
            skill = report(f'candidate_{n}'); skill.update(decision='reject', critical_defects=['wrong_collar'])
            if n == best_number: skill['scores']['garment_construction'] = 99
            reports.append(skill)
        with self.assertRaises(ValueError): self.store.compare('request-1', {'viewed_digests':{}, 'reports':reports})
        self.store.compare('request-1', {'viewed_digests':action['input_digests'], 'reports':reports})
        selected = self.store.inspect('request-1')['quality_cycle']['selection']
        self.assertFalse(selected['quality_passed'])
        self.assertEqual(selected['alias'], f'candidate_{best_number}')
        record = self.store.inspect('request-1')
        self.assertEqual(record['state'], 'waiting')
        self.assertEqual(record['review_seq'], 1)
        self.assertEqual(record['best_of_budget']['artifact_id'], str(int(selected['candidate_digests'][selected['alias']][:8], 16)))
        self.assertEqual(len(self.store.inspect('request-1')['quality_cycle']['attempts']), 3)
        chosen = record['best_of_budget']
        with self.assertRaises(ValueError): self.store.consume_best_resume('request-1','intent',1,dict(chosen, sha256='0'*64))
        self.store.consume_best_resume('request-1','intent',1,chosen)
        self.store.acknowledge_review_resume('request-1','intent')
        self.assertFalse(self.store.consume_best_resume('request-1','intent',1,chosen))
        review = self.store.next(); self.assertEqual(review['action'], 'review_best_candidate')
        self.assertEqual(review['quality_attempt'], int(chosen['alias'][-1]))
        skill = report(chosen['alias'])
        evidence = {'viewed_digests':review['input_digests'], 'quality_binding':review['quality_binding'], 'review_binding':review['review_binding'],
            'skill_report':skill, 'checks':[{'name':name,'status':'passed','evidence':'visible'} for name in hs.QC_CHECKS]}
        evidence['skill_report']['confidence'] = 0.4
        self.store.qc('request-1', evidence)
        self.assertEqual(self.store.inspect('request-1')['review_seq'], 2)
        with self.assertRaises(ValueError): self.store.consume_best_resume('request-1','intent',1,chosen)
        self.store.consume_best_resume('request-1','intent-2',2,chosen)
        self.store.acknowledge_review_resume('request-1','intent-2')
        evidence['skill_report'] = report(chosen['alias'])
        with self.assertRaises(ValueError): self.store.qc('request-1', evidence)
        evidence['review_binding'] = self.store.next()['review_binding']; self.store.qc('request-1', evidence)
        self.assertEqual(self.store.result('request-1')['image']['artifact_id'],chosen['artifact_id'])
        self.assertFalse(self.store.inspect('request-1')['quality_cycle']['selection']['quality_passed'])

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

    def test_cooperative_host_renews_pending_session_while_dispatch_blocks(self):
        import threading, time
        from optimization_worker.cooperative_research_host import ClaimGate, GatedClient, CooperativeResearchHost
        fixture = Fixture(); self.addCleanup(fixture.close)
        raw = Client(fixture.url, 'fixture-key')
        gate = ClaimGate(lambda: True)
        client = GatedClient(raw, gate)
        store = hs.SessionStore(self.root / 'cooperative-host.sqlite', client.identity)
        worker = Worker(client, self.root / 'cooperative-worker.sqlite', providers={'research.keywords': hs.SessionProvider(store)})
        entered, release = threading.Event(), threading.Event()
        def dispatch(): entered.set(); release.wait(3)
        host = CooperativeResearchHost(gate, worker, dispatch, poll_seconds=.02)
        outer = threading.Thread(target=host.tick)
        try:
            outer.start(); self.assertTrue(entered.wait(1))
            deadline = time.monotonic() + 2
            while len([e for e in fixture.events if e[0] == 'heartbeat']) < 2 and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertGreaterEqual(len([e for e in fixture.events if e[0] == 'heartbeat']), 2)
            self.assertEqual(len([e for e in fixture.events if e[0] == 'claim']), 1)
            action=store.next(); self.assertEqual(action['action'],'analyze')
            store.submit(action['request_id'],result(),caps())
            host.stop_new_claims()
            deadline=time.monotonic()+2
            while fixture.state != 'succeeded' and time.monotonic()<deadline: time.sleep(.01)
            self.assertEqual(fixture.state,'succeeded')
        finally:
            host.stop_new_claims(); release.set(); outer.join(2)
            if host.worker_thread: host.worker_thread.join(2)

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

    def test_library_recollection_requires_bound_ordered_manifest_before_upload(self):
        variant={'candidate':'MLM123','product_url':'https://articulo.mercadolibre.com.mx/MLM-123-product-_JM','library_id':19}
        self.store.enqueue('gallery-old', 'gallery.collect', {'schema_version':'1','reference_item_id':'MLM123','selected_variant':variant}, {'snapshot':{'case_id':12,'company_id':2,'payload':{}},'artifacts':{}})
        path=self.root/'observed.png';path.write_bytes(png())
        urls=['https://cdn.example.com/a.png','https://cdn.example.com/b.png']
        result={'schema_version':'1','evidence':[{'id':'page','source':variant['product_url'],'observed_at':'2026-11-15T00:00:00Z'}], 'reference_item_id':'MLM123','selected_variant':variant,'observed_at':'2026-11-15T00:00:00Z','completeness_status':'complete', 'images':[{'image_id':str(i),'order':i,'role':'main' if i==0 else 'gallery','source':url,'evidence_ids':['page']} for i,url in enumerate(urls)], 'browser_observation':{'selected_variant_verified':True,'gallery_only_verified':True,'page_url':variant['product_url'],'screenshot_path':str(path),'observed_gallery_count':2,'blocked':False,'observed_variant':{'color':'Rosa','size':'G'}}}
        class Upload:
            calls=0
            def create_artifact(inner,*args,**kwargs):
                inner.calls+=1;return {'artifact_id':90+inner.calls,'digest':hashlib.sha256(kwargs['data']).hexdigest()}
        client=Upload(); files={'0':str(path),'1':str(path)}
        with self.assertRaises(ValueError): self.store.gallery('gallery-old',result,files,client,caps())
        with self.assertRaises(ValueError): self.store.gallery('gallery-old',result,files,client,caps(),manifest={'detail_url':variant['product_url'],'images':urls[::-1]})
        import copy
        no_variant=copy.deepcopy(result); no_variant['browser_observation'].pop('observed_variant')
        with self.assertRaises(ValueError): self.store.gallery('gallery-old',no_variant,files,client,caps(),manifest={'detail_url':variant['product_url'],'images':urls})
        bad=copy.deepcopy(result); bad['reference_item_id']='MLM999'; bad['selected_variant']['candidate']='MLM999'
        self.assertEqual(client.calls,0)
        self.store.gallery('gallery-old',result,files,client,caps(),manifest={'detail_url':variant['product_url'],'images':urls})
        self.assertEqual([x['source'] for x in self.store.result('gallery-old')['images']],urls)
        variant31=dict(variant,library_id=21)
        self.store.enqueue('gallery-31','gallery.collect',{'schema_version':'1','reference_item_id':'MLM123','selected_variant':variant31},{'snapshot':{'case_id':12,'company_id':2,'payload':{}},'artifacts':{}})
        urls31=['https://cdn.example.com/'+str(i)+'.png' for i in range(31)]
        result31=copy.deepcopy(result);result31['selected_variant']=variant31;result31['browser_observation']['observed_gallery_count']=31
        result31['images']=[{'image_id':str(i),'order':i,'role':'main' if i==0 else 'gallery','source':url,'evidence_ids':['page']} for i,url in enumerate(urls31)]
        self.store.gallery('gallery-31',result31,{str(i):str(path) for i in range(31)},client,caps(),manifest={'detail_url':variant['product_url'],'images':urls31})
        self.assertEqual([x['source'] for x in self.store.result('gallery-31')['images']],urls31)

        forged=dict(variant, candidate='MLM999', library_id=20)
        self.store.enqueue('gallery-forged','gallery.collect',{'schema_version':'1','reference_item_id':'MLM999','selected_variant':forged},{'snapshot':{'case_id':12,'company_id':2,'payload':{}},'artifacts':{}})
        wrong=copy.deepcopy(result); wrong['reference_item_id']='MLM999'; wrong['selected_variant']=forged
        with self.assertRaises(ValueError): self.store.gallery('gallery-forged',wrong,files,client,caps(),manifest={'detail_url':variant['product_url'],'images':urls})


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
        hs.fence(Lease(), journal_path, 'request-1', task_id=21)
        with self.assertRaises(ValueError):
            hs.fence(Lease(), journal_path, 'request-1', task_id=22)

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

    def test_stale_old_request_does_not_shadow_live_work_and_keeps_unknown(self):
        self.prepare()
        action = self.store.next()
        self.store.dispatch('request-1', caps(), action['input_digests'])
        self.store.error('request-1', 'unknown')
        self.store.enqueue('request-new', 'research.keywords', {'schema_version': '1', 'facts_artifact_id': '3', 'site_id': 'MLM'}, self.context)
        self.store.synchronize([{'request_id': 'request-1', 'done': True, 'outcome': 'lease_lost'}, {'request_id': 'request-new', 'claim': {'id': 30}}])
        self.assertEqual(self.store.next()['request_id'], 'request-new')
        audit = self.store.inspect('request-1')
        self.assertTrue(audit['retired'])
        self.assertEqual(audit['state'], 'unknown')
        self.assertFalse(self.store.dispatch('request-1', caps(), action['input_digests'])['should_invoke'])
        path = self.root / 'late.png'
        path.write_bytes(png('blue'))
        self.store.archive_image('request-1', path)
        self.assertTrue(Path(self.store.inspect('request-1')['late_result_path']).is_file())

    def test_moderation_or_known_failure_finishes_original_worker_and_next_claim(self):
        for outcome in ('failed', 'moderation_blocked'):
            with self.subTest(outcome=outcome):
                fixture = Fixture()
                self.addCleanup(fixture.close)
                client = Client(fixture.url, 'fixture-key')
                store = hs.SessionStore(self.root / (outcome + '-host.sqlite'), client.identity)
                worker = Worker(client, self.root / (outcome + '-worker.sqlite'), providers={'research.keywords': hs.SessionProvider(store)})
                self.assertEqual(worker.once(), 'host_pending')
                action = store.next()
                store.error(action['request_id'], outcome)
                self.assertEqual(worker.once(), 'failed')
                failures = [event[1]['body'] for event in fixture.events if event[0] == 'fail']
                self.assertEqual(failures[0]['code'], outcome)
                self.assertEqual(store.next()['action'], 'idle')
                fixture.state = 'queued'
                fixture.claim['id'] += 1
                self.assertEqual(worker.once(), 'host_pending')
                self.assertNotEqual(store.next()['request_id'], action['request_id'])

    def test_concurrent_retirement_and_dispatch_never_reactivates_retired_request(self):
        from concurrent.futures import ThreadPoolExecutor
        action = self.prepare()
        def retire():
            self.store.synchronize([])
        def dispatch():
            return self.store.dispatch('request-1', caps(), action['input_digests'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            retired = pool.submit(retire)
            claim = pool.submit(dispatch)
            retired.result()
            claim.result()
        self.assertTrue(self.store.inspect('request-1')['retired'])
        self.assertFalse(self.store.dispatch('request-1', caps(), action['input_digests'])['should_invoke'])
        self.assertEqual(self.store.next()['action'], 'idle')

    def test_standalone_qc_without_provenance_does_not_resolve_any_target(self):
        from optimization_worker.handlers import prepare
        class Inputs:
            def snapshot(inner, ref):
                return {'payload': {}}
            def artifact(inner, ref):
                if ref == '3':
                    return {'payload': {'operation': 'garment_swap', 'source_artifact_ids': ['2'], 'target_artifact_ids': ['4']}}
                return {'data': 'verified-' + ref}
        context = prepare(Inputs(), {'task_type': 'image.qc', 'snapshot_id': 15,
            'payload': {'schema_version': '1', 'image_artifact_id': '1', 'generation_plan_id': '3'}})
        self.assertEqual(set(context['artifacts']), {'1', '2', '3'})
        self.assertNotIn('qc_target_artifact_id', context)

    def test_live_synchronization_skips_stale_fence_and_does_not_retire_concurrent_enqueue(self):
        from optimization_worker.journal import Journal
        from optimization_worker.client import APIError
        journal_path = self.root / 'live-sync.sqlite'
        journal = Journal(journal_path, 'identity')
        journal.save({'claim': {'id': 21, 'input_hash': 'a', 'lease_epoch': 1}, 'request_id': 'request-1'})
        journal.close()
        self.prepare()
        store = self.store
        context = self.context
        class RejectedLease:
            def heartbeat(inner, claim):
                # A Worker can save/enqueue a fresh request after the host's
                # authoritative snapshot but before it applies retirement.
                store.enqueue('concurrent-new', 'research.keywords', {'schema_version': '1', 'facts_artifact_id': '3', 'site_id': 'MLM'}, context)
                raise APIError(409)
        hs.synchronize_live(RejectedLease(), store, journal_path)
        self.assertTrue(store.inspect('request-1')['retired'])
        self.assertFalse(store.inspect('concurrent-new').get('retired', False))
        self.assertEqual(store.next()['request_id'], 'concurrent-new')

    def test_retired_upload_can_reconcile_existing_receipt_without_unretiring(self):
        action = self.prepare()
        self.store.dispatch('request-1', caps(), action['input_digests'])
        path = self.root / 'late-result.png'
        path.write_bytes(png('blue'))
        class Lost:
            def create_artifact(inner, *args, **kwargs):
                raise OSError('response lost')
            def artifact(inner, ref):
                return {'data': base64.b64encode(png('blue')).decode(), 'mime_type': 'image/png', 'case_id': 12, 'company_id': 1}
        client = Lost()
        with self.assertRaises(OSError):
            self.store.image('request-1', path, client)
        self.store.synchronize([])
        self.store.reconcile('request-1', 'candidate', '95', client)
        retired = self.store.inspect('request-1')
        self.assertEqual(retired['uploads']['candidate']['artifact_id'], '95')
        self.assertTrue(retired['retired'])
        self.assertEqual(self.store.next()['action'], 'idle')
        with self.assertRaises(ValueError):
            self.store.image('request-1', path, client)

class ResearchResumeActionTests(unittest.TestCase):
    def test_prepared_research_exposes_bound_plan_for_resumption(self):
        import tempfile
        from pathlib import Path
        from optimization_worker.host_session import SessionStore
        with tempfile.TemporaryDirectory() as tmp:
            store=SessionStore(Path(tmp)/'host.sqlite','identity')
            bound={'plan':{'queries':['one','two','three'],'platform':'shein-us'},'collector_id':'rec123','digest':'digest'}
            with store._db() as db:
                store._put(db,{'request_id':'r','task_type':'research.bestseller','state':'prepared','payload':{},'context':{},'images':{},'collector_plan':bound})
            action=store.next(kinds={'research.bestseller'})
            self.assertEqual(action['collector_plan'],bound['plan'])
            self.assertEqual(action['collector_id'],'rec123')

    def test_pending_gallery_exposes_immutable_manifest_for_resumption(self):
        import tempfile
        from pathlib import Path
        from optimization_worker.host_session import SessionStore
        with tempfile.TemporaryDirectory() as tmp:
            store=SessionStore(Path(tmp)/'host.sqlite','identity')
            manifest={'detail_url':'https://example.test/item','images':['https://example.test/a'],'screenshot_path':'/private/screenshot','screenshot_sha256':'hash'}
            with store._db() as db:
                store._put(db,{'request_id':'r','task_type':'research.bestseller','state':'candidate_images_pending','payload':{},'context':{},'images':{},'collector_pending':{'result':{'candidates':[]},'gallery_manifests':{'platform:1':manifest}}})
            action=store.next(kinds={'research.bestseller'})
            self.assertEqual(action['gallery_manifests']['platform:1']['images'],manifest['images'])
            self.assertNotIn('screenshot_path',action['gallery_manifests']['platform:1'])

"""Pure domain behavior; namespace loading avoids addon/Odoo model initialization."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import sys
import types
import unittest
from pathlib import Path

addon = Path(__file__).resolve().parents[1] / 'addons' / 'meli_optimization'
pkg = types.ModuleType('optimization_unit')
pkg.__path__ = [str(addon)]
sys.modules.setdefault('optimization_unit', pkg)
try:
    domain = importlib.import_module('optimization_unit.domain')
except ModuleNotFoundError as exc:
    if not exc.name.startswith('optimization_unit.domain'):
        raise
    domain = None


def evidence():
    return [{'id': 'source:any-id', 'source': 'https://example.test/item',
             'observed_at': '2026-11-15T00:00:00Z'}]

class InternalImageQCContract(unittest.TestCase):
    def test_manual_accept_preserves_report_and_identifies_decision(self):
        digest = 'a' * 64
        qc = {'status':'manual_accepted','candidate_sha256':digest,'report':{'decision':'reject'},'decision_event_id':42}
        domain.validate_internal_image_qc({'image':{'sha256':digest},'visual_qc':qc})
        for invalid in (dict(qc, decision_event_id=0), dict(qc, decision_event_id=True), dict(qc, candidate_sha256='b'*64)):
            with self.assertRaises(domain.DomainError):
                domain.validate_internal_image_qc({'image':{'sha256':digest},'visual_qc':invalid})
    def test_completed_legacy_report_can_be_checked_without_accepting_new_legacy_results(self):
        digest = 'a' * 64
        names = ('garment_construction', 'color_material', 'garment_details', 'identity_pose',
                 'accessories', 'scene_lighting_framing', 'text_layout')
        report = {'quality_binding': {'candidate_sha256': digest}, 'skill_report': {'decision': 'accept'},
                  'checks': [{'name': name, 'status': 'passed'} for name in names]}
        result = {'image': {'sha256': digest}, 'visual_qc': report}
        domain.validate_internal_image_qc(result, allow_legacy=True)
        with self.assertRaises(domain.DomainError):
            domain.validate_internal_image_qc(result)
        report['checks'][0]['status'] = 'failed'
        with self.assertRaises(domain.DomainError):
            domain.validate_internal_image_qc(result, allow_legacy=True)

    def test_passed_report_binds_the_actual_image(self):
        digest = 'a' * 64
        result = {'image': {'sha256': digest}, 'visual_qc': {'status': 'passed', 'candidate_sha256': digest, 'report': {'decision': 'accept'}}}
        domain.validate_internal_image_qc(result)
        for qc in ({'status': 'failed', 'candidate_sha256': digest, 'report': {}},
                   {'status': 'passed', 'candidate_sha256': digest, 'report': {}},
                   {'status': 'passed', 'candidate_sha256': 'b' * 64, 'report': {}},
                   {'candidate_sha256': digest, 'report': {}}, None):
            with self.subTest(qc=qc), self.assertRaises(domain.DomainError):
                domain.validate_internal_image_qc({'image': {'sha256': digest}, 'visual_qc': qc})


def titles():
    return {'schema_version': '1', 'evidence': evidence(),
            'candidates': [{'text': 'Vestido elegante modelo %s' % n,
                            'evidence_ids': ['source:any-id']} for n in range(10)]}


def research():
    return {'schema_version': '1', 'status': 'partial', 'reason': 'pool_exhausted',
            'evidence': evidence(), 'candidates': [{
                'platform': 'MLM', 'item_id': 'MLM123',
                'url': 'https://example.test/item', 'raw_url': 'https://example.test/item?x=1',
                'query': 'vestido', 'position': 1, 'is_ad': None,
                'sold_display': '+500 vendidos', 'sold_lower_bound': 500,
                'sold_upper_bound': None, 'review_count': None, 'rating': None,
                'image_url': 'https://example.test/image.jpg', 'visual_category': 'dress',
                'observed_at': '2026-11-15T00:00:00Z',
                'selection_reason': 'same garment', 'evidence_ids': ['source:any-id']} ]}


def gallery():
    return {'schema_version': '1', 'reference_item_id': 'MLM123',
            'selected_variant': {'color': 'black'}, 'observed_at': '2026-11-15T00:00:00Z',
            'gallery_manifest_hash': 'a' * 64, 'completeness_status': 'complete',
            'evidence': evidence(), 'images': [{
                'image_id': 'img-1', 'order': 0, 'role': 'front', 'artifact_id': 'art:1',
                'source': 'https://example.test/image.jpg', 'evidence_ids': ['source:any-id'],
                'mime_type': 'image/jpeg', 'width': 1200, 'height': 1600,
                'sha256': 'b' * 64}]}


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(domain, 'pure optimization domain implementation is missing')

    def reject(self, code, function, *args):
        with self.assertRaises(domain.DomainError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)
        self.assertTrue(str(caught.exception))

    def test_hash_canonicalizes_nested_key_order_and_unicode(self):
        expected = hashlib.sha256('{"a":["ñ",1],"z":null}'.encode()).hexdigest()
        self.assertEqual(domain.canonical_hash({'z': None, 'a': ['ñ', 1]}), expected)
        self.assertNotEqual(domain.canonical_hash([1, 2]), domain.canonical_hash([2, 1]))

    def test_hash_rejects_unpaired_surrogate_keys_and_values(self):
        for value in ({'bad': '\ud800'}, {'\udfff': 'bad'}):
            with self.subTest(value=repr(value)):
                self.reject('invalid_json', domain.canonical_hash, value)

    def test_non_json_values_are_rejected_not_coerced(self):
        for value in (float('nan'), float('inf'), {1: 'bad'}, {'x': (1, 2)}, {'x': object()}):
            with self.subTest(value=value):
                self.reject('invalid_json', domain.canonical_hash, value)

    def test_input_types_have_required_fields_and_preserve_extensions(self):
        cases = {
            'product.facts': {'product_snapshot_id': 'snap'},
            'research.keywords': {'facts_artifact_id': 'facts', 'site_id': 'MLM'},
            'research.bestseller': {'facts_artifact_id': 'facts', 'keywords_artifact_id': 'kw', 'site_id': 'MLM'},
            'gallery.collect': {'reference_item_id': 'item', 'selected_variant': {'size': 'M'}},
            'title.generate': {'facts_artifact_id': 'facts', 'keywords_artifact_id': 'kw', 'language': 'es-MX'},
            'image.generate': {'source_artifact_ids': ['source'], 'target_artifact_id': 'target', 'generation_plan_id': 'plan'},
            'image.qc': {'image_artifact_id': 'image', 'generation_plan_id': 'plan'},
        }
        for task_type, fields in cases.items():
            value = dict(fields, schema_version='1', extension={'unknown': None})
            with self.subTest(task_type=task_type):
                self.assertIs(domain.validate_input(task_type, value), value)
                for field in fields:
                    bad = value.copy(); del bad[field]
                    self.reject('invalid_input', domain.validate_input, task_type, bad)
        self.reject('unsupported_task', domain.validate_input, 'not.a.task', {})
        self.reject('unsupported_task', domain.validate_result, 'not.a.task', {})
        self.reject('invalid_input', domain.validate_input, 'product.facts', {'schema_version': '2'})

    def test_transition_policy_blocks_terminal_reopen_and_skipped_execution(self):
        for before, after in [('draft', 'queued'), ('queued', 'running'), ('running', 'succeeded'),
                              ('running', 'waiting'), ('waiting', 'queued'), ('running', 'retry_wait'),
                              ('retry_wait', 'queued'), ('draft', 'cancelled')]:
            domain.validate_transition(before, after)
        for before, after in [('succeeded', 'queued'), ('failed', 'running'), ('queued', 'succeeded'),
                              ('draft', 'draft'), ('unknown', 'queued')]:
            self.reject('invalid_transition', domain.validate_transition, before, after)

    def test_lease_rejects_expiry_stale_token_epoch_and_non_utc_time(self):
        now = datetime(2026, 11, 15, tzinfo=timezone.utc)
        active = {'state': 'running', 'lease_token': 'lease', 'lease_epoch': 2}
        domain.validate_lease(active, 'lease', 2, now + timedelta(seconds=1), now)
        for token, epoch, expiry in [('old', 2, now + timedelta(seconds=1)),
                                      ('lease', 1, now + timedelta(seconds=1)), ('lease', 2, now)]:
            self.reject('invalid_lease', domain.validate_lease, active, token, epoch, expiry, now)
        self.reject('invalid_lease', domain.validate_lease, dict(active, state='queued'), 'lease', 2, now + timedelta(seconds=1), now)
        self.reject('invalid_lease', domain.validate_lease, active, 'lease', True, now + timedelta(seconds=1), now)
        self.reject('invalid_lease', domain.validate_lease, active, 'lease', 2, now.replace(tzinfo=None), now)

    def test_titles_require_ten_distinct_grounded_candidates_without_length_cap(self):
        value = titles(); value['candidates'][0]['text'] = 'Vestido ' + 'elegante ' * 100
        self.assertIs(domain.validate_title_candidates(value), value)
        self.assertIs(domain.validate_result('title.generate', value), value)
        for mutation in ('count', 'duplicate', 'evidence', 'blank'):
            bad = titles()
            if mutation == 'count': bad['candidates'].pop()
            elif mutation == 'duplicate': bad['candidates'][1]['text'] = '  VESTIDO   ELEGANTE MODELO 0 '
            elif mutation == 'blank': bad['candidates'][1]['text'] = '  '
            else: bad['candidates'][1]['evidence_ids'] = ['invented']
            self.reject('invalid_result', domain.validate_title_candidates, bad)

    def test_research_keeps_unknown_metrics_and_raw_sales_and_explicit_status(self):
        value = research(); original = copy.deepcopy(value)
        self.assertIs(domain.validate_research_candidates(value), value)
        self.assertEqual(value, original)
        self.assertIs(domain.validate_result('research.bestseller', value), value)
        empty = dict(value, status='empty', candidates=[])
        domain.validate_research_candidates(empty)
        for updates in ({'status': 'complete', 'candidates': []}, {'status': 'empty'},
                        {'reason': ''}, {'status': 'success'}):
            self.reject('invalid_result', domain.validate_research_candidates, dict(value, **updates))

    def test_research_rejects_fabricated_evidence_and_mistyped_metrics(self):
        for field, replacement in [('evidence_ids', ['invented']), ('sold_lower_bound', True),
                                   ('rating', '4.5'), ('review_count', -1), ('is_ad', 'unknown')]:
            bad = research(); bad['candidates'][0][field] = replacement
            self.reject('invalid_result', domain.validate_research_candidates, bad)
        bad = research(); bad['evidence'][0]['observed_at'] = 'yesterday'
        self.reject('invalid_result', domain.validate_research_candidates, bad)

    def test_gallery_requires_identity_order_image_metadata_and_evidence(self):
        value = gallery()
        self.assertIs(domain.validate_gallery(value), value)
        self.assertIs(domain.validate_result('gallery.collect', value), value)
        for field, replacement in [('width', 0), ('height', True), ('mime_type', 'text/plain'),
                                   ('sha256', 'bad'), ('evidence_ids', []), ('artifact_id', '')]:
            bad = gallery(); bad['images'][0][field] = replacement
            self.reject('invalid_result', domain.validate_gallery, bad)
        bad = gallery(); bad['images'].append(dict(bad['images'][0], image_id='different'))
        self.reject('invalid_result', domain.validate_gallery, bad)

    def test_other_results_validate_typed_evidence_and_quality_outcomes(self):
        cases = {
            'product.facts': {'facts': [{'name': 'category', 'value': 'dress', 'evidence_ids': ['source:any-id']}], 'uncertainties': []},
            'research.keywords': {'status': 'complete', 'keywords': [{'text': 'vestido', 'evidence_ids': ['source:any-id']}]},
            'image.generate': {'image': gallery()['images'][0], 'request_id': 'req-1', 'visual_qc': {'status': 'passed', 'candidate_sha256': gallery()['images'][0]['sha256'], 'report': {'decision': 'accept'}}},
            'image.qc': {'image_artifact_id': 'img', 'status': 'passed', 'checks': [
                {'name': 'garment', 'status': 'passed', 'evidence_ids': ['source:any-id']}]},
        }
        for task_type, fields in cases.items():
            value = dict(fields, schema_version='1', evidence=evidence())
            with self.subTest(task_type=task_type):
                self.assertIs(domain.validate_result(task_type, value), value)
                self.reject('invalid_result', domain.validate_result, task_type, {'schema_version': '1'})
        bad = dict(cases['image.qc'], schema_version='1', evidence=evidence())
        bad['checks'][0]['status'] = 'unknown'
        self.reject('invalid_result', domain.validate_result, 'image.qc', bad)

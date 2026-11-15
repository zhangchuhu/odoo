from odoo.tests import TransactionCase, tagged
from odoo.exceptions import AccessError, ValidationError
from ..domain import canonical_hash


@tagged('post_install', '-at_install')
class WorkbenchCase(TransactionCase):
    def setUp(self):
        super().setUp()
        self.ui = self.env['meli.optimization.case']

    def create_case(self, **extra):
        return self.ui.workbench_create(dict({'shop': 'Test shop', 'item_id': 'MLM1', 'title': 'Original'}, **extra))

    def test_create_snapshot_preserves_unknown(self):
        before = self.ui.workbench_overview()['counts']['cases']
        detail = self.create_case()
        self.assertIsNone(detail['snapshot']['payload']['available_quantity'])
        self.assertEqual(detail['snapshot']['payload']['source'], 'manual')
        self.assertEqual(self.ui.workbench_overview()['counts']['cases'], before + 1)

    def test_worker_readonly_and_company_boundary(self):
        detail = self.create_case()
        company = self.env['res.company'].create({'name': 'Other workbench company'})
        worker = self.env['res.users'].create({'name': 'Readonly', 'login': 'workbench-worker',
            'company_id': company.id, 'company_ids': [(6, 0, company.ids)],
            'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        scoped = self.ui.with_user(worker).with_context(allowed_company_ids=company.ids)
        self.assertEqual(scoped.workbench_overview()['counts']['cases'], 0)
        with self.assertRaises(AccessError):
            scoped.workbench_detail(detail['id'])
        with self.assertRaises(AccessError):
            scoped.workbench_create({'shop': 'x', 'item_id': 'x', 'title': 'x'})

    def test_proposal_exact_candidate_and_stale_snapshot(self):
        detail = self.create_case()
        task = self.env['meli.optimization.task'].create({'case_id': detail['id'],
            'snapshot_id': detail['snapshot']['id'], 'task_type': 'title.generate',
            'payload': {'schema_version': '1', 'facts_artifact_id': 'facts', 'keywords_artifact_id': 'keywords', 'language': 'es-MX'}})
        claim = task.claim(['title.generate'])
        result = {'schema_version': '1', 'evidence': [{'id': 'e', 'source': 'test', 'observed_at': '2026-11-15T00:00:00Z'}],
            'candidates': [{'text': 'Candidate %s' % i, 'evidence_ids': ['e']} for i in range(10)]}
        task.complete(claim['lease_token'], claim['lease_epoch'], 'done', result)
        with self.assertRaises(ValidationError):
            self.ui.workbench_propose(detail['id'], task.id, 'changed-result', 'title', [2])
        proposal = self.ui.workbench_propose(detail['id'], task.id, canonical_hash(result), 'title', [2])
        self.assertEqual(proposal['payload']['desired_value'], 'Candidate 2')
        with self.assertRaises(ValidationError):
            self.ui.workbench_confirm(detail['id'], proposal['id'], 'wrong')
        self.assertTrue(self.ui.workbench_confirm(detail['id'], proposal['id'], proposal['digest']))
        other = self.create_case(item_id='MLM2')
        with self.assertRaises(ValidationError):
            self.ui.workbench_propose(other['id'], task.id, canonical_hash(result), 'title', [0])
        self.ui.workbench_snapshot(detail['id'], {'shop': 'Test shop', 'item_id': 'MLM1', 'title': 'New'})
        with self.assertRaises(ValidationError):
            self.ui.workbench_propose(detail['id'], task.id, canonical_hash(result), 'title', [0])
        with self.assertRaises(ValidationError):
            self.ui.workbench_confirm(detail['id'], proposal['id'], proposal['digest'])

    def test_task_control_and_no_lease_exposure(self):
        detail = self.create_case()
        task = self.ui.workbench_task(detail['id'], 'product.facts', {})
        self.ui.workbench_control(detail['id'], task, 'cancel')
        row = self.ui.workbench_detail(detail['id'])['tasks'][0]
        self.assertEqual(row['state'], 'cancelled')
        self.assertNotIn('lease_token', row)
        with self.assertRaises(ValidationError):
            self.ui.workbench_task(detail['id'], 'ads.fake', {})

    def test_batch_filter_and_artifact_case_ownership(self):
        detail = self.create_case(batch_name='Filter target')
        case = self.ui.browse(detail['id'])
        result = self.ui.workbench_overview(batch_id=case.batch_id.id)
        self.assertEqual([row['id'] for row in result['rows']], [case.id])
        other = self.create_case(item_id='OTHER')
        artifact = self.env['meli.optimization.artifact'].create({'case_id': other['id'], 'payload': {}, 'digest': canonical_hash({})})
        with self.assertRaises(ValidationError):
            self.ui.workbench_task(case.id, 'research.keywords', {'facts_artifact_id': str(artifact.id), 'site_id': 'MLM'})

    def test_gallery_proposal_requires_owned_binary_evidence(self):
        detail = self.create_case()
        task = self.env['meli.optimization.task'].create({'case_id': detail['id'], 'snapshot_id': detail['snapshot']['id'],
            'task_type': 'gallery.collect', 'payload': {'schema_version': '1', 'reference_item_id': 'REF', 'selected_variant': {'sku': 'A'}}})
        result = {'schema_version': '1', 'evidence': [{'id': 'e', 'source': 'fixture', 'observed_at': '2026-11-15T00:00:00Z'}],
            'reference_item_id': 'REF', 'selected_variant': {'sku': 'A'}, 'observed_at': '2026-11-15T00:00:00Z',
            'gallery_manifest_hash': 'a' * 64, 'completeness_status': 'complete', 'images': [
                {'image_id': 'A', 'order': 0, 'role': 'front', 'artifact_id': '999999', 'source': 'fixture',
                 'evidence_ids': ['e'], 'mime_type': 'image/png', 'width': 10, 'height': 10, 'sha256': 'a' * 64}]}
        claim = task.claim(['gallery.collect'])
        task.complete(claim['lease_token'], claim['lease_epoch'], 'gallery', result)
        with self.assertRaises(ValidationError):
            self.ui.workbench_propose(detail['id'], task.id, canonical_hash(result), 'images', [0])

    def test_archive_result_advances_fresh_workflow_and_is_idempotent(self):
        detail = self.create_case()
        facts_id = self.ui.workbench_task(detail['id'], 'product.facts', {})
        task = self.env['meli.optimization.task'].browse(facts_id)
        result = {'schema_version': '1', 'evidence': [{'id': 'e', 'source': 'snapshot', 'observed_at': '2026-11-15T00:00:00Z'}],
            'facts': [{'name': 'title', 'value': 'Original', 'evidence_ids': ['e']}], 'uncertainties': []}
        with self.assertRaises(ValidationError):
            self.ui.workbench_archive(detail['id'], task.id, canonical_hash(result))
        claim = task.claim(['product.facts'])
        task.complete(claim['lease_token'], claim['lease_epoch'], 'facts', result)
        with self.assertRaises(ValidationError):
            self.ui.workbench_archive(detail['id'], task.id, 'wrong')
        artifact_id = self.ui.workbench_archive(detail['id'], task.id, canonical_hash(result))
        self.assertEqual(self.ui.workbench_archive(detail['id'], task.id, canonical_hash(result)), artifact_id)
        artifact = self.env['meli.optimization.artifact'].browse(artifact_id)
        self.assertEqual(artifact.payload, result)
        self.assertEqual(artifact.result_task_id, task)
        self.assertEqual(artifact.digest, canonical_hash(result))
        keywords_id = self.ui.workbench_task(detail['id'], 'research.keywords', {'facts_artifact_id': str(artifact_id), 'site_id': 'MLM'})
        keywords = self.env['meli.optimization.task'].browse(keywords_id)
        output = {'schema_version': '1', 'evidence': result['evidence'], 'status': 'complete', 'keywords': [{'text': 'Vestido', 'evidence_ids': ['e']}]}
        claim = keywords.claim(['research.keywords'])
        keywords.complete(claim['lease_token'], claim['lease_epoch'], 'keywords', output)
        keyword_artifact = self.ui.workbench_archive(detail['id'], keywords.id, canonical_hash(output))
        self.assertTrue(self.ui.workbench_task(detail['id'], 'title.generate', {'facts_artifact_id': str(artifact_id), 'keywords_artifact_id': str(keyword_artifact), 'language': 'es-MX'}))
        other = self.create_case(item_id='OTHER')
        with self.assertRaises(ValidationError):
            self.ui.workbench_archive(other['id'], task.id, canonical_hash(result))
        self.ui.workbench_snapshot(detail['id'], {'shop': 'Test shop', 'item_id': 'MLM1', 'title': 'Changed'})
        with self.assertRaises(ValidationError):
            self.ui.workbench_archive(detail['id'], task.id, canonical_hash(result))

    def test_search_matches_displayed_snapshot_identity(self):
        detail = self.create_case(title='Obsolete Unique Name')
        self.ui.workbench_snapshot(detail['id'], {'shop': 'Test shop', 'item_id': 'CURRENT-ITEM', 'title': 'Current Unique Name'})
        self.assertEqual([r['id'] for r in self.ui.workbench_overview(query='Current Unique')['rows']], [detail['id']])
        self.assertEqual([r['id'] for r in self.ui.workbench_overview(query='CURRENT-ITEM')['rows']], [detail['id']])
        self.assertFalse(self.ui.workbench_overview(query='Obsolete Unique')['rows'])

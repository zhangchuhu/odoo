from odoo.tests import TransactionCase, tagged
from odoo.exceptions import AccessError, ValidationError
from ..domain import canonical_hash
import base64
import io
from PIL import Image


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

    def test_collection_started_only_matches_current_collection_snapshot(self):
        detail = self.ui.workbench_collection_create({'garment': 'dress', 'source_sku': 'SKU1'})
        case = self.ui.browse(detail['id'])
        self.assertFalse(self.ui.workbench_detail(case.id)['collection_started'])
        snap = self.ui._wb_snapshot(case)
        case._set({'collection_started_snapshot_id': snap.id})
        self.assertTrue(self.ui.workbench_detail(case.id)['collection_started'])
        self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': dict(snap.payload)})
        self.assertFalse(self.ui.workbench_detail(case.id)['collection_started'])
        self.assertFalse(self.create_case()['collection_started'])

    def test_source_images_follow_snapshot_order_in_overview_and_detail(self):
        detail = self.create_case()
        case = self.ui.browse(detail['id'])
        refs = []
        for color in ('red', 'blue'):
            data = io.BytesIO()
            Image.new('RGB', (2, 2), color).save(data, 'PNG')
            refs.append(self.ui.workbench_upload_garment(case.id, base64.b64encode(data.getvalue()).decode(), 'image/png'))
        payload = dict(detail['snapshot']['payload'], source_image_artifact_ids=[str(refs[1]), str(refs[0])])
        self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': payload})
        rows = self.ui.workbench_overview(query='MLM1')['rows']
        expected = [refs[1], refs[0]]
        self.assertEqual([x['artifact_id'] for x in rows[0]['source_images']], expected)
        self.assertEqual([x['artifact_id'] for x in self.ui.workbench_detail(case.id)['snapshot']['source_images']], expected)

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
            'snapshot_id': detail['snapshot']['id'], 'task_type': 'product.facts',
            'payload': {'schema_version': '1', 'product_snapshot_id': str(detail['snapshot']['id'])}})
        # Isolate proposal behavior from dependency validation.
        task._set({'task_type': 'title.generate'})
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

    def test_attempt_history_has_only_scoped_safe_fields(self):
        detail = self.create_case()
        task_id = self.ui.workbench_task(detail['id'], 'product.facts', {})
        task = self.env['meli.optimization.task'].browse(task_id)
        task.claim(['product.facts'])
        row = next(t for t in self.ui.workbench_detail(detail['id'])['tasks'] if t['id'] == task_id)
        self.assertEqual(len(row['attempts']), 1)
        self.assertEqual(set(row['attempts'][0]), {'epoch', 'created_at'})
        self.assertEqual(row['attempts'][0]['epoch'], 1)

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

    def test_title_flow_advances_and_is_idempotent(self):
        detail = self.create_case()
        case = self.ui.browse(detail['id'])
        initial = self.ui.workbench_title_flow(case.id)
        self.assertEqual(initial['stage'], 'product.facts')
        self.assertEqual(self.ui.workbench_title_flow(case.id)['task_id'], initial['task_id'])
        facts = self.env['meli.optimization.task'].browse(initial['task_id'])
        evidence = [{'id': 'e', 'source': 'snapshot', 'observed_at': '2026-11-15T00:00:00Z'}]
        claim = facts.claim(['product.facts'])
        facts.complete(claim['lease_token'], claim['lease_epoch'], 'flow-facts', {'schema_version':'1','evidence':evidence,'facts':[{'name':'title','value':'Original','evidence_ids':['e']}],'uncertainties':[]})
        keywords = self.env['meli.optimization.task'].search([('case_id','=',case.id),('task_type','=','research.keywords')])
        self.assertEqual(len(keywords), 1)
        claim = keywords.claim(['research.keywords'])
        keywords.complete(claim['lease_token'], claim['lease_epoch'], 'flow-keywords', {'schema_version':'1','evidence':evidence,'status':'complete','keywords':[{'text':'vestido','evidence_ids':['e']}]})
        titles = self.env['meli.optimization.task'].search([('case_id','=',case.id),('task_type','=','title.generate')])
        self.assertEqual(len(titles), 1)
        self.assertEqual(self.ui.workbench_title_flow(case.id)['task_id'], titles.id)

    def test_title_flow_does_not_advance_empty_keywords_or_stale_snapshot(self):
        detail = self.create_case()
        case = self.ui.browse(detail['id'])
        facts = self.env['meli.optimization.task'].browse(self.ui.workbench_title_flow(case.id)['task_id'])
        evidence = [{'id': 'e', 'source': 'snapshot', 'observed_at': '2026-11-15T00:00:00Z'}]
        claim = facts.claim(['product.facts'])
        facts.complete(claim['lease_token'], claim['lease_epoch'], 'empty-flow-facts', {'schema_version':'1','evidence':evidence,'facts':[{'name':'title','value':'Original','evidence_ids':['e']}],'uncertainties':[]})
        keywords = self.env['meli.optimization.task'].search([('case_id','=',case.id),('task_type','=','research.keywords')])
        claim = keywords.claim(['research.keywords'])
        keywords.complete(claim['lease_token'], claim['lease_epoch'], 'empty-flow-keywords', {'schema_version':'1','evidence':evidence,'status':'empty','keywords':[],'reason':'no evidence'})
        self.assertFalse(self.env['meli.optimization.task'].search([('case_id','=',case.id),('task_type','=','title.generate')]))
        self.ui.workbench_snapshot(case.id, {'shop':'Test','item_id':'MLM1','title':'Changed'})
        self.assertEqual(self.ui.workbench_title_flow(case.id)['stage'], 'product.facts')

    def test_replayed_completion_never_recreates_cancelled_stage(self):
        detail = self.create_case()
        case = self.ui.browse(detail['id'])
        facts = self.env['meli.optimization.task'].browse(self.ui.workbench_title_flow(case.id)['task_id'])
        evidence = [{'id':'e','source':'snapshot','observed_at':'2026-11-15T00:00:00Z'}]
        result = {'schema_version':'1','evidence':evidence,'facts':[{'name':'title','value':'Original','evidence_ids':['e']}],'uncertainties':[]}
        claim = facts.claim(['product.facts'])
        facts.complete(claim['lease_token'], claim['lease_epoch'], 'replay-flow-facts', result)
        keywords = self.env['meli.optimization.task'].search([('case_id','=',case.id),('task_type','=','research.keywords')])
        self.assertEqual(len(keywords), 1)
        keywords.cancel()
        facts.complete(claim['lease_token'], claim['lease_epoch'], 'replay-flow-facts', result)
        self.assertEqual(self.ui.workbench_title_flow(case.id)['task_id'], keywords.id)
        self.assertEqual(self.env['meli.optimization.task'].search_count([('case_id','=',case.id),('task_type','=','research.keywords')]), 1)
        self.assertEqual(keywords.state, 'cancelled')

    def test_old_snapshot_completion_does_not_advance_current_flow(self):
        detail = self.create_case()
        case = self.ui.browse(detail['id'])
        facts = self.env['meli.optimization.task'].browse(self.ui.workbench_title_flow(case.id)['task_id'])
        self.ui.workbench_snapshot(case.id, {'shop':'Test','item_id':'MLM1','title':'Changed'})
        claim = facts.claim(['product.facts'])
        evidence = [{'id':'e','source':'snapshot','observed_at':'2026-11-15T00:00:00Z'}]
        facts.complete(claim['lease_token'], claim['lease_epoch'], 'stale-flow-facts', {'schema_version':'1','evidence':evidence,'facts':[{'name':'title','value':'Original','evidence_ids':['e']}],'uncertainties':[]})
        self.assertFalse(self.env['meli.optimization.task'].search([('case_id','=',case.id),('task_type','=','research.keywords')]))

    def test_title_flow_rejects_facts_as_keywords(self):
        detail = self.create_case()
        task_id = self.ui.workbench_task(detail['id'], 'product.facts', {})
        task = self.env['meli.optimization.task'].browse(task_id)
        result = {'schema_version': '1', 'evidence': [{'id': 'e', 'source': 'snapshot', 'observed_at': '2026-11-15T00:00:00Z'}], 'facts': [{'name': 'title', 'value': 'Original', 'evidence_ids': ['e']}], 'uncertainties': []}
        claim = task.claim(['product.facts'])
        task.complete(claim['lease_token'], claim['lease_epoch'], 'test-flow-facts', result)
        fact_id = self.ui.workbench_archive(detail['id'], task.id, canonical_hash(result))
        with self.assertRaises(ValidationError):
            self.ui.workbench_task(detail['id'], 'title.generate', {'facts_artifact_id': str(fact_id), 'keywords_artifact_id': str(fact_id), 'language': 'es-MX'})
        count = self.env['meli.optimization.task'].search_count([('case_id', '=', detail['id'])])
        with self.assertRaises(ValidationError):
            self.env['meli.optimization.task'].create({'case_id': detail['id'], 'snapshot_id': detail['snapshot']['id'], 'task_type': 'title.generate', 'payload': {'schema_version': '1', 'facts_artifact_id': str(fact_id), 'keywords_artifact_id': str(fact_id), 'language': 'es-MX'}})
        self.assertEqual(count, self.env['meli.optimization.task'].search_count([('case_id', '=', detail['id'])]))

    def test_search_matches_displayed_snapshot_identity(self):
        detail = self.create_case(title='Obsolete Unique Name')
        self.ui.workbench_snapshot(detail['id'], {'shop': 'Test shop', 'item_id': 'CURRENT-ITEM', 'title': 'Current Unique Name'})
        self.assertEqual([r['id'] for r in self.ui.workbench_overview(query='Current Unique')['rows']], [detail['id']])
        self.assertEqual([r['id'] for r in self.ui.workbench_overview(query='CURRENT-ITEM')['rows']], [detail['id']])
        self.assertFalse(self.ui.workbench_overview(query='Obsolete Unique')['rows'])

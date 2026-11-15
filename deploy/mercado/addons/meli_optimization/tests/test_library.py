"""Disposable, explicitly synthetic archived image fixtures; no external traffic."""
import base64
import hashlib
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import AccessError, ValidationError
from ..domain import canonical_hash


@tagged('post_install', '-at_install')
class LibraryCase(TransactionCase):
    def setUp(self):
        super().setUp()
        self.ui = self.env['meli.optimization.case']

    def collection(self):
        return self.ui.workbench_collection_create({'name': 'Test only dress research',
            'source_url': 'https://example.test/source', 'garment': 'test dress', 'site_id': 'MLM'})

    def gallery(self, detail):
        raw = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC')
        artifact = self.env['meli.optimization.artifact'].create({'case_id': detail['id'],
            'data': base64.b64encode(raw), 'digest': hashlib.sha256(raw).hexdigest(), 'mime_type': 'image/png'})
        task_id = self.ui.workbench_task(detail['id'], 'gallery.collect',
            {'reference_item_id': 'TEST-REF', 'selected_variant': {'seller_sku': 'TEST-SKU'}})
        task = self.env['meli.optimization.task'].browse(task_id)
        result = {'schema_version': '1', 'evidence': [{'id': 'fixture', 'source': 'https://example.test/item', 'observed_at': '2026-11-15T00:00:00Z'}],
            'reference_item_id': 'TEST-REF', 'selected_variant': {'seller_sku': 'TEST-SKU'},
            'observed_at': '2026-11-15T00:00:00Z', 'gallery_manifest_hash': 'a' * 64,
            'completeness_status': 'complete', 'images': [{'image_id': 'TEST-IMAGE', 'order': 0,
                'role': 'front', 'artifact_id': str(artifact.id), 'source': 'https://example.test/item',
                'evidence_ids': ['fixture'], 'mime_type': 'image/png', 'width': 1, 'height': 1, 'sha256': artifact.digest}]}
        claim = task.claim(['gallery.collect'])
        task.complete(claim['lease_token'], claim['lease_epoch'], 'test-gallery', result)
        return task, artifact

    def test_standalone_collection_and_idempotent_library(self):
        detail = self.collection()
        overview = self.ui.workbench_overview()
        self.assertFalse(any(r['id'] == detail['id'] for r in overview['rows']))
        collection_batch = self.ui.browse(detail['id']).batch_id.id
        self.assertFalse(any(b['id'] == collection_batch for b in overview['batches']))
        self.assertIn(detail['id'], [r['id'] for r in self.ui.workbench_collections()])
        task, artifact = self.gallery(detail)
        entry = self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(task.result))
        self.assertEqual(entry, self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(task.result)))
        row = self.ui.workbench_library('TEST-REF')['rows'][0]
        self.assertEqual(row['id'], entry)
        self.assertEqual(row['payload']['images'][0]['sha256'], artifact.digest)
        with self.assertRaises(AccessError):
            self.env['meli.optimization.library'].browse(entry).write({'name': 'tampered'})

    def test_import_rejects_wrong_hash_stale_missing_and_wrong_sku(self):
        detail = self.collection()
        task, artifact = self.gallery(detail)
        with self.assertRaises(ValidationError):
            self.ui.workbench_library_import(detail['id'], task.id, 'wrong')
        original = task.result
        task._set({'result': dict(original, selected_variant={'seller_sku': 'FORGED'})})
        with self.assertRaises(ValidationError):
            self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(task.result))
        task._set({'result': original})
        artifact._set({'data': False})
        with self.assertRaises(ValidationError):
            self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(task.result))
        self.env['meli.optimization.snapshot'].create({'case_id': detail['id'], 'payload': {'changed': True}})
        with self.assertRaises(ValidationError):
            self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(task.result))

    def test_cross_case_selection_freezes_exact_scope_and_origin(self):
        detail = self.collection()
        task, artifact = self.gallery(detail)
        library_id = self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(task.result))
        destination = self.ui.workbench_create({'shop': 'Test', 'item_id': 'DEST', 'title': 'Destination'})
        source = self.env['meli.optimization.artifact'].create({'case_id': destination['id'], 'data': artifact.data,
            'digest': artifact.digest, 'mime_type': artifact.mime_type})
        selected = self.ui.workbench_library_generate(destination['id'], library_id, [0], [source.id])
        self.assertEqual(len(selected['task_ids']), 1)
        generated = self.env['meli.optimization.task'].browse(selected['task_ids'])
        clone = self.env['meli.optimization.artifact'].browse(int(generated.payload['target_artifact_id']))
        self.assertEqual(clone.case_id.id, destination['id'])
        self.assertEqual(clone.library_origin_id.id, library_id)
        self.assertEqual(clone.library_artifact_id, artifact)
        plan = self.env['meli.optimization.artifact'].browse(int(generated.payload['generation_plan_id']))
        self.assertEqual(plan.payload['target_count'], 1)
        self.assertEqual(plan.payload['source_artifact_ids'], [str(source.id)])
        self.assertEqual(plan.payload['library_result_hash'], canonical_hash(task.result))
        with self.assertRaises(ValidationError):
            self.ui.workbench_library_generate(destination['id'], library_id, [0, 0], [source.id])
        company = self.env['res.company'].create({'name': 'Other library company'})
        cross = self.ui.with_context(allowed_company_ids=[company.id, self.env.company.id])
        other = cross.workbench_create({'shop': 'Other', 'item_id': 'OTHER', 'title': 'Other company'})
        with self.assertRaises(AccessError):
            cross.workbench_library_generate(other['id'], library_id, [0], [source.id])

    def test_binary_content_and_dimensions_must_be_real_images(self):
        detail = self.collection()
        task, artifact = self.gallery(detail)
        bad = b'not an image'
        digest = hashlib.sha256(bad).hexdigest()
        artifact._set({'data': base64.b64encode(bad), 'digest': digest})
        result = dict(task.result)
        result['images'] = [dict(result['images'][0], sha256=digest)]
        task._set({'result': result})
        with self.assertRaises(ValidationError):
            self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(result))

    def test_library_rejects_non_web_source_and_other_company_reads(self):
        detail = self.collection()
        task, artifact = self.gallery(detail)
        original = task.result
        forged = dict(original, images=[dict(original['images'][0], source='file:///etc/passwd')])
        task._set({'result': forged})
        with self.assertRaises(ValidationError):
            self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(forged))
        task._set({'result': original})
        entry = self.ui.workbench_library_import(detail['id'], task.id, canonical_hash(original))
        company = self.env['res.company'].create({'name': 'Excluded library company'})
        worker = self.env['res.users'].create({'name': 'Library readonly', 'login': 'library-excluded',
            'company_id': company.id, 'company_ids': [(6, 0, company.ids)],
            'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        scoped = self.ui.with_user(worker).with_context(allowed_company_ids=company.ids)
        self.assertFalse(scoped.workbench_library()['rows'])
        with self.assertRaises(AccessError):
            self.env['meli.optimization.library'].with_user(worker).with_context(allowed_company_ids=company.ids).browse(entry).read(['payload'])
        with self.assertRaises(AccessError):
            scoped.workbench_library_import(detail['id'], task.id, canonical_hash(original))

    def test_upload_source_reference_preserves_actual_bytes_in_collection_snapshot(self):
        import io
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (3, 4), 'navy').save(buffer, format='PNG')
        data = base64.b64encode(buffer.getvalue()).decode()
        detail = self.ui.workbench_collection_create({'garment': 'Test source image', 'image_data': data, 'image_mime': 'image/png'})
        references = detail['snapshot']['payload']['source_image_artifact_ids']
        self.assertEqual(len(references), 1)
        self.assertEqual(self.env['meli.optimization.artifact'].browse(references).data.decode(), data)
        with self.assertRaises(ValidationError):
            self.ui.workbench_upload_garment(detail['id'], base64.b64encode(b'not an image').decode(), 'image/png')

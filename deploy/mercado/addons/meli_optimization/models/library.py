"""Persistent verified gallery library. URLs are provenance, never fetch targets."""
import base64
import hashlib
import io
from PIL import Image, UnidentifiedImageError
from urllib.parse import urlsplit
from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from .optimization import PREFIX, ExactJson, bounded, MAX_BINARY
from ..domain import canonical_hash, validate_gallery


def verified_image(raw, mime_type, dimensions=None):
    try:
        if len(raw) > MAX_BINARY:
            raise ValueError('Image exceeds 8 MiB')
        image = Image.open(io.BytesIO(raw))
        if Image.MIME.get(image.format) != mime_type or image.width * image.height > 40_000_000:
            raise ValueError('Unsupported image MIME or dimensions')
        if dimensions and image.size != dimensions:
            raise ValueError('Image dimensions do not match evidence')
        image.verify()
    except (ValueError, OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValidationError('图片内容、尺寸或媒体类型无法验证') from exc


class Library(models.Model):
    _name = PREFIX + 'library'
    _inherit = PREFIX + 'protected'
    _description = 'Verified company image library'
    _internal_only = True
    name = fields.Char(required=True)
    site_id = fields.Char(required=True)
    case_id = fields.Many2one(PREFIX + 'case', required=True, ondelete='restrict')
    task_id = fields.Many2one(PREFIX + 'task', required=True, ondelete='restrict')
    snapshot_id = fields.Many2one(PREFIX + 'snapshot', required=True, ondelete='restrict')
    result_hash = fields.Char(required=True)
    payload = ExactJson(required=True)
    _unique_import = models.Constraint('UNIQUE(task_id, result_hash)', 'Gallery already imported')


class LibraryArtifact(models.Model):
    _inherit = PREFIX + 'artifact'
    library_origin_id = fields.Many2one(PREFIX + 'library', readonly=True, ondelete='restrict')
    library_artifact_id = fields.Many2one(PREFIX + 'artifact', readonly=True, ondelete='restrict')

    @api.model_create_multi
    def create(self, vals_list):
        protected = {'library_origin_id', 'library_artifact_id'}
        if any(protected.intersection(v) for v in vals_list) or any('default_' + k in self.env.context for k in protected):
            raise AccessError('Library provenance is assigned only by checked selection')
        return super().create(vals_list)


class CollectionWorkbench(models.Model):
    _inherit = PREFIX + 'case'
    collection = fields.Boolean(default=False, readonly=True, index=True)

    @api.model
    def workbench_collection_create(self, values):
        self._authorize()
        if not isinstance(values, dict) or not str(values.get('garment', '')).strip():
            raise ValidationError('请填写源服装描述')
        source_url = str(values.get('source_url') or '').strip()
        if source_url and urlsplit(source_url).scheme not in ('https', 'http'):
            raise ValidationError('来源链接仅支持 HTTP(S)，不会由服务器抓取')
        name = str(values.get('name') or values['garment'])[:200]
        batch = self.env[PREFIX + 'batch'].create({'name': '独立爆款采集'})
        case = self.create({'name': name, 'batch_id': batch.id, 'collection': True})
        source_ids = []
        if values.get('image_data'):
            source_ids.append(self.workbench_upload_garment(case.id, values['image_data'], values.get('image_mime')))
        self.env[PREFIX + 'snapshot'].create({'case_id': case.id, 'payload': {
            'source_image_artifact_ids': source_ids, 'garment': str(values['garment'])[:4000], 'source_url': source_url[:2000],
            'site_id': str(values.get('site_id') or 'MLM')[:30], 'source': 'user_supplied_research'}})
        self.workbench_task(case.id, 'product.facts', {})
        return self.workbench_detail(case.id)

    @api.model
    def workbench_collections(self):
        self._authorize('worker')
        return [{'id': c.id, 'name': c.name, 'snapshot': self._wb_snapshot(c).payload}
                for c in self.search([('collection', '=', True)], order='id desc', limit=200)]

    @api.model
    def workbench_upload_garment(self, case_id, data, mime_type):
        case = self._wb_case(case_id, True)
        if mime_type not in ('image/png', 'image/jpeg', 'image/webp'):
            raise ValidationError('服装参考图支持 PNG、JPEG 或 WebP')
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValidationError('图片编码无效') from exc
        verified_image(raw, mime_type)
        return self.env[PREFIX + 'artifact'].create({'case_id': case.id, 'data': data,
            'mime_type': mime_type, 'digest': hashlib.sha256(raw).hexdigest()}).id

    def _library_image(self, case, image):
        ref = str(image.get('artifact_id', ''))
        artifact = self.env[PREFIX + 'artifact'].browse(int(ref)).exists() if ref.isdigit() else self.env[PREFIX + 'artifact']
        if not artifact:
            raise ValidationError('图片缺少归档产物')
        artifact._authorize('worker')
        if artifact.case_id != case or artifact.company_id != case.company_id or not artifact.data or artifact.digest != image.get('sha256') or artifact.mime_type != image.get('mime_type'):
            raise ValidationError('图片归属、二进制、摘要或媒体类型未验证')
        raw = base64.b64decode(artifact.data)
        verified_image(raw, artifact.mime_type, (image['width'], image['height']))
        if hashlib.sha256(raw).hexdigest() != artifact.digest:
            raise ValidationError('归档图片摘要不匹配')
        return artifact

    @api.model
    def workbench_library_import(self, case_id, task_id, result_hash):
        case = self._wb_case(case_id, True)
        case._lock()
        task = self.env[PREFIX + 'task'].browse(int(task_id)).exists()
        task._authorize('worker')
        if not task or task.case_id != case or task.snapshot_id != self._wb_snapshot(case) or task.task_type != 'gallery.collect' or task.state != 'succeeded' or canonical_hash(task.result) != result_hash:
            raise ValidationError('仅可导入当前快照的已完成且未改变的图库结果')
        validate_gallery(task.result)
        if any(task.result[k] != task.payload[k] for k in ('reference_item_id', 'selected_variant')):
            raise ValidationError('采集结果商品或 SKU 不匹配')
        for image in task.result['images']:
            origin = urlsplit(image['source'])
            if origin.scheme not in ('https', 'http') or not origin.hostname or origin.username or origin.password:
                raise ValidationError('图库图片必须保留有效的 HTTP(S) 来源链接')
            self._library_image(case, image)
        library = self.env[PREFIX + 'library']
        existing = library.search([('task_id', '=', task.id), ('result_hash', '=', result_hash)], limit=1)
        if existing:
            return existing.id
        payload = dict(task.result, source_snapshot=self._wb_snapshot(case).payload)
        bounded(payload)
        return library._append({'company_id': case.company_id.id, 'case_id': case.id, 'task_id': task.id,
            'snapshot_id': task.snapshot_id.id, 'result_hash': result_hash, 'name': task.result['reference_item_id'],
            'site_id': str(self._wb_snapshot(case).payload.get('site_id') or 'unknown'), 'payload': payload}).id

    @api.model
    def workbench_library(self, query='', site=''):
        self._authorize('worker')
        domain = [('site_id', '=', site)] if site else []
        if query:
            domain += [('name', 'ilike', str(query)[:200])]
        entries = self.env[PREFIX + 'library'].search(domain, order='id desc', limit=200)
        return {'rows': [{'id': e.id, 'name': e.name, 'site_id': e.site_id, 'result_hash': e.result_hash,
            'payload': e.payload, 'company_id': e.company_id.id} for e in entries]}

    @api.model
    def workbench_library_generate(self, case_id, library_id, indices, source_artifact_ids, expected_revision=None):
        case = self._wb_case(case_id, True)
        case._lock()
        if type(expected_revision) is not int or expected_revision != case.revision:
            raise ValidationError('目标商品快照已改变或版本缺失，请刷新并重新选择图库')
        library = self.env[PREFIX + 'library'].browse(int(library_id)).exists()
        if not library:
            raise ValidationError('图库不存在')
        library._authorize('worker')
        if library.company_id != case.company_id:
            raise AccessError('图库与目标商品必须属于同一公司')
        images = library.payload['images']
        if not isinstance(indices, list) or not indices or any(type(i) is not int or i < 0 or i >= len(images) for i in indices) or len(set(indices)) != len(indices):
            raise ValidationError('请选择不重复的有效图片')
        if not isinstance(source_artifact_ids, list) or not source_artifact_ids or any(type(i) not in (int, str) or not str(i).isdigit() for i in source_artifact_ids) or len(set(source_artifact_ids)) != len(source_artifact_ids):
            raise ValidationError('请单独选择源服装参考图')
        artifacts = self.env[PREFIX + 'artifact']
        for ref in source_artifact_ids:
            source = artifacts.browse(int(ref)).exists()
            source._authorize('worker')
            if not source or source.case_id != case or source.company_id != case.company_id or not source.data or source.mime_type not in ('image/png', 'image/jpeg', 'image/webp'):
                raise ValidationError('源服装必须为当前商品的已归档图片')
            try:
                raw = base64.b64decode(source.data, validate=True)
            except (ValueError, TypeError) as exc:
                raise ValidationError('源服装图片编码无效') from exc
            verified_image(raw, source.mime_type)
            if hashlib.sha256(raw).hexdigest() != source.digest:
                raise ValidationError('源服装归档图片摘要不匹配')
        clones = []
        for index in indices:
            original = self._library_image(library.case_id, images[index])
            clones.append(artifacts._append({'case_id': case.id, 'company_id': case.company_id.id,
                'data': original.data, 'digest': original.digest, 'mime_type': original.mime_type,
                'library_origin_id': library.id, 'library_artifact_id': original.id}))
        plan_payload = {'schema_version': '1', 'operation': 'garment_swap', 'case_id': case.id,
            'snapshot_id': self._wb_snapshot(case).id, 'input_revision': expected_revision, 'library_id': library.id,
            'library_result_hash': library.result_hash, 'selected_indices': indices, 'target_count': len(indices),
            'source_artifact_ids': [str(i) for i in source_artifact_ids], 'target_artifact_ids': [str(a.id) for a in clones],
            'preserve': ['identity', 'pose', 'accessories', 'scene', 'lighting', 'framing']}
        plan = artifacts.create({'case_id': case.id, 'payload': plan_payload, 'digest': bounded(plan_payload)})
        task_ids = [self.workbench_task(case.id, 'image.generate', {'source_artifact_ids': plan_payload['source_artifact_ids'],
            'target_artifact_id': str(a.id), 'generation_plan_id': str(plan.id)}) for a in clones]
        return {'task_ids': task_ids, 'generation_plan_id': plan.id, 'target_count': len(indices)}

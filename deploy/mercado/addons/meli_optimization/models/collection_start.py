"""Session-only one-click collection using an existing immutable snapshot.

RPC: workbench_collection_start(case_id, expected_snapshot_id, expected_revision).
No user-supplied pipeline inputs or inferred threshold defaults. The case row
lock/fence serializes this operation against worker completion and archiving.
The call queues or reuses work; it never invokes an external provider or resumes
waiting, failed or cancelled tasks. Successful stages use the existing derived
pipeline. Responses expose actual task state, never claimed browser capability.
"""
import base64
import hashlib
import math

from odoo import api, models
from odoo.exceptions import AccessError, ValidationError
from .optimization import PREFIX
from .library import verified_image
from ..domain import canonical_hash, validate_result


class CollectionStart(models.Model):
    _inherit = PREFIX + 'case'

    def _collection_start_missing(self, snapshot):
        """Validate persisted inputs, including old imports without full config."""
        if not snapshot or not isinstance(snapshot.payload, dict):
            return ['snapshot']
        payload = snapshot.payload
        missing = []
        for field in ('source_sku', 'garment'):
            if not isinstance(payload.get(field), str) or not payload[field].strip():
                missing.append(field)
        config = payload.get('collection_config')
        if not isinstance(config, dict):
            missing.append('collection_config')
        else:
            expected = {'platform', 'result_limit', 'min_sold', 'min_reviews', 'min_rating'}
            if set(config) != expected:
                missing.append('collection_config')
            platform = config.get('platform')
            if platform not in ('mercado-libre-mx', 'shein-us'):
                missing.append('platform')
            expected_site = {'mercado-libre-mx': 'MLM', 'shein-us': 'SHEIN_US'}.get(platform) if isinstance(platform, str) else None
            if not expected_site or payload.get('site_id') != expected_site:
                missing.append('site_id')
            for key in ('min_sold', 'min_reviews', 'result_limit'):
                value = config.get(key)
                if type(value) is not int or value < (1 if key == 'result_limit' else 0):
                    missing.append(key)
            rating = config.get('min_rating')
            if type(rating) not in (int, float) or not math.isfinite(rating) or not 0 <= rating <= 5:
                missing.append('min_rating')
        refs = payload.get('source_image_artifact_ids')
        if (not isinstance(refs, list) or not refs or
                any(type(ref) not in (str, int) or not str(ref).isdigit() for ref in refs)
                or len({str(ref) for ref in refs}) != len(refs)):
            missing.append('source_image_artifact_ids')
        else:
            for ref in refs:
                artifact = self.env[PREFIX + 'artifact'].browse(int(ref)).exists()
                if (not artifact or artifact.case_id != self or artifact.company_id != self.company_id
                        or not artifact.data or artifact.mime_type not in ('image/png', 'image/jpeg', 'image/webp')):
                    missing.append('source_image_artifact_ids')
                    break
                artifact._authorize('worker')
                try:
                    raw = base64.b64decode(artifact.data, validate=True)
                    verified_image(raw, artifact.mime_type)
                    if hashlib.sha256(raw).hexdigest() != artifact.digest:
                        raise ValueError('Source image digest changed')
                except (ValueError, TypeError, ValidationError):
                    missing.append('source_image_artifact_ids')
                    break
        return list(dict.fromkeys(missing))

    def _collection_start_response(self, snapshot, task=None, *, reused=True, code=None, message=None, missing=None):
        detail = (task.status_detail or {}) if task else {}
        return {'case_id': self.id, 'snapshot_id': snapshot.id if snapshot else False,
                'revision': self.revision, 'task_id': task.id if task else False,
                'stage': task.task_type if task else 'configuration',
                'state': task.state if task else None, 'reused': reused,
                'code': code or detail.get('code') or (task.state if task else 'collection_data_missing'),
                'message': message or detail.get('message') or ('已复用采集流程，当前执行状态见任务列表。' if reused else '采集流程已排队，将按事实、关键词、爆款研究顺序执行。'),
                'can_start': not bool(missing), 'missing_fields': missing or []}

    @api.model
    def workbench_collection_start(self, case_id, expected_snapshot_id, expected_revision):
        case = self._wb_case(case_id, True)
        if case.company_id != self.env.company or not case.collection:
            raise AccessError('仅可启动当前公司的独立采集')
        case._lock()
        case._collection_case_fence()
        if case.collection_archived:
            raise ValidationError('已归档采集不能开始，请返回采集列表')
        snapshot = self._wb_snapshot(case)
        if (type(expected_snapshot_id) is not int or type(expected_revision) is not int
                or not snapshot or snapshot.id != expected_snapshot_id or case.revision != expected_revision):
            raise ValidationError('采集快照已改变，请刷新后再开始')
        missing = case._collection_start_missing(snapshot)
        if missing:
            return case._collection_start_response(snapshot, code='collection_data_missing', missing=missing,
                message='此历史采集缺少原图、平台或筛选配置，无法开始；请归档后使用完整数据重新创建。')
        case._set({'collection_started_snapshot_id': snapshot.id})
        tasks = self.env[PREFIX + 'task'].search([
            ('case_id', '=', case.id), ('snapshot_id', '=', snapshot.id), ('research_archived', '=', False),
            ('task_type', 'in', ['product.facts', 'research.keywords', 'research.bestseller'])], order='id desc')
        requests = self.env[PREFIX + 'external.request'].search([
            ('task_id', 'in', tasks.ids), ('state', 'in', ['unknown', 'dispatched']), ('retry_superseded', '=', False)], order='id desc', limit=1)
        if requests:
            current = requests.task_id
            if current.state == 'waiting' and (current.status_detail or {}).get('message'):
                return case._collection_start_response(snapshot, current)
            return case._collection_start_response(snapshot, current, code='external_result_unknown',
                message='已有外部请求正在执行或结果尚未核实；保留原任务，核实前不会重复请求。')
        values = {}
        for kind in ('product.facts', 'research.keywords', 'research.bestseller'):
            # Match lineage, not just task kind. A historical artifact must never
            # silently supply dependencies for a different facts/keyword result.
            expected = dict(values)
            if kind == 'product.facts':
                expected['product_snapshot_id'] = str(snapshot.id)
            current = tasks.filtered(lambda t: t.task_type == kind and all(
                t.payload.get(key) == value for key, value in expected.items()))[:1]
            if not current:
                task_id = case.workbench_task(case.id, kind, values)
                current = self.env[PREFIX + 'task'].browse(task_id)
                return case._collection_start_response(snapshot, current, reused=False)
            if current.state != 'succeeded':
                return case._collection_start_response(snapshot, current)
            validate_result(kind, current.result)
            if kind == 'research.keywords' and (current.result.get('status') == 'empty' or not current.result.get('keywords')):
                return case._collection_start_response(snapshot, current, code='keywords_empty',
                    message='关键词研究已结束但没有可验证关键词，未创建爆款研究任务。')
            if kind == 'product.facts' and not current.result.get('facts'):
                return case._collection_start_response(snapshot, current, code='facts_empty',
                    message='商品事实为空，未创建关键词任务。')
            if not current._attempt_derived():
                return case._collection_start_response(snapshot, current)
            if kind == 'research.bestseller':
                return case._collection_start_response(snapshot, current,
                    code=(current.status_detail or {}).get('code') or 'collection_complete',
                    message=(current.status_detail or {}).get('message') or '采集研究已完成。')
            artifact_id = case.workbench_archive(case.id, current.id, canonical_hash(current.result))
            if kind == 'product.facts':
                values = {'facts_artifact_id': str(artifact_id), 'site_id': snapshot.payload['site_id']}
            else:
                values = dict(values, keywords_artifact_id=str(artifact_id))
            # The derived action may have created the next task while this case
            # lock is held. Refresh the query before deciding anything is absent.
            tasks = self.env[PREFIX + 'task'].search([
                ('case_id', '=', case.id), ('snapshot_id', '=', snapshot.id), ('research_archived', '=', False)], order='id desc')

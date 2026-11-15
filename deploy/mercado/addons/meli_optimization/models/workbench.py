"""Session-authenticated operations facade. Never returns worker leases or credentials."""
from odoo import api, models
from odoo.exceptions import AccessError, ValidationError
from .optimization import PREFIX, bounded
from ..domain import canonical_hash

TASK_INPUTS = {
    'product.facts': [], 'research.keywords': ['facts_artifact_id', 'site_id'],
    'research.bestseller': ['facts_artifact_id', 'keywords_artifact_id', 'site_id'],
    'gallery.collect': ['reference_item_id', 'selected_variant'],
    'title.generate': ['facts_artifact_id', 'keywords_artifact_id', 'language'],
    'image.generate': ['source_artifact_ids', 'target_artifact_id', 'generation_plan_id'],
    'image.qc': ['image_artifact_id', 'generation_plan_id'],
}


class OperationsWorkbench(models.Model):
    _inherit = PREFIX + 'case'

    def _wb_case(self, case_id, edit=False):
        self._authorize('editor' if edit else 'worker')
        case = self.browse(int(case_id)).exists()
        if not case:
            raise AccessError('商品流程不存在或无权访问')
        case.ensure_one()
        case._authorize('editor' if edit else 'worker')
        return case

    def _wb_snapshot(self, case):
        return self.env[PREFIX + 'snapshot'].search([('case_id', '=', case.id)], order='id desc', limit=1)

    @api.model
    def workbench_overview(self, query='', page=0, batch_id=False, state='', shop='', site=''):
        self._authorize('worker')
        page = max(0, int(page))
        domain = []
        if query:
            domain += [('name', 'ilike', str(query)[:200])]
        if batch_id:
            domain += [('batch_id', '=', int(batch_id))]
        # Snapshot scope filters are observations, never platform account access grants.
        rows = []
        for case in self.search(domain, order='id desc'):
            snap = self._wb_snapshot(case)
            payload = snap.payload if snap and isinstance(snap.payload, dict) else {}
            tasks = self.env[PREFIX + 'task'].search([('case_id', '=', case.id)])
            if shop and payload.get('shop') != shop or site and payload.get('site_id') != site:
                continue
            if state and not any(t.state == state for t in tasks):
                continue
            rows.append({'id': case.id, 'name': case.name, 'revision': case.revision,
                'batch': case.batch_id.name, 'batch_id': case.batch_id.id, 'product': payload,
                'tasks': len(tasks), 'waiting': len(tasks.filtered(lambda t: t.state == 'waiting')),
                'failed': len(tasks.filtered(lambda t: t.state == 'failed'))})
        task_model = self.env[PREFIX + 'task']
        proposals = self.env[PREFIX + 'proposal'].search([])
        confirmed = self.env[PREFIX + 'confirmation'].search([]).mapped('proposal_id')
        return {'rows': rows[page * 20:(page + 1) * 20], 'total': len(rows), 'page': page,
            'counts': {'cases': self.search_count([]), 'batches': self.env[PREFIX + 'batch'].search_count([]),
                'waiting': task_model.search_count([('state', '=', 'waiting')]),
                'failed': task_model.search_count([('state', '=', 'failed')]),
                'pending': len(proposals - confirmed)},
            'batches': self.env[PREFIX + 'batch'].search_read([], ['name'], order='id desc'),
            'shops': sorted({str(r['product'].get('shop')) for r in rows if r['product'].get('shop')}),
            'company': self.env.company.display_name,
            'can_edit': self.env.su or self.env.user.has_group('meli_optimization.group_editor'),
            'task_inputs': TASK_INPUTS}

    @api.model
    def workbench_detail(self, case_id):
        case = self._wb_case(case_id)
        snap = self._wb_snapshot(case)
        tasks = self.env[PREFIX + 'task'].search([('case_id', '=', case.id)], order='id desc')
        proposals = self.env[PREFIX + 'proposal'].search([('case_id', '=', case.id)], order='id desc')
        confirmations = self.env[PREFIX + 'confirmation'].search([('proposal_id', 'in', proposals.ids)])
        return {'id': case.id, 'name': case.name, 'revision': case.revision, 'batch': case.batch_id.name,
            'snapshot': {'id': snap.id, 'payload': snap.payload, 'digest': snap.digest} if snap else None,
            'tasks': [{'id': t.id, 'task_type': t.task_type, 'state': t.state,
                'snapshot_id': t.snapshot_id.id, 'result': t.result,
                'result_hash': canonical_hash(t.result), 'status_detail': t.status_detail} for t in tasks],
            'artifacts': self.env[PREFIX + 'artifact'].search_read([('case_id', '=', case.id)], ['digest', 'mime_type', 'payload']),
            'proposals': [{'id': p.id, 'digest': p.digest, 'payload': p.payload,
                'current': p.input_revision == case.revision,
                'confirmed': p in confirmations.mapped('proposal_id')} for p in proposals],
            'events': self.env[PREFIX + 'event'].search_read([('task_id', 'in', tasks.ids)], ['kind', 'task_id', 'create_date', 'receipt'], order='id desc', limit=100)}

    def _wb_product(self, values):
        if not isinstance(values, dict):
            raise ValidationError('商品字段无效')
        result = {}
        for key in ('shop', 'item_id', 'title', 'seller_sku', 'image_url', 'site_id'):
            result[key] = str(values.get(key) or '').strip()[:2000]
        if not all(result[k] for k in ('shop', 'item_id', 'title')):
            raise ValidationError('请填写店铺、商品 ID 和原标题')
        result['site_id'] = result['site_id'] or 'MLM'
        quantity = values.get('available_quantity')
        if quantity in ('', None):
            quantity = None
        elif type(quantity) is not int or quantity < 0:
            raise ValidationError('库存必须为空或非负整数')
        result.update(available_quantity=quantity, value_status='unknown' if quantity is None else 'known', source='manual')
        return result

    @api.model
    def workbench_create(self, values):
        self._authorize()
        payload = self._wb_product(values)
        batch_id = values.get('batch_id')
        if batch_id:
            batch = self.env[PREFIX + 'batch'].browse(int(batch_id)).exists()
            batch.ensure_one()
            batch._authorize()
        else:
            batch = self.env[PREFIX + 'batch'].create({'name': str(values.get('batch_name') or '手动优化批次')[:200]})
        case = self.create({'name': payload['item_id'] + ' · ' + payload['title'], 'batch_id': batch.id})
        self.env[PREFIX + 'snapshot'].create({'case_id': case.id, 'payload': payload})
        return self.workbench_detail(case.id)

    @api.model
    def workbench_snapshot(self, case_id, values):
        case = self._wb_case(case_id, True)
        self.env[PREFIX + 'snapshot'].create({'case_id': case.id, 'payload': self._wb_product(values)})
        return self.workbench_detail(case.id)

    @api.model
    def workbench_task(self, case_id, task_type, values):
        case = self._wb_case(case_id, True)
        case._lock()
        snap = self._wb_snapshot(case)
        if not snap or task_type not in TASK_INPUTS or not isinstance(values, dict):
            raise ValidationError('需要快照和已支持的任务类型')
        payload = {'schema_version': '1'}
        for key in TASK_INPUTS[task_type]:
            value = values.get(key)
            if key.endswith('_artifact_id') or key in ('generation_plan_id', 'source_artifact_ids'):
                refs = value if isinstance(value, list) else [value]
                if not refs or any(not str(ref).isdigit() for ref in refs):
                    raise ValidationError('请选择本商品流程的产物')
                for ref in refs:
                    artifact = self.env[PREFIX + 'artifact'].browse(int(ref)).exists()
                    artifact._authorize('worker')
                    if not artifact or artifact.case_id != case:
                        raise ValidationError('产物必须属于当前商品流程')
                value = [str(ref) for ref in refs] if key == 'source_artifact_ids' else str(value)
            payload[key] = value
        if task_type == 'product.facts':
            payload['product_snapshot_id'] = str(snap.id)
        return self.env[PREFIX + 'task'].create({'case_id': case.id, 'snapshot_id': snap.id,
            'task_type': task_type, 'payload': payload}).id

    @api.model
    def workbench_control(self, case_id, task_id, operation):
        case = self._wb_case(case_id, True)
        task = self.env[PREFIX + 'task'].browse(int(task_id)).exists()
        task._authorize()
        if not task or task.case_id != case or operation not in ('resume', 'cancel'):
            raise ValidationError('任务归属或操作无效')
        return task.resume() if operation == 'resume' else task.cancel()

    @api.model
    def workbench_propose(self, case_id, task_id, result_hash, kind, indices):
        case = self._wb_case(case_id, True)
        case._lock()
        task = self.env[PREFIX + 'task'].browse(int(task_id)).exists()
        task._authorize()
        snap = self._wb_snapshot(case)
        if not task or task.case_id != case or task.snapshot_id != snap or task.state != 'succeeded' or canonical_hash(task.result) != result_hash:
            raise ValidationError('候选不属于当前商品、快照或结果版本，请刷新')
        if not isinstance(indices, list) or not indices or any(type(i) is not int or i < 0 for i in indices) or len(set(indices)) != len(indices):
            raise ValidationError('请选择有效且不重复的候选')
        if kind == 'title' and task.task_type == 'title.generate' and len(indices) == 1:
            choices = task.result['candidates']
            if max(indices) >= len(choices):
                raise ValidationError('候选不存在')
            desired = choices[indices[0]]['text']
            before = snap.payload.get('title')
        elif kind == 'images' and task.task_type in ('gallery.collect', 'image.generate'):
            choices = task.result.get('images') or [task.result['image']]
            if max(indices) >= len(choices):
                raise ValidationError('图片不存在')
            desired = [choices[i] for i in indices]
            for image in desired:
                reference = str(image.get('artifact_id', ''))
                artifact = self.env[PREFIX + 'artifact'].browse(int(reference)).exists() if reference.isdigit() else self.env[PREFIX + 'artifact']
                if not artifact:
                    raise ValidationError('图片必须有已归档的产物证据')
                artifact._authorize('worker')
                if artifact.case_id != case or artifact.digest != image.get('sha256') or artifact.mime_type != image.get('mime_type') or not artifact.data:
                    raise ValidationError('图片产物归属、摘要或媒体类型不匹配')
            before = snap.payload.get('image_url')
        elif kind == 'reference' and task.task_type == 'research.bestseller' and len(indices) == 1:
            choices = task.result['candidates']
            if max(indices) >= len(choices):
                raise ValidationError('参考商品不存在')
            desired, before = choices[indices[0]], None
        else:
            raise ValidationError('候选类型无效')
        payload = {'action': kind, 'before_value': before, 'desired_value': desired,
            'snapshot_id': snap.id, 'task_id': task.id, 'result_hash': result_hash,
            'indices': indices, 'scope': snap.payload.get('item_id'), 'execution_available': False}
        bounded(payload)
        proposal = self.env[PREFIX + 'proposal'].create({'case_id': case.id, 'payload': payload})
        return {'id': proposal.id, 'digest': proposal.digest, 'payload': payload}

    @api.model
    def workbench_confirm(self, case_id, proposal_id, digest):
        case = self._wb_case(case_id, True)
        proposal = self.env[PREFIX + 'proposal'].browse(int(proposal_id)).exists()
        proposal._authorize()
        if not proposal or proposal.case_id != case:
            raise ValidationError('方案不属于当前商品')
        return proposal.confirm(digest).id

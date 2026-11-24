"""Session-authenticated operations facade. Never returns worker leases or credentials."""
from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from .optimization import PREFIX, bounded
from ..domain import canonical_hash, validate_result

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

    title_flow_snapshot_id = fields.Many2one(PREFIX + 'snapshot', readonly=True, ondelete='restrict')

    @api.model_create_multi
    def create(self, vals_list):
        if 'default_title_flow_snapshot_id' in self.env.context or any('title_flow_snapshot_id' in v for v in vals_list):
            raise AccessError('Title flow is server managed')
        return super().create(vals_list)

    @api.model
    def workbench_title_flow(self, case_id):
        case = self._wb_case(case_id, True)
        case._lock()
        snapshot = self._wb_snapshot(case)
        if not snapshot:
            raise ValidationError('需要商品快照')
        if snapshot.payload.get('site_id') != 'MLM':
            raise ValidationError('标题流程暂仅支持已确认的墨西哥站点 MLM')
        case._set({'title_flow_snapshot_id': snapshot.id})
        task = case._title_flow_advance()
        return {'task_id': task.id if task else False, 'stage': task.task_type if task else 'complete'}

    def _title_flow_advance(self):
        self.ensure_one()
        self._lock()
        snap = self._wb_snapshot(self)
        if not snap or self.title_flow_snapshot_id != snap:
            return self.env[PREFIX + 'task']
        tasks = self.env[PREFIX + 'task'].sudo().search([('case_id', '=', self.id), ('snapshot_id', '=', snap.id)], order='id desc')
        artifacts = self.env[PREFIX + 'artifact'].sudo()
        prior = {}
        for kind in ('product.facts', 'research.keywords', 'title.generate'):
            existing = tasks.filtered(lambda t: t.task_type == kind)
            if kind == 'research.keywords' and prior.get('product.facts'):
                existing = existing.filtered(lambda t: t.payload.get('facts_artifact_id') == str(prior['product.facts']))
            if kind == 'title.generate' and prior.get('research.keywords'):
                existing = existing.filtered(lambda t: t.payload.get('facts_artifact_id') == str(prior['product.facts']) and t.payload.get('keywords_artifact_id') == str(prior['research.keywords']))
            task = existing[:1]
            if not task:
                payload = {'schema_version': '1'}
                if kind == 'product.facts': payload['product_snapshot_id'] = str(snap.id)
                elif kind == 'research.keywords': payload.update(facts_artifact_id=str(prior['product.facts']), site_id=snap.payload['site_id'])
                else: payload.update(facts_artifact_id=str(prior['product.facts']), keywords_artifact_id=str(prior['research.keywords']), language='es-MX')
                old = tasks.filtered(lambda t: t.title_flow_key == str(snap.id) + ':' + kind)
                if old:
                    old._set({'title_flow_key': False})
                created = self.env[PREFIX + 'task'].sudo().create({'case_id': self.id, 'snapshot_id': snap.id, 'task_type': kind, 'payload': payload})
                created._set({'title_flow_key': str(snap.id) + ':' + kind})
                return created
            if task.state != 'succeeded' or kind == 'title.generate':
                return task
            if kind == 'research.keywords' and task.result.get('status') == 'empty':
                return task
            digest = canonical_hash(task.result)
            artifact = artifacts.search([('case_id', '=', self.id), ('result_task_id', '=', task.id), ('result_hash', '=', digest)], limit=1)
            if not artifact:
                validate_result(kind, task.result)
                artifact = artifacts._append({'company_id': self.company_id.id, 'case_id': self.id, 'payload': task.result,
                    'digest': bounded(task.result), 'mime_type': 'application/json', 'result_task_id': task.id,
                    'result_snapshot_id': snap.id, 'result_hash': digest})
            prior[kind] = artifact.id
        return self.env[PREFIX + 'task']

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

    def _wb_source_images(self, case, snapshot):
        payload = snapshot.payload if snapshot and isinstance(snapshot.payload, dict) else {}
        refs = payload.get('source_image_artifact_ids') or []
        if not isinstance(refs, list):
            return []
        ids = [int(ref) for ref in refs if type(ref) in (str, int) and str(ref).isdigit()]
        records = self.env[PREFIX + 'artifact'].search([
            ('id', 'in', ids), ('case_id', '=', case.id), ('company_id', '=', case.company_id.id),
            ('mime_type', 'in', ['image/png', 'image/jpeg', 'image/webp'])])
        available = set(records.ids)
        return [{'artifact_id': ref, 'url': '/web/image/meli.optimization.artifact/%s/data' % ref}
                for ref in ids if ref in available]

    @api.model
    def workbench_overview(self, query='', page=0, batch_id=False, state='', shop='', site=''):
        self._authorize('worker')
        page = max(0, int(page))
        domain = [('collection', '=', False)]
        query = str(query or '').strip()[:200].casefold()
        if batch_id:
            domain += [('batch_id', '=', int(batch_id))]
        # Snapshot scope filters are observations, never platform account access grants.
        rows = []
        for case in self.search(domain, order='id desc'):
            snap = self._wb_snapshot(case)
            payload = snap.payload if snap and isinstance(snap.payload, dict) else {}
            if query and not any(query in str(value or '').casefold() for value in (payload.get('title') or case.name, payload.get('item_id'))):
                continue
            tasks = self.env[PREFIX + 'task'].search([('case_id', '=', case.id)])
            if shop and payload.get('shop') != shop or site and payload.get('site_id') != site:
                continue
            if state and not any(t.state == state for t in tasks):
                continue
            rows.append({'id': case.id, 'name': case.name, 'revision': case.revision,
                'batch': case.batch_id.name, 'batch_id': case.batch_id.id, 'product': payload,
                'source_images': self._wb_source_images(case, snap),
                'tasks': len(tasks), 'waiting': len(tasks.filtered(lambda t: t.state == 'waiting')),
                'failed': len(tasks.filtered(lambda t: t.state == 'failed'))})
        collection_batches = self.search([('collection', '=', True)]).mapped('batch_id').ids
        batches = self.env[PREFIX + 'batch'].search([('id', 'not in', collection_batches)], order='id desc')
        task_model = self.env[PREFIX + 'task']
        proposals = self.env[PREFIX + 'proposal'].search([])
        confirmed = self.env[PREFIX + 'confirmation'].search([]).mapped('proposal_id')
        return {'rows': rows[page * 20:(page + 1) * 20], 'total': len(rows), 'page': page,
            'counts': {'cases': self.search_count([('collection', '=', False)]), 'batches': len(batches),
                'waiting': task_model.search_count([('state', '=', 'waiting')]),
                'failed': task_model.search_count([('state', '=', 'failed')]),
                'pending': len(proposals - confirmed)},
            'batches': batches.read(['name']),
            'shops': sorted({str(r['product'].get('shop')) for r in rows if r['product'].get('shop')}),
            'company': self.env.company.display_name,
            'can_edit': self.env.su or self.env.user.has_group('meli_optimization.group_editor'),
            'task_inputs': TASK_INPUTS}

    @api.model
    def workbench_detail(self, case_id):
        case = self._wb_case(case_id)
        snap = self._wb_snapshot(case)
        tasks = self.env[PREFIX + 'task'].search([('case_id', '=', case.id), ('research_archived', '=', False)], order='id desc')
        # Attempt is internal-only: expose only epoch and creation time, scoped to
        # tasks already visible through this authorized case. Never expose lease data.
        attempts = self.env[PREFIX + 'attempt'].sudo().search([('task_id', 'in', tasks.ids), ('company_id', '=', case.company_id.id)], order='epoch asc')
        attempt_rows = {}
        for attempt in attempts:
            attempt_rows.setdefault(attempt.task_id.id, []).append({'epoch': attempt.epoch, 'created_at': fields.Datetime.to_string(attempt.create_date)})
        attempts_by_epoch = {(task_id, row['epoch']): row for task_id, rows in attempt_rows.items() for row in rows}
        failures = self.env[PREFIX + 'event'].sudo().search([
            ('task_id', 'in', tasks.ids), ('company_id', '=', case.company_id.id), ('kind', 'in', ['wait', 'fail']),
        ], order='id')
        for failure in failures:
            # Submission keys acquire an epoch prefix starting with the second lease.
            parts = failure.submission_key.split(':', 2)
            epoch = int(parts[1]) if len(parts) == 3 and parts[0] == 'epoch' and parts[1].isascii() and parts[1].isdecimal() else 1
            row = attempts_by_epoch.get((failure.task_id.id, epoch))
            if row is not None and isinstance(failure.payload, dict):
                row['failure'] = {'at': fields.Datetime.to_string(failure.create_date),
                                  'code': failure.payload.get('code'), 'message': failure.payload.get('message')}
        proposals = self.env[PREFIX + 'proposal'].search([('case_id', '=', case.id)], order='id desc')
        confirmations = self.env[PREFIX + 'confirmation'].search([('proposal_id', 'in', proposals.ids)])
        return {'id': case.id, 'name': case.name, 'revision': case.revision, 'batch': case.batch_id.name,
            'shop_id':case.shop_id.id or False, 'variant_scoped':bool(case.variant_identity),
            'collection': case.collection, 'collection_started': bool(case.collection and snap and case.collection_started_snapshot_id == snap), 'snapshot': {'id': snap.id, 'payload': snap.payload, 'digest': snap.digest, 'source_images': self._wb_source_images(case, snap)} if snap else None,
            'retry_events': self.env[PREFIX + 'event'].search_read([('task_id', 'in', tasks.ids), ('kind', '=', 'unknown_research_retry')], ['kind', 'task_id', 'receipt'], order='id asc'),
            'tasks': [{'id': t.id, 'task_type': t.task_type, 'state': t.state,
                'lease_epoch': t.lease_epoch,
                'lease_expired': bool(t.state == 'running' and t.lease_expires_at and t.lease_expires_at <= fields.Datetime.now()),
                'snapshot_id': t.snapshot_id.id, 'result': t.result,
                'generation_plan_id': t.payload.get('generation_plan_id') if t.task_type == 'image.generate' else False,
                'target_artifact_id': t.payload.get('target_artifact_id') if t.task_type == 'image.generate' else False,
                'result_hash': canonical_hash(t.result), 'status_detail': t.status_detail, 'attempts': attempt_rows.get(t.id, [])} for t in tasks],
            'artifacts': self.env[PREFIX + 'artifact'].search_read([('case_id', '=', case.id)], ['digest', 'mime_type', 'payload', 'result_task_id', 'result_snapshot_id', 'result_hash', 'library_origin_id', 'library_artifact_id']),
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
        if case.variant_identity:
            raise ValidationError('变体商品请通过平台同步更新快照')
        self.env[PREFIX + 'snapshot'].create({'case_id': case.id, 'payload': self._wb_product(values)})
        return self.workbench_detail(case.id)

    @api.model
    def workbench_task(self, case_id, task_type, values):
        case = self._wb_case(case_id, True)
        if task_type == 'image.qc':
            raise ValidationError('图片质检已包含在逐图生成任务中')
        case._lock()
        snap = self._wb_snapshot(case)
        if not snap or task_type not in TASK_INPUTS or not isinstance(values, dict):
            raise ValidationError('需要快照和已支持的任务类型')
        if (case.collection and task_type in ('product.facts', 'research.keywords', 'research.bestseller')
                and case.collection_started_snapshot_id != snap):
            raise ValidationError('请先点击一键开始采集')
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
        task=self.env[PREFIX + 'task'].create({'case_id': case.id, 'snapshot_id': snap.id,
            'task_type': task_type, 'payload': payload})
        if case.collection and task_type == 'research.bestseller' and self.env['ir.config_parameter'].sudo().get_param('meli_optimization.scoped_auto_research','0') == '1':
            task.prepare_scoped_research()
        return task.id

    @api.model
    def workbench_archive(self, case_id, task_id, result_hash):
        case = self._wb_case(case_id, True)
        case._lock()
        task = self.env[PREFIX + 'task'].browse(int(task_id)).exists()
        task._authorize()
        snapshot = self._wb_snapshot(case)
        if not task or task.case_id != case or task.snapshot_id != snapshot or task.state != 'succeeded' or task.task_type not in ('product.facts', 'research.keywords') or canonical_hash(task.result) != result_hash:
            raise ValidationError('仅可归档当前商品快照的已完成事实或关键词结果，请刷新')
        validate_result(task.task_type, task.result)
        artifacts = self.env[PREFIX + 'artifact']
        existing = artifacts.search([('case_id', '=', case.id), ('result_task_id', '=', task.id), ('result_hash', '=', result_hash)], limit=1)
        if existing:
            return existing.id
        return artifacts._append({'company_id': case.company_id.id, 'case_id': case.id,
            'payload': task.result, 'digest': bounded(task.result), 'mime_type': 'application/json',
            'result_task_id': task.id, 'result_snapshot_id': snapshot.id, 'result_hash': result_hash}).id

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
        elif kind == 'images' and task.task_type == 'image.generate':
            choices = [task.result['image']]
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
            case._require_passed_image_qc(task, desired)
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
        if kind == 'images':
            payload['quality_resolution'] = (task.result.get('visual_qc') or {}).get('status', 'passed')
        bounded(payload)
        proposal = self.env[PREFIX + 'proposal'].create({'case_id': case.id, 'payload': payload})
        return {'id': proposal.id, 'digest': proposal.digest, 'payload': payload}

    @api.model
    def workbench_image_plan_propose(self, case_id, plan_id, task_ids):
        case = self._wb_case(case_id, True)
        case._lock()
        if type(plan_id) is not int or plan_id <= 0 or not isinstance(task_ids, list) or not task_ids or any(type(i) is not int or i <= 0 for i in task_ids) or len(set(task_ids)) != len(task_ids):
            raise ValidationError('请选择有效且不重复的图片任务')
        tasks = self.env[PREFIX + 'task'].browse(task_ids).exists()
        if len(tasks) != len(task_ids):
            raise ValidationError('图片任务不存在')
        payload = {'action': 'images', 'generation_plan_id': plan_id, 'snapshot_id': self._wb_snapshot(case).id,
                   'task_ids': task_ids, 'result_hashes': [canonical_hash(t.result) for t in tasks],
                   'desired_value': [t.result.get('image') if isinstance(t.result, dict) else None for t in tasks],
                   'quality_resolutions': [(t.result.get('visual_qc') or {}).get('status', 'passed') for t in tasks],
                   'before_value': self._wb_snapshot(case).payload.get('image_url'),
                   'scope': self._wb_snapshot(case).payload.get('item_id'), 'execution_available': False}
        case._validate_image_plan_proposal(payload)
        bounded(payload)
        proposal = self.env[PREFIX + 'proposal'].create({'case_id': case.id, 'payload': payload})
        return {'id': proposal.id, 'digest': proposal.digest, 'payload': payload}

    def _validate_image_plan_proposal(self, payload):
        self.ensure_one()
        ids, hashes, images = payload.get('task_ids'), payload.get('result_hashes'), payload.get('desired_value')
        resolutions = payload.get('quality_resolutions')
        plan_id = payload.get('generation_plan_id')
        if (type(plan_id) is not int or plan_id <= 0 or not isinstance(ids, list) or not ids
                or any(type(i) is not int or i <= 0 for i in ids) or len(set(ids)) != len(ids)
                or not isinstance(hashes, list) or not isinstance(images, list) or not isinstance(resolutions, list)
                or len(ids) != len(hashes) or len(ids) != len(images) or len(ids) != len(resolutions)):
            raise ValidationError('图片方案来源无效')
        plan = self.env[PREFIX + 'artifact'].browse(plan_id).exists()
        data = plan.payload if plan else None
        snap = self._wb_snapshot(self)
        if (not plan or plan.case_id != self or plan.company_id != self.company_id or not isinstance(data, dict)
                or plan.digest != canonical_hash(data) or data.get('operation') != 'garment_swap'
                or data.get('case_id') != self.id or data.get('snapshot_id') != snap.id or payload.get('snapshot_id') != snap.id
                or not isinstance(data.get('target_artifact_ids'), list)):
            raise ValidationError('生成计划或商品快照已失效')
        targets = set()
        for task_id, result_hash, image, resolution in zip(ids, hashes, images, resolutions):
            task = self.env[PREFIX + 'task'].browse(task_id).exists()
            if (not task or task.task_type != 'image.generate' or task.case_id != self or task.company_id != self.company_id
                    or task.snapshot_id != snap or str(task.payload.get('generation_plan_id')) != str(plan_id)
                    or canonical_hash(task.result) != result_hash or not isinstance(image, dict)
                    or resolution != (task.result.get('visual_qc') or {}).get('status', 'passed')):
                raise ValidationError('图片任务、计划或结果已变化')
            target = str(task.payload.get('target_artifact_id') or '')
            if not target or target in targets or target not in [str(x) for x in data['target_artifact_ids']]:
                raise ValidationError('目标构图不存在或重复')
            targets.add(target)
            self._require_passed_image_qc(task, [image])

    @api.model
    def workbench_confirm(self, case_id, proposal_id, digest):
        case = self._wb_case(case_id, True)
        proposal = self.env[PREFIX + 'proposal'].browse(int(proposal_id)).exists()
        proposal._authorize()
        if not proposal or proposal.case_id != case:
            raise ValidationError('方案不属于当前商品')
        if proposal.payload.get('action') == 'images' and proposal.payload.get('task_ids'):
            case._validate_image_plan_proposal(proposal.payload)
        elif proposal.payload.get('action') == 'images':
            task = self.env[PREFIX + 'task'].browse(int(proposal.payload.get('task_id') or 0)).exists()
            if not task or task.case_id != case or task.snapshot_id != case._wb_snapshot(case) or task.task_type != 'image.generate' or canonical_hash(task.result) != proposal.payload.get('result_hash'):
                raise ValidationError('图片方案已过期或来源无效')
            case._require_passed_image_qc(task, proposal.payload.get('desired_value') or [])
        return proposal.confirm(digest).id

    def _require_passed_image_qc(self, task, images):
        self.ensure_one()
        from ..domain import validate_internal_image_qc, DomainError
        if task.state != 'succeeded' or task.case_id != self or task.snapshot_id != self._wb_snapshot(self):
            raise ValidationError('生成任务或商品快照无效')
        try:
            validate_internal_image_qc(task.result, allow_legacy=True)
            task._validate_manual_review_decision(task.result)
        except DomainError as exc:
            raise ValidationError('图片需要通过当前任务的内部质检') from exc
        for image in images:
            ref = str(image.get('artifact_id') or '')
            result_image = task.result.get('image') or {}
            artifact = self.env[PREFIX + 'artifact'].browse(int(ref)).exists() if ref.isdigit() else self.env[PREFIX + 'artifact']
            if (image != result_image or not artifact or artifact.case_id != self or artifact.company_id != self.company_id
                    or not artifact.data or artifact.digest != image.get('sha256') or artifact.mime_type != image.get('mime_type')):
                raise ValidationError('生成图片与质检报告不匹配')


class ResultArtifact(models.Model):
    _inherit = PREFIX + 'artifact'

    result_task_id = fields.Many2one(PREFIX + 'task', readonly=True, ondelete='restrict', index=True)
    result_snapshot_id = fields.Many2one(PREFIX + 'snapshot', readonly=True, ondelete='restrict')
    result_hash = fields.Char(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        provenance = {'result_task_id', 'result_snapshot_id', 'result_hash'}
        if any(provenance.intersection(values) for values in vals_list) or any('default_' + key in self.env.context for key in provenance):
            raise AccessError('Result provenance is assigned only by checked archival')
        return super().create(vals_list)


class ImageProposalConfirmation(models.Model):
    _inherit = PREFIX + 'proposal'

    def confirm(self, digest):
        self._authorize()
        self.ensure_one()
        if (self.payload or {}).get('action') == 'images':
            self.case_id._bump_qc_revision()
        self.case_id._lock()
        payload = self.payload or {}
        if payload.get('action') == 'images' and payload.get('task_ids'):
            self.case_id._validate_image_plan_proposal(payload)
        elif payload.get('action') == 'images':
            ref = payload.get('task_id')
            task = self.env[PREFIX + 'task'].browse(int(ref)).exists() if str(ref or '').isdigit() else self.env[PREFIX + 'task']
            if (not task or task.case_id != self.case_id or task.snapshot_id != self.case_id._wb_snapshot(self.case_id)
                    or task.task_type != 'image.generate' or task.state != 'succeeded'
                    or canonical_hash(task.result) != payload.get('result_hash')
                    or (payload.get('quality_resolution') is not None and payload.get('quality_resolution') != (task.result.get('visual_qc') or {}).get('status', 'passed'))
                    or payload.get('desired_value') != [task.result.get('image')]):
                raise ValidationError('图片方案已过期或来源无效')
            self.case_id._require_passed_image_qc(task, payload['desired_value'])
        return super().confirm(digest)


class TitleFlowTask(models.Model):
    _inherit = PREFIX + 'task'

    title_flow_key = fields.Char(readonly=True, index=True)
    _unique_title_flow_key = models.Constraint('UNIQUE(title_flow_key)', 'Title flow stage already exists')

    @api.model_create_multi
    def create(self, vals_list):
        if 'default_title_flow_key' in self.env.context or any('title_flow_key' in v for v in vals_list):
            raise AccessError('Title flow key is server managed')
        return super().create(vals_list)

    def complete(self, token, epoch, key, body):
        self._authorize('worker')
        self._lock()
        was_succeeded = self.state == 'succeeded'
        receipt = super().complete(token, epoch, key, body)
        if not was_succeeded and self.task_type in ('product.facts', 'research.keywords') and self.state == 'succeeded':
            self.case_id.sudo()._title_flow_advance()
        return receipt

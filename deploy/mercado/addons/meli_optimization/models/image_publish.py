"""Read-only verification for a confirmed image plan before platform publication."""
import base64
import hashlib
import json
import uuid
from datetime import timedelta
from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.addons.meli_platform_adapter import transport

from .optimization import PREFIX, ExactJson
from ..domain import canonical_hash
from ..domain.image_publish import collect_preflight, reserve_step, mark_dispatched, finish_step, image_upload_form, picture_update_payload, user_product_picture_ids, legacy_scope_matches, legacy_update_payload, legacy_update_matches, safe_publish_diagnostic, group_scope_matches, ready_publication_state
from ..domain.variant_groups import publishable_variants
from ..domain.image_batch_plan import plan_global_batches, start_plan, batch_update_payload, reconcile_batch_step, finish_batch_step, retry_batch_step


class ImagePublication(models.Model):
    _name = PREFIX + 'image.publication'
    _inherit = PREFIX + 'protected'
    _description = 'Durable image publication request'
    _parent_field = ('case_id', 'case')
    _internal_only = True
    case_id = fields.Many2one(PREFIX + 'case', required=True, ondelete='restrict', index=True)
    proposal_id = fields.Many2one(PREFIX + 'proposal', required=True, ondelete='restrict')
    actor_id = fields.Many2one('res.users', required=True, ondelete='restrict')
    attempt = fields.Integer(required=True, default=1)
    preview_digest = fields.Char(required=True, index=True)
    preview = ExactJson(required=True)
    state = fields.Selection([('queued','Queued'), ('uploading','Uploading'), ('linking','Linking'), ('updating','Updating'),
        ('reconciliation_required','Needs reconciliation'), ('published','Published'), ('failed','Failed')], required=True, default='queued', index=True)
    progress = ExactJson()
    _unique_preview = models.Constraint('UNIQUE(case_id, proposal_id, preview_digest, attempt)', 'Image publication attempt already exists')


class ImagePublicationTargetFence(models.Model):
    _name = PREFIX + 'image.publication.target.fence'
    _description = 'Global serialization fence for a platform product'
    target_key = fields.Char(required=True, index=True)
    version = fields.Integer(required=True, default=0)
    _unique_target = models.Constraint('UNIQUE(target_key)', 'Image publication target already exists')


class ImagePublishingWorkbench(models.Model):
    _inherit = PREFIX + 'case'

    def _image_publish_prepare_image(self, case, image):
        ref = str(image.get('artifact_id') or '') if isinstance(image, dict) else ''
        artifact = self.env[PREFIX + 'artifact'].browse(int(ref)).exists() if ref.isdigit() else self.env[PREFIX + 'artifact']
        if not artifact or artifact.case_id != case or artifact.company_id != case.company_id or not artifact.data:
            raise ValidationError('生成图片归档不存在')
        try:
            raw = base64.b64decode(artifact.data, validate=True)
            if hashlib.sha256(raw).hexdigest() != image.get('sha256') or artifact.digest != image.get('sha256') or artifact.mime_type != image.get('mime_type'):
                raise ValueError('图片摘要不匹配')
            from .library import verified_image
            verified_image(raw, artifact.mime_type)
            return image_upload_form(raw, artifact.mime_type, uuid.uuid4().hex)
        except (ValueError, TypeError) as exc:
            raise ValidationError('生成图片无法上传：%s' % exc) from exc

    @api.model
    def workbench_detail(self, case_id):
        detail = super().workbench_detail(case_id)
        rows = self.env[PREFIX + 'image.publication'].search([('case_id', '=', detail['id'])], order='id desc')
        detail['image_publications'] = [{'id':row.id, 'proposal_id':row.proposal_id.id, 'state':row.state,
            'phase':(row.progress or {}).get('phase'), 'uploaded':len((row.progress or {}).get('uploaded', [])),
            'total':(row.progress or {}).get('total'), 'code':(row.progress or {}).get('code'),
            'diagnostics':[{k:v for k,v in entry.items() if k != 'nonce'} for entry in (row.progress or {}).get('diagnostics', [])],
            'can_resume':row.actor_id.id == self.env.uid and row.state in ('queued','uploading','linking','updating') and not (row.progress or {}).get('dispatched'),
            'can_reconcile':row.actor_id.id == self.env.uid and (row.state == 'reconciliation_required' or bool((row.progress or {}).get('dispatched'))),
            'can_abandon':row.actor_id.id == self.env.uid and row.state == 'reconciliation_required' and (row.progress or {}).get('operation') == 'upload'}
            for row in rows]
        return detail

    def _image_publish_read(self, shop, path):
        response = transport.send('GET', path, token=shop._token(), timeout=30)
        if not 200 <= response.status < 300 or (path.startswith('/items/CBT') and response.status != 200):
            status = response.status
            response.close()
            raise ValidationError('平台商品信息读取失败 (HTTP %s)' % status)
        if path.endswith('/attributes'):
            try:
                return response.peek_json_array()
            finally:
                response.close()
        return response.json()

    def _image_publish_group_current(self, case, row, shop):
        target = row.preview['target']
        snapshot = case._wb_snapshot(case)
        if snapshot.id != row.preview['snapshot_id'] or case.revision != row.proposal_id.input_revision or shop.id != row.preview['shop_id']:
            raise ValidationError('商品快照或店铺已变化')
        case._validate_image_plan_proposal(row.proposal_id.payload)
        current = collect_preflight(lambda path: self._image_publish_read(shop, path), target['item_id'],
            shop.site, shop.seller_id, shop.marketplace_seller_id, shop.logistic_type, target['selected_variant_id'])
        if not group_scope_matches(target,current):
            raise ValidationError('平台商品或变体范围已变化')
        return {'root':current['picture_ids'], 'variants':{v['id']:v['picture_ids'] for v in current['variants']}}

    @api.model
    def workbench_image_publish_preview(self, case_id, proposal_id, digest, variant_id=None):
        case = self._wb_case(case_id, True)
        if type(proposal_id) is not int or proposal_id <= 0:
            raise ValidationError('图片方案无效')
        proposal = self.env[PREFIX + 'proposal'].browse(proposal_id).exists()
        if not proposal or proposal.case_id != case:
            raise ValidationError('图片方案不属于当前商品')
        proposal._authorize()
        if (proposal.digest != digest or proposal.input_revision != case.revision
                or proposal.payload.get('action') != 'images' or not proposal.payload.get('task_ids')):
            raise ValidationError('图片方案或商品快照已改变')
        confirmation = self.env[PREFIX + 'confirmation'].search([
            ('proposal_id', '=', proposal.id), ('digest', '=', digest), ('user_id', '=', self.env.uid)], limit=1)
        if not confirmation or not confirmation.is_valid():
            raise ValidationError('请先确认图片方案')
        case._validate_image_plan_proposal(proposal.payload)
        for image in proposal.payload['desired_value']:
            self._image_publish_prepare_image(case, image)
        snapshot = case._wb_snapshot(case)
        shop = case._gallery_shop(case, snapshot.payload)
        if shop.company_id != case.company_id:
            raise ValidationError('商品和店铺公司不一致')
        try:
            target = collect_preflight(lambda path: self._image_publish_read(shop, path),
                snapshot.payload.get('item_id'), shop.site, shop.seller_id, shop.marketplace_seller_id, shop.logistic_type, variant_id)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if case.variant_identity:
            scope = snapshot.payload.get('variant_scope')
            if (not isinstance(scope,dict) or scope.get('model') != target['model'] or scope.get('parent_id') != target['parent_id']
                    or scope.get('variant_ids') != case.variant_members):
                raise ValidationError('变体和当前快照不匹配')
            if target.get('selected_variant_id') and target['selected_variant_id'] not in scope['variant_ids']:
                raise ValidationError('所选 SKU 不属于当前图片变体')
            if target.get('selected_group_variant_ids') and set(target['selected_group_variant_ids']) != set(scope['variant_ids']):
                raise ValidationError('平台图片变体与当前商品快照的规格范围不一致')
            current = self._variant_read_product(shop, snapshot.payload.get('item_id'))
            try:allowed=publishable_variants(scope,current,target)
            except ValueError as exc:raise ValidationError(str(exc)) from exc
            if target.get('variants'):target['allowed_variant_ids']=allowed
        desired_count = len(proposal.payload['desired_value'])
        if desired_count > min(50, target['picture_limit']):
            raise ValidationError('图片数量超过平台类别限制')
        if target.get('variants') and desired_count > target['variant_picture_limit']:
            raise ValidationError('图片数量超过平台规格图片限制')
        if target.get('selected_group_variant_ids'):
            placeholders = ['PREVIEW_IMAGE_' + str(i) for i in range(desired_count)]
            try:
                plan = plan_global_batches(target['picture_ids'], target['variants'], target['selected_group_variant_ids'],
                                    placeholders, min(50, target['picture_limit']), target['variant_picture_limit'])
            except ValueError as exc:
                raise ValidationError('无法安全规划图片发布：%s' % exc) from exc
            target['batch_steps'] = len(plan)
        case._lock()
        case.invalidate_recordset(['revision'])
        if case.revision != proposal.input_revision or case._wb_snapshot(case) != snapshot:
            raise ValidationError('平台读取期间商品快照已改变')
        case._validate_image_plan_proposal(proposal.payload)
        payload = {'case_id':case.id, 'snapshot_id':snapshot.id, 'proposal_id':proposal.id,
                   'proposal_digest':proposal.digest, 'shop_id':shop.id, 'shop_name':shop.name,
                   'seller_id':shop.seller_id,
                   'source_sku':str(snapshot.payload.get('seller_sku') or snapshot.payload.get('seller_custom_field') or '')[:200],
                   'target':target, 'desired_value':proposal.payload['desired_value'], 'mode':'replace_variant' if target.get('variants') else 'replace'}
        can_write = self.env.su or self.env.user.has_group('meli_bff.group_writer')
        too_full = target['model'] == 'traditional' and not target.get('selected_group_variant_ids') and len(target['picture_ids']) + desired_count > min(50, target['picture_limit'])
        selection_needed = bool(target.get('variants') and not target.get('selected_variant_id'))
        unavailable_code = 'variant_selection_required' if selection_needed else 'temporary_picture_limit' if too_full else 'write_permission_required' if not can_write else None
        return dict(payload, digest=canonical_hash(payload), execution_available=bool(can_write and not too_full and not selection_needed), unavailable_code=unavailable_code)

    @api.model
    def workbench_image_publish_start(self, case_id, proposal_id, proposal_digest, preview_digest, variant_id=None, confirm_variant=False):
        case = self._wb_case(case_id, True)
        if not self.env.su and not self.env.user.has_group('meli_bff.group_writer'):
            raise AccessError('发布需要店铺平台写入权限')
        preview = self.workbench_image_publish_preview(case_id, proposal_id, proposal_digest, variant_id)
        if (preview['target']['model'] == 'traditional'
                and not preview['target'].get('selected_group_variant_ids')
                and len(preview['target']['picture_ids']) + len(preview['desired_value']) > min(50, preview['target']['picture_limit'])):
            raise ValidationError('传统刊登绑定新图时，新旧图片总数超过平台类别限制')
        if not preview['execution_available']:
            raise ValidationError('当前商品模型暂不支持图片发布')
        if preview['target'].get('variants') and confirm_variant is not True:
            raise ValidationError('请确认服装参考图与所选 SKU 一致')
        if not isinstance(preview_digest, str) or preview_digest != preview['digest']:
            raise ValidationError('平台图片或发布范围已变化，请重新核验')
        case._lock()
        target_key = preview['seller_id'] + ':' + preview['target']['parent_id']
        self.env.cr.execute('''INSERT INTO meli_optimization_image_publication_target_fence (target_key, version)
            VALUES (%s, 1) ON CONFLICT (target_key) DO UPDATE
            SET version = meli_optimization_image_publication_target_fence.version + 1''', [target_key])
        model = self.env[PREFIX + 'image.publication']
        existing = model.search([('case_id', '=', case.id), ('proposal_id', '=', proposal_id), ('preview_digest', '=', preview_digest)], order='attempt desc', limit=1)
        if existing and existing.state != 'failed':
            if existing.actor_id.id != self.env.uid:
                raise ValidationError('该图片方案已有其他用户发起的发布，请由发起人继续处理')
            return {'id':existing.id, 'state':existing.state}
        active = model.sudo().search([('state', 'in', ['queued','uploading','linking','updating','reconciliation_required'])])
        if any(isinstance(job.preview, dict) and job.preview.get('seller_id') == preview['seller_id']
               and isinstance(job.preview.get('target'), dict) and job.preview['target'].get('parent_id') == preview['target']['parent_id'] for job in active):
            raise ValidationError('该平台商品已有发布操作未完成，请先处理')
        progress = {'phase':'ready', 'uploaded':[], 'total':len(preview['desired_value'])}
        if preview['target'].get('selected_group_variant_ids'):
            progress.update(model='traditional_group', target=preview['target'])
        elif preview['target']['model'] == 'traditional':
            progress.update(model='traditional', linked=[])
        row = model._append({'case_id':case.id, 'company_id':case.company_id.id, 'proposal_id':proposal_id,
            'actor_id':self.env.uid, 'preview_digest':preview_digest, 'preview':preview, 'state':'queued', 'attempt':existing.attempt + 1 if existing else 1,
            'progress':progress})
        return {'id':row.id, 'state':row.state}

    @api.model
    def workbench_image_publish_migrate_global(self, case_id, publication_id, confirm=False):
        case = self._wb_case(case_id, True)
        if not self.env.su and not self.env.user.has_group('meli_bff.group_writer'):
            raise AccessError('发布需要店铺平台写入权限')
        row = self.env[PREFIX + 'image.publication'].browse(publication_id).exists()
        if not row or row.case_id != case or row.actor_id.id != self.env.uid:
            raise AccessError('发布记录不属于当前用户或商品')
        row._authorize('editor')
        progress = dict(row.progress or {})
        entries = list(progress.get('diagnostics') or [])
        receipt = entries[-1] if entries else {}
        if (confirm is not True or row.state != 'reconciliation_required' or progress.get('phase') != 'reconciliation_required'
                or progress.get('model') != 'traditional_group' or progress.get('operation') != 'link'
                or receipt.get('nonce') != progress.get('nonce') or receipt.get('http_status') != 400
                or receipt.get('code') != 'validation_error' or 'item.action.invalid: Cannot modify CBT item from this resource' not in receipt.get('message','')):
            raise ValidationError('缺少可核验的 CBT 接口拒绝回执，不能切换发布方式')
        if (progress.get('batch') or {}).get('index') != 0:
            raise ValidationError('该任务已经绑定部分图片，需先核对并制定恢复方案')
        shop = case._gallery_shop(case, case._wb_snapshot(case).payload)
        if shop.id != row.preview.get('shop_id') or shop.company_id != case.company_id:
            raise ValidationError('发布店铺已变化')
        current = self._image_publish_group_current(case, row, shop)
        old_batch = progress.get('batch') or {}
        before = old_batch.get('initial') if old_batch.get('index') == 0 else {k:old_batch['plan'][old_batch['index']-1][k] for k in ('root','variants')}
        if current != before:
            raise ValidationError('平台状态已变化，不能切换发布方式')
        target=progress['target']
        plan=plan_global_batches(current['root'], [{'id':key,'picture_ids':value} for key,value in current['variants'].items()],
            target['selected_group_variant_ids'], progress['uploaded'], min(50,target['picture_limit']), target['variant_picture_limit'])
        batch=start_plan(plan,current)
        case._lock()
        row._lock()
        if row.progress != progress:
            raise ValidationError('发布状态已变化，请重新核对')
        now=fields.Datetime.to_string(fields.Datetime.now())
        entries.append({'operation':'update','stage':'global_migration','started_at':now,'finished_at':now,'outcome':'authorized',
                        'code':'global_update_selected','message':'CBT 绑定接口明确拒绝；已核对平台状态并切换到 Global Selling 更新接口',
                        'actor_id':self.env.uid, 'previous_plan_digest':old_batch.get('digest')})
        progress.update(batch=batch,phase='ready',code='global_update_selected',diagnostics=entries[-300:], previous_batch=old_batch)
        for key in ('nonce','operation','index','dispatched'):
            progress.pop(key,None)
        row._set({'state':'updating','progress':progress})
        return {'id':row.id,'state':'updating','code':'global_update_selected'}

    @api.model
    def workbench_image_publish_retry_link(self, case_id, publication_id, confirm_retry=False):
        case = self._wb_case(case_id, True)
        if not self.env.su and not self.env.user.has_group('meli_bff.group_writer'):
            raise AccessError('发布需要店铺平台写入权限')
        row = self.env[PREFIX + 'image.publication'].browse(publication_id).exists()
        if not row or row.case_id != case or row.actor_id.id != self.env.uid:
            raise AccessError('发布记录不属于当前用户或商品')
        row._authorize('editor')
        progress = dict(row.progress or {})
        if (confirm_retry is not True or row.state != 'reconciliation_required' or progress.get('phase') != 'reconciliation_required'
                or progress.get('model') != 'traditional_group' or progress.get('operation') != 'link'):
            raise ValidationError('需要明确确认才能重试结果未知的图片绑定')
        sent = fields.Datetime.to_datetime(progress.get('dispatched_at')) if progress.get('dispatched_at') else None
        if not sent or fields.Datetime.now() < sent + timedelta(seconds=180):
            raise ValidationError('平台请求可能仍在执行，请稍后核对')
        shop = case._gallery_shop(case, case._wb_snapshot(case).payload)
        if shop.id != row.preview.get('shop_id') or shop.company_id != case.company_id:
            raise ValidationError('发布店铺已变化')
        current = self._image_publish_group_current(case, row, shop)
        try:
            batch = retry_batch_step(progress['batch'], current)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        case._lock()
        row._lock()
        if row.progress != progress:
            raise ValidationError('发布状态已变化，请重新核对')
        now = fields.Datetime.to_string(fields.Datetime.now())
        entries = list(progress.get('diagnostics') or [])
        entries.append({'operation':'link','stage':'retry_authorized','started_at':now,'finished_at':now,
                        'outcome':'authorized','code':'operator_retry','message':'核对平台状态未变化后由操作人确认重试', 'actor_id':self.env.uid})
        progress.update(batch=batch, phase='ready', code='retry_authorized', diagnostics=entries[-300:])
        for key in ('nonce','operation','index','dispatched'):
            progress.pop(key, None)
        row._set({'state':'linking','progress':progress})
        return {'id':row.id,'state':'linking','code':'retry_authorized'}

    @api.model
    def workbench_image_publish_abandon(self, case_id, publication_id):
        case = self._wb_case(case_id, True)
        if not self.env.su and not self.env.user.has_group('meli_bff.group_writer'):
            raise AccessError('发布需要店铺平台写入权限')
        row = self.env[PREFIX + 'image.publication'].browse(publication_id).exists()
        if not row or row.case_id != case or row.actor_id.id != self.env.uid:
            raise AccessError('发布记录不属于当前用户或商品')
        row._authorize('editor')
        case._lock()
        row._lock()
        progress = dict(row.progress or {})
        if row.state != 'reconciliation_required' or progress.get('phase') != 'reconciliation_required' or progress.get('operation') != 'upload' or progress.get('linked'):
            raise ValidationError('只有未绑定的未知上传可以放弃')
        progress.update(phase='failed', code='unknown_upload_abandoned', abandoned_at=fields.Datetime.to_string(fields.Datetime.now()))
        row._set({'state':'failed', 'progress':progress})
        return {'id':row.id, 'state':'failed', 'code':'unknown_upload_abandoned'}

    @api.model
    def workbench_image_publish_reserve(self, case_id, publication_id):
        case = self._wb_case(case_id, True)
        if not self.env.su and not self.env.user.has_group('meli_bff.group_writer'):
            raise AccessError('发布需要店铺平台写入权限')
        if type(publication_id) is not int or publication_id <= 0:
            raise ValidationError('发布记录无效')
        row = self.env[PREFIX + 'image.publication'].browse(publication_id).exists()
        if not row or row.case_id != case or row.actor_id.id != self.env.uid:
            raise AccessError('发布记录不属于当前用户或商品')
        row._authorize('editor')
        case._lock()
        row._lock()
        case._validate_image_plan_proposal(row.proposal_id.payload)
        progress = row.progress or {}
        if progress.get('phase') == 'in_flight':
            if progress.get('dispatched'):
                return {'id':row.id, 'state':row.state, 'code':'dispatch_in_progress'}
            return {'id':row.id, 'state':row.state, 'operation':progress['operation'], 'nonce':progress['nonce']}
        try:
            progress = reserve_step(progress, uuid.uuid4().hex)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        state = {'upload':'uploading','link':'linking','update':'updating'}[progress['operation']]
        row._set({'state':state, 'progress':progress})
        return {'id':row.id, 'state':state, 'operation':progress['operation'], 'nonce':progress['nonce']}

    def _image_publish_fence(self, publication_id, nonce):
        # The dispatch marker is committed before making an external request.
        with self.env.registry.cursor() as cr:
            env = api.Environment(cr, self.env.uid, dict(self.env.context))
            row = env[PREFIX + 'image.publication'].browse(publication_id).exists()
            if not row or row.actor_id.id != env.uid:
                raise AccessError('发布记录不属于当前用户')
            row._authorize('editor')
            row._lock()
            try:
                progress = mark_dispatched(row.progress, nonce)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            progress['dispatched_at'] = fields.Datetime.to_string(fields.Datetime.now())
            entries = list(progress.get('diagnostics') or [])
            entries.append({'nonce':nonce, 'operation':progress['operation'], 'step':progress.get('index'),
                            'started_at':progress['dispatched_at'], 'stage':'request'})
            progress['diagnostics'] = entries[-300:]
            row._set({'progress':progress})
            cr.commit()

    def _image_publish_complete(self, publication_id, nonce, outcome, diagnostic=None, **values):
        with self.env.registry.cursor() as cr:
            env = api.Environment(cr, self.env.uid, dict(self.env.context))
            row = env[PREFIX + 'image.publication'].browse(publication_id).exists()
            if not row or row.actor_id.id != env.uid:
                raise AccessError('发布记录不属于当前用户')
            row._authorize('editor')
            row._lock()
            completed_operation = (row.progress or {}).get('operation')
            try:
                progress = finish_step(row.progress, nonce, outcome, **values)
            except ValueError:
                progress = finish_step(row.progress, nonce, 'unknown')
            progress['last_completed_at'] = fields.Datetime.to_string(fields.Datetime.now())
            entries = list(progress.get('diagnostics') or [])
            if entries and entries[-1].get('nonce') == nonce:
                entry = dict(entries[-1], **(diagnostic or {}), finished_at=progress['last_completed_at'], outcome=outcome)
                entries[-1] = entry
                progress['diagnostics'] = entries
            phase = progress['phase']
            state = ready_publication_state(completed_operation) if phase == 'ready' else phase
            row._set({'state':state, 'progress':progress})
            cr.commit()
            return {'id':row.id, 'state':state, 'progress':progress}

    @api.model
    def workbench_image_publish_dispatch(self, case_id, publication_id, nonce):
        case = self._wb_case(case_id, True)
        if not self.env.su and not self.env.user.has_group('meli_bff.group_writer'):
            raise AccessError('发布需要店铺平台写入权限')
        row = self.env[PREFIX + 'image.publication'].browse(publication_id).exists()
        if not row or row.case_id != case or row.actor_id.id != self.env.uid:
            raise AccessError('发布记录不属于当前用户或商品')
        row._authorize('editor')
        progress = row.progress or {}
        if progress.get('phase') != 'in_flight' or progress.get('nonce') != nonce or progress.get('dispatched'):
            raise ValidationError('发布步骤未领取或已派发')
        shop = case._gallery_shop(case, case._wb_snapshot(case).payload)
        is_group = progress.get('model') == 'traditional_group'
        if is_group:
            current = self._image_publish_group_current(case, row, shop)
            batch = progress.get('batch')
            expected = (batch['initial'] if batch and batch['index'] == 0 else
                        {k:batch['plan'][batch['index'] - 1][k] for k in ('root','variants')} if batch else
                        {'root':row.preview['target']['picture_ids'], 'variants':{v['id']:v['picture_ids'] for v in row.preview['target']['variants']}})
            if current != expected:
                raise ValidationError('平台图片已变化，请重新核验')
        else:
            preview = self.workbench_image_publish_preview(case_id, row.proposal_id.id, row.proposal_id.digest, row.preview['target'].get('selected_variant_id'))
            legacy_ok = (row.preview['target']['model'] == 'traditional'
                and all(preview.get(k) == row.preview.get(k) for k in ('case_id','snapshot_id','proposal_id','proposal_digest','shop_id','seller_id','source_sku','desired_value','mode'))
                and legacy_scope_matches(row.preview['target'], preview['target'], progress.get('linked', [])))
            if preview['digest'] != row.preview_digest and not legacy_ok:
                raise ValidationError('平台图片或发布范围已变化，请重新核验')
        token = shop._token()
        operation = progress['operation']
        if operation == 'upload':
            image = row.preview['desired_value'][progress['index']]
            body, content_type = self._image_publish_prepare_image(case, image)
            path, method = '/pictures/items/upload', 'POST'
        elif operation == 'link':
            picture_id = progress['batch']['plan'][progress['batch']['index']]['picture'] if is_group else progress['uploaded'][progress['index']]
            picture_update_payload([picture_id])
            body = json.dumps({'id':picture_id}, separators=(',', ':')).encode('utf-8')
            content_type, method = 'application/json', 'POST'
            path = '/items/' + row.preview['target']['target_id'] + '/pictures'
        elif operation == 'update':
            update = batch_update_payload(progress['batch']['plan'][progress['batch']['index']]) if is_group else legacy_update_payload(row.preview['target'], progress['uploaded']) if row.preview['target'].get('variants') else picture_update_payload(progress['uploaded'])
            body = json.dumps(update, separators=(',', ':')).encode('utf-8')
            content_type, method = 'application/json', 'PUT'
            path = ('/global/user-products/' if row.preview['target']['model'] == 'user_product' else '/global/items/') + row.preview['target']['target_id']
        else:
            raise ValidationError('发布步骤无效')
        self._image_publish_fence(row.id, nonce)
        outcome, values, stage, status, response_data = 'unknown', {}, 'request', None, {}
        try:
            response = transport.send(method, path, token=token, body=body, content_type=content_type, timeout=120)
            status, stage = response.status, 'response'
            if 200 <= response.status < 300:
                data = response.json()
                response_data = data
                if data.get('success') is False or data.get('errors'):
                    outcome = 'failed' if operation == 'upload' else 'unknown'
                elif operation == 'upload':
                    values = {'picture_id':data.get('id')}
                    picture_update_payload(progress['uploaded'] + [data.get('id')])
                    outcome = 'success'
                elif operation == 'link':
                    if data.get('id') != picture_id:
                        raise ValueError('平台图片绑定回执不匹配')
                    stage = 'readback' if is_group else 'response'
                    values = {'observed_ids':self._image_publish_group_current(case, row, shop)} if is_group else {'picture_id':picture_id}
                    outcome = 'success'
                else:
                    stage = 'readback'
                    if is_group:
                        observed = self._image_publish_group_current(case, row, shop)
                    elif row.preview['target']['model'] == 'user_product':
                        product = self._image_publish_read(shop, '/user-products/' + row.preview['target']['target_id'])
                        observed = user_product_picture_ids(product, row.preview['target']['target_id'], shop.seller_id)
                    else:
                        current = collect_preflight(lambda current_path: self._image_publish_read(shop,current_path),
                            row.preview['target']['item_id'], shop.site, shop.seller_id, shop.marketplace_seller_id, shop.logistic_type,
                            row.preview['target'].get('selected_variant_id'))
                        if any(current.get(k) != row.preview['target'].get(k) for k in ('model','target_id','parent_id','item_id','affected_sites')):
                            raise ValueError('平台关联发生变化')
                        if row.preview['target'].get('variants'):
                            observed = progress['uploaded'] if legacy_update_matches(row.preview['target'], current, progress['uploaded']) else []
                        else:
                            observed = current['picture_ids']
                    values = {'observed_ids':observed}
                    outcome = 'success'
            else:
                status = response.status
                try:
                    response_data = response.peek_json()
                except Exception:
                    response_data = {}
                finally:
                    response.close()
                outcome = 'failed' if operation == 'upload' and 400 <= status < 500 and status != 408 else 'unknown'
        except Exception as exc:
            outcome, values = 'unknown', {}
            response_data = {'error':type(exc).__name__, 'message':str(exc)}
        diagnostic = safe_publish_diagnostic(operation, stage, status, response_data)
        if outcome == 'success' and not diagnostic['code']:
            diagnostic['code'] = 'ok'
        return self._image_publish_complete(row.id, nonce, outcome, diagnostic=diagnostic, **values)

    @api.model
    def workbench_image_publish_reconcile(self, case_id, publication_id):
        case = self._wb_case(case_id, True)
        if not self.env.su and not self.env.user.has_group('meli_bff.group_writer'):
            raise AccessError('发布需要店铺平台写入权限')
        row = self.env[PREFIX + 'image.publication'].browse(publication_id).exists()
        if not row or row.case_id != case or row.actor_id.id != self.env.uid:
            raise AccessError('发布记录不属于当前用户或商品')
        row._authorize('editor')
        progress = dict(row.progress or {})
        if progress.get('phase') == 'in_flight' and progress.get('dispatched'):
            dispatched_at = fields.Datetime.to_datetime(progress.get('dispatched_at')) if progress.get('dispatched_at') else None
            if not dispatched_at or fields.Datetime.now() < dispatched_at + timedelta(seconds=180):
                raise ValidationError('平台请求仍可能在执行，请稍后再核对')
        elif progress.get('phase') != 'reconciliation_required':
            raise ValidationError('当前发布记录不需要核对')
        if progress.get('operation') == 'upload':
            case._lock()
            row._lock()
            if row.progress != progress:
                raise ValidationError('发布状态已变化，请重新核对')
            progress.update(phase='reconciliation_required', code='upload_outcome_unknown')
            row._set({'state':'reconciliation_required', 'progress':progress})
            return {'id':row.id, 'state':'reconciliation_required', 'code':'upload_outcome_unknown',
                    'message':'上传结果未知，无法确认平台图片 ID；请检查后人工处理。'}
        if progress.get('operation') not in ('link','update'):
            raise ValidationError('发布核对阶段无效')
        shop = case._gallery_shop(case, case._wb_snapshot(case).payload)
        if shop.id != row.preview.get('shop_id') or shop.company_id != case.company_id:
            raise ValidationError('发布店铺已变化，无法核对')
        if progress.get('model') == 'traditional_group':
            current = self._image_publish_group_current(case, row, shop)
            case._lock()
            row._lock()
            if row.progress != progress:
                raise ValidationError('发布状态已变化，请重新核对')
            batch = progress.get('batch')
            if progress.get('phase') == 'in_flight':
                batch = finish_batch_step(batch, progress['nonce'], 'unknown')
            batch = reconcile_batch_step(batch, current)
            progress.update(batch=batch, phase=batch['phase'], code='verified' if batch['phase'] == 'published' else 'step_verified' if batch['phase'] == 'ready' else 'platform_outcome_unknown', reconciled_at=fields.Datetime.to_string(fields.Datetime.now()))
            if batch['phase'] != 'reconciliation_required':
                for key in ('nonce','operation','index','dispatched'):
                    progress.pop(key, None)
            state = 'linking' if batch['phase'] == 'ready' else batch['phase']
            row._set({'state':state, 'progress':progress})
            return {'id':row.id, 'state':state, 'code':progress['code']}
        try:
            current = collect_preflight(lambda path: self._image_publish_read(shop, path), row.preview['target']['item_id'],
                shop.site, shop.seller_id, shop.marketplace_seller_id, shop.logistic_type, row.preview['target'].get('selected_variant_id'))
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        target = row.preview['target']
        if current['model'] != target['model'] or current['target_id'] != target['target_id'] or current['parent_id'] != target['parent_id']:
            raise ValidationError('平台商品关联已变化，无法核对')
        case._lock()
        row._lock()
        if row.progress != progress:
            raise ValidationError('发布状态已变化，请重新核对')
        if progress.get('operation') == 'link':
            index = progress.get('index')
            uploaded = progress.get('uploaded') or []
            if type(index) is not int or index < 0 or index >= len(uploaded):
                raise ValidationError('图片绑定记录无效')
            linked = list(progress.get('linked') or []) + [uploaded[index]]
            if legacy_scope_matches(target, current, linked):
                restored = finish_step(dict(progress, phase='in_flight'), progress['nonce'], 'success', picture_id=uploaded[index])
                restored.update(code='link_verified', reconciled_at=fields.Datetime.to_string(fields.Datetime.now()))
                row._set({'state':'linking', 'progress':restored})
                return {'id':row.id, 'state':'linking', 'code':'link_verified'}
            progress.update(phase='reconciliation_required', code='link_outcome_unknown')
            row._set({'state':'reconciliation_required', 'progress':progress})
            return {'id':row.id, 'state':'reconciliation_required', 'code':'link_outcome_unknown'}
        matched = (legacy_update_matches(target, current, progress.get('uploaded')) if target.get('variants')
                   else current['picture_ids'] == progress.get('uploaded'))
        progress.update(phase='published' if matched else 'reconciliation_required',
                        code='verified' if matched else 'platform_images_mismatch',
                        reconciled_at=fields.Datetime.to_string(fields.Datetime.now()))
        row._set({'state':'published' if matched else 'reconciliation_required', 'progress':progress})
        return {'id':row.id, 'state':row.state, 'code':progress['code']}

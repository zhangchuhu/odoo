import base64
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from ..domain import canonical_hash, validate_input, validate_result, validate_lease, validate_transition, DomainError

PREFIX = 'meli.optimization.'
MAX_JSON = 256 * 1024
MAX_BINARY = 8 * 1024 * 1024
_RESEARCH_ARTIFACT_GUARD = object()


def bounded(value):
    try:
        digest = canonical_hash(value)
        if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_JSON:
            raise ValidationError('JSON exceeds 256 KiB')
        return digest
    except (DomainError, TypeError, ValueError) as exc:
        raise ValidationError(str(exc)) from exc


class ExactJson(fields.Json):
    # Odoo Json collapses empty objects to SQL NULL; immutable digests must preserve them.
    def convert_to_cache(self, value, record, validate=True):
        return json.loads(json.dumps(value, ensure_ascii=False)) if value is not None else None


class Protected(models.AbstractModel):
    _name = PREFIX + 'protected'
    _description = 'Company scoped immutable optimization record'
    company_id = fields.Many2one('res.company', required=True, index=True, default=lambda self: self.env.company)
    _parent_field = None
    _internal_only = False
    _create_role = 'editor'

    def _authorize(self, role='editor'):
        if not self.env.su and not self.env.user.active:
            raise AccessError('Inactive user')
        if not self.env.su and not self.env.user.has_group('meli_optimization.group_' + role):
            raise AccessError('Optimization role required')
        if self:
            self.check_access('read')
            if any(r.company_id not in self.env.companies for r in self):
                raise AccessError('Company access denied')

    def _creation_values(self, values):
        # Validate the same effective inputs the ORM will store, including
        # context/user defaults. Company normally derives from the parent;
        # caller-supplied company defaults must still pass that boundary.
        missing = [name for name in self._fields if name not in values
                   and (name != 'company_id' or 'default_company_id' in self.env.context)]
        return dict(self.default_get(missing), **values)

    @api.model_create_multi
    def create(self, vals_list):
        self._authorize(self._create_role)
        if self._internal_only:
            raise AccessError('Use the fenced business operation')
        prepared = []
        for original in vals_list:
            vals = self._creation_values(original)
            if self._parent_field:
                field, model = self._parent_field
                parent = self.env[PREFIX + model].browse(vals.get(field)).exists()
                parent.ensure_one()
                parent._authorize(self._create_role)
                company = parent.company_id.id
            else:
                company = self.env.company.id
            if vals.get('company_id', company) != company:
                raise ValidationError('Company mismatch')
            vals['company_id'] = company
            prepared.append(vals)
        return super().create(prepared)

    def write(self, vals):
        raise AccessError('Optimization records are immutable; use business operations')

    def unlink(self):
        raise AccessError('Optimization audit records cannot be deleted')

    def _set(self, vals):
        return super(Protected, self).write(vals)

    def _append(self, vals):
        return super(Protected, self).create(vals)

    def _lock(self):
        self.ensure_one()
        self.env.cr.execute('SELECT id FROM "' + self._table + '" WHERE id=%s FOR UPDATE', [self.id])
        self.invalidate_recordset()


class Batch(models.Model):
    _name = PREFIX + 'batch'
    _inherit = PREFIX + 'protected'
    _description = 'Optimization batch'
    name = fields.Char(required=True)


class Case(models.Model):
    _name = PREFIX + 'case'
    _inherit = PREFIX + 'protected'
    _description = 'Optimization case'
    _parent_field = ('batch_id', 'batch')
    name = fields.Char(required=True)
    batch_id = fields.Many2one(PREFIX + 'batch', required=True, ondelete='restrict')
    revision = fields.Integer(default=0, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if 'default_revision' in self.env.context or any('revision' in v for v in vals_list):
            raise AccessError('Revision is server managed')
        return super().create([dict(self._creation_values(v), revision=0) for v in vals_list])


class Snapshot(models.Model):
    _name = PREFIX + 'snapshot'
    _inherit = PREFIX + 'protected'
    _description = 'Immutable product snapshot'
    _parent_field = ('case_id', 'case')
    case_id = fields.Many2one(PREFIX + 'case', required=True, ondelete='restrict')
    payload = ExactJson(required=True)
    digest = fields.Char(required=True, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._creation_values(v) for v in vals_list]
        vals_list = [dict(v, digest=bounded(v.get('payload'))) for v in vals_list]
        for v in vals_list:
            case = self.env[PREFIX + 'case'].browse(v['case_id'])
            case._authorize()
            case._lock()
            refs = (v.get('payload') or {}).get('source_image_artifact_ids', [])
            if not isinstance(refs, list) or any(type(ref) not in (str, int) or not str(ref).isdigit() for ref in refs) or len({str(ref) for ref in refs}) != len(refs):
                raise ValidationError('源图片引用无效')
            for ref in refs:
                artifact = self.env[PREFIX + 'artifact'].browse(int(ref)).exists()
                if not artifact or artifact.case_id != case or artifact.company_id != case.company_id or not artifact.data or artifact.mime_type not in ('image/png', 'image/jpeg', 'image/webp'):
                    raise ValidationError('源图片归属或内容无效')
                artifact._authorize('worker')
                try:
                    raw = base64.b64decode(artifact.data, validate=True)
                except (ValueError, TypeError) as exc:
                    raise ValidationError('源图片编码无效') from exc
                from .library import verified_image
                verified_image(raw, artifact.mime_type)
                if hashlib.sha256(raw).hexdigest() != artifact.digest:
                    raise ValidationError('源图片摘要不匹配')
        records = super().create(vals_list)
        for record in records:
            case = record.case_id
            case._set({'revision': case.revision + 1})
        return records


class Task(models.Model):
    _name = PREFIX + 'task'
    _inherit = PREFIX + 'protected'
    _description = 'Fenced optimization task'
    _scoped_fresh_claim_guard = True
    _parent_field = ('case_id', 'case')
    case_id = fields.Many2one(PREFIX + 'case', required=True, ondelete='restrict')
    snapshot_id = fields.Many2one(PREFIX + 'snapshot', required=True, ondelete='restrict')
    task_type = fields.Char(required=True, index=True)
    payload = ExactJson(required=True)
    state = fields.Selection([(s, s) for s in ('queued', 'running', 'waiting', 'succeeded', 'failed', 'cancelled')], default='queued', required=True, index=True)
    dependency_ids = fields.Many2many(PREFIX + 'task', 'meli_opt_task_dependency', 'task_id', 'dependency_id')
    lease_token = fields.Char(readonly=True)
    lease_epoch = fields.Integer(default=0, readonly=True)
    lease_expires_at = fields.Datetime(readonly=True)
    worker_uid = fields.Many2one('res.users', readonly=True)
    result = ExactJson(readonly=True)
    status_detail = ExactJson(readonly=True)
    request_ids = fields.One2many(PREFIX + 'external.request', 'task_id', readonly=True)
    research_archived = fields.Boolean(default=False, readonly=True, index=True)
    scoped_research = fields.Boolean(default=False, readonly=True, index=True)
    scoped_coverage = fields.Selection([('unknown', 'Unknown')], default='unknown', required=True, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        forbidden = {'state', 'lease_token', 'lease_epoch', 'lease_expires_at', 'worker_uid', 'result', 'status_detail', 'request_ids', 'scoped_research', 'scoped_coverage', 'research_archived'}
        if any('default_' + name in self.env.context for name in forbidden):
            raise AccessError('Task state is server managed')
        for v in vals_list:
            if forbidden.intersection(v):
                raise AccessError('Task state is server managed')
        vals_list = [dict(self._creation_values(v), state='queued', lease_token=False, lease_epoch=0,
                          lease_expires_at=False, worker_uid=False, result=None, status_detail=None,
                          request_ids=[]) for v in vals_list]
        cases = self.env[PREFIX + 'case'].browse(sorted({v.get('case_id') for v in vals_list if v.get('case_id')})).exists()
        for case in cases:
            case._lock()
            case._collection_case_fence()
            if getattr(case, 'collection_archived', False):
                raise ValidationError('已归档的独立采集不能创建任务')
        for v in vals_list:
            bounded(v.get('payload'))
            try:
                validate_input(v['task_type'], v['payload'])
            except DomainError as exc:
                raise ValidationError(str(exc)) from exc
        for v in vals_list:
            case = self.env[PREFIX + 'case'].browse(v.get('case_id')).exists()
            if not case:
                raise ValidationError('商品流程不存在')
            case._authorize()
            if (case.collection and v['task_type'] in ('product.facts', 'research.keywords', 'research.bestseller')
                    and (case.collection_started_snapshot_id.id != v.get('snapshot_id')
                         or case._wb_snapshot(case).id != v.get('snapshot_id'))):
                raise ValidationError('请先点击一键开始采集')
            required = {'research.keywords': {'facts_artifact_id': 'product.facts'},
                        'research.bestseller': {'facts_artifact_id': 'product.facts', 'keywords_artifact_id': 'research.keywords'},
                        'title.generate': {'facts_artifact_id': 'product.facts', 'keywords_artifact_id': 'research.keywords'}}.get(v['task_type'], {})
            for field, expected in required.items():
                ref = v['payload'].get(field)
                artifact = self.env[PREFIX + 'artifact'].browse(int(ref)).exists() if isinstance(ref, str) and ref.isdigit() else self.env[PREFIX + 'artifact']
                if not artifact:
                    raise ValidationError('缺少有效的依赖产物')
                artifact._authorize('worker')
                source = artifact.result_task_id
                if (artifact.company_id != case.company_id or artifact.case_id != case or
                    artifact.result_snapshot_id.id != v.get('snapshot_id') or not source or source.task_type != expected or
                    source.state != 'succeeded' or artifact.result_hash != canonical_hash(source.result) or
                    artifact.digest != canonical_hash(artifact.payload) or artifact.payload != source.result):
                    raise ValidationError('依赖产物类型、快照或来源不匹配')
                if expected == 'research.keywords' and artifact.payload.get('status') == 'empty':
                    raise ValidationError('关键词结果为空，不能用于下游任务')
        records = super().create(vals_list)
        for record in records:
            if record.snapshot_id.case_id != record.case_id or any(d.case_id != record.case_id for d in record.dependency_ids):
                raise ValidationError('Snapshot/dependencies must belong to task case')
            if record.id in record.dependency_ids.ids:
                raise ValidationError('Self dependency')
        return records

    @api.model
    def research_queue_precheck(self):
        """Only reveal whether a currently authorized, ready research task exists."""
        self._authorize('worker')
        domain=[('company_id','in',self.env.companies.ids),('task_type','=','research.bestseller'),('research_archived','=',False),
                '|',('case_id.collection','=',False),('case_id.collection_started_snapshot_id','!=',False),
                '|',('state','=','queued'),'&',('state','=','running'),('lease_expires_at','<=',fields.Datetime.now()),
                ('case_id.collection_archived','=',False),
                ('dependency_ids','not any',[('state','!=','succeeded')]),
                ('request_ids','not any',[('state','in',['unknown','dispatched']),('retry_superseded','=',False)])]
        scoped = self.env.user.has_group('meli_optimization.group_research_worker') and not self.env.su and not self.env.user.has_group('base.group_system')
        domain.append(('scoped_research', '=', bool(scoped)))
        if scoped: domain.append(('state', '=', 'queued'))
        candidates = self.search(domain, order='id')
        return {'claimable': any(task._collection_start_authorized() for task in candidates)}

    def _collection_start_authorized(self):
        self.ensure_one()
        case = self.case_id
        if not case.collection or self.task_type not in ('product.facts', 'research.keywords', 'research.bestseller'):
            return True
        return (case.collection_started_snapshot_id == self.snapshot_id
                and case._wb_snapshot(case) == self.snapshot_id)

    def prepare_scoped_research(self):
        self.ensure_one()
        self._authorize('editor')
        self.case_id._lock()
        self._lock()
        if (self.task_type != 'research.bestseller' or self.state != 'queued' or self.lease_epoch or self.scoped_research
                or self.request_ids or self.env[PREFIX + 'attempt'].search_count([('task_id', '=', self.id)])
                or self.env[PREFIX + 'event'].search_count([('task_id', '=', self.id)])):
            raise ValidationError('Only a fresh queued research task can be scoped')
        self._set({'scoped_research': True, 'scoped_coverage': 'unknown'})
        return True

    def prior_research_lease_inactive(self, token, epoch, old_request):
        """Return only whether a company-local prior research lease is inactive."""
        self.ensure_one()
        if (self.env.su or self.env.user.has_group('base.group_system') or
                not self.env.user.has_group('meli_optimization.group_research_worker')):
            raise AccessError('Scoped non-administrator worker required')
        self._fence(token,epoch)
        if self.task_type!='research.bestseller' or not self.scoped_research or not isinstance(old_request,str):
            raise ValidationError('Scoped research lease required')
        records=self.env[PREFIX+'external.request'].sudo().search([
            ('request_id','=',old_request),('company_id','=',self.company_id.id)],limit=2)
        if len(records)!=1: return False
        old=records.task_id
        if old.id == self.id:
            return bool(records.retry_superseded)
        if old.task_type!='research.bestseller' or not old.scoped_research: return False
        old._lock()
        return bool(old.state!='running' or (old.lease_expires_at and old.lease_expires_at<=fields.Datetime.now()))

    def authorize_search_checkpoint(self, token, epoch, request_id, source_request, source_epoch):
        """Authorize reuse of observations only. Both requests remain immutable."""
        self.ensure_one()
        self._fence(token, epoch)
        if (self.env.su or self.env.user.has_group('base.group_system')
                or not self.env.user.has_group('meli_optimization.group_research_worker')
                or self.task_type != 'research.bestseller' or not self.scoped_research):
            raise AccessError('Scoped research worker and task required')
        if (type(source_epoch) is not int or not 0 < source_epoch < epoch or request_id == source_request
                or any(not isinstance(r, str) or not 0 < len(r) <= 200 for r in (source_request, request_id))):
            raise ValidationError('Invalid checkpoint lineage')
        requests = self.request_ids
        source = requests.filtered(lambda r: r.request_id == source_request)
        target = requests.filtered(lambda r: r.request_id == request_id)
        if (len(source) != 1 or not (source.retry_superseded or source.state == 'failed')
                or len(target) != 1 or target.retry_superseded or target.state not in ('dispatched', 'unknown')):
            return {'allowed': False}
        events = self.env[PREFIX + 'event']
        for rid, initial_epoch in ((request_id, epoch), (source_request, source_epoch)):
            key = 'scoped-request:' + hashlib.sha256(rid.encode()).hexdigest()
            event = events.search([('task_id', '=', self.id), ('company_id', '=', self.company_id.id),
                                   ('kind', '=', 'scoped_request_registration'), ('submission_key', '=', key),
                                   ('worker_uid', '=', self.env.uid)], limit=1)
            if not event or event.payload != {'request_id': rid, 'initial_epoch': initial_epoch}:
                return {'allowed': False}
        return {'allowed': True, 'task_id': self.id, 'snapshot_id': self.snapshot_id.id,
                'input_hash': canonical_hash(self.payload), 'request_id': request_id, 'lease_epoch': epoch,
                'source_request_id': source_request, 'source_epoch': source_epoch}

    def _verify_gallery_receipts(self, request_id, evidence_id, evidence_digest, images):
        if (not isinstance(evidence_id, str) or not re.fullmatch(r'[1-9][0-9]*', evidence_id)
                or not isinstance(evidence_digest, str) or not re.fullmatch(r'[a-f0-9]{64}', evidence_digest)
                or not isinstance(images, dict) or len(images) > 1500
                or any(not isinstance(key, str) or not re.fullmatch(r'image:[a-f0-9]{64}', key) for key in images)):
            raise ValidationError('Invalid original gallery receipts')
        evidence_key = 'evidence:' + hashlib.sha256(b'').hexdigest()
        expected = dict(images)
        expected[evidence_key] = {'artifact_id': evidence_id, 'digest': evidence_digest}
        events = self.env[PREFIX + 'event'].search([('task_id', '=', self.id), ('kind', '=', 'research_artifact')])
        actual = events.filtered(lambda e: e.payload.get('request_id') == request_id)
        if len(actual) != len(expected):
            raise ValidationError('Original gallery receipt set differs')
        for event in actual:
            key = event.payload.get('intent_id')
            receipt = event.receipt
            if (event.worker_uid != self.env.user or event.company_id != self.company_id
                    or expected.get(key) != {'artifact_id': str(receipt['artifact_id']), 'digest': receipt['digest']}):
                raise ValidationError('Original gallery receipt differs')
            artifact = self.env[PREFIX + 'artifact'].browse(receipt['artifact_id']).exists()
            if (not artifact or artifact.case_id != self.case_id or artifact.digest != receipt['digest']
                    or (artifact.mime_type != 'application/json' if key == evidence_key
                        else artifact.mime_type not in ('image/jpeg', 'image/png', 'image/webp'))):
                raise ValidationError('Original gallery artifact differs')

    def _has_registered_evidence(self, requests):
        evidence_key = 'evidence:' + hashlib.sha256(b'').hexdigest()
        keys = [self._research_intent_key(r.request_id, evidence_key) for r in requests]
        return bool(keys and self.env[PREFIX + 'event'].sudo().search_count([
            ('task_id', '=', self.id), ('company_id', '=', self.company_id.id),
            ('kind', '=', 'research_artifact'), ('submission_key', 'in', keys)], limit=1))

    def continue_registered_research(self, token, epoch, request_id, intent_id, snapshot_id, input_hash,
                                     *, evidence_artifact_id=None, evidence_digest=None, confirmed_images=None):
        """Reattach the original worker to its in-progress registered request."""
        self.ensure_one()
        self._authorize('worker')
        if (self.env.su or self.env.user.has_group('base.group_system')
                or not self.env.user.has_group('meli_optimization.group_research_worker')):
            raise AccessError('Scoped non-administrator research worker required')
        if (not isinstance(token, str) or not 0 < len(token) <= 200 or type(epoch) is not int or epoch < 1
                or not isinstance(request_id, str) or not 0 < len(request_id) <= 200
                or not isinstance(intent_id, str) or len(intent_id) != 32
                or any(ch not in '0123456789abcdef' for ch in intent_id)
                or type(snapshot_id) is not int or not isinstance(input_hash, str)):
            raise ValidationError('Original request and lease identity required')
        self.case_id._lock()
        self._lock()
        gallery = any(value is not None for value in (evidence_artifact_id, evidence_digest, confirmed_images))
        waiting = self.state == 'waiting' and gallery
        if ((not waiting and (self.state != 'running' or self.worker_uid != self.env.user or not self.lease_expires_at))
                or self.task_type != 'research.bestseller' or not self.scoped_research
                or self.research_archived or self.case_id.collection_archived or not self._collection_start_authorized()
                or self.lease_epoch != epoch or self.snapshot_id.id != snapshot_id or canonical_hash(self.payload) != input_hash):
            raise ValidationError('Original running research lease required')
        unresolved = self.request_ids.filtered(lambda r: not r.retry_superseded and r.state in ('dispatched', 'unknown'))
        if len(unresolved) != 1 or unresolved.request_id != request_id:
            raise ValidationError('Sole original outstanding request required')
        events = self.env[PREFIX + 'event']
        domain = [('task_id', '=', self.id), ('company_id', '=', self.company_id.id), ('worker_uid', '=', self.env.uid)]
        registration = events.search(domain + [('kind', '=', 'scoped_request_registration'),
            ('submission_key', '=', 'scoped-request:' + hashlib.sha256(request_id.encode()).hexdigest())], limit=1)
        if not registration or registration.payload != {'request_id': request_id, 'initial_epoch': epoch}:
            raise AccessError('Original scoped registration required')
        # Retain the request's epoch so existing query checkpoints stay usable.
        # A rotated token fences the previous process. Only the journaled recovery
        # intent may replay an acknowledgement lost after the transaction committed.
        identity = {'token': token, 'epoch': epoch, 'request_id': request_id, 'intent_id': intent_id}
        if gallery:
            self._verify_gallery_receipts(request_id, evidence_artifact_id, evidence_digest, confirmed_images)
            identity['gallery'] = canonical_hash([evidence_artifact_id, evidence_digest, confirmed_images])
        digest = bounded(identity)
        key = 'scoped-continue:' + digest
        if waiting:
            wait = events.search(domain + [('kind', '=', 'wait')], order='id desc', limit=1)
            if (self.lease_token or self.worker_uid or self.lease_expires_at or unresolved.state != 'unknown'
                    or (self.status_detail or {}).get('code') != 'external_result_unknown'
                    or not wait or wait.payload != self.status_detail or
                    wait.digest != bounded({'body': wait.payload, 'token': token, 'epoch': epoch, 'kind': 'wait'})):
                raise ValidationError('Original waiting research lease required')
        elif self.lease_token != token or gallery:
            prior = events.search(domain + [('kind', '=', 'scoped_lease_continue'), ('submission_key', '=', key)], limit=1)
            current_token_digest = canonical_hash(self.lease_token)
            latest = events.search(domain + [('kind', '=', 'scoped_gallery_refresh'), ('digest', '=', digest)],
                                   order='id desc', limit=1) if gallery and prior else None
            if not prior or (latest or prior).receipt.get('token_digest') != current_token_digest:
                raise ValidationError('Original lease token required')
            if gallery and self.lease_expires_at <= fields.Datetime.now():
                self._set({'lease_token':str(uuid.uuid4()), 'lease_expires_at':fields.Datetime.now() + timedelta(seconds=120)})
                events._append({'task_id':self.id, 'company_id':self.company_id.id, 'worker_uid':self.env.uid,
                    'kind':'scoped_gallery_refresh', 'submission_key':'gallery-refresh:' + canonical_hash([digest, current_token_digest]),
                    'digest':digest, 'payload':{'request_id':request_id, 'epoch':epoch},
                    'receipt':{'token_digest':canonical_hash(self.lease_token)}})
            return {'request_id': request_id, 'claim': self._envelope()}
        now = fields.Datetime.now()
        if waiting or self.lease_expires_at <= now:
            self._set({'lease_token': str(uuid.uuid4()), 'state': 'running', 'worker_uid': self.env.uid, 'status_detail': None})
            events._append({'task_id': self.id, 'company_id': self.company_id.id, 'worker_uid': self.env.uid,
                'kind': 'scoped_lease_continue', 'submission_key': key, 'digest': digest,
                'payload': {'request_id': request_id, 'epoch': epoch}, 'receipt': {'token_digest': canonical_hash(self.lease_token)}})
        self._set({'lease_expires_at': now + timedelta(seconds=120)})
        return {'request_id': request_id, 'claim': self._envelope()}

    def scoped_resume_preflight(self, token, epoch, request_id):
        """Permit inspection of a known terminal request only, never unknown replay."""
        self.ensure_one()
        if (self.env.su or self.env.user.has_group('base.group_system')
                or not self.env.user.has_group('meli_optimization.group_research_worker')):
            raise AccessError('Scoped non-administrator worker required')
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
            raise ValidationError('Request identity required')
        self._fence(token, epoch)
        if self.task_type != 'research.bestseller' or not self.scoped_research:
            raise ValidationError('Scoped research task required')
        record = self.env[PREFIX + 'external.request'].search([('task_id', '=', self.id), ('company_id', '=', self.company_id.id), ('request_id', '=', request_id)], limit=1)
        if not record or record.state != 'succeeded':
            raise ValidationError('Request requires reconciliation')
        events = self.env[PREFIX + 'event'].search([('task_id', '=', self.id), ('company_id', '=', self.company_id.id), ('kind', '=', 'checkpoint')], order='id')
        history = [event for event in events if isinstance(event.payload, dict) and event.payload.get('request_id') == request_id]
        if not history or history[0].worker_uid.id != self.env.uid:
            raise AccessError('Original request owner required')
        if any(event.payload.get('state') in ('unknown', 'dispatched') for event in history):
            raise ValidationError('Prior uncertainty requires independently verified reconciliation')
        return {'task_id': self.id, 'request_id': request_id, 'resume_allowed': True, 'coverage': 'unknown'}

    def _scoped_retry_authorized(self):
        self.ensure_one()
        events = self.env[PREFIX + 'event'].sudo()
        domain = [('task_id', '=', self.id)]
        retry = events.search(domain + [('kind', '=', 'unknown_research_retry')], order='id desc', limit=1)
        if retry and retry.payload.get('epoch') == self.lease_epoch:
            return True
        resume = events.search(domain + [('kind', '=', 'resume')], order='id desc', limit=1)
        return bool(resume and resume.payload == {'code': 'window_registration_failed', 'epoch': self.lease_epoch})

    def claim(self, capabilities):
        self._authorize('worker')
        scoped = self.env.user.has_group('meli_optimization.group_research_worker') and not self.env.su and not self.env.user.has_group('base.group_system')
        if self.env.user.has_group('meli_optimization.group_research_worker') and not scoped:
            raise AccessError('Scoped worker cannot be an administrator')
        if not isinstance(capabilities, list) or not capabilities or len(capabilities) > 32 or any(not isinstance(c, str) for c in capabilities):
            raise ValidationError('Capabilities must be a nonempty string list')
        # Apply coarse readiness in SQL, then recheck the current snapshot after
        # locking. Do not cap the candidates before that check: old snapshots can
        # otherwise occupy the entire window and starve a later ready task.
        candidates = self or self.search([
            ('company_id', 'in', self.env.companies.ids), ('task_type', 'in', capabilities), ('research_archived', '=', False),
            '|', '|', ('case_id.collection', '=', False), ('task_type', 'not in', ['product.facts','research.keywords','research.bestseller']), ('case_id.collection_started_snapshot_id','!=',False),
            '|', ('state', '=', 'queued'), '&', ('state', '=', 'running'),
            ('lease_expires_at', '<=', fields.Datetime.now()),
            ('dependency_ids', 'not any', [('state', '!=', 'succeeded')]),
            ('request_ids', 'not any', [('state', 'in', ['unknown', 'dispatched']), ('retry_superseded', '=', False)]),
            ('scoped_research', '=', bool(scoped)),
        ], order='id')
        for task in candidates:
            task._authorize('worker')
            case = task.case_id
            self.env.cr.execute('SELECT id FROM meli_optimization_case WHERE id=%s FOR UPDATE SKIP LOCKED', [case.id])
            if not self.env.cr.fetchone():
                continue
            case.invalidate_recordset()
            if getattr(case, 'collection_archived', False):
                continue
            if not task._collection_start_authorized():
                continue
            self.env.cr.execute('SELECT id FROM meli_optimization_task WHERE id=%s FOR UPDATE SKIP LOCKED', [task.id])
            if not self.env.cr.fetchone():
                continue
            task.invalidate_recordset()
            now = fields.Datetime.now()
            if task.research_archived:
                continue
            if (scoped and (task.task_type != 'research.bestseller' or not task.scoped_research)) or (not scoped and task.scoped_research):
                continue
            if scoped and task.state != 'queued':
                continue
            if scoped and not task.lease_epoch and (task.request_ids
                    or self.env[PREFIX + 'attempt'].search_count([('task_id', '=', task.id)])
                    or self.env[PREFIX + 'event'].search_count([('task_id', '=', task.id)])):
                continue
            if scoped and task.lease_epoch and not task._scoped_retry_authorized():
                continue
            if task.task_type not in capabilities or task.state not in ('queued', 'running') or (task.state == 'running' and task.lease_expires_at > now):
                continue
            if any(d.state != 'succeeded' for d in task.dependency_ids):
                continue
            if task._unresolved_requests():
                continue
            if task.state == 'queued':
                task._transition('running')
            task._set({'state': 'running', 'lease_token': str(uuid.uuid4()), 'lease_epoch': task.lease_epoch + 1, 'lease_expires_at': now + timedelta(seconds=120), 'worker_uid': self.env.uid, 'status_detail': None})
            self.env[PREFIX + 'attempt']._append({'task_id': task.id, 'company_id': task.company_id.id, 'epoch': task.lease_epoch, 'worker_uid': self.env.uid})
            return task._envelope()
        return False

    def _envelope(self):
        return {'input_hash': canonical_hash(self.payload), 'schema_version': '1', 'id': self.id, 'task_type': self.task_type, 'payload': self.payload, 'snapshot_id': self.snapshot_id.id, 'company_id': self.company_id.id, 'lease_token': self.lease_token, 'lease_epoch': self.lease_epoch, 'lease_expires_at': fields.Datetime.to_string(self.lease_expires_at), 'heartbeat_seconds': 30}

    def _fence(self, token, epoch):
        self._authorize('worker')
        self.case_id._lock()
        self._lock()
        if not self._collection_start_authorized():
            raise ValidationError('请先点击一键开始采集')
        try:
            validate_lease({'state': self.state, 'lease_token': self.lease_token, 'lease_epoch': self.lease_epoch}, token, epoch, self.lease_expires_at.replace(tzinfo=timezone.utc) if self.lease_expires_at else None, datetime.now(timezone.utc))
        except DomainError as exc:
            raise ValidationError(str(exc)) from exc
        if self.worker_uid.id != self.env.uid:
            raise AccessError('Lease belongs to another worker')

    def recover_lease(self, request_id, token, epoch, recovery_intent_id):
        """Rotate an expired eligible lease while retaining its original request."""
        self.ensure_one()
        self._authorize('worker')
        if (not isinstance(request_id, str) or not request_id or len(request_id) > 200
                or not isinstance(token, str) or not token or len(token) > 200
                or type(epoch) is not int or epoch < 1
                or not isinstance(recovery_intent_id, str)
                or len(recovery_intent_id) != 32
                or any(ch not in '0123456789abcdef' for ch in recovery_intent_id)):
            raise ValidationError('Invalid recovery credentials')
        self.case_id._lock()
        self._lock()
        self.invalidate_recordset()
        if not self._collection_start_authorized():
            raise ValidationError('请先点击一键开始采集')
        if self.scoped_research:
            raise ValidationError('Scoped research lease recovery requires independently verified reconciliation')
        if self.task_type not in ('research.bestseller', 'research.keywords', 'product.facts') or self.case_id.collection_archived:
            raise ValidationError('Only active supported tasks can be recovered')
        digest = bounded({'request_id': request_id, 'token': token, 'epoch': epoch,
                          'recovery_intent_id': recovery_intent_id})
        key = 'lease-recover:' + digest
        events = self.env[PREFIX + 'event']
        previous = events.search([('task_id', '=', self.id), ('kind', '=', 'lease_recover')])
        if any(e.payload.get('previous_epoch') == epoch and e.digest != digest for e in previous):
            raise ValidationError('Recovery intent conflicts with existing attempt')
        prior = events.search([('task_id', '=', self.id), ('submission_key', '=', key)], limit=1)
        now = fields.Datetime.now()
        requests = self.env[PREFIX + 'external.request'].search([
            ('task_id', '=', self.id), ('state', 'in', ['unknown', 'dispatched']), ('retry_superseded', '=', False)])
        if prior:
            lineage = events.search([('task_id', '=', self.id), ('digest', '=', digest),
                                     ('kind', 'in', ['lease_recover', 'lease_recover_refresh'])])
            if (prior.kind != 'lease_recover' or prior.worker_uid.id != self.env.uid
                    or self.state != 'running' or self.worker_uid.id != self.env.uid
                    or len(requests) != 1 or requests.request_id != request_id
                    or not self.lease_expires_at
                    or not any(e.worker_uid.id == self.env.uid and
                               e.receipt.get('lease_epoch') == self.lease_epoch for e in lineage)):
                raise ValidationError('Recovery receipt is no longer active')
            if self.lease_expires_at > now:
                return self._envelope()
            next_epoch = self.lease_epoch + 1
            self._set({'lease_token': str(uuid.uuid4()), 'lease_epoch': next_epoch,
                       'lease_expires_at': now + timedelta(seconds=120)})
            self.env[PREFIX + 'attempt']._append({'task_id': self.id, 'epoch': next_epoch, 'worker_uid': self.env.uid})
            events._append({'task_id': self.id, 'company_id': self.company_id.id,
                'submission_key': key + ':' + str(next_epoch), 'digest': digest,
                'kind': 'lease_recover_refresh', 'payload': {'request_id': request_id, 'previous_epoch': next_epoch - 1},
                'receipt': {'task_id': self.id, 'lease_epoch': next_epoch}, 'worker_uid': self.env.uid})
            return self._envelope()
        waiting_fence = False
        if (self.state == 'waiting' and self.lease_epoch == epoch
                and not self.lease_token and not self.worker_uid and not self.lease_expires_at
                and (self.status_detail or {}).get('code') == 'external_result_unknown'
                and len(requests) == 1 and requests.request_id == request_id and requests.state == 'unknown'):
            waits = events.search([('task_id', '=', self.id), ('kind', '=', 'wait'),
                                   ('worker_uid', '=', self.env.uid)])
            waiting_fence = any(e.payload == self.status_detail and e.digest == bounded({
                'body': e.payload, 'token': token, 'epoch': epoch, 'kind': 'wait'}) for e in waits)
        running_fence = (self.state == 'running' and self.worker_uid.id == self.env.uid
                and self.lease_token == token and self.lease_epoch == epoch
                and self.lease_expires_at and self.lease_expires_at <= now
                and len(requests) == 1 and requests.request_id == request_id)
        if not waiting_fence and not running_fence:
            raise ValidationError('Expired or verified waiting research lease and request required')
        self._set({'state': 'running', 'worker_uid': self.env.uid, 'status_detail': None,
                   'lease_token': str(uuid.uuid4()), 'lease_epoch': epoch + 1,
                   'lease_expires_at': now + timedelta(seconds=120)})
        self.env[PREFIX + 'attempt']._append({'task_id': self.id, 'epoch': epoch + 1, 'worker_uid': self.env.uid})
        events._append({'task_id': self.id, 'company_id': self.company_id.id,
            'submission_key': key, 'digest': digest, 'kind': 'lease_recover',
            'payload': {'request_id': request_id, 'previous_epoch': epoch},
            'receipt': {'task_id': self.id, 'lease_epoch': epoch + 1}, 'worker_uid': self.env.uid})
        return self._envelope()

    def heartbeat(self, token, epoch):
        self._fence(token, epoch)
        self._set({'lease_expires_at': fields.Datetime.now() + timedelta(seconds=120)})
        return self._envelope()

    def _research_intent_key(self, request_id, intent_id):
        if any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in (request_id, intent_id)):
            raise ValidationError('Invalid research request or intent')
        return 'research-artifact:' + hashlib.sha256(json.dumps([request_id, intent_id], ensure_ascii=False).encode()).hexdigest()

    def research_artifact(self, token, epoch, request_id, intent_id, artifact):
        self.ensure_one()
        self._fence(token, epoch)
        if self.task_type != 'research.bestseller': raise ValidationError('Research task required')
        key = self._research_intent_key(request_id, intent_id)
        if not self.env[PREFIX + 'external.request'].search_count([('task_id', '=', self.id), ('request_id', '=', request_id), ('retry_superseded', '=', False), ('state', 'in', ['unknown', 'dispatched'])]):
            raise ValidationError('Active research request required')
        if not isinstance(artifact, dict) or set(artifact) - {'data', 'payload', 'digest', 'mime_type'}:
            raise ValidationError('Invalid artifact')
        digest = hashlib.sha256(json.dumps(artifact, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        events = self.env[PREFIX + 'event']
        prior = events.search([('task_id', '=', self.id), ('submission_key', '=', key)], limit=1)
        if prior:
            if prior.kind != 'research_artifact' or prior.digest != digest or prior.worker_uid.id != self.env.uid: raise ValidationError('Research intent conflict')
            return prior.receipt
        seal_key = 'scoped-manifest:' + hashlib.sha256(request_id.encode()).hexdigest()
        if self.scoped_research and events.search_count([('task_id', '=', self.id), ('submission_key', '=', seal_key)]):
            raise ValidationError('Research manifest is sealed')
        item = self.env[PREFIX + 'artifact'].with_context(_research_artifact_guard=_RESEARCH_ARTIFACT_GUARD).create(dict(artifact, case_id=self.case_id.id))
        receipt = {'artifact_id': item.id, 'digest': item.digest, 'coverage': 'unknown'}
        events._append({'task_id': self.id, 'company_id': self.company_id.id, 'submission_key': key, 'digest': digest, 'kind': 'research_artifact', 'payload': {'request_id': request_id, 'intent_id': intent_id}, 'receipt': receipt, 'worker_uid': self.env.uid})
        return receipt

    def seal_scoped_manifest(self, token, epoch, request_id, intents):
        self.ensure_one()
        self._fence(token, epoch)
        if (not self.scoped_research or self.task_type != 'research.bestseller' or self.env.su
                or self.env.user.has_group('base.group_system') or not self.env.user.has_group('meli_optimization.group_research_worker')):
            raise AccessError('Scoped research worker required')
        self._research_intent_key(request_id, 'validate')
        if (not isinstance(intents, list) or not intents or len(intents) > 500
                or any(not isinstance(i, str) or not i.strip() or len(i) > 200 for i in intents)
                or len(set(intents)) != len(intents)):
            raise ValidationError('Invalid intent manifest')
        requests = self.env[PREFIX + 'external.request'].search([('task_id', '=', self.id), ('request_id', '=', request_id), ('retry_superseded', '=', False), ('state', 'in', ['unknown', 'dispatched', 'succeeded'])], limit=1)
        if not requests: raise ValidationError('Active or succeeded request required')
        events = self.env[PREFIX + 'event']
        registration_key = 'scoped-request:' + hashlib.sha256(request_id.encode()).hexdigest()
        registration = events.search([('task_id', '=', self.id), ('submission_key', '=', registration_key), ('kind', '=', 'scoped_request_registration')], limit=1)
        if not registration or registration.worker_uid.id != self.env.uid: raise AccessError('Scoped registration required')
        evidence = events.search([('task_id', '=', self.id), ('kind', '=', 'research_artifact')])
        actual = [e for e in evidence if isinstance(e.payload, dict) and e.payload.get('request_id') == request_id]
        if any(e.worker_uid.id != self.env.uid or not e.receipt.get('artifact_id') or not e.receipt.get('digest') for e in actual):
            raise ValidationError('Invalid intent receipt')
        if {e.payload.get('intent_id') for e in actual} != set(intents) or len(actual) != len(intents):
            raise ValidationError('Manifest and receipts differ')
        payload = {'request_id': request_id, 'intents': sorted(intents)}
        digest = bounded(payload)
        key = 'scoped-manifest:' + hashlib.sha256(request_id.encode()).hexdigest()
        prior = events.search([('task_id', '=', self.id), ('submission_key', '=', key)], limit=1)
        if prior:
            if prior.kind != 'scoped_manifest' or prior.digest != digest or prior.worker_uid.id != self.env.uid: raise ValidationError('Manifest conflict')
            return dict(prior.receipt)
        receipt = {'request_id': request_id, 'intent_count': len(intents), 'coverage': 'unknown'}
        events._append({'task_id': self.id, 'company_id': self.company_id.id, 'submission_key': key, 'digest': digest,
                        'kind': 'scoped_manifest', 'payload': payload, 'receipt': receipt, 'worker_uid': self.env.uid})
        return receipt

    def research_intent_query(self, token, epoch, request_id, intent_id):
        self.ensure_one()
        self._fence(token, epoch)
        if self.task_type != 'research.bestseller': raise ValidationError('Research task required')
        key = self._research_intent_key(request_id, intent_id)
        event = self.env[PREFIX + 'event'].search([('task_id', '=', self.id), ('submission_key', '=', key), ('kind', '=', 'research_artifact')], limit=1)
        if event and event.worker_uid.id != self.env.uid: raise AccessError('Intent belongs to another worker')
        return dict(event.receipt) if event else {'coverage': 'unknown', 'artifact_id': None, 'digest': None}

    def research_intent_reconcile(self, request_id, intent_id):
        """Company editor read-only lookup; absence does not establish coverage."""
        self.ensure_one()
        self._authorize('editor')
        self._lock()
        if self.task_type != 'research.bestseller' or self.state != 'waiting':
            raise ValidationError('Waiting research task required')
        key = self._research_intent_key(request_id, intent_id)
        request_record = self.env[PREFIX + 'external.request'].search([
            ('task_id', '=', self.id), ('company_id', '=', self.company_id.id), ('request_id', '=', request_id)], limit=1)
        if not request_record: raise ValidationError('Research request not found')
        event = self.env[PREFIX + 'event'].search([
            ('task_id', '=', self.id), ('company_id', '=', self.company_id.id),
            ('submission_key', '=', key), ('kind', '=', 'research_artifact')], limit=1)
        return dict(event.receipt, coverage='unknown') if event else {'coverage': 'unknown', 'artifact_id': None, 'digest': None}

    def _submit(self, token, epoch, key, body, kind):
        self._authorize('worker')
        self.case_id._lock()
        self._lock()
        if not isinstance(key, str) or not key or len(key) > 128:
            raise ValidationError('Submission key required (max 128)')
        digest = bounded({'body': body, 'token': token, 'epoch': epoch, 'kind': kind})
        event_key = key if epoch == 1 else f'epoch:{epoch}:{key}'
        event = self.env[PREFIX + 'event'].search([('task_id', '=', self.id), ('submission_key', '=', event_key)], limit=1)
        if event:
            if event.digest != digest or event.worker_uid.id != self.env.uid:
                raise ValidationError('Submission key conflict')
            return event.receipt
        self._fence(token, epoch)
        if kind == 'complete':
            if self._unresolved_requests():
                raise ValidationError('Unresolved external requests prevent completion')
            try:
                validate_result(self.task_type, body)
            except DomainError as exc:
                raise ValidationError(str(exc)) from exc
            if self.task_type == 'image.generate':
                self._validate_manual_review_decision(body)
                image = body['image']
                ref = str(image.get('artifact_id') or '')
                artifact = self.env[PREFIX + 'artifact'].sudo().browse(int(ref)).exists() if ref.isdigit() else self.env[PREFIX + 'artifact']
                if not artifact or artifact.case_id != self.case_id or artifact.company_id != self.company_id or not artifact.data or artifact.digest != image.get('sha256') or artifact.mime_type != image.get('mime_type'):
                    raise ValidationError('生成图片产物归属、摘要或类型不匹配')
            self._transition('succeeded')
            self._set({'result': body, 'status_detail': None})
        elif kind in ('fail', 'wait'):
            if (not isinstance(body, dict) or set(body) != {'code', 'message'}
                    or any(not isinstance(body[k], str) or not body[k].strip() for k in ('code', 'message'))
                    or len(body['code']) > 128 or len(body['message']) > 4096):
                raise ValidationError('Status requires code (1-128) and message (1-4096)')
            self._transition('failed' if kind == 'fail' else 'waiting')
            self._set({'status_detail': body})
        else:
            if not isinstance(body, dict) or not isinstance(body.get('request_id'), str) or not body['request_id'] or len(body['request_id']) > 200 or body.get('state') not in ('unknown', 'dispatched', 'succeeded', 'failed'):
                raise ValidationError('Checkpoint requires request_id and explicit request state')
            requests = self.env[PREFIX + 'external.request']
            existing = requests.search([('task_id', '=', self.id), ('request_id', '=', body['request_id'])], limit=1)
            if existing:
                if existing.retry_superseded:
                    raise ValidationError('Superseded external request cannot be reused')
                if existing.state in ('succeeded', 'failed') and existing.state != body['state']:
                    raise ValidationError('Terminal request state cannot change')
                existing._set({'state': body['state']})
            else:
                requests._append({'task_id': self.id, 'company_id': self.company_id.id, 'request_id': body['request_id'], 'state': body['state']})
            if self.scoped_research:
                registration_key = 'scoped-request:' + hashlib.sha256(body['request_id'].encode()).hexdigest()
                registrations = self.env[PREFIX + 'event']
                registration = registrations.search([('task_id', '=', self.id), ('submission_key', '=', registration_key)], limit=1)
                if registration:
                    if registration.kind != 'scoped_request_registration' or registration.worker_uid.id != self.env.uid:
                        raise AccessError('Scoped request belongs to another worker')
                else:
                    if (not self.env.user.has_group('meli_optimization.group_research_worker')
                            or self.env.su or self.env.user.has_group('base.group_system')):
                        raise AccessError('Scoped non-administrator worker required')
                    registration_payload = {'request_id': body['request_id'], 'initial_epoch': epoch}
                    registrations._append({'task_id': self.id, 'company_id': self.company_id.id,
                        'submission_key': registration_key, 'digest': bounded(registration_payload),
                        'kind': 'scoped_request_registration', 'payload': registration_payload,
                        'receipt': {'request_id': body['request_id'], 'coverage': 'unknown'}, 'worker_uid': self.env.uid})
        receipt = {'task_id': self.id, 'submission_key': key, 'state': self.state, 'accepted': True}
        self.env[PREFIX + 'event']._append({'task_id': self.id, 'company_id': self.company_id.id, 'submission_key': event_key, 'digest': digest, 'kind': kind, 'payload': body, 'receipt': receipt, 'worker_uid': self.env.uid})
        return receipt

    def checkpoint(self, token, epoch, key, body):
        return self._submit(token, epoch, key, body, 'checkpoint')

    def complete(self, token, epoch, key, body):
        return self._submit(token, epoch, key, body, 'complete')

    def fail(self, token, epoch, key, body):
        return self._submit(token, epoch, key, body, 'fail')

    def wait(self, token, epoch, key, body):
        return self._submit(token, epoch, key, body, 'wait')

    def _unresolved_requests(self):
        return self.env[PREFIX + 'external.request'].search_count([
            ('task_id', '=', self.id), ('state', 'in', ['unknown', 'dispatched']), ('retry_superseded', '=', False)])

    @api.model
    def _historical_retry_sources(self, company_ids):
        """Identify only audited replacement chains whose successor still exists."""
        events = self.env[PREFIX + 'event'].sudo().search([
            ('company_id', 'in', company_ids), ('kind', 'in', ['unknown_task_retry', 'unknown_research_retry'])])
        sources = set()
        for event in events:
            receipt = event.receipt if isinstance(event.receipt, dict) else {}
            successor_id = receipt.get('task_id')
            if type(successor_id) is not int or successor_id == event.task_id.id:
                continue
            successor = self.sudo().browse(successor_id).exists()
            source = event.task_id
            if (successor and successor.company_id == source.company_id and successor.case_id == source.case_id
                    and successor.snapshot_id == source.snapshot_id and successor.task_type == source.task_type
                    and receipt.get('source_task_id') == source.id):
                sources.add(source.id)
        return list(sources)

    def reconcile_interrupted_research(self, request_id, key, evidence):
        """Record a human's explicit verification of a read-only interrupted run."""
        self.ensure_one()
        self._authorize('editor')
        if (not isinstance(request_id, str) or not request_id or len(request_id) > 200
                or not isinstance(key, str) or not key or len(key) > 100
                or not isinstance(evidence, dict) or set(evidence) != {'read_only_browser_confirmed', 'no_upload_confirmed', 'explanation'}
                or evidence['read_only_browser_confirmed'] is not True or evidence['no_upload_confirmed'] is not True
                or not isinstance(evidence['explanation'], str) or not evidence['explanation'].strip()
                or len(evidence['explanation']) > 4000):
            raise ValidationError('Explicit manual verification and explanation required')
        self.case_id._lock()
        self._lock()
        payload = {'request_id': request_id, 'evidence': evidence, 'expected_state': 'unknown'}
        digest = bounded(payload)
        submission_key = 'manual-reconcile:' + key
        prior = self.env[PREFIX + 'event'].search([('task_id', '=', self.id), ('submission_key', '=', submission_key)], limit=1)
        if prior:
            if prior.kind != 'manual_reconciliation' or prior.digest != digest or prior.worker_uid.id != self.env.uid:
                raise ValidationError('Reconciliation key conflict')
            return prior.receipt
        if self.state != 'waiting' or self.task_type != 'research.bestseller' or (self.status_detail or {}).get('code') != 'external_result_unknown':
            raise ValidationError('Only interrupted waiting research requests can be reconciled')
        requests = self.env[PREFIX + 'external.request'].search([('task_id', '=', self.id), ('request_id', '=', request_id)], limit=1)
        if not requests or requests.state != 'unknown':
            raise ValidationError('Expected unknown request not found')
        requests._set({'state': 'failed'})
        receipt = {'task_id': self.id, 'request_id': request_id, 'request_state': 'failed', 'state': 'waiting'}
        self.env[PREFIX + 'event']._append({'task_id': self.id, 'company_id': self.company_id.id,
            'submission_key': submission_key, 'digest': digest, 'kind': 'manual_reconciliation',
            'payload': payload, 'receipt': receipt, 'worker_uid': self.env.uid})
        return receipt

    def _transition(self, target):
        try:
            validate_transition(self.state, target)
        except DomainError as exc:
            raise ValidationError(str(exc)) from exc
        vals = {'state': target}
        if target != 'running':
            vals.update(lease_token=False, lease_expires_at=False, worker_uid=False)
        self._set(vals)

    def retry_expired_unknown_research(self, token, epoch, request_id):
        """Retire one expired scoped lease and create an auditable new attempt."""
        self.ensure_one()
        self._authorize('worker')
        if (self.env.su or self.env.user.has_group('base.group_system') or
                not self.env.user.has_group('meli_optimization.group_research_worker')):
            raise AccessError('Scoped non-administrator research worker required')
        if not isinstance(token, str) or not token or type(epoch) is not int or not isinstance(request_id,str) or not request_id:
            raise ValidationError('Original lease and request required')
        self.case_id._lock()
        self._lock()
        events=self.env[PREFIX+'event'].sudo()
        prior=events.search([('task_id','=',self.id),('kind','=','expired_unknown_research_retry'),('submission_key','=',f'expired-unknown-research-retry:{epoch}')],limit=1)
        if prior:
            if prior.worker_uid.id!=self.env.uid or prior.payload != {'request_id':request_id,'epoch':epoch}:
                raise AccessError('Original request owner required')
            return dict(prior.receipt)
        if (self.task_type!='research.bestseller' or not self.scoped_research or self.state!='running'
                or self.worker_uid.id!=self.env.uid or self.lease_token!=token or self.lease_epoch!=epoch
                or not self.lease_expires_at or self.lease_expires_at>fields.Datetime.now()):
            raise ValidationError('Expired original scoped lease required')
        external=self.env[PREFIX+'external.request'].sudo().search([('task_id','=',self.id),('request_id','=',request_id),('retry_superseded','=',False),('state','=','unknown')],limit=1)
        if not external: raise ValidationError('Unknown original request required')
        if self._has_registered_evidence(external):
            raise ValidationError('请接回已有研究证据的原请求，继续图库核验')
        self._set({'state': 'waiting', 'lease_token': False, 'lease_expires_at': False, 'worker_uid': False,
                   'status_detail': {'code': 'lease_expired', 'message': '原任务租约已过期，待恢复采集。'}})
        receipt=self._retry_unknown_research(automatic=True)
        payload={'request_id':request_id,'epoch':epoch}
        events._append({'task_id':self.id,'company_id':self.company_id.id,'submission_key':f'expired-unknown-research-retry:{epoch}',
            'digest':bounded(payload),'kind':'expired_unknown_research_retry','payload':payload,'receipt':receipt,'worker_uid':self.env.uid})
        return receipt

    def retry_unknown_research(self):
        automatic = not self.env.su and self.env.user.has_group('meli_optimization.group_research_worker')
        return self._retry_unknown_research(automatic=automatic)

    def _retry_unknown_research(self, *, automatic):
        """Retain the original task and cap automatic retries independently per task."""
        self.ensure_one()
        scoped = (not self.env.su and not self.env.user.has_group('base.group_system')
                  and self.env.user.has_group('meli_optimization.group_research_worker'))
        if scoped:
            self._authorize('worker')
        else:
            self._authorize('editor')
        self.case_id._lock()
        self.case_id._collection_case_fence()
        self._lock()
        if not self._collection_start_authorized():
            raise ValidationError('请先点击一键开始采集')
        if self.id in self._historical_retry_sources(self.company_id.ids):
            raise ValidationError('This historical attempt has an audited successor')
        if self.task_type != 'research.bestseller' or self.state not in ('waiting', 'queued') or self.case_id.collection_archived or self.research_archived:
            raise ValidationError('Waiting active research task required')
        events = self.env[PREFIX + 'event'].sudo()
        key = f'unknown-research-retry:{self.lease_epoch}'
        prior = events.search([('task_id', '=', self.id), ('submission_key', '=', key)], limit=1)
        if prior:
            return dict(prior.receipt)
        if not self._unresolved_requests():
            raise ValidationError('No uncertain request to retry')
        if automatic:
            if (self.status_detail or {}).get('code') in ('captcha_required', 'login_required', 'page_unreadable'):
                return {'task_id': self.id, 'source_task_id': self.id, 'state': 'waiting'}
            if self.scoped_research and self._has_registered_evidence(
                self.request_ids.filtered(lambda r: not r.retry_superseded and r.state in ('unknown', 'dispatched'))
            ):
                return {'task_id': self.id, 'source_task_id': self.id, 'state': 'waiting'}
            prior_retries = events.search([
                ('task_id', '=', self.id), ('kind', '=', 'unknown_research_retry'),
            ], order='id desc', limit=2)
            consecutive = []
            for retry in prior_retries:
                if retry.payload.get('trigger') == 'operator':
                    break
                consecutive.append(retry)
            if len(consecutive) >= 2:
                detail = self.status_detail or {}
                if detail.get('code') != 'research_retry_limit':
                    self._set({'status_detail': {
                        'code': 'research_retry_limit',
                        'message': '连续两次自动重试仍未完成，处理原因后可恢复原任务。',
                        'previous_code': detail.get('code'), 'previous_message': detail.get('message'),
                    }})
                return {'task_id': self.id, 'source_task_id': self.id, 'state': 'waiting'}
            if consecutive and fields.Datetime.now() < consecutive[0].create_date + timedelta(minutes=2):
                return {'task_id': self.id, 'source_task_id': self.id, 'state': 'waiting'}
        unresolved = self.request_ids.filtered(lambda r: r.state in ('unknown', 'dispatched') and not r.retry_superseded)
        receipt = {'task_id': self.id, 'source_task_id': self.id, 'state': 'queued'}
        payload = {'request_ids': unresolved.mapped('request_id'), 'epoch': self.lease_epoch,
                   'accepted_duplicate_results': True, 'trigger': 'automatic' if automatic else 'operator'}
        unresolved._set({'retry_superseded': True})
        self._transition('queued')
        self._set({'status_detail': None})
        events._append({'task_id': self.id, 'company_id': self.company_id.id,
            'submission_key': key, 'digest': bounded(payload),
            'kind': 'unknown_research_retry', 'payload': payload,
            'receipt': receipt, 'worker_uid': self.env.uid})
        return receipt

    @api.model
    def retry_unknown_research_batch(self, limit=20):
        self._authorize('worker')
        if (self.env.su or self.env.user.has_group('base.group_system')
                or not self.env.user.has_group('meli_optimization.group_research_worker')):
            raise AccessError('Scoped non-administrator research worker required')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValidationError('Invalid batch limit')
        # Scan only authorized companies. Internal audit records may not be visible
        # under the scoped worker rule, so find already processed IDs internally.
        company_ids = self.env.companies.ids
        handled = self._historical_retry_sources(company_ids)
        events = self.env[PREFIX + 'event'].sudo()
        candidates = self.sudo().search([
            ('company_id', 'in', company_ids), ('task_type', '=', 'research.bestseller'), ('research_archived', '=', False),
            ('state', '=', 'waiting'), ('case_id.collection_archived', '=', False),
            ('id', 'not in', handled),
            ('request_ids', 'any', [('state', 'in', ['unknown', 'dispatched']), ('retry_superseded', '=', False)]),
        ], order='id')
        results, failed = [], []
        for task in candidates:
            if len(results) >= limit:
                break
            if not task._collection_start_authorized():
                continue
            try:
                # Roll back only the failed item; later tasks must not starve.
                with self.env.cr.savepoint():
                    receipt = task._retry_unknown_research(automatic=True)
                if receipt['state'] == 'queued':
                    results.append(receipt)
            except (ValidationError, AccessError) as exc:
                failed.append({'task_id': task.id, 'error': str(exc)[:500]})
        return {'retried': results, 'failed': failed}

    def retry_unknown_task(self):
        """Create one new attempt without changing the uncertain request."""
        self.ensure_one()
        self._authorize('worker' if self.env.su else 'editor')
        self.case_id._lock()
        self.case_id._collection_case_fence()
        self._lock()
        if not self._collection_start_authorized():
            raise ValidationError('请先点击一键开始采集')
        if self.id in self._historical_retry_sources(self.company_id.ids):
            raise ValidationError('This historical attempt has an audited successor')
        if self.task_type not in ('product.facts', 'research.keywords', 'image.generate') or self.state not in ('waiting', 'queued') or self.case_id.collection_archived:
            raise ValidationError('Waiting active task required')
        events = self.env[PREFIX + 'event'].sudo()
        key = f'unknown-task-retry:{self.lease_epoch}'
        prior = events.search([('task_id', '=', self.id), ('submission_key', '=', key)], limit=1)
        if prior:
            return dict(prior.receipt)
        if not self._unresolved_requests():
            raise ValidationError('No uncertain request to retry')
        unresolved = self.request_ids.filtered(lambda r: r.state in ('unknown', 'dispatched') and not r.retry_superseded)
        receipt = {'task_id': self.id, 'source_task_id': self.id, 'state': 'queued'}
        payload = {'request_ids': unresolved.mapped('request_id'), 'epoch': self.lease_epoch,
                   'accepted_duplicate_results': True}
        unresolved._set({'retry_superseded': True})
        self._transition('queued')
        self._set({'status_detail': None})
        events._append({'task_id': self.id, 'company_id': self.company_id.id,
            'submission_key': key, 'digest': bounded(payload), 'kind': 'unknown_task_retry',
            'payload': payload, 'receipt': receipt, 'worker_uid': self.env.uid})
        return receipt

    @api.model
    def retry_unknown_tasks_batch(self, task_types, limit=20):
        self._authorize('worker')
        allowed = {'product.facts', 'research.keywords', 'image.generate'}
        if not isinstance(task_types, list) or not task_types or any(type(t) is not str or t not in allowed for t in task_types):
            raise ValidationError('Invalid task types')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValidationError('Invalid batch limit')
        companies = self.env.companies.ids
        handled = self._historical_retry_sources(companies)
        candidates = self.sudo().search([('company_id', 'in', companies), ('task_type', 'in', task_types),
            ('state', '=', 'waiting'), ('case_id.collection_archived', '=', False), ('id', 'not in', handled),
            ('request_ids', 'any', [('state', 'in', ['unknown', 'dispatched']), ('retry_superseded', '=', False)])], order='id')
        results, failed = [], []
        for task in candidates:
            if len(results) >= limit: break
            if not task._collection_start_authorized():
                continue
            try:
                with self.env.cr.savepoint():
                    receipt = task.retry_unknown_task()
                results.append(receipt)
            except (ValidationError, AccessError) as exc:
                failed.append({'task_id': task.id, 'error': str(exc)[:500]})
        return {'retried': results, 'failed': failed}

    def archive_research_task(self):
        """Hide and fence a research task while retaining evidence and audit."""
        self.ensure_one()
        self._authorize('editor')
        self.case_id._lock()
        self._lock()
        if self.task_type != 'research.bestseller':
            raise ValidationError('Research task required')
        if self.research_archived:
            return {'task_id': self.id, 'archived': True}
        self._set({'research_archived': True, 'state': 'cancelled', 'lease_token': False,
                   'lease_expires_at': False, 'worker_uid': False})
        receipt = {'task_id': self.id, 'archived': True}
        self.env[PREFIX + 'event']._append({'task_id': self.id, 'company_id': self.company_id.id,
            'submission_key': 'archive-research-task', 'digest': bounded(receipt),
            'kind': 'archive_research_task', 'payload': {}, 'receipt': receipt, 'worker_uid': self.env.uid})
        return receipt

    def _control(self, operation):
        self._authorize()
        self.case_id._lock()
        if operation == 'resume':
            self.case_id._collection_case_fence()
        if operation == 'resume' and getattr(self.case_id, 'collection_archived', False):
            raise ValidationError('已归档的独立采集不能恢复任务')
        self._lock()
        if self.research_archived:
            raise ValidationError('Archived research task cannot be resumed')
        if operation == 'resume' and not self._collection_start_authorized():
            raise ValidationError('请先点击一键开始采集')
        if operation == 'resume' and self.state == 'queued':
            kinds = ['unknown_research_retry'] if self.task_type == 'research.bestseller' else ['unknown_task_retry']
            prior = self.env[PREFIX + 'event'].sudo().search([('task_id', '=', self.id), ('kind', 'in', kinds)], order='id desc', limit=1)
            if prior and prior.payload.get('epoch') == self.lease_epoch:
                return dict(prior.receipt)
        if operation == 'resume' and self._unresolved_requests():
            if self.task_type == 'research.bestseller':
                return self.retry_unknown_research()
            if self.task_type in ('product.facts', 'research.keywords', 'image.generate'):
                return self.retry_unknown_task()
            raise ValidationError('Unresolved external requests require reconciliation')
        resume_payload = {}
        if (operation == 'resume' and self.state == 'waiting' and self.scoped_research
                and self.task_type == 'research.bestseller'
                and (self.status_detail or {}).get('code') == 'window_registration_failed'):
            waits = self.env[PREFIX + 'event'].sudo().search([('task_id', '=', self.id), ('kind', '=', 'wait')], order='id desc', limit=1)
            if not waits or waits.payload != self.status_detail:
                raise ValidationError('Window registration wait is not audited')
            registrations = self.env[PREFIX + 'event'].sudo().search([
                ('task_id', '=', self.id), ('kind', '=', 'scoped_request_registration')])
            request_ids = [event.payload.get('request_id') for event in registrations
                           if event.payload.get('initial_epoch') == self.lease_epoch]
            failed = self.env[PREFIX + 'external.request'].sudo().search([
                ('task_id', '=', self.id), ('request_id', 'in', request_ids), ('state', '=', 'failed')])
            if len(request_ids) != 1 or len(failed) != 1:
                raise ValidationError('Original failed research request required')
            resume_payload = {'code': 'window_registration_failed', 'epoch': self.lease_epoch}
        self._transition('queued' if operation == 'resume' else 'cancelled')
        if operation == 'resume':
            self._set({'status_detail': None})
        receipt = {'task_id': self.id, 'state': self.state}
        self.env[PREFIX + 'event']._append({'task_id': self.id, 'company_id': self.company_id.id,
            'submission_key': 'control-' + str(uuid.uuid4()), 'digest': bounded(receipt),
            'kind': operation, 'payload': resume_payload, 'receipt': receipt, 'worker_uid': self.env.uid})
        return receipt

    def resume(self):
        return self._control('resume')

    def cancel(self):
        return self._control('cancel')


class Attempt(models.Model):
    _name = PREFIX + 'attempt'
    _inherit = PREFIX + 'protected'
    _description = 'Task attempt audit'
    _internal_only = True
    task_id = fields.Many2one(PREFIX + 'task', required=True, ondelete='restrict')
    epoch = fields.Integer(required=True)
    worker_uid = fields.Many2one('res.users', required=True)
    _unique_epoch = models.Constraint('UNIQUE(task_id, epoch)', 'Attempt epoch must be unique')


class Event(models.Model):
    _name = PREFIX + 'event'
    _inherit = PREFIX + 'protected'
    _description = 'Idempotent task submission'
    _internal_only = True
    task_id = fields.Many2one(PREFIX + 'task', required=True, ondelete='restrict')
    submission_key = fields.Char(required=True)
    digest = fields.Char(required=True)
    kind = fields.Char(required=True)
    payload = ExactJson(required=True)
    receipt = ExactJson(required=True)
    worker_uid = fields.Many2one('res.users', required=True)
    _unique_key = models.Constraint('UNIQUE(task_id, submission_key)', 'Submission key must be unique')


class ExternalRequest(models.Model):
    _name = PREFIX + 'external.request'
    _inherit = PREFIX + 'protected'
    _description = 'External request reconciliation ledger'
    _internal_only = True
    task_id = fields.Many2one(PREFIX + 'task', required=True, ondelete='restrict')
    request_id = fields.Char(required=True)
    state = fields.Selection([(s, s) for s in ('unknown', 'dispatched', 'succeeded', 'failed')], required=True)
    retry_superseded = fields.Boolean(default=False, readonly=True)
    _unique_request = models.Constraint('UNIQUE(task_id, request_id)', 'Request must be unique')


class Artifact(models.Model):
    _name = PREFIX + 'artifact'
    _inherit = PREFIX + 'protected'
    _description = 'Verified immutable artifact'
    _create_role = 'worker'
    _parent_field = ('case_id', 'case')
    case_id = fields.Many2one(PREFIX + 'case', required=True, ondelete='restrict')
    payload = ExactJson()
    data = fields.Binary(attachment=False)
    mime_type = fields.Char()
    digest = fields.Char(required=True)

    @api.model_create_multi
    def create(self, vals_list):
        if (not self.env.su and self.env.user.has_group('meli_optimization.group_research_worker')
                and self.env.context.get('_research_artifact_guard') is not _RESEARCH_ARTIFACT_GUARD):
            raise AccessError('Scoped research worker must use the fenced research artifact operation')
        vals_list = [self._creation_values(v) for v in vals_list]
        for v in vals_list:
            if bool(v.get('data')) == ('payload' in v):
                raise ValidationError('Provide exactly one JSON payload or binary data')
            if v.get('data'):
                try:
                    raw = base64.b64decode(v['data'], validate=True)
                except (ValueError, TypeError) as exc:
                    raise ValidationError('Invalid base64') from exc
                if len(raw) > MAX_BINARY:
                    raise ValidationError('Binary exceeds 8 MiB')
                digest = hashlib.sha256(raw).hexdigest()
            else:
                digest = bounded(v['payload'])
            if v.get('digest') != digest:
                raise ValidationError('Artifact digest mismatch')
        return super().create(vals_list)


class Proposal(models.Model):
    _name = PREFIX + 'proposal'
    _inherit = PREFIX + 'protected'
    _description = 'Immutable optimization proposal'
    _parent_field = ('case_id', 'case')
    case_id = fields.Many2one(PREFIX + 'case', required=True, ondelete='restrict')
    payload = ExactJson(required=True)
    digest = fields.Char(required=True)
    input_revision = fields.Integer(required=True)

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for original in vals_list:
            v = self._creation_values(original)
            case = self.env[PREFIX + 'case'].browse(v['case_id'])
            case._authorize()
            case._lock()
            prepared.append(dict(v, digest=bounded(v['payload']), input_revision=case.revision))
        return super().create(prepared)

    def confirm(self, digest):
        self._authorize()
        self.ensure_one()
        self.case_id._lock()
        if digest != self.digest or self.input_revision != self.case_id.revision:
            raise ValidationError('Proposal has changed inputs or hash')
        return self.env[PREFIX + 'confirmation']._append({'proposal_id': self.id, 'company_id': self.company_id.id, 'digest': self.digest, 'user_id': self.env.uid})


class Confirmation(models.Model):
    _name = PREFIX + 'confirmation'
    _inherit = PREFIX + 'protected'
    _description = 'User confirmation bound to proposal and inputs'
    _internal_only = True
    proposal_id = fields.Many2one(PREFIX + 'proposal', required=True, ondelete='restrict')
    digest = fields.Char(required=True)
    user_id = fields.Many2one('res.users', required=True)

    def is_valid(self):
        self._authorize()
        self.ensure_one()
        return self.user_id.id == self.env.uid and self.digest == self.proposal_id.digest and self.proposal_id.input_revision == self.proposal_id.case_id.revision

import base64
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from ..domain import canonical_hash, validate_input, validate_result, validate_lease, DomainError

PREFIX = 'meli.optimization.'
MAX_JSON = 256 * 1024
MAX_BINARY = 8 * 1024 * 1024


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

    @api.model_create_multi
    def create(self, vals_list):
        self._authorize(self._create_role)
        if self._internal_only:
            raise AccessError('Use the fenced business operation')
        prepared = []
        for original in vals_list:
            vals = dict(original)
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
        if any('revision' in v for v in vals_list):
            raise AccessError('Revision is server managed')
        return super().create(vals_list)


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
        vals_list = [dict(v, digest=bounded(v.get('payload'))) for v in vals_list]
        for v in vals_list:
            case = self.env[PREFIX + 'case'].browse(v['case_id'])
            case._authorize()
            case._lock()
        records = super().create(vals_list)
        for record in records:
            case = record.case_id
            case._set({'revision': case.revision + 1})
        return records


class Task(models.Model):
    _name = PREFIX + 'task'
    _inherit = PREFIX + 'protected'
    _description = 'Fenced optimization task'
    _parent_field = ('case_id', 'case')
    case_id = fields.Many2one(PREFIX + 'case', required=True, ondelete='restrict')
    snapshot_id = fields.Many2one(PREFIX + 'snapshot', required=True, ondelete='restrict')
    task_type = fields.Char(required=True, index=True)
    payload = ExactJson(required=True)
    state = fields.Selection([(s, s) for s in ('queued', 'running', 'succeeded', 'failed', 'cancelled')], default='queued', required=True, index=True)
    dependency_ids = fields.Many2many(PREFIX + 'task', 'meli_opt_task_dependency', 'task_id', 'dependency_id')
    lease_token = fields.Char(readonly=True)
    lease_epoch = fields.Integer(default=0, readonly=True)
    lease_expires_at = fields.Datetime(readonly=True)
    worker_uid = fields.Many2one('res.users', readonly=True)
    result = ExactJson(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        forbidden = {'state', 'lease_token', 'lease_epoch', 'lease_expires_at', 'worker_uid', 'result'}
        for v in vals_list:
            if forbidden.intersection(v):
                raise AccessError('Task state is server managed')
            bounded(v.get('payload'))
            try:
                validate_input(v['task_type'], v['payload'])
            except DomainError as exc:
                raise ValidationError(str(exc)) from exc
        records = super().create(vals_list)
        for record in records:
            if record.snapshot_id.case_id != record.case_id or any(d.case_id != record.case_id for d in record.dependency_ids):
                raise ValidationError('Snapshot/dependencies must belong to task case')
            if record.id in record.dependency_ids.ids:
                raise ValidationError('Self dependency')
        return records

    def claim(self, capabilities):
        self._authorize('worker')
        if not isinstance(capabilities, list) or not capabilities or len(capabilities) > 32 or any(not isinstance(c, str) for c in capabilities):
            raise ValidationError('Capabilities must be a nonempty string list')
        candidates = self or self.search([('company_id', 'in', self.env.companies.ids), ('task_type', 'in', capabilities), ('state', 'in', ['queued', 'running'])], order='id', limit=100)
        for task in candidates:
            task._authorize('worker')
            self.env.cr.execute('SELECT id FROM meli_optimization_task WHERE id=%s FOR UPDATE SKIP LOCKED', [task.id])
            if not self.env.cr.fetchone():
                continue
            task.invalidate_recordset()
            now = fields.Datetime.now()
            if task.task_type not in capabilities or task.state not in ('queued', 'running') or (task.state == 'running' and task.lease_expires_at > now):
                continue
            if any(d.state != 'succeeded' for d in task.dependency_ids):
                continue
            if self.env[PREFIX + 'external.request'].search_count([('task_id', '=', task.id), ('state', 'in', ['unknown', 'dispatched'])]):
                continue
            task._set({'state': 'running', 'lease_token': str(uuid.uuid4()), 'lease_epoch': task.lease_epoch + 1, 'lease_expires_at': now + timedelta(seconds=120), 'worker_uid': self.env.uid})
            self.env[PREFIX + 'attempt']._append({'task_id': task.id, 'company_id': task.company_id.id, 'epoch': task.lease_epoch, 'worker_uid': self.env.uid})
            return task._envelope()
        return False

    def _envelope(self):
        return {'input_hash': canonical_hash(self.payload), 'schema_version': '1', 'id': self.id, 'task_type': self.task_type, 'payload': self.payload, 'snapshot_id': self.snapshot_id.id, 'company_id': self.company_id.id, 'lease_token': self.lease_token, 'lease_epoch': self.lease_epoch, 'lease_expires_at': fields.Datetime.to_string(self.lease_expires_at), 'heartbeat_seconds': 30}

    def _fence(self, token, epoch):
        self._authorize('worker')
        self._lock()
        try:
            validate_lease({'state': self.state, 'lease_token': self.lease_token, 'lease_epoch': self.lease_epoch}, token, epoch, self.lease_expires_at.replace(tzinfo=timezone.utc) if self.lease_expires_at else None, datetime.now(timezone.utc))
        except DomainError as exc:
            raise ValidationError(str(exc)) from exc
        if self.worker_uid.id != self.env.uid:
            raise AccessError('Lease belongs to another worker')

    def heartbeat(self, token, epoch):
        self._fence(token, epoch)
        self._set({'lease_expires_at': fields.Datetime.now() + timedelta(seconds=120)})
        return self._envelope()

    def _submit(self, token, epoch, key, body, kind):
        self._authorize('worker')
        self._lock()
        if not isinstance(key, str) or not key or len(key) > 128:
            raise ValidationError('Submission key required (max 128)')
        digest = bounded({'body': body, 'token': token, 'epoch': epoch, 'kind': kind})
        event = self.env[PREFIX + 'event'].search([('task_id', '=', self.id), ('submission_key', '=', key)], limit=1)
        if event:
            if event.digest != digest or event.worker_uid.id != self.env.uid:
                raise ValidationError('Submission key conflict')
            return event.receipt
        self._fence(token, epoch)
        if kind == 'complete':
            try:
                validate_result(self.task_type, body)
            except DomainError as exc:
                raise ValidationError(str(exc)) from exc
            self._set({'state': 'succeeded', 'result': body})
        else:
            if not isinstance(body, dict) or not isinstance(body.get('request_id'), str) or not body['request_id'] or len(body['request_id']) > 200 or body.get('state') not in ('unknown', 'dispatched', 'succeeded', 'failed'):
                raise ValidationError('Checkpoint requires request_id and explicit request state')
            requests = self.env[PREFIX + 'external.request']
            existing = requests.search([('task_id', '=', self.id), ('request_id', '=', body['request_id'])], limit=1)
            if existing:
                if existing.state in ('succeeded', 'failed') and existing.state != body['state']:
                    raise ValidationError('Terminal request state cannot change')
                existing._set({'state': body['state']})
            else:
                requests._append({'task_id': self.id, 'company_id': self.company_id.id, 'request_id': body['request_id'], 'state': body['state']})
        receipt = {'task_id': self.id, 'submission_key': key, 'state': self.state, 'accepted': True}
        self.env[PREFIX + 'event']._append({'task_id': self.id, 'company_id': self.company_id.id, 'submission_key': key, 'digest': digest, 'kind': kind, 'payload': body, 'receipt': receipt, 'worker_uid': self.env.uid})
        return receipt

    def checkpoint(self, token, epoch, key, body):
        return self._submit(token, epoch, key, body, 'checkpoint')

    def complete(self, token, epoch, key, body):
        return self._submit(token, epoch, key, body, 'complete')


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
        for v in vals_list:
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

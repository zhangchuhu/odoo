"""Narrow JSON HTTP surface. All mutations delegate to fenced ORM operations."""
import json

from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError, ValidationError
from werkzeug.exceptions import BadRequest, NotFound, RequestEntityTooLarge

from ..models.optimization import MAX_JSON, MAX_BINARY, PREFIX


def body(limit=MAX_JSON):
    if request.httprequest.mimetype != 'application/json':
        raise BadRequest('application/json required')
    request.httprequest.max_content_length = limit
    raw = request.httprequest.get_data(cache=True)
    if len(raw) > limit:
        raise RequestEntityTooLarge()
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadRequest('Invalid JSON') from exc
    if not isinstance(data, dict):
        raise BadRequest('JSON object required')
    return data


def record(model, identifier):
    item = request.env[PREFIX + model].browse(identifier).exists()
    if not item:
        raise NotFound()
    item.check_access('read')
    return item


def reply(value, status=200):
    return request.make_json_response({'success': True, 'data': value}, status=status)


def operation(callback):
    # Savepoint ensures caught business errors do not retain partial writes.
    try:
        with request.env.cr.savepoint():
            return callback()
    except AccessError:
        return request.make_json_response({'success': False, 'error': 'access_denied'}, status=403)
    except ValidationError as exc:
        return request.make_json_response({'success': False, 'error': 'invalid_operation', 'message': str(exc)}, status=409)
    except (KeyError, TypeError, ValueError):
        return request.make_json_response({'success': False, 'error': 'invalid_request'}, status=400)


class OptimizationAPI(http.Controller):
    @http.route('/api/meli/optimization/capabilities', type='http', auth='meli_optimization', methods=['GET'], csrf=False)
    def capabilities(self):
        request.env[PREFIX + 'task']._authorize('worker')
        return reply({'live_execution': False, 'code': 'unsupported_capability', 'reason': 'Live platform writes are unverified and disabled', 'task_types': ['product.facts', 'research.keywords', 'research.bestseller', 'gallery.collect', 'title.generate', 'image.generate', 'image.qc']})

    @http.route('/api/meli/optimization/cases', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def create_case(self):
        def run():
            data = body()
            batch = request.env[PREFIX + 'batch'].create({'name': data['batch_name']})
            case = request.env[PREFIX + 'case'].create({'name': data['name'], 'batch_id': batch.id})
            snap = request.env[PREFIX + 'snapshot'].create({'case_id': case.id, 'payload': data['snapshot']})
            return reply({'batch_id': batch.id, 'case_id': case.id, 'snapshot_id': snap.id, 'snapshot_digest': snap.digest}, 201)
        return operation(run)

    @http.route('/api/meli/optimization/cases/<int:case_id>', type='http', auth='meli_optimization', methods=['GET'], csrf=False)
    def get_case(self, case_id):
        def run():
            case = record('case', case_id)
            case._authorize('worker')
            tasks = request.env[PREFIX + 'task'].search([('case_id', '=', case.id)])
            return reply({'id': case.id, 'name': case.name, 'company_id': case.company_id.id, 'revision': case.revision, 'tasks': [{'id': t.id, 'task_type': t.task_type, 'state': t.state, 'result': t.result} for t in tasks]})
        return operation(run)

    @http.route('/api/meli/optimization/snapshots/<int:snapshot_id>', type='http', auth='meli_optimization', methods=['GET'], csrf=False)
    def get_snapshot(self, snapshot_id):
        def run():
            snapshot = record('snapshot', snapshot_id)
            snapshot._authorize('worker')
            return reply({'snapshot_id': snapshot.id, 'case_id': snapshot.case_id.id, 'company_id': snapshot.company_id.id, 'payload': snapshot.payload, 'digest': snapshot.digest})
        return operation(run)

    @http.route('/api/meli/optimization/cases/<int:case_id>/tasks', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def create_task(self, case_id):
        def run():
            data = body()
            record('case', case_id)._authorize()
            task = request.env[PREFIX + 'task'].create({'case_id': case_id, 'snapshot_id': data['snapshot_id'], 'task_type': data['task_type'], 'payload': data['payload'], 'dependency_ids': [(6, 0, data.get('dependency_ids', []))]})
            return reply({'task_id': task.id, 'state': task.state}, 201)
        return operation(run)

    @http.route('/api/meli/optimization/cases/<int:case_id>/artifacts', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def create_artifact(self, case_id):
        def run():
            data = body(MAX_BINARY * 2)
            record('case', case_id)._authorize('worker')
            vals = {k: data[k] for k in ('digest', 'payload', 'data', 'mime_type') if k in data}
            vals['case_id'] = case_id
            artifact = request.env[PREFIX + 'artifact'].create(vals)
            return reply({'artifact_id': artifact.id, 'digest': artifact.digest}, 201)
        return operation(run)

    @http.route('/api/meli/optimization/artifacts/<int:artifact_id>', type='http', auth='meli_optimization', methods=['GET'], csrf=False)
    def get_artifact(self, artifact_id):
        def run():
            artifact = record('artifact', artifact_id)
            artifact._authorize('worker')
            data = artifact.data
            return reply({'artifact_id': artifact.id, 'digest': artifact.digest, 'payload': artifact.payload, 'data': data.decode('ascii') if isinstance(data, bytes) else data, 'mime_type': artifact.mime_type})
        return operation(run)

    @http.route('/api/meli/optimization/cases/<int:case_id>/proposals', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def create_proposal(self, case_id):
        def run():
            data = body()
            proposal = request.env[PREFIX + 'proposal'].create({'case_id': case_id, 'payload': data['payload']})
            return reply({'proposal_id': proposal.id, 'digest': proposal.digest, 'input_revision': proposal.input_revision}, 201)
        return operation(run)

    @http.route('/api/meli/optimization/proposals/<int:proposal_id>/confirm', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def confirm(self, proposal_id):
        def run():
            confirmation = record('proposal', proposal_id).confirm(body()['digest'])
            return reply({'confirmation_id': confirmation.id, 'valid': confirmation.is_valid()})
        return operation(run)

    @http.route('/internal/meli-optimization/claim', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def claim(self):
        return operation(lambda: reply(request.env[PREFIX + 'task'].claim(body()['capabilities'])))

    @http.route('/internal/meli-optimization/tasks/<int:task_id>/heartbeat', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def heartbeat(self, task_id):
        def run():
            data = body()
            return reply(record('task', task_id).heartbeat(data['lease_token'], data['lease_epoch']))
        return operation(run)

    @http.route('/internal/meli-optimization/tasks/<int:task_id>/checkpoint', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def checkpoint(self, task_id):
        return self._submission(task_id, 'checkpoint')

    @http.route('/internal/meli-optimization/tasks/<int:task_id>/complete', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def complete(self, task_id):
        return self._submission(task_id, 'complete')

    def _submission(self, task_id, kind):
        def run():
            data = body()
            task = record('task', task_id)
            method = task.complete if kind == 'complete' else task.checkpoint
            return reply(method(data['lease_token'], data['lease_epoch'], data['submission_key'], data['body']))
        return operation(run)

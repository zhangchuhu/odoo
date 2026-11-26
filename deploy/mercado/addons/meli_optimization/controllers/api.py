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
    @http.route('/api/meli/optimization/bestseller-import/batch/<int:batch_id>', type='http', auth='meli_optimization', methods=['GET'], csrf=False)
    def bestseller_import_batch_details(self, batch_id):
        return operation(lambda: reply(request.env[PREFIX + 'case'].bestseller_batch_details(batch_id)))
    @http.route('/api/meli/optimization/bestseller-import/step', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def bestseller_import_step(self):
        def run():
            data = body()
            if not {'batch_id','request_key','config'} <= set(data) or set(data) - {'batch_id','request_key','config','retry_failed'}:
                raise ValueError('Invalid import request')
            return reply(request.env[PREFIX + 'case'].import_bestseller_batch(data['batch_id'], data['request_key'], data['config'], data.get('retry_failed', False)))
        return operation(run)
    @http.route('/api/meli/optimization/screening/preview', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def screening_preview(self):
        def run():
            data = body()
            return reply(request.env[PREFIX + 'case'].screening_preview(data['shop_id'], data['request_key'], data['criteria']))
        return operation(run)

    @http.route('/api/meli/optimization/screening/<int:preview_id>/execute', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def screening_execute(self, preview_id):
        return operation(lambda: reply(request.env[PREFIX + 'case'].screening_execute(preview_id, body()['digest'])))

    @http.route('/api/meli/optimization/screening/history', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def screening_history(self):
        return operation(lambda: reply(request.env[PREFIX + 'case'].screening_history()))

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
            return reply({'id': case.id, 'name': case.name, 'company_id': case.company_id.id, 'revision': case.revision, 'tasks': [{'id': t.id, 'task_type': t.task_type, 'state': t.state, 'result': t.result, 'status_detail': t.status_detail} for t in tasks]})
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
            return reply({'artifact_id': artifact.id, 'case_id': artifact.case_id.id, 'company_id': artifact.company_id.id, 'digest': artifact.digest, 'payload': artifact.payload, 'data': data.decode('ascii') if isinstance(data, bytes) else data, 'mime_type': artifact.mime_type})
        return operation(run)

    @http.route('/internal/meli-optimization/tasks/<int:task_id>/research-artifacts', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def research_artifact(self, task_id):
        def run():
            data = body(MAX_BINARY * 2)
            if set(data) != {'lease_token', 'lease_epoch', 'request_id', 'intent_id', 'artifact'}: raise ValueError('Invalid request')
            return reply(record('task', task_id).research_artifact(data['lease_token'], data['lease_epoch'], data['request_id'], data['intent_id'], data['artifact']))
        return operation(run)

    @http.route('/internal/meli-optimization/tasks/<int:task_id>/research-intents/query', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def research_intent_query(self, task_id):
        def run():
            data = body()
            if set(data) != {'lease_token', 'lease_epoch', 'request_id', 'intent_id'}: raise ValueError('Invalid request')
            return reply(record('task', task_id).research_intent_query(data['lease_token'], data['lease_epoch'], data['request_id'], data['intent_id']))
        return operation(run)

    @http.route('/api/meli/optimization/tasks/<int:task_id>/research-intents/reconcile', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def research_intent_reconcile(self, task_id):
        def run():
            data = body()
            if set(data) != {'request_id', 'intent_id'}: raise ValueError('Invalid request')
            return reply(record('task', task_id).research_intent_reconcile(data['request_id'], data['intent_id']))
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

    @http.route('/internal/meli-optimization/research-queue', type='http', auth='meli_optimization', methods=['GET'], csrf=False)
    def research_queue_precheck(self):
        return operation(lambda: reply(request.env[PREFIX+'task'].research_queue_precheck()))

    @http.route('/internal/meli-optimization/research-retry-unknown', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def research_retry_unknown(self):
        return operation(lambda: reply(request.env[PREFIX+'task'].retry_unknown_research_batch(limit=body()['limit'])))

    @http.route('/internal/meli-optimization/research-retry-expired', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def research_retry_expired(self):
        def run():
            data=body()
            if set(data)!={'task_id','lease_token','lease_epoch','request_id'} or type(data['task_id']) is not int: raise ValueError('Original request required')
            task=request.env[PREFIX+'task'].browse(data['task_id']).exists()
            if not task: raise ValueError('Task unavailable')
            return reply(task.retry_expired_unknown_research(data['lease_token'],data['lease_epoch'],data['request_id']))
        return operation(run)

    @http.route('/internal/meli-optimization/retry-unknown-tasks', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def retry_unknown_tasks(self):
        def run():
            data = body()
            if set(data) != {'task_types', 'limit'}: raise ValueError('Invalid request')
            return reply(request.env[PREFIX+'task'].retry_unknown_tasks_batch(data['task_types'], data['limit']))
        return operation(run)

    @http.route('/internal/meli-optimization/claim', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def claim(self):
        def run():
            data = body()
            target = data.get('task_id')
            if target is not None and (type(target) is not int or target < 1):
                raise ValueError('Invalid target task')
            tasks = record('task', target) if target is not None else request.env[PREFIX + 'task']
            return reply(tasks.claim(data['capabilities']))
        return operation(run)

    @http.route('/internal/meli-optimization/tasks/<int:task_id>/claim', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def claim_target(self, task_id):
        return operation(lambda: reply(record('task', task_id).claim(body()['capabilities'])))

    @http.route('/internal/meli-optimization/tasks/<int:task_id>/recover-lease', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def recover_lease(self, task_id):
        def run():
            data = body()
            return reply(record('task', task_id).recover_lease(data['request_id'], data['lease_token'], data['lease_epoch'], data['recovery_intent_id']))
        return operation(run)

    @http.route('/internal/meli-optimization/tasks/<int:task_id>/heartbeat', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def heartbeat(self, task_id):
        def run():
            data = body()
            return reply(record('task', task_id).heartbeat(data['lease_token'], data['lease_epoch']))
        return operation(run)

    @http.route('/internal/meli-optimization/tasks/<int:task_id>/host-progress', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def host_progress(self, task_id):
        def run():
            data = body()
            return reply(record('task', task_id).host_progress(data['lease_token'], data['lease_epoch'], data['request_id'], data['stage'], data.get('intent_id'), data.get('review_seq'), data.get('review_kind'), data.get('candidate_artifact_id'), data.get('candidate_sha256'), data.get('candidate_attempt_id'), data.get('candidate_attempt_number')))
        return operation(run)

    @http.route('/internal/meli-optimization/tasks/<int:task_id>/research-progress', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def research_progress(self, task_id):
        def run():
            data=body()
            return reply(record('task',task_id).research_progress(data['lease_token'],data['lease_epoch'],data['request_id'],data['progress']))
        return operation(run)

    @http.route('/internal/meli-optimization/tasks/<int:task_id>/host-review-evidence', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def host_review_evidence(self, task_id):
        def run():
            data = body()
            return reply(record('task', task_id).host_review_evidence(data['lease_token'], data['lease_epoch'], data['evidence']))
        return operation(run)

    @http.route('/internal/meli-optimization/tasks/<int:task_id>/checkpoint', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def checkpoint(self, task_id):
        return self._submission(task_id, 'checkpoint')

    @http.route('/internal/meli-optimization/tasks/<int:task_id>/complete', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def complete(self, task_id):
        return self._submission(task_id, 'complete')

    @http.route('/internal/meli-optimization/tasks/<int:task_id>/fail', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def fail(self, task_id):
        return self._submission(task_id, 'fail')

    @http.route('/internal/meli-optimization/tasks/<int:task_id>/wait', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def wait(self, task_id):
        return self._submission(task_id, 'wait')

    @http.route('/internal/meli-optimization/tasks/<int:task_id>/skip', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def skip(self, task_id):
        return self._submission(task_id, 'skip')

    @http.route('/api/meli/optimization/tasks/<int:task_id>/resume', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def resume(self, task_id):
        def run():
            body()
            return reply(record('task', task_id).resume())
        return operation(run)

    @http.route('/api/meli/optimization/tasks/<int:task_id>/reconcile-interrupted-research', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def reconcile_interrupted_research(self, task_id):
        def run():
            data = body()
            return reply(record('task', task_id).reconcile_interrupted_research(data['request_id'], data['idempotency_key'], data['evidence']))
        return operation(run)

    @http.route('/api/meli/optimization/tasks/<int:task_id>/cancel', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def cancel(self, task_id):
        def run():
            body()
            return reply(record('task', task_id).cancel())
        return operation(run)

    def _submission(self, task_id, kind):
        def run():
            data = body()
            task = record('task', task_id)
            method = {'complete': task.complete, 'checkpoint': task.checkpoint, 'fail': task.fail, 'wait': task.wait, 'skip': task.skip}[kind]
            return reply(method(data['lease_token'], data['lease_epoch'], data['submission_key'], data['body']))
        return operation(run)

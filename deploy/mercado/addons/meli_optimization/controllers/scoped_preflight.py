"""Narrow opt-in preflight; its trial artifact is always rolled back."""
from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request
from .api import body, operation, record, reply
from ..domain.contracts import canonical_hash


class _RollbackTrial(Exception):
    pass

def _identity(case):
    case._authorize('worker')
    scoped=request.env.user.has_group('meli_optimization.group_research_worker')
    non_admin=not request.env.su and not request.env.user.has_group('base.group_system')
    denied=False
    try:
        with request.env.cr.savepoint():
            value={'probe':'scoped-preflight'}
            request.env['meli.optimization.artifact'].create({'case_id':case.id,'payload':value,'digest':canonical_hash(value)})
            raise _RollbackTrial()
    except AccessError:
        denied=True
    except _RollbackTrial:
        pass
    request.env.invalidate_all(flush=False)
    return {'scoped_worker':bool(scoped),'non_admin':bool(non_admin),'legacy_artifact_denied':denied}


class ScopedPreflightAPI(http.Controller):
    @http.route('/internal/meli-optimization/tasks/<int:task_id>/research-intents/seal', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def seal(self,task_id):
        def run():
            data=body()
            if set(data)!={'request_id','intents','lease_token','lease_epoch'}: raise ValueError('Invalid manifest request')
            return reply(record('task',task_id).seal_scoped_manifest(data['lease_token'],data['lease_epoch'],data['request_id'],data['intents']))
        return operation(run)
    @http.route('/internal/meli-optimization/scoped-identity', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def identity(self):
        def run():
            data=body()
            if set(data)!={'case_id'} or type(data['case_id']) is not int or data['case_id']<=0: raise ValueError('Case required')
            case=record('case',data['case_id'])
            status=_identity(case)
            return reply(dict(status,case_id=case.id,new_tasks_only=getattr(request.env['meli.optimization.task'],'_scoped_fresh_claim_guard',False) is True))
        return operation(run)
    @http.route('/internal/meli-optimization/prior-research-lease', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def prior_research_lease(self):
        def run():
            data=body()
            if set(data)!={'task_id','lease_token','lease_epoch','old_request_id'} or type(data['task_id']) is not int: raise ValueError('Current lease required')
            task=record('task',data['task_id'])
            return reply({'inactive':task.prior_research_lease_inactive(data['lease_token'],data['lease_epoch'],data['old_request_id'])})
        return operation(run)

    @http.route('/internal/meli-optimization/research-search-checkpoint', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def search_checkpoint(self):
        def run():
            data = body()
            if (set(data) != {'task_id', 'request_id', 'lease_epoch', 'lease_token', 'source_request_id', 'source_epoch'}
                    or type(data['task_id']) is not int):
                raise ValueError('Task and both request identities required')
            task = record('task', data['task_id'])
            return reply(task.authorize_search_checkpoint(data['lease_token'], data['lease_epoch'], data['request_id'],
                                                          data['source_request_id'], data['source_epoch']))
        return operation(run)

    @http.route('/internal/meli-optimization/continue-registered-research', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def continue_registered(self):
        def run():
            data = body()
            required = {'task_id','request_id','lease_token','lease_epoch','intent_id','snapshot_id','input_hash'}
            gallery = {'evidence_artifact_id', 'evidence_digest', 'confirmed_images'}
            if set(data) not in (required, required | gallery) or type(data['task_id']) is not int:
                raise ValueError('Task, request and lease identity required')
            return reply(record('task', data['task_id']).continue_registered_research(
                data['lease_token'], data['lease_epoch'], data['request_id'], data['intent_id'],
                data['snapshot_id'], data['input_hash'], **{k:data[k] for k in gallery if k in data}))
        return operation(run)

    @http.route('/internal/meli-optimization/scoped-resume-preflight', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def resume(self):
        def run():
            data=body()
            if set(data)!={'task_id','request_id','lease_token','lease_epoch'} or type(data['task_id']) is not int or data['task_id']<=0: raise ValueError('Original task and request required')
            return reply(record('task',data['task_id']).scoped_resume_preflight(data['lease_token'],data['lease_epoch'],data['request_id']))
        return operation(run)
    @http.route('/api/meli/optimization/tasks/<int:task_id>/prepare-scoped-research', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def prepare(self, task_id):
        def run():
            if body() != {}: raise ValueError('Empty request required')
            record('task',task_id).prepare_scoped_research()
            return reply({'scoped':True,'coverage':'unknown'})
        return operation(run)

    @http.route('/internal/meli-optimization/scoped-preflight', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def preflight(self):
        def run():
            data=body()
            if set(data)!={'task_id'} or type(data['task_id']) is not int or data['task_id']<=0: raise ValueError('Task required')
            task=record('task',data['task_id']); task._authorize('worker')
            task._lock()
            identity=_identity(task.case_id)
            fresh=(task.task_type=='research.bestseller' and task.scoped_research and task.state=='queued' and not task.lease_epoch and not task.request_ids and not request.env['meli.optimization.attempt'].search_count([('task_id','=',task.id)]) and not request.env['meli.optimization.event'].search_count([('task_id','=',task.id)]))
            retry_allowed = (task.task_type == 'research.bestseller' and task.scoped_research
                             and task.state == 'queued' and task.lease_epoch > 0
                             and not task.research_archived and not task.case_id.collection_archived
                             and task._collection_start_authorized() and not task._unresolved_requests()
                             and task._scoped_retry_authorized())
            status = dict(identity, task_id=task.id, fresh_task=bool(fresh))
            if retry_allowed:
                status['retry_allowed'] = True
            return reply(status)
        return operation(run)

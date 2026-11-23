from odoo import http
from odoo.http import request
from .api import body, operation, reply, record
import base64
import hashlib
import secrets
from ..domain.contracts import canonical_hash


class _RollbackArtifactProbe(Exception):
    pass

class CallbackProbeAPI(http.Controller):
    @http.route('/internal/meli-optimization/callback-probe/<string:action>', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def probe(self, action):
        def run():
            data=body(1024)
            probe=request.env['meli.optimization.callback.probe']
            if action=='artifact-rollback' and set(data)=={'case_id'}:
                if type(data['case_id']) is not int or data['case_id'] <= 0:
                    raise ValueError('Invalid case')
                request.env['meli.optimization.case']._authorize('worker')
                case=record('case',data['case_id'])
                case._authorize('worker')
                from ..models.optimization import _RESEARCH_ARTIFACT_GUARD
                model=request.env['meli.optimization.artifact'].with_context(_research_artifact_guard=_RESEARCH_ARTIFACT_GUARD)
                payload={'probe':secrets.token_hex(16)}
                raw=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+X2ioAAAAASUVORK5CYII=')
                json_digest=canonical_hash(payload); image_digest=hashlib.sha256(raw).hexdigest()
                try:
                    with request.env.cr.savepoint():
                        a=model.create({'case_id':case.id,'payload':payload,'digest':json_digest})
                        b=model.create({'case_id':case.id,'data':base64.b64encode(raw),'mime_type':'image/png','digest':image_digest})
                        (a|b).flush_recordset()
                        (a|b).invalidate_recordset()
                        a._authorize('worker'); b._authorize('worker')
                        if (a.payload!=payload or a.digest!=json_digest or b.digest!=image_digest or
                            hashlib.sha256(base64.b64decode(b.data,validate=True)).hexdigest()!=image_digest):
                            raise ValueError('Artifact probe readback mismatch')
                        raise _RollbackArtifactProbe()
                except _RollbackArtifactProbe:
                    request.env.invalidate_all(flush=False)
                    return reply({'artifact_orm_write_read_verified':True,'task_completion_verified':False})
            if action=='create' and set(data)=={'nonce'}:
                return reply(probe.probe_create(data['nonce']))
            if action in ('read','delete') and set(data)=={'probe_id','nonce'}:
                if action=='read': return reply(probe.probe_read(data['probe_id'],data['nonce']))
                return reply({'deleted':probe.probe_delete(data['probe_id'],data['nonce'])})
            raise ValueError('Invalid request')
        return operation(run)

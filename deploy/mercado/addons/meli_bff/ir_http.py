import logging
import re
import time

from werkzeug.exceptions import HTTPException, Unauthorized

from odoo import http, models
from odoo.exceptions import AccessDenied, AccessError, UserError
from odoo.http import request

from .protocol import BffError, MAX_BODY, resolve_route, parse_raw_query
from .service import authorize

_logger = logging.getLogger(__name__)


class BffAccessLogFilter(logging.Filter):
    """Werkzeug otherwise records rejected query secrets in its access line."""
    def filter(self, record):
        message = record.getMessage()
        if '/api/meli' in message:
            record.msg = re.sub(r'(/api/meli[^\s?\"]*)\?[^\s\"]*', r'\1?[redacted]', message)
            record.args = ()
        return True


logging.getLogger('werkzeug').addFilter(BffAccessLogFilter())


def is_bff():
    path = request.httprequest.path
    return path == '/api/meli' or path.startswith('/api/meli/')


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _auth_method_meli_bff(cls):
        request._meli_started = time.monotonic()
        match = re.fullmatch(r'Bearer ([^\s]+)', request.httprequest.headers.get('Authorization', ''), re.I)
        if not match:
            raise Unauthorized('API key required')
        uid = request.env['res.users.apikeys']._check_credentials(scope='rpc', key=match[1])
        if not uid:
            raise Unauthorized('Invalid API key')
        # Session context is not an authority for stateless API company selection.
        request.update_env(user=uid, context={}, su=False)
        if not request.env.user.active or request.env.user._is_public():
            raise Unauthorized('Inactive API user')
        request.session.can_save = False

    @classmethod
    def _pre_dispatch(cls, rule, args):
        if rule.endpoint.routing.get('auth') == 'meli_bff':
            raw_target = request.httprequest.environ.get('RAW_URI') or request.httprequest.environ.get('REQUEST_URI') or request.httprequest.path
            if '%' in raw_target.split('?', 1)[0]:
                raise BffError(404, 'Encoded paths are not supported')
            operation = resolve_route(request.httprequest.method, request.httprequest.path[len('/api/meli'):])
            store = authorize(request.env, operation)
            request._meli_company = store.company_id.id if store is not None else request.env.companies.ids
            request._meli_pairs, request._meli_query = parse_raw_query(operation, request.httprequest.query_string)
            request._meli_operation = operation
            request.httprequest.max_content_length = MAX_BODY
            request._meli_body = request.httprequest.get_data(cache=True)
            if len(request._meli_body) > MAX_BODY:
                raise BffError(413, 'Request body is too large')
        return super()._pre_dispatch(rule, args)

    @classmethod
    def _handle_error(cls, exception):
        if not is_bff():
            return super()._handle_error(exception)
        if isinstance(exception, BffError):
            status, code = exception.status, exception.code
        elif isinstance(exception, (AccessDenied, http.SessionExpiredException)):
            status, code = 401, 'Invalid API key'
        elif isinstance(exception, AccessError):
            status, code = 403, 'Access denied'
        elif isinstance(exception, HTTPException):
            status, code = exception.code or 500, exception.name
        elif isinstance(exception, UserError):
            status, code = 409, 'Shop authorization unavailable; retry or authorize the shop'
        else:
            status, code = 500, 'Internal BFF error'
        response = request.make_json_response({'success': False, 'error': code}, status=status)
        if status == 401:
            response.headers['WWW-Authenticate'] = 'Bearer'
        cls._meli_audit(status)
        return response

    @classmethod
    def _meli_audit(cls, status):
        op = getattr(request, '_meli_operation', None)
        elapsed = time.monotonic() - getattr(request, '_meli_started', time.monotonic())
        _logger.info('meli_bff uid=%s company=%s shop=%s method=%s operation=%s status=%s elapsed=%.3f',
                     request.env.uid, getattr(request, '_meli_company', None),
                     op.store_id if op else None, request.httprequest.method,
                     op.name if op else 'unresolved', status, elapsed)

    @classmethod
    def _post_dispatch(cls, response):
        if is_bff():
            cls._meli_audit(response.status_code)
        return super()._post_dispatch(response)

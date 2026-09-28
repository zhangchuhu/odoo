from odoo import http
from odoo.http import request, Response

from . import service
from .protocol import BffError, MAX_BODY, MAX_PICTURE


class MeliBff(http.Controller):
    @http.route(['/api/meli', '/api/meli/<path:suffix>'], type='http', auth='meli_bff',
                csrf=False, save_session=False, max_content_length=MAX_BODY)
    def dispatch(self, **ignored):
        operation = request._meli_operation
        if operation.name == 'pictures':
            if request.httprequest.mimetype != 'multipart/form-data':
                raise BffError(415, 'Multipart image upload is required')
            if request.httprequest.form:
                raise BffError(400, 'Upload form fields are not supported')
            uploads = list(request.httprequest.files.items(multi=True))
            if len(uploads) != 1 or uploads[0][0] != 'file':
                raise BffError(400, 'Exactly one file is required')
            upload = uploads[0][1]
            upload.stream.seek(0, 2)
            size = upload.stream.tell()
            upload.stream.seek(0)
            if size > MAX_PICTURE:
                raise BffError(413, 'Image exceeds 10 MB')
            if not size:
                raise BffError(400, 'Image is empty')
        result = service.execute(request.env, operation, request._meli_pairs,
                                 request._meli_body, request.httprequest.headers.get('Content-Type'),
                                 request.httprequest.headers.get('Accept'), request._meli_query)
        if isinstance(result, dict):
            return request.make_json_response(result)
        response = Response(result.iter_body(), status=result.status, headers=result.headers)
        response.call_on_close(result.close)
        return response

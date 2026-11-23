import json
from urllib3.filepost import encode_multipart_formdata

from odoo import http
from odoo.http import request, Response

from . import service
from .protocol import BffError, MAX_BODY, MAX_PICTURE, Operation
from odoo.addons.meli_platform_adapter.registry import RegistryError
from odoo.addons.meli_platform_adapter.registry.builtin import REGISTRY


class MeliBff(http.Controller):
    @http.route('/api/meli/operations', type='http', auth='meli_bff', methods=['GET'],
                csrf=False, save_session=False, max_content_length=MAX_BODY)
    def operations(self, **ignored):
        service.authorize(request.env, Operation('shops', 'GET'))
        pairs = list(request.httprequest.args.items(multi=True))
        if any(k not in ('shop_id', 'domain') for k, _ in pairs) or len({k for k, _ in pairs}) != len(pairs):
            raise BffError(400, 'Invalid catalog filters')
        params = dict(pairs)
        shop = params.get('shop_id')
        if shop is not None:
            if not shop.isascii() or not shop.isdigit() or len(shop) > 10 or int(shop) < 1:
                raise BffError(400, 'Invalid shop ID')
            store = service.authorize(request.env, Operation('shop', 'GET', int(shop)))
            request._meli_company = store.company_id.id
        allowed_write = request.env.user.has_group('meli_bff.group_writer')
        entries = [e for e in REGISTRY.catalog() if (allowed_write or e['permission'] == 'read')
                   and (not params.get('domain') or e['domain'] == params['domain'])]
        response = request.make_json_response({'contract_version': REGISTRY.version, 'operations': entries})
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    @http.route('/api/meli/operations/<string:operation_id>/execute', type='http', auth='meli_bff',
                methods=['POST'], csrf=False, save_session=False, max_content_length=MAX_BODY)
    def execute_registered(self, operation_id, **ignored):
        try:
            entry = REGISTRY.definition(operation_id)
        except RegistryError:
            raise BffError(404, 'Unknown operation') from None
        if entry['exposure'] != 'mcp':
            raise BffError(404, 'Unknown operation')
        multipart = entry['request_kind'] == 'multipart'
        if request.httprequest.mimetype != ('multipart/form-data' if multipart else 'application/json'):
            raise BffError(415, 'Unsupported content type')
        try:
            if multipart:
                form = request.httprequest.form
                files = request.httprequest.files
                if set(form.keys()) != {'request'} or len(form.getlist('request')) != 1 or set(files.keys()) != {'file'} or len(files.getlist('file')) != 1:
                    raise BffError(400, 'Exactly one request and one file are required')
                payload = json.loads(form['request'])
            else:
                payload = json.loads(request._meli_body)
        except (ValueError, UnicodeError):
            raise BffError(400, 'Invalid JSON body') from None
        if not isinstance(payload, dict) or set(payload) != {'contract_version', 'shop_id', 'arguments'}:
            raise BffError(400, 'Invalid operation envelope')
        shop_id = payload['shop_id']
        if isinstance(shop_id, bool) or not isinstance(shop_id, int) or not 0 < shop_id < 10**10:
            raise BffError(400, 'Invalid shop ID')
        # Use the legacy, audited shop authorization before validating or executing arguments.
        shop_operation = Operation('shop', 'GET' if entry['permission'] == 'read' else 'POST', shop_id)
        store = service.authorize(request.env, shop_operation)
        request._meli_company = store.company_id.id
        if payload['contract_version'] != REGISTRY.version:
            raise BffError(409, 'Operation contract version changed')
        try:
            REGISTRY.validate(operation_id, payload['arguments'])
        except RegistryError:
            raise BffError(400, 'Invalid operation arguments') from None
        handler = entry['handler_id']
        if handler == 'fulfillment_stock_batch':
            result = service.fulfillment_stock_batch(request.env, store, payload['arguments'],
                                                     accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'order_shipments':
            result = service.order_shipments(request.env, store, payload['arguments']['order_id'],
                                             accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'item_batch_read':
            result = service.item_batch_read(request.env, store, payload['arguments']['item_ids'],
                                             accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'item_mappings':
            result = service.item_mappings(request.env, store, payload['arguments']['item_id'])
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'marketplace_item_read':
            result = service.marketplace_item_read(request.env, store, payload['arguments']['item_id'],
                                                    accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'claim_return_cost':
            args = payload['arguments']
            claim_id = args['claim_id']
            path = entry['upstream']['path'].replace('{claim_id}', claim_id)
            query = [('calculate_amount_usd', str(args['calculate_amount_usd']).lower())] if 'calculate_amount_usd' in args else []
            result = service.read_claim(request.env, store, claim_id, path, query=query,
                                        accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'claim_partial_refund':
            result = service.claim_partial_refund(request.env, store, payload['arguments'],
                                                  accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'claim_resolution_write':
            result = service.claim_resolution(request.env, store, operation_id, payload['arguments'],
                                              accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'claim_message_write':
            result = service.send_claim_message(request.env, store, payload['arguments'],
                                                accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'fulfillment_stock':
            result = service.fulfillment_stock(request.env, store, operation_id, payload['arguments'],
                                                accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'claim_search':
            result = service.search_claims(request.env, store, payload['arguments'])
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'seller_profile':
            result = service.seller_profile(request.env, store, operation_id)
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'shipment_orders':
            result = service.shipment_orders(request.env, store, payload['arguments']['shipment_id'],
                                             accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'picture_upload':
            upload = request.httprequest.files['file']
            suffixes = {'image/jpeg': 'jpg', 'image/png': 'png'}
            suffix = suffixes.get(upload.mimetype)
            if not suffix:
                raise BffError(400, 'Image must be JPEG or PNG')
            data = upload.stream.read(MAX_PICTURE + 1)
            if len(data) > MAX_PICTURE:
                raise BffError(413, 'Image exceeds 10 MB')
            if not data:
                raise BffError(400, 'Image is empty')
            body, content_type = encode_multipart_formdata({'file': ('upload.' + suffix, data, upload.mimetype)})
            operation = Operation('pictures', 'POST', shop_id)
            request._meli_operation = operation
            result = service.execute(request.env, operation, [], body, content_type=content_type,
                                     accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'shipment_tracking':
            result = service.shipment_tracking(request.env, store, operation_id, payload['arguments'],
                                               accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'order_search':
            result = service.search_orders(request.env, store, payload['arguments'])
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'seller_promotions':
            result = service.read_seller_promotions(request.env, store, accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'reference_read':
            result = service.read_reference(request.env, store, entry['upstream']['path'], payload['arguments'],
                                            accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'product_ads_read':
            result = service.read_product_ads(request.env, store, payload['arguments'],
                                              accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'item_resource_read':
            args = payload['arguments']
            path = entry['upstream']['path'].replace('{item_id}', args['item_id'])
            result = service.read_item_resource(request.env, store, args, path,
                                                accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'question_search':
            result = service.search_questions(request.env, store, payload['arguments'])
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'claim_read':
            claim_id = payload['arguments']['claim_id']
            path = entry['upstream']['path'].replace('{claim_id}', claim_id)
            result = service.read_claim(request.env, store, claim_id, path,
                                        accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'pack_messages':
            result = service.pack_messages(request.env, store, operation_id, payload['arguments'],
                                           accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'question_access':
            result = service.question_access(request.env, store, operation_id, payload['arguments'],
                                             accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'pack_read':
            result = service.read_pack(request.env, store, payload['arguments']['pack_id'],
                                       accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'order_read':
            order_id = payload['arguments']['order_id']
            path = entry['upstream']['path'].replace('{order_id}', order_id)
            result = service.read_order(request.env, store, order_id, path,
                                        accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        if handler == 'shipment_read':
            shipment_id = payload['arguments']['shipment_id']
            path = entry['upstream']['path'].replace('{shipment_id}', shipment_id)
            result = service.read_shipment(request.env, store, shipment_id, path,
                                           accept=request.httprequest.headers.get('Accept'))
            response = Response(result.iter_body(), status=result.status, headers=result.headers)
            response.call_on_close(result.close)
            return response
        pairs = []
        if handler == 'legacy_item_get':
            operation = Operation('item', 'GET', shop_id, payload['arguments']['item_id'])
            body = b''
        elif handler == 'legacy_description_get':
            operation = Operation('description', 'GET', shop_id, payload['arguments']['item_id'])
            body = b''
        elif handler == 'legacy_item_update':
            operation = Operation('item', 'PUT', shop_id, payload['arguments']['item_id'])
            body = json.dumps(payload['arguments']['item'], ensure_ascii=False).encode('utf-8')
        elif handler == 'legacy_items_search':
            operation = Operation('items', 'GET', shop_id)
            body = b''
            args = payload['arguments']
            if 'scope' in args:
                pairs.append(('scope', args['scope']))
            for key, value in args.get('query', {}).items():
                for part in value if isinstance(value, list) else [value]:
                    pairs.append((key, str(part).lower() if isinstance(part, bool) else str(part)))
        elif handler == 'legacy_item_create':
            operation = Operation('items', 'POST', shop_id)
            body = json.dumps(payload['arguments']['item'], ensure_ascii=False).encode('utf-8')
        elif handler == 'legacy_description_update':
            operation = Operation('description', 'PUT', shop_id, payload['arguments']['item_id'])
            body = json.dumps({'plain_text': payload['arguments']['plain_text']}, ensure_ascii=False).encode('utf-8')
        else:
            raise BffError(404, 'Unknown operation handler')
        request._meli_operation = operation
        result = service.execute(request.env, operation, pairs, body, content_type='application/json',
                                 accept=request.httprequest.headers.get('Accept'))
        if isinstance(result, dict):
            return request.make_json_response(result)
        response = Response(result.iter_body(), status=result.status, headers=result.headers)
        response.call_on_close(result.close)
        return response

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

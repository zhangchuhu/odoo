from odoo import http
from odoo.http import request
from .api import body, operation, reply


class HostHealthAPI(http.Controller):
    @http.route('/internal/meli-optimization/host/heartbeat', type='http', auth='meli_optimization', methods=['POST'], csrf=False)
    def heartbeat(self):
        def run():
            data = body()
            return reply(request.env['meli.optimization.case'].host_heartbeat(data['configuration'], data['authentication'], data['last_result']))
        return operation(run)

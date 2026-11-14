from datetime import timedelta
from odoo import fields
from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install', 'optimization_http')
class OptimizationHTTPCase(HttpCase):
    def test_stateless_auth_routes_and_bounded_ingress(self):
        user = self.env['res.users'].create({'name': 'API Worker', 'login': 'optimization-http-worker', 'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        key = self.env['res.users.apikeys'].with_user(user).sudo()._generate('rpc', 'test only', fields.Datetime.now() + timedelta(hours=1))
        url = '/api/meli/optimization/capabilities'
        self.assertEqual(self.url_open(url).status_code, 401)
        headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
        response = self.url_open(url, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['data']['live_execution'])
        response = self.url_open('/internal/meli-optimization/claim', json={'capabilities': ['product.facts']}, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['data'])
        response = self.url_open('/internal/meli-optimization/claim', data='[1,2]', headers=headers)
        self.assertEqual(response.status_code, 400)
        response = self.url_open('/internal/meli-optimization/claim', data='{"x":"' + 'x' * 270000 + '"}', headers=headers)
        self.assertEqual(response.status_code, 413)
        response = self.url_open('/api/meli/optimization/cases', json={'batch_name': 'Denied', 'name': 'Denied', 'snapshot': {'item': 'A'}}, headers=headers)
        self.assertEqual(response.status_code, 403)

    def test_worker_reads_snapshot_payload(self):
        batch = self.env['meli.optimization.batch'].create({'name': 'HTTP snapshot'})
        case = self.env['meli.optimization.case'].create({'name': 'HTTP snapshot', 'batch_id': batch.id})
        snapshot = self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {'item_id': 'MLM-A'}})
        user = self.env['res.users'].create({'name': 'Snapshot Worker', 'login': 'snapshot-http-worker', 'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        key = self.env['res.users.apikeys'].with_user(user).sudo()._generate('rpc', 'test only', fields.Datetime.now() + timedelta(hours=1))
        response = self.url_open('/api/meli/optimization/snapshots/' + str(snapshot.id), headers={'Authorization': 'Bearer ' + key})
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertEqual(response.json()['data']['payload'], {'item_id': 'MLM-A'})
        self.assertEqual(response.json()['data']['digest'], snapshot.digest)

from odoo.tests import TransactionCase, tagged
from odoo.exceptions import ValidationError
from odoo import fields
from datetime import timedelta


@tagged('post_install', '-at_install')
class HostHealthCase(TransactionCase):
    def test_heartbeat_is_scoped_and_expires(self):
        case = self.env['meli.optimization.case']
        self.assertFalse(case.workbench_host_status()['online'])
        case.host_heartbeat('configured', 'unknown', 'idle')
        status = case.workbench_host_status()
        self.assertTrue(status['online'])
        self.assertEqual(status['configuration'], 'configured')
        self.assertEqual(status['authentication'], 'unknown')
        self.assertNotIn('capabilities', status)
        with self.assertRaises(ValidationError):
            case.host_heartbeat('unverified', 'unknown', 'idle')
        other = self.env['res.company'].create({'name': 'Host company isolation'})
        self.assertFalse(case.with_company(other).workbench_host_status()['online'])
        self.env.user.company_ids |= other
        multi = case.with_context(allowed_company_ids=[self.env.company.id, other.id])
        multi.host_heartbeat('missing_executable', 'unknown', 'unavailable')
        self.assertTrue(multi.with_company(other).workbench_host_status()['online'])
        self.assertEqual(multi.with_company(other).workbench_host_status()['configuration'], 'missing_executable')
        self.env['meli.optimization.host.health'].search([]).write({'last_seen_at': fields.Datetime.now() - timedelta(seconds=121)})
        self.assertFalse(case.workbench_host_status()['online'])

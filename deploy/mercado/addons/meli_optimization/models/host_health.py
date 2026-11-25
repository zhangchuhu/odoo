from datetime import timedelta
from odoo import api, fields, models
from odoo.exceptions import ValidationError

CONFIGURATION = {'configured', 'missing_config', 'missing_executable'}
RESULTS = {'idle', 'submitted', 'failed', 'unknown', 'unavailable', 'result_ready', 'capability_missing',
           'api_unavailable', 'stale_lease', 'authentication_error'}


class HostHealth(models.Model):
    _name = 'meli.optimization.host.health'
    _description = 'Optimization text host health'
    company_id = fields.Many2one('res.company', required=True, index=True)
    user_id = fields.Many2one('res.users', required=True)
    last_seen_at = fields.Datetime(required=True)
    configuration = fields.Char(required=True)
    authentication = fields.Char(required=True)
    last_result = fields.Char(required=True)
    _one_per_worker = models.Constraint('UNIQUE(company_id,user_id)', 'Only one health record per worker')


class HostHealthCase(models.Model):
    _inherit = 'meli.optimization.case'

    @api.model
    def workbench_detail(self, case_id):
        result = super().workbench_detail(case_id)
        case = self.browse(int(case_id))
        result['host_status'] = self.with_company(case.company_id).workbench_host_status()
        return result

    @api.model
    def host_heartbeat(self, configuration, authentication, last_result):
        self._authorize('worker')
        if configuration not in CONFIGURATION or authentication != 'unknown' or last_result not in RESULTS:
            raise ValidationError('Unsupported host status')
        uid = self.env.uid
        health = self.env['meli.optimization.host.health'].sudo()
        for company in sorted(self.env.companies.ids):
            self.env.cr.execute('SELECT pg_advisory_xact_lock(%s,%s)', (company, uid))
            entry = health.search([('company_id', '=', company), ('user_id', '=', uid)], limit=1)
            values = {'last_seen_at': fields.Datetime.now(), 'configuration': configuration, 'authentication': authentication, 'last_result': last_result}
            if entry:
                entry.write(values)
            else:
                health.create(dict(values, company_id=company, user_id=uid))
        return True

    @api.model
    def workbench_host_status(self):
        self._authorize('worker')
        entries = self.env['meli.optimization.host.health'].sudo().search([('company_id', '=', self.env.company.id)], order='last_seen_at desc')
        now = fields.Datetime.now()
        active = entries.filtered(lambda e: e.last_seen_at and e.last_seen_at >= now - timedelta(seconds=120))
        latest = entries[:1]
        return {'online': bool(active), 'last_seen_at': fields.Datetime.to_string(latest.last_seen_at) if latest else False,
                'configuration': latest.configuration if latest else 'unknown', 'authentication': latest.authentication if latest else 'unknown',
                'last_result': latest.last_result if latest else 'unknown'}

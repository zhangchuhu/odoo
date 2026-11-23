"""Company/user-scoped short-lived transport probes, separate from audit data."""
import hmac
import re
import uuid
from datetime import timedelta
from odoo import api, fields, models
from odoo.exceptions import ValidationError
from .callback_probe_contract import nonce_digest

class CallbackProbe(models.Model):
    _name = 'meli.optimization.callback.probe'
    _description = 'Ephemeral callback transport probe'
    probe_id = fields.Char(required=True, index=True)
    company_id = fields.Many2one('res.company', required=True, index=True)
    user_id = fields.Many2one('res.users', required=True, index=True)
    nonce_hash = fields.Char(required=True)
    expires_at = fields.Datetime(required=True, index=True)
    _unique_probe = models.Constraint('UNIQUE(probe_id)', 'Probe identifier must be unique')

    @api.model
    def _authorize_probe(self):
        self.env['meli.optimization.case']._authorize('worker')

    @api.model
    def _digest(self, nonce):
        try: return nonce_digest(nonce)
        except ValueError: raise ValidationError('Invalid probe request') from None

    @api.model
    def probe_create(self, nonce):
        self._authorize_probe()
        digest=self._digest(nonce)
        uid=self.env.uid;company=self.env.company.id;now=fields.Datetime.now()
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s,%s)',(company,uid))
        records=self.sudo();domain=[('company_id','=',company),('user_id','=',uid)]
        records.search(domain+[('expires_at','<=',now)]).unlink()
        if records.search_count(domain)>=32: raise ValidationError('Probe limit reached')
        entry=records.create({'probe_id':uuid.uuid4().hex,'company_id':company,'user_id':uid,
                              'nonce_hash':digest,'expires_at':now+timedelta(seconds=120)})
        return {'probe_id':entry.probe_id,'digest':digest}

    @api.model
    def _owned(self, identifier, nonce):
        self._authorize_probe();digest=self._digest(nonce)
        if not isinstance(identifier,str) or not re.fullmatch(r'[0-9a-f]{32}',identifier):
            raise ValidationError('Probe unavailable')
        entry=self.sudo().search([('probe_id','=',identifier),('company_id','=',self.env.company.id),
            ('user_id','=',self.env.uid),('expires_at','>',fields.Datetime.now())],limit=1)
        if not entry or not hmac.compare_digest(entry.nonce_hash,digest): raise ValidationError('Probe unavailable')
        return entry

    @api.model
    def probe_read(self, identifier, nonce):
        entry=self._owned(identifier,nonce)
        return {'probe_id':entry.probe_id,'digest':entry.nonce_hash}

    @api.model
    def probe_delete(self, identifier, nonce):
        self._owned(identifier,nonce).unlink()
        return True

    @api.autovacuum
    def _vacuum_expired_probes(self):
        self.sudo().search([('expires_at','<=',fields.Datetime.now())]).unlink()

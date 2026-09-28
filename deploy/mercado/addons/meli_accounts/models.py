from odoo import fields, models
from odoo.exceptions import ValidationError

MANAGER = 'meli_oerp.group_mercadolibre_manager'

class MeliStore(models.Model):
    _name = 'meli.independent.store'
    _description = 'Independent Mercado Libre Store'

    name = fields.Char(required=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    site = fields.Selection([('MLM', 'Mexico (MLM)')], default='MLM', required=True)
    account_type = fields.Selection([('global', 'Global Selling')], default='global', required=True)
    logistic_type = fields.Selection([('remote', '跨境直发'), ('fulfillment', 'Full 仓')], string='物流方式')
    marketplace_seller_id = fields.Char(string='站点 Seller ID', readonly=True, copy=False)
    business_model = fields.Char(string='业务模式', readonly=True)
    pricing_model = fields.Char(string='定价模式', readonly=True)
    marketplace_user_product = fields.Boolean(string='站点 User Products', readonly=True)
    seller_id = fields.Char(readonly=True, copy=False)
    nickname = fields.Char(readonly=True, copy=False)
    authorized_at = fields.Datetime(readonly=True, copy=False)
    expires_at = fields.Datetime(readonly=True, copy=False)
    access_token = fields.Char(groups='base.group_system', copy=False)
    refresh_token = fields.Char(groups='base.group_system', copy=False)
    state = fields.Selection([('pending', 'Awaiting authorization'), ('connected', 'Connected')], default='pending', readonly=True, copy=False)

    def action_authorize(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': '/meli/stores/%s/authorize' % self.id, 'target': 'self'}

    def _check_seller(self, seller_id):
        self.ensure_one()
        if not seller_id or not str(seller_id).isdigit():
            raise ValidationError('Invalid seller identity.')
        if self.seller_id and self.seller_id != str(seller_id):
            raise ValidationError('This store is already linked to another seller.')
        if self.env['res.company'].sudo().search_count([('mercadolibre_seller_id', '=', str(seller_id))]):
            raise ValidationError('This is the original store. Sign in with the second store account.')
        if self.sudo().search_count([('seller_id', '=', str(seller_id)), ('id', '!=', self.id)]):
            raise ValidationError('This seller is already linked to another store.')

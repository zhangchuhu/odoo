from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    @classmethod
    def _auth_method_meli_optimization(cls):
        # Reuse stateless key verification without triggering legacy route resolution.
        return cls._auth_method_meli_bff()

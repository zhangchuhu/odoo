"""Store persistence boundary for the platform adapter's OAuth lifecycle."""
from contextlib import contextmanager
from odoo import api, SUPERUSER_ID
from odoo.exceptions import AccessError, UserError
from odoo.addons.meli_platform_adapter import oauth


class StoreCredentialStorage:
    @staticmethod
    @contextmanager
    def refresh_context(store):
        with store.env.registry.cursor() as cr:
            cr.execute("SET LOCAL lock_timeout = '2s'")
            env = api.Environment(cr, store.env.uid, dict(store.env.context), su=store.env.su)
            if not env.user.active and not (env.su and env.uid == SUPERUSER_ID):
                raise AccessError('Inactive user')
            current = env['meli.independent.store'].browse(store.id).exists()
            if not current:
                raise UserError('店铺尚未保存，请保存后重试。')
            current.check_access('read')
            if not env.su and current.company_id not in env.companies:
                raise AccessError('Shop is outside the allowed companies')
            cr.execute('SELECT id FROM meli_independent_store WHERE id=%s FOR UPDATE', [store.id])
            current = current.sudo()
            current.invalidate_recordset()
            yield current
            cr.commit()


def credential_token(store):
    return oauth.credential_token(store, StoreCredentialStorage)

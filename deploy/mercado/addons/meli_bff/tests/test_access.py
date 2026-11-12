from odoo.tests import TransactionCase, tagged
from odoo.exceptions import AccessError


@tagged('post_install', '-at_install')
class TestAccess(TransactionCase):
    def test_bff_writer_cannot_read_secrets_or_edit_store_credentials(self):
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'BFF API only', 'login': 'bff-api-only',
            'group_ids': [(6, 0, [self.env.ref('meli_bff.group_writer').id])],
        })
        store = self.env['meli.independent.store'].create({'name': 'Restricted', 'access_token': 'hidden'})
        visible = store.with_user(user)
        self.assertEqual(visible.name, 'Restricted')
        with self.assertRaises(AccessError):
            visible.read(['access_token'])
        with self.assertRaises(AccessError):
            visible.write({'seller_id': '999'})

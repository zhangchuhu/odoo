from pathlib import Path
import secrets
credentials = Path('/home/ubuntu/odoo/deploy/mercado/private/admin.txt')
admin = env.ref('base.user_admin')
if not credentials.exists():
    password = secrets.token_urlsafe(24)
    credentials.write_text('Login: admin\nPassword: ' + password + '\n')
    credentials.chmod(0o600)
    admin.write({'login': 'admin', 'password': password})
for xmlid in ['meli_oerp.group_mercadolibre_manager', 'meli_oerp.group_mercadolibre_reader', 'product.group_product_variant']:
    admin.write({'group_ids': [(4, env.ref(xmlid).id)]})
company = env.company
flags = {name: False for name, field in company._fields.items() if name.startswith('mercadolibre_cron_') and field.type == 'boolean'}
flags.update({'mercadolibre_process_notifications': False, 'mercadolibre_order_confirmation': 'manual', 'mercadolibre_order_confirmation_full': 'manual'})
company.write(flags)
cron_ids = env['ir.model.data'].search([('module', '=', 'meli_oerp'), ('model', '=', 'ir.cron')]).mapped('res_id')
env['ir.cron'].browse(cron_ids).exists().write({'active': False})
env['ir.config_parameter'].sudo().set_param('web.base.url', 'http://localhost:8069')
env.cr.commit()
print('Administrator and connector baseline configured; store authorization pending.')

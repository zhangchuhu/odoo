env['ir.config_parameter'].sudo().set_param('web.base.url', 'https://erp.yingshi.dev')
env['ir.config_parameter'].sudo().set_param('web.base.url.freeze', True)
env.company.write({'mercadolibre_redirect_uri': 'https://erp.yingshi.dev/meli_login'})
env.cr.commit()

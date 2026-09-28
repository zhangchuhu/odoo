import requests
from odoo import models
from odoo.exceptions import UserError
from .credentials import credential_token

API = 'https://api.mercadolibre.com'

class StoreAPI(models.Model):
    _inherit = 'meli.independent.store'

    def _token(self):
        return credential_token(self)

    def _get(self, path, params=None):
        try:
            r = requests.get(API + path, headers={'Authorization':'Bearer '+self._token()}, params=params, timeout=30)
            if not r.ok:
                raise UserError('美客多读取失败（HTTP %s），请稍后重试。' % r.status_code)
            return r.json()
        except (requests.RequestException, ValueError):
            raise UserError('美客多连接失败，请稍后重试。')

    def _publishing_target(self):
        self.ensure_one()
        if self.site != 'MLM' or self.logistic_type != 'fulfillment':
            raise UserError('当前发布流程支持墨西哥 Full 仓，请检查目标店铺。')
        d = self._get('/marketplace/users/'+self.seller_id)
        matches = [x for x in d.get('marketplaces',[]) if x.get('site_id')==self.site and x.get('logistic_type')=='fulfillment']
        if len(matches)!=1 or str(matches[0]['user_id']) != self.marketplace_seller_id:
            raise UserError('目标 Full 仓账号发生变化，请核对店铺配置。')
        market = matches[0]
        if market.get('pricing_model') != 'listing_price' or market.get('business_model') != 'CBT Local Fulfillment':
            raise UserError('目标店铺定价或业务模式不匹配，暂不能发布。')
        me = self._get('/users/me')
        if str(me.get('id')) != self.seller_id or me.get('site_id') != 'CBT':
            raise UserError('授权账号与目标店铺不匹配。')
        return {'seller':self.seller_id, 'site_seller':self.marketplace_seller_id,
                'site':self.site, 'logistics':self.logistic_type,
                'user_products':'user_product_seller' in me.get('tags',[])}

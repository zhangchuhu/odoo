"""Archive the platform gallery bound to the current immutable snapshot."""
import base64
import hashlib
import requests
from odoo import api, models
from odoo.exceptions import ValidationError
from ..domain.snapshot_image_fetch import fetch, picture_urls, platform_pictures
from ..domain.image_publish import resolve_target
from ..domain.variant_groups import traditional_groups, bind_site_variations
from .library import verified_image
from odoo.addons.meli_platform_adapter import transport
import re

class SnapshotGallery(models.Model):
    _inherit = 'meli.optimization.case'

    def _gallery_shop(self, case, payload):
        if case.shop_id:
            return self._screen_shop(case.shop_id.id)
        name, site = payload.get('shop'), payload.get('site_id')
        if not isinstance(name, str) or not name.strip() or not isinstance(site, str) or not site:
            raise ValidationError('历史快照缺少店铺或站点信息，无法确定授权店铺')
        shops = self.env['meli.independent.store'].search([
            ('company_id', '=', case.company_id.id), ('name', '=', name), ('site', '=', site)], limit=2)
        if len(shops) != 1:
            raise ValidationError('历史快照的授权店铺无法唯一确定，请检查店铺和站点')
        return self._screen_shop(shops.id)

    @api.model
    def workbench_archive_snapshot_gallery(self, case_id, expected_revision):
        case = self._wb_case(case_id, True)
        if type(expected_revision) is not int or expected_revision != case.revision:
            raise ValidationError('商品快照已改变，请刷新后重试')
        snapshot = self._wb_snapshot(case)
        payload = dict(snapshot.payload or {})
        if payload.get('source_image_artifact_ids') and not case.variant_identity:
            raise ValidationError('当前快照已归档原图')
        shop = self._gallery_shop(case, payload)
        item_id = payload.get('id') or payload.get('item_id')
        if not isinstance(item_id, str) or not re.fullmatch(r'MLM[0-9]+', item_id) or shop.company_id != case.company_id:
            raise ValidationError('无法验证店铺商品身份')
        token = shop._token()
        def get(path):
            response = transport.send('GET', path, token=token, query=(), timeout=30)
            try:
                if not 200 <= response.status < 300:
                    raise ValidationError('授权平台商品读取失败')
                return response.json()
            finally:
                response.close()
        identity = get('/users/me')
        mapping = get('/marketplace/users/' + shop.seller_id)
        markets = mapping.get('marketplaces')
        if (str(identity.get('id')) != shop.seller_id or str(mapping.get('user_id')) != shop.seller_id or
            not isinstance(markets, list) or len([m for m in markets if isinstance(m, dict) and
                str(m.get('user_id')) == shop.marketplace_seller_id and m.get('site_id') == shop.site and
                (not shop.logistic_type or m.get('logistic_type') == shop.logistic_type)]) != 1):
            raise ValidationError('授权店铺站点或 Seller 映射不匹配')
        try:
            item=get('/marketplace/items/' + item_id)
            scope=payload.get('variant_scope')
            if case.variant_identity:
                if not isinstance(scope,dict) or scope.get('model')!='traditional' or scope.get('variant_ids') != case.variant_members:
                    raise ValueError('变体身份缺失')
                parent_id=item.get('cbt_item_id')
                if parent_id!=scope.get('parent_id') or not isinstance(parent_id,str) or not re.fullmatch(r'CBT[0-9]+',parent_id):
                    raise ValueError('商品关联已变化')
                parent=get('/marketplace/items/'+parent_id)
                resolved=resolve_target(item,parent,item_id,shop.site,shop.seller_id,shop.marketplace_seller_id,shop.logistic_type)
                if resolved['model']!='traditional':raise ValueError('商品模型已变化')
                groups=traditional_groups(parent)
                if item.get('variations') or parent.get('variations'):
                    groups=bind_site_variations(groups,item.get('variations'))
                match=[g for g in groups if g['key']==scope.get('key') and g['variant_ids']==scope['variant_ids']]
                if len(match)!=1 or not match[0]['selectable'] or match[0].get('attribute_signature')!=scope.get('attribute_signature') or match[0].get('site_variant_ids',[])!=scope.get('site_variant_ids',[]):
                    raise ValueError('变体或图片已变化，请重新核对')
                if not payload.get('source_image_artifact_ids') and match[0]['picture_ids']!=scope.get('picture_ids'):
                    raise ValueError('变体图片已变化，请重新读取商品')
                scope=dict(scope,picture_ids=match[0]['picture_ids'])
                payload['variant_scope']=scope
                payload['pictures']=[{'id':pid,'secure_url':url} for pid,url in zip(match[0]['picture_ids'],match[0]['picture_urls'])]
            else:
                payload['pictures'] = platform_pictures(item, item_id, shop.site, shop.marketplace_seller_id)
        except ValueError as exc:
            raise ValidationError('平台商品身份或图库不匹配：%s' % exc) from exc
        try:
            urls = picture_urls(payload.get('pictures'))
            pictures = []
            for url in urls:
                raw, mime = fetch(url, requests.get)
                verified_image(raw, mime)
                pictures.append((raw, mime))
        except (ValueError, requests.RequestException) as exc:
            raise ValidationError('平台商品图库缺失或下载失败，请同步平台完整图库后重试') from exc
        # Remote reads happen before the lock. Fence against a concurrent change.
        case._lock()
        case.invalidate_recordset(['revision'])
        if case.revision != expected_revision or self._wb_snapshot(case).id != snapshot.id:
            raise ValidationError('商品快照已改变，请刷新后重试')
        artifacts = self.env['meli.optimization.artifact']
        old_ids=(snapshot.payload or {}).get('source_image_artifact_ids') or []
        if case.variant_identity and old_ids and scope.get('picture_ids')==(snapshot.payload.get('variant_scope') or {}).get('picture_ids'):
            old=artifacts.browse([int(x) for x in old_ids]).exists()
            if len(old)==len(pictures) and old.mapped('digest')==[hashlib.sha256(raw).hexdigest() for raw,mime in pictures]:
                return self.workbench_detail(case.id)
        refs = []
        for raw, mime in pictures:
            ref = artifacts.create({'case_id': case.id, 'data': base64.b64encode(raw),
                                    'mime_type': mime, 'digest': hashlib.sha256(raw).hexdigest()})
            refs.append(str(ref.id))
        payload['source_image_artifact_ids'] = refs
        snapshots=self.env['meli.optimization.snapshot']
        if case.variant_identity:
            from .variant_workbench import _VARIANT_SNAPSHOT_GUARD
            snapshots=snapshots.with_context(_variant_snapshot_guard=_VARIANT_SNAPSHOT_GUARD)
        snapshots.create({'case_id': case.id, 'payload': payload})
        return self.workbench_detail(case.id)

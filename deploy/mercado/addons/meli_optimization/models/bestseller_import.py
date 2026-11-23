"""Idempotent per-product import from a completed authorized screening batch."""
import re
import base64
import requests
from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError, UserError
from odoo.addons.meli_platform_adapter import transport, api as platform_api
from .optimization import PREFIX, ExactJson, bounded
from .library import verified_image
from ..domain.snapshot_image_fetch import fetch, picture_urls, platform_pictures


class BestsellerImport(models.Model):
    _name = PREFIX + 'bestseller.import'
    _inherit = PREFIX + 'protected'
    _description = 'Per-product bestseller import ledger'
    _internal_only = True
    batch_id = fields.Many2one(PREFIX + 'batch', required=True, ondelete='restrict')
    request_key = fields.Char(required=True)
    manifest = fields.Char(required=True)
    source_ids = ExactJson(required=True)
    results = ExactJson(required=True)
    _unique_key = models.Constraint('UNIQUE(company_id, request_key)', 'Import request already exists')


class BestsellerImportCase(models.Model):
    _inherit = PREFIX + 'case'
    collection_import_source_id = fields.Many2one(PREFIX + 'case', readonly=True, ondelete='restrict', index=True)
    collection_import_key = fields.Char(readonly=True, index=True)
    collection_import_snapshot_id = fields.Many2one(PREFIX + 'snapshot', readonly=True, ondelete='restrict')
    _unique_collection_source = models.Constraint('UNIQUE(company_id, collection_import_key, collection_import_source_id)', 'Source already imported for this request')

    def _bestseller_first_image(self, source, shop, item):
        token = shop._token()
        def get(path):
            response=transport.send('GET',path,token=token,query=(),timeout=20)
            try:
                if not 200 <= response.status < 300: raise ValidationError('Platform product unavailable')
                return response.json()
            finally: response.close()
        identity=get('/users/me'); mapping=get('/marketplace/users/'+shop.seller_id)
        markets=mapping.get('marketplaces')
        if (str(identity.get('id'))!=shop.seller_id or str(mapping.get('user_id'))!=shop.seller_id or not isinstance(markets,list) or len([m for m in markets if isinstance(m,dict) and str(m.get('user_id'))==shop.marketplace_seller_id and m.get('site_id')==shop.site and (not shop.logistic_type or m.get('logistic_type')==shop.logistic_type)])!=1):
            raise ValidationError('Shop identity or marketplace mapping mismatch')
        try:
            pictures=platform_pictures(get('/marketplace/items/'+item),item,shop.site,shop.marketplace_seller_id)
            urls=picture_urls(pictures)
        except ValueError as exc:
            raise ValidationError('Platform product identity or gallery metadata invalid') from exc
        if not urls: raise ValidationError('Source image unavailable')
        proxy=platform_api._configured_proxy()
        def image_get(url, **kwargs):
            if proxy: kwargs['proxies']=proxy
            return requests.get(url, **kwargs)
        try:
            raw,mime=fetch(urls[0],image_get)
        except (ValueError, requests.RequestException) as exc:
            raise ValidationError('Platform source image download failed') from exc
        verified_image(raw,mime)
        return base64.b64encode(raw).decode(),mime

    @api.model_create_multi
    def create(self, vals_list):
        protected = {'collection_import_source_id','collection_import_key','collection_import_snapshot_id'}
        if any(protected.intersection(v) for v in vals_list) or any('default_'+k in self.env.context for k in protected):
            raise AccessError('Import provenance is server managed')
        return super().create(vals_list)

    @api.model
    def bestseller_batch_details(self, batch_id):
        self._authorize('editor')
        if type(batch_id) is not int or batch_id <= 0: raise ValidationError('Invalid batch')
        batch=self.env[PREFIX+'batch'].browse(batch_id).exists()
        if not batch: raise ValidationError('Batch not found')
        batch._authorize('editor')
        if batch.company_id != self.env.company: raise AccessError('Company mismatch')
        screen=self.env[PREFIX+'screening'].search([('batch_id','=',batch.id),('company_id','=',batch.company_id.id)],limit=2)
        if len(screen)!=1 or not screen.outcome or screen.outcome.get('batch_id')!=batch.id: raise ValidationError('Completed screening batch required')
        shop=self._screen_shop(screen.shop_id.id)
        if shop.company_id!=batch.company_id or shop.site!='MLM' or shop.marketplace_seller_id!=screen.seller_id: raise AccessError('Shop identity mismatch')
        cases=self.search([('batch_id','=',batch.id),('company_id','=',batch.company_id.id),('collection','=',False)],order='id')
        if set(cases.ids)!=set(screen.outcome.get('case_ids',[])): raise ValidationError('Screening batch membership mismatch')
        items=[]
        for case in cases:
            case._authorize('editor')
            snap=self._wb_snapshot(case); payload=snap.payload or {}
            item=payload.get('id') or payload.get('item_id')
            if case.shop_id!=shop or not isinstance(item,str) or not re.fullmatch(r'MLM[0-9]+',item) or payload.get('site_id')!='MLM' or str(payload.get('seller_id'))!=str(shop.marketplace_seller_id): raise ValidationError('Source identity mismatch')
            items.append({'case_id':case.id,'item_id':item,'title':payload.get('title'),'snapshot_id':snap.id,'revision':case.revision,'image_count':len(payload.get('source_image_artifact_ids') or []),'collection_ids':self.search([('company_id','=',batch.company_id.id),('collection_import_source_id','=',case.id)]).ids})
        return {'batch_id':batch.id,'shop_id':shop.id,'items':items}

    @api.model
    def import_bestseller_batch(self, batch_id, request_key, config, retry_failed=False):
        self._authorize('editor')
        if type(batch_id) is not int or batch_id <= 0 or not isinstance(request_key,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',request_key):
            raise ValidationError('Invalid batch or request key')
        keys = {'platform','result_limit','min_sold','min_reviews','min_rating'}
        if not isinstance(config,dict) or set(config)!=keys or config['platform']!='mercado-libre-mx' or type(config['result_limit']) is not int or not 1<=config['result_limit']<=1000 or any(type(config[k]) is not int or not 0<=config[k]<=1000000000 for k in ('min_sold','min_reviews')) or type(config['min_rating']) not in (int,float) or not 0<=config['min_rating']<=5 or type(retry_failed) is not bool:
            raise ValidationError('Invalid collection configuration')
        batch=self.env[PREFIX+'batch'].browse(batch_id).exists()
        if not batch: raise ValidationError('Batch not found')
        batch._authorize('editor')
        if batch.company_id != self.env.company: raise AccessError('Company mismatch')
        screening=self.env[PREFIX+'screening'].search([('batch_id','=',batch.id),('company_id','=',batch.company_id.id)],limit=2)
        if len(screening)!=1 or not screening.outcome or screening.outcome.get('batch_id')!=batch.id:
            raise ValidationError('Completed screening batch required')
        shop=self._screen_shop(screening.shop_id.id)
        if shop.company_id!=batch.company_id or shop.site!='MLM' or shop.marketplace_seller_id!=screening.seller_id:
            raise AccessError('Shop identity mismatch')
        self.env.cr.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',[f'bestseller-import:{batch.company_id.id}:{request_key}'])
        if not self.env.cr.fetchone()[0]: return {'job_state':'running','code':'step_in_progress','resume_action':'execute_same_request'}
        ledger=self.env[PREFIX+'bestseller.import'].sudo().search([('company_id','=',batch.company_id.id),('request_key','=',request_key)],limit=1)
        manifest=bounded({'batch_id':batch.id,'config':config})
        if ledger and ledger.manifest!=manifest: raise ValidationError('Request key conflict')
        if not ledger:
            cases=self.search([('batch_id','=',batch.id),('company_id','=',batch.company_id.id),('collection','=',False)],order='id')
            if not cases or set(cases.ids)!=set(screening.outcome.get('case_ids',[])):
                raise ValidationError('Screening batch membership mismatch')
            ledger=self.env[PREFIX+'bestseller.import'].sudo()._append({'company_id':batch.company_id.id,'batch_id':batch.id,'request_key':request_key,'manifest':manifest,'source_ids':cases.ids,'results':[]})
        results=list(ledger.results or [])
        done={r['source_case_id'] for r in results if r['status'] in ('success','failed')}
        remaining=[i for i in ledger.source_ids if i not in done]
        if not remaining and retry_failed:
            remaining=[r['source_case_id'] for r in results if r['status']=='failed'][:1]
            if remaining: results=[r for r in results if r['source_case_id']!=remaining[0]]
        if remaining:
            source=self.browse(remaining[0]).exists()
            try:
                with self.env.cr.savepoint():
                    if not source or source.batch_id!=batch or source.shop_id!=shop or source.company_id!=batch.company_id:
                        raise ValidationError('Source ownership mismatch')
                    source._authorize('editor')
                    snap=self._wb_snapshot(source); payload=dict(snap.payload or {})
                    item=payload.get('id') or payload.get('item_id')
                    if not isinstance(item,str) or not re.fullmatch(r'MLM[0-9]+',item) or payload.get('site_id')!='MLM' or str(payload.get('seller_id'))!=str(shop.marketplace_seller_id):
                        raise ValidationError('Source identity mismatch')
                    existing=self.search([('collection_import_key','=',request_key),('collection_import_source_id','=',source.id),('company_id','=',batch.company_id.id)],limit=1)
                    if existing:
                        detail=self.workbench_detail(existing.id)
                        if existing.collection_import_snapshot_id:
                            snap=existing.collection_import_snapshot_id
                            previous=snap.payload or {}
                            if (previous.get('id') or previous.get('item_id'))!=item:
                                raise ValidationError('Imported source item identity changed')
                    else:
                        refs=payload.get('source_image_artifact_ids') or []
                        if refs:
                            if not str(refs[0]).isdigit(): raise ValidationError('Source image unavailable')
                            image=self.env[PREFIX+'artifact'].browse(int(refs[0])).exists()
                            if not image or image.case_id!=source or image.company_id!=source.company_id or not image.data or image.mime_type not in ('image/png','image/jpeg','image/webp'):
                                raise ValidationError('Source image ownership mismatch')
                            image._authorize('worker')
                            image_data=image.data.decode() if isinstance(image.data,bytes) else image.data
                            image_mime=image.mime_type
                        else:
                            image_data,image_mime=self._bestseller_first_image(source,shop,item)
                        # Fence the selected immutable snapshot before creating a
                        # dependent case; a newer gallery must never be silently
                        # attributed to an earlier selection.
                        source._lock()
                        if self._wb_snapshot(source).id != snap.id:
                            raise ValidationError('Source snapshot changed; retry')
                        values={'name':item,'garment':str(payload.get('title') or source.name),'source_sku':item,'image_data':image_data,'image_mime':image_mime,**config}
                        detail=self.workbench_collection_create(values)
                        target=self.browse(detail['id']);target._set({'collection_import_key':request_key,'collection_import_source_id':source.id,'collection_import_snapshot_id':snap.id})
                    results.append({'source_case_id':source.id,'source_snapshot_id':snap.id,'item_id':item,'collection_id':detail['id'],'status':'success'})
            except (ValidationError, AccessError, ValueError, TypeError) as exc:
                results.append({'source_case_id':remaining[0],'status':'failed','error':str(exc)[:500]})
            except (UserError, transport.BffError, requests.RequestException):
                results.append({'source_case_id':remaining[0],'status':'failed','error':'Shop authorization or platform read unavailable'})
            ledger._set({'results':results})
        pending=len(ledger.source_ids)-len({r['source_case_id'] for r in results})
        failed=sum(r['status']=='failed' for r in results)
        return {'batch_id':batch.id,'request_key':request_key,'job_state':'running' if pending else ('partial' if failed else 'completed'),'items':results,'pending':pending,'resume_action':'execute_same_request' if pending else ('retry_failed' if failed else None)}

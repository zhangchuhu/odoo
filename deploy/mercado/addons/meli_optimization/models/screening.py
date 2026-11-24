"""Authenticated, idempotent automatic screening. External reads precede DB locks."""
import re
from datetime import datetime, timezone
from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.addons.meli_bff.service import authorize
from odoo.addons.meli_bff.protocol import Operation
from odoo.addons.meli_platform_adapter import transport
from .optimization import PREFIX, ExactJson, bounded
from ..domain import product_facts_from_snapshot, validate_result
from ..domain.screening_resume import ReplayReads, CollectionPending, CheckpointExpired, observation_window
from ..domain.screening import parse_criteria, screen_products, collect_shop, timestamp, sku_size
from ..domain.image_publish import resolve_target
from ..domain.variant_groups import traditional_groups, bind_site_variations


class Screening(models.Model):
    _name = PREFIX + 'screening'
    _inherit = PREFIX + 'protected'
    _description = 'Immutable shop screening request and outcome'
    _internal_only = True
    shop_id = fields.Many2one('meli.independent.store', required=True, ondelete='restrict', index=True)
    request_key = fields.Char(required=True)
    seller_id = fields.Char(required=True)
    criteria = ExactJson(required=True)
    digest = fields.Char(required=True)
    batch_id = fields.Many2one(PREFIX + 'batch', ondelete='restrict')
    outcome = ExactJson()
    _identity = models.Constraint('UNIQUE(company_id, shop_id, request_key)', 'Screening request already exists')


class ScreeningWorkbench(models.Model):
    _inherit = PREFIX + 'case'
    shop_id = fields.Many2one('meli.independent.store', readonly=True, ondelete='restrict', index=True)

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            shop_id = values.get('shop_id', self.env.context.get('default_shop_id'))
            if shop_id:
                shop = self.env['meli.independent.store'].browse(shop_id).exists()
                if not shop or shop.company_id != self.env.company:
                    raise AccessError('商品的店铺归属必须与优化公司一致')
                shop.check_access('read')
        return super().create(vals_list)

    def _screen_shop(self, shop_id):
        self._authorize()
        shop = authorize(self.env, Operation('shop', 'GET', int(shop_id)))
        if not all(re.fullmatch(r'[1-9][0-9]{0,19}', value or '') for value in (shop.seller_id, shop.marketplace_seller_id)):
            raise ValidationError('店铺缺少已验证的站点 Seller ID')
        return shop

    @api.model
    def screening_preview(self, shop_id, request_key, criteria):
        shop = self._screen_shop(shop_id)
        try:
            criteria = parse_criteria(criteria)
            if criteria['site_id'] != shop.site or timestamp(criteria['end']) > datetime.now(timezone.utc):
                raise ValueError('Shop site mismatch or future sales window')
        except (ValueError, TypeError, KeyError) as exc:
            raise ValidationError(str(exc)) from exc
        if not isinstance(request_key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_key):
            raise ValidationError('稳定 request_key 必须为 1–128 位字母、数字或 _.:-')
        digest = bounded(dict(criteria=criteria, shop_id=shop.id, seller_id=shop.marketplace_seller_id, company_id=shop.company_id.id))
        # Serialize only the short local preview operation, never remote calls.
        self.env.cr.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', [f'screen:{shop.company_id.id}:{shop.id}:{request_key}'])
        model = self.env[PREFIX + 'screening'].with_company(shop.company_id)
        existing = model.search([('shop_id', '=', shop.id), ('request_key', '=', request_key)], limit=1)
        if existing and existing.digest != digest:
            raise ValidationError('同一请求身份不可改变筛选条件，请使用新的 request_key')
        row = existing or model._append(dict(company_id=shop.company_id.id, shop_id=shop.id, seller_id=shop.marketplace_seller_id, request_key=request_key, criteria=criteria, digest=digest))
        return dict(id=row.id, digest=row.digest, shop_id=shop.id, company_id=shop.company_id.id, seller_id=row.seller_id, criteria=row.criteria,
            semantics='product sales_units <= sales_max AND sum(each picture-variant group SKU available_quantity) >= stock_min; paid orders created in [start,end); cancellations excluded, returns/refunds not deducted; unknown excluded' + ('; every listed SKU in the group has verified SIZE and stock > 0; no variations requires explicit one-size; unlisted intended sizes are not inferred' if criteria.get('require_all_skus_in_stock') else ''),
            execute_tool='optimization_screen_execute', batch_id=row.batch_id.id or None)

    def _screen_collect(self, shop, criteria, checkpoint=None):
        token = shop._token()
        def get(path, query=()):
            response = transport.send('GET', path, token=token, query=query, timeout=30)
            if not 200 <= response.status < 300:
                status = response.status
                response.close()
                raise ValidationError(f'店铺完整数据不可用 (HTTP {status})；未创建优化批次，请核验账户接口权限')
            return response.json()
        if checkpoint is not None:
            get = ReplayReads(checkpoint, get).get
        identity = get('/users/me')
        if str(identity.get('id')) != shop.seller_id:
            raise ValidationError('授权令牌与店铺主 Seller 不匹配；未采集商品或创建批次')
        mapping = get('/marketplace/users/' + shop.seller_id)
        markets = mapping.get('marketplaces')
        if str(mapping.get('user_id')) != shop.seller_id or not isinstance(markets, list):
            raise ValidationError('无法验证店铺主账户与站点映射')
        matches = [m for m in markets if isinstance(m, dict) and str(m.get('user_id')) == shop.marketplace_seller_id
            and m.get('site_id') == shop.site and (not shop.logistic_type or m.get('logistic_type') == shop.logistic_type)]
        if len(matches) != 1:
            raise ValidationError('站点 Seller、站点或物流映射已改变，请重新授权店铺')
        products,orders,source=collect_shop(get, shop.marketplace_seller_id, criteria)
        product_criteria=dict(criteria,require_all_skus_in_stock=False)
        matched={p['id'] for p in screen_products(products,orders,product_criteria,shop.marketplace_seller_id,source.get('orders_complete'))['matches']}
        verified=[]
        for product in products:
            if product['id'] not in matched:continue
            try:
                parent_id=product.get('cbt_item_id')
                if not isinstance(parent_id,str) or not re.fullmatch(r'CBT[0-9]+',parent_id):raise ValueError('缺少母商品身份')
                parent=get('/marketplace/items/'+parent_id)
                target=resolve_target(product,parent,product['id'],shop.site,shop.seller_id,shop.marketplace_seller_id,shop.logistic_type)
                if target['model']!='traditional':raise ValueError('User Product 图片变体尚未核验')
                groups=traditional_groups(parent)
                if any(not g['selectable'] for g in groups):raise ValueError('图片变体归属不完整')
                if product.get('variations') or parent.get('variations'):
                    groups=bind_site_variations(groups,product.get('variations'))
                else:
                    quantity=product.get('available_quantity')
                    groups[0]['stock']=quantity if type(quantity) is int and quantity>=0 else None
                product['_verified_variant_groups']=groups
                product['_verified_parent_id']=parent_id
                verified.append((product,parent_id,parent))
            except (ValueError,TypeError,KeyError) as exc:
                product['_variant_error']=str(exc)[:300]
        for product,parent_id,previous_parent in verified:
            if get('/marketplace/items/'+parent_id) != previous_parent:
                product.pop('_verified_variant_groups',None)
                product['_variant_error']='母商品变体或图片在采集期间发生变化'
        source['variant_groups_checked']=True
        return products,orders,source

    @api.model
    def screening_execute(self, preview_id, digest):
        self._authorize()
        row = self.env[PREFIX + 'screening'].browse(int(preview_id)).exists()
        if not row:
            raise AccessError('筛选预览不存在或无权访问')
        row._authorize()
        shop = self._screen_shop(row.shop_id.id)
        if row.company_id != shop.company_id or row.seller_id != shop.marketplace_seller_id or row.criteria['site_id'] != shop.site:
            raise AccessError('店铺归属或授权已改变，请重新预览')
        if digest != row.digest:
            raise ValidationError('请先展示解析后的预览，并传入原始 digest')
        if row.batch_id:
            return row.outcome
        # One bounded read per step. Nonblocking cross-process lock prevents
        # duplicate fetches; no detached work survives the HTTP request.
        self.env.cr.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))', [f'screen-step:{row.id}'])
        if not self.env.cr.fetchone()[0]:
            return {'preview_id': row.id, 'job_state': 'running', 'code': 'step_in_progress', 'resume_action': 'execute_same_preview'}
        row.invalidate_recordset()
        if row.batch_id:
            return row.outcome
        previous = row.outcome if isinstance(row.outcome, dict) else {}
        checkpoint = previous.get('job_checkpoint', {}) if previous.get('job_state') == 'running' else {}
        started = previous.get('started_at') if checkpoint else datetime.now(timezone.utc).isoformat()
        try:
            if checkpoint.get('responses') and checkpoint.get('variant_schema') != 1:
                raise CheckpointExpired('旧检查点不包含变体图片身份')
            checkpoint['variant_schema']=1
            observation_window(checkpoint)
            products, orders, source = self._screen_collect(shop, row.criteria, checkpoint)
            window = observation_window(checkpoint)
            source = dict(source, collection_started_at=started, **{k: v for k, v in window.items() if k != 'collection_started_at'})
            source['collection_started_at'] = window.get('collection_started_at', started)
            criteria_for_products=dict(row.criteria,require_all_skus_in_stock=False) if source.get('variant_groups_checked') else row.criteria
            result = screen_products(products, orders, criteria_for_products, row.seller_id, source.get('orders_complete'))
            if source.get('variant_groups_checked'):
                expanded=[]
                for product in result['matches']:
                    groups=product.get('_verified_variant_groups')
                    if not isinstance(groups,list) or not groups:
                        result['unknown'].append({'item_id':product['id'],'reason':'variant_identity_unverified','detail':product.get('_variant_error') or '变体身份不可核验'})
                        continue
                    for group in groups:
                        if group.get('stock') is None:
                            result['unknown'].append({'item_id':product['id'],'reason':'variant_stock_unknown','detail':group['label']});continue
                        if group['stock'] < row.criteria['stock_min']:continue
                        child={k:v for k,v in product.items() if not k.startswith('_')}
                        child['available_quantity']=group['stock']
                        child['sales_scope']='product'
                        child['variations']=[v for v in product['variations'] if str(v.get('id')) in group.get('site_variant_ids',[])]
                        if row.criteria.get('require_all_skus_in_stock'):
                            members=child['variations'] or [product]
                            evidence=[]
                            for sku in members:
                                size=sku_size(sku,one_size=not child['variations'])
                                qty=sku.get('available_quantity')
                                if size is None or type(qty) is not int or qty<0:break
                                evidence.append({'sku_id':str(sku['id']),'size':size,'available_quantity':qty})
                            if len(evidence)!=len(members):
                                result['unknown'].append({'item_id':product['id'],'reason':'variant_size_unknown','detail':group['label']});continue
                            if any(x['available_quantity']==0 for x in evidence):continue
                            child['sku_size_evidence']=evidence
                        child['pictures']=[{'id':p,'secure_url':u} for p,u in zip(group['picture_ids'],group['picture_urls'])]
                        child['thumbnail']=group['picture_urls'][0] if group['picture_urls'] else None
                        child['_variant_scope']={'model':'traditional','parent_id':product['_verified_parent_id'],'key':group['key'],
                            'label':group['label'],'variant_ids':group['variant_ids'],'site_variant_ids':group.get('site_variant_ids',[]),
                            'picture_ids':group['picture_ids'],'sizes':group['sizes'],'attribute_signature':group.get('attribute_signature')}
                        expanded.append(child)
                result['matches']=expanded
        except CheckpointExpired:
            state = {'preview_id': row.id, 'job_state': 'failed', 'code': 'checkpoint_expired',
                'message': '采集观察已超过 2 小时，或旧检查点缺少可信读取时间；未创建批次。再次执行同一预览才会重新采集。', 'resume_action': 'execute_same_preview_recollects'}
            row._set({'outcome': state})
            return state
        except CollectionPending:
            state = {'preview_id': row.id, 'job_state': 'running', 'completed_reads': len(checkpoint.get('responses', [])), 'started_at': started,
                'resume_action': 'execute_same_preview', 'job_checkpoint': checkpoint}
            row._set({'outcome': state})
            return {k: v for k, v in state.items() if k != 'job_checkpoint'}
        except (ValueError, TypeError, KeyError, AttributeError, ValidationError, transport.BffError):
            # Discard incomplete observations. Retrying recollects the same frozen
            # window with fresh reads; no partial result can be mistaken for zero.
            state = {'preview_id': row.id, 'job_state': 'failed', 'code': 'collection_failed',
                'message': '完整销售/库存证据不可验证；未创建批次。核验账户权限或数据变化后重试原预览。', 'resume_action': 'execute_same_preview_recollects'}
            row._set({'outcome': state})
            return state
        observed_at = window.get('observed_at', datetime.now(timezone.utc).isoformat())
        committed_at = datetime.now(timezone.utc).isoformat()
        with self.env.cr.savepoint():
            row._lock()
            if row.batch_id:
                return row.outcome
            # Authorization is rechecked at the persistence boundary.
            self._screen_shop(shop.id)
            scoped = self.with_company(shop.company_id)
            batch = self.env[PREFIX + 'batch'].with_company(shop.company_id).create({'name': f'自动筛选 · {shop.name} · {row.request_key}'})
            cases = []
            for product in result['matches']:
                scope=product.get('_variant_scope')
                if scope:
                    fence=f'{shop.company_id.id}:{shop.id}:{product["id"]}'
                    self.env.cr.execute('''INSERT INTO meli_optimization_variant_creation_fence (target_key,version) VALUES (%s,1)
                        ON CONFLICT (target_key) DO UPDATE SET version=meli_optimization_variant_creation_fence.version+1''',[fence])
                    existing=scoped.sudo().search([('company_id','=',shop.company_id.id),('shop_id','=',shop.id),('variant_item_id','=',product['id'])])
                    members=set(scope['variant_ids'])
                    if existing.filtered(lambda c:bool(members.intersection(set(c.variant_members or []))) or (not members and c.variant_members==[])):
                        result['unknown'].append({'item_id':product['id'],'reason':'variant_already_exists','detail':scope['label']})
                        continue
                case = scoped.create({'name': product['id'] + (' · '+scope['label'] if scope else '') + ' · ' + str(product.get('title') or ''), 'batch_id': batch.id, 'shop_id': shop.id})
                if scope:
                    identity=bounded({'company_id':shop.company_id.id,'shop_id':shop.id,'item_id':product['id'],'members':scope['variant_ids']})
                    case._set({'variant_identity':identity,'variant_item_id':product['id'],'variant_members':scope['variant_ids']})
                payload = dict({k:v for k,v in product.items() if not k.startswith('_')}, item_id=product['id'], shop=shop.name, shop_id=shop.id, company_id=shop.company_id.id,
                    seller_id=row.seller_id, source=source, observed_at=observed_at, screening_criteria=row.criteria,
                    screening_id=row.id, screening_digest=row.digest, value_status='known' if product.get('available_quantity') is not None else 'unknown')
                if scope:payload['variant_scope']=scope
                snapshots=self.env[PREFIX + 'snapshot'].with_company(shop.company_id)
                if scope:
                    from .variant_workbench import _VARIANT_SNAPSHOT_GUARD
                    snapshots=snapshots.with_context(_variant_snapshot_guard=_VARIANT_SNAPSHOT_GUARD)
                snapshot = snapshots.create({'case_id': case.id, 'payload': payload})
                # Local observation arrangement is part of persistence, not a
                # provider job. Use the same builder and checked archival as the
                # manual worker flow, without claiming a worker lease.
                task_id = scoped.workbench_task(case.id, 'product.facts', {})
                task = self.env[PREFIX + 'task'].browse(task_id)
                facts = product_facts_from_snapshot({'snapshot_id': snapshot.id, 'payload': snapshot.payload})
                validate_result('product.facts', facts)
                result_hash = bounded(facts)
                task._transition('running')
                task._set({'result': facts})
                task._transition('succeeded')
                receipt = {'task_id': task.id, 'submission_key': 'screening-facts',
                    'state': 'succeeded', 'accepted': True, 'execution': 'local_snapshot', 'screening_id': row.id}
                self.env[PREFIX + 'event']._append({'company_id': shop.company_id.id, 'task_id': task.id,
                    'submission_key': 'screening-facts', 'kind': 'complete', 'payload': facts,
                    'digest': bounded({'body': facts, 'execution': 'local_snapshot', 'screening_id': row.id}),
                    'receipt': receipt, 'worker_uid': self.env.uid})
                scoped.workbench_archive(case.id, task.id, result_hash)
                # Preserve immediately usable observations while a separate
                # queued task enriches this exact immutable snapshot with vision.
                self.env[PREFIX + 'task'].with_company(shop.company_id).create({
                    'case_id': case.id, 'snapshot_id': snapshot.id,
                    'task_type': 'product.facts', 'payload': {
                        'schema_version': '1', 'product_snapshot_id': str(snapshot.id),
                        'screening_visual_enrichment': row.id,
                    },
                })
                cases.append(case.id)
            outcome = dict(preview_id=row.id, batch_id=batch.id, case_ids=cases, matched=len(cases), unknown=result['unknown'], evaluated=result['evaluated'], source=source, observed_at=observed_at, committed_at=committed_at)
            bounded(outcome)
            row._set({'batch_id': batch.id, 'outcome': outcome})
            return outcome

    @api.model
    def screening_history(self):
        self._authorize('worker')
        return [{'id': r.id, 'shop_id': r.shop_id.id, 'criteria': r.criteria, 'digest': r.digest, 'batch_id': r.batch_id.id or None, 'outcome': {k: v for k, v in (r.outcome or {}).items() if k != 'job_checkpoint'}}
            for r in self.env[PREFIX + 'screening'].search([], order='id desc', limit=100)]

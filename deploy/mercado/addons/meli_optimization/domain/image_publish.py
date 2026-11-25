"""Identify an image update target from platform-returned, shop-bound records."""
import json
import re
from .snapshot_image_fetch import validate_url


def ready_publication_state(operation):
    try:
        return {'upload':'uploading','link':'linking','update':'updating'}[operation]
    except (KeyError, TypeError):
        raise ValueError('发布步骤无效') from None


def safe_publish_diagnostic(operation, stage, status=None, data=None, exception=None):
    if operation not in ('upload','link','update') or stage not in ('request','response','readback'):
        raise ValueError('诊断阶段无效')
    data = data if isinstance(data, dict) else {}
    raw_code = data.get('error') or data.get('code') or (type(exception).__name__ if exception else '')
    code = '' if not raw_code else raw_code if isinstance(raw_code, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', raw_code) else 'platform_error'
    message = data.get('message') if isinstance(data.get('message'), str) else ''
    causes = data.get('cause')
    if isinstance(causes, list):
        for cause in causes[:3]:
            if isinstance(cause, dict):
                detail = [cause.get(k) for k in ('code','message') if isinstance(cause.get(k), str)]
                if detail:
                    message += ' | ' + ': '.join(detail)
    message = re.sub(r'https?://\S+', '[url]', message, flags=re.I)
    message = re.sub(r'(?i)bearer\s+\S+', 'Bearer [redacted]', message)
    message = re.sub(r'(?i)(access_token|refresh_token|authorization|password|secret)\s*[:=]\s*\S+', r'\1=[redacted]', message)
    message = ''.join(c if c.isprintable() else ' ' for c in message)[:300]
    return {'operation':operation, 'stage':stage, 'http_status':status if type(status) is int and 100 <= status <= 599 else None,
            'code':code, 'message':message}


def group_scope_matches(original, current):
    if not isinstance(original,dict) or not isinstance(current,dict): return False
    keys=('model','target_id','parent_id','item_id','affected_sites','category_id','picture_limit','variant_picture_limit')
    if any(original.get(k)!=current.get(k) for k in keys): return False
    try:
        def normalize(item):
            ids=item['selected_group_variant_ids']
            if not isinstance(ids,list) or len(set(ids))!=len(ids): raise ValueError()
            variants=item['variants']
            identities={v['id']:(v.get('sku'),tuple(sorted(v.get('attributes') or []))) for v in variants}
            if len(identities)!=len(variants): raise ValueError()
            groups=[]
            for g in item['picture_groups']:
                members=g['variant_ids']
                if len(set(members))!=len(members): raise ValueError()
                groups.append((json.dumps(g['key'],sort_keys=True,separators=(',',':')),tuple(sorted(members))))
            return tuple(sorted(ids)),identities,sorted(groups)
        return normalize(original)==normalize(current)
    except (KeyError,TypeError,ValueError): return False


def image_upload_form(raw, mime, boundary):
    if (not isinstance(raw, bytes) or not raw or len(raw) > 10 * 1024 * 1024
            or mime not in ('image/jpeg','image/png') or not isinstance(boundary, str)
            or not re.fullmatch(r'[0-9a-f]{32}', boundary)):
        raise ValueError('上传图片格式、大小或边界无效')
    marker = boundary.encode('ascii')
    if marker in raw:
        raise ValueError('上传边界与图片内容冲突')
    extension = 'jpg' if mime == 'image/jpeg' else 'png'
    prefix = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="image.{extension}"\r\n'
              f'Content-Type: {mime}\r\n\r\n').encode('ascii')
    return prefix + raw + b'\r\n--' + marker + b'--\r\n', 'multipart/form-data; boundary=' + boundary


def picture_update_payload(picture_ids):
    if (not isinstance(picture_ids, list) or not 1 <= len(picture_ids) <= 50
            or any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value) for value in picture_ids)
            or len(set(picture_ids)) != len(picture_ids)):
        raise ValueError('图片 ID 列表无效或重复')
    return {'pictures': [{'id': value} for value in picture_ids]}


def user_product_picture_ids(product, target_id, seller_id):
    if (not isinstance(product, dict) or product.get('id') != target_id
            or str(product.get('user_id', product.get('seller_id'))) != str(seller_id)):
        raise ValueError('User Product 归属无法确认')
    pictures = product.get('pictures')
    if not isinstance(pictures, list) or any(not isinstance(p, dict) for p in pictures):
        raise ValueError('平台图片列表不可用')
    picture_ids = [p.get('id') for p in pictures]
    picture_update_payload(picture_ids)
    return picture_ids


def legacy_scope_matches(original, current, linked):
    if not isinstance(original, dict) or not isinstance(current, dict) or not isinstance(linked, list):
        return False
    if any(original.get(k) != current.get(k) for k in ('model','target_id','parent_id','item_id','affected_sites','category_id','picture_limit','variants','selected_variant_id','variant_picture_limit','picture_groups','selected_group_variant_ids')):
        return False
    old = original.get('picture_ids')
    if not isinstance(old, list) or len(set(old + linked)) != len(old) + len(linked):
        return False
    return current.get('picture_ids') == old + linked


def legacy_update_payload(target, uploaded):
    picture_update_payload(uploaded)
    variants, selected, old = target.get('variants'), target.get('selected_variant_id'), target.get('picture_ids')
    if not isinstance(variants, list) or not isinstance(old, list) or not isinstance(selected, str):
        raise ValueError('规格范围不可用')
    picture_update_payload(old)
    if any(not isinstance(v, dict) or not isinstance(v.get('id'), str) or not re.fullmatch(r'[0-9]{1,30}', v['id']) for v in variants):
        raise ValueError('规格标识无效')
    if len({v['id'] for v in variants}) != len(variants) or sum(v['id'] == selected for v in variants) != 1:
        raise ValueError('目标规格无法唯一确认')
    result_variants = []
    for variation in variants:
        if not isinstance(variation, dict) or not isinstance(variation.get('id'), str) or not re.fullmatch(r'[0-9]{1,30}', variation['id']):
            raise ValueError('规格标识无效')
        current = variation.get('picture_ids')
        picture_update_payload(current)
        if any(ref not in old for ref in current):
            raise ValueError('规格图片不属于原商品图库')
        result_variants.append({'id':variation['id'], 'picture_ids':uploaded if variation['id'] == selected else current})
    full = list(uploaded) + [ref for ref in old if ref not in uploaded]
    return dict(picture_update_payload(full), variations=result_variants)


def legacy_update_matches(original, current, uploaded):
    try:
        expected = legacy_update_payload(original, uploaded)
        if any(original.get(k) != current.get(k) for k in ('model','target_id','parent_id','item_id','affected_sites','category_id','picture_limit','selected_variant_id','variant_picture_limit','selected_group_variant_ids')):
            return False
        if current.get('picture_ids') != [p['id'] for p in expected['pictures']]:
            return False
        actual = current.get('variants')
        if not isinstance(actual, list):
            return False
        return {v['id']:v['picture_ids'] for v in actual} == {v['id']:v['picture_ids'] for v in expected['variations']}
    except (KeyError, TypeError, ValueError):
        return False


def reserve_step(progress, nonce):
    if (not isinstance(progress, dict) or progress.get('phase') != 'ready'
            or not isinstance(nonce, str) or not re.fullmatch(r'[0-9a-f]{32}', nonce)
            or type(progress.get('total')) is not int or not 1 <= progress['total'] <= 50
            or not isinstance(progress.get('uploaded'), list) or len(progress['uploaded']) > progress['total']):
        raise ValueError('发布步骤不可领取')
    uploaded = progress['uploaded']
    if uploaded:
        picture_update_payload(uploaded)
    model = progress.get('model', 'user_product')
    if model == 'traditional_group' and len(uploaded) == progress['total']:
        from .image_batch_plan import plan_global_batches, start_plan, reserve_batch_step
        target = progress['target']
        batch = progress.get('batch')
        if not batch:
            plan = plan_global_batches(target['picture_ids'], target['variants'], target['selected_group_variant_ids'],
                                uploaded, min(50, target['picture_limit']), target['variant_picture_limit'])
            initial = {'root':target['picture_ids'], 'variants':{v['id']:v['picture_ids'] for v in target['variants']}}
            batch = start_plan(plan, initial)
        batch = reserve_batch_step(batch, nonce)
        step = batch['plan'][batch['index']]
        return dict(progress, phase='in_flight', nonce=nonce, operation=step['kind'], index=batch['index'], batch=batch)
    linked = progress.get('linked', [])
    if model not in ('user_product','traditional','traditional_group') or not isinstance(linked, list) or linked != uploaded[:len(linked)]:
        raise ValueError('发布步骤记录无效')
    operation = 'upload' if len(uploaded) < progress['total'] else 'link' if model == 'traditional' and len(linked) < len(uploaded) else 'update'
    index = len(linked) if operation == 'link' else len(uploaded)
    return dict(progress, phase='in_flight', nonce=nonce, operation=operation, index=index)


def mark_dispatched(progress, nonce):
    if (not isinstance(progress, dict) or progress.get('phase') != 'in_flight'
            or progress.get('nonce') != nonce or progress.get('dispatched')):
        raise ValueError('发布请求已派发或身份无效')
    result = dict(progress, dispatched=True)
    if progress.get('model') == 'traditional_group' and progress.get('operation') != 'upload':
        from .image_batch_plan import dispatch_batch_step
        result['batch'] = dispatch_batch_step(progress['batch'], nonce)
    return result


def finish_step(progress, nonce, outcome, *, picture_id=None, observed_ids=None):
    if (not isinstance(progress, dict) or progress.get('phase') != 'in_flight' or progress.get('dispatched') is not True
            or progress.get('nonce') != nonce or outcome not in ('success','failed','unknown')
            or progress.get('operation') not in ('upload','link','update')):
        raise ValueError('发布回执与当前步骤不匹配')
    result = dict(progress)
    if progress.get('model') == 'traditional_group' and progress['operation'] != 'upload':
        from .image_batch_plan import finish_batch_step, reconcile_batch_step
        batch = finish_batch_step(progress['batch'], nonce, outcome)
        if outcome == 'success' and isinstance(observed_ids, dict):
            batch = reconcile_batch_step(batch, observed_ids)
        result.update(batch=batch, phase=batch['phase'], code='verified' if batch['phase'] == 'published' else 'platform_outcome_unknown' if batch['phase'] == 'reconciliation_required' else None)
        if batch['phase'] != 'reconciliation_required':
            for key in ('nonce','operation','index','dispatched'):
                result.pop(key, None)
        return result
    if outcome != 'success':
        result['phase'] = 'reconciliation_required' if outcome == 'unknown' else 'failed'
        result['code'] = 'platform_outcome_unknown' if outcome == 'unknown' else 'platform_write_rejected'
        return result
    if progress['operation'] == 'upload':
        uploaded = list(progress['uploaded']) + [picture_id]
        picture_update_payload(uploaded)
        result.update(phase='ready', uploaded=uploaded)
    elif progress['operation'] == 'link':
        linked = list(progress.get('linked', []))
        if progress.get('index') != len(linked) or progress['index'] >= len(progress['uploaded']) or picture_id != progress['uploaded'][progress['index']]:
            raise ValueError('图片绑定回执不匹配')
        result.update(phase='ready', linked=linked + [picture_id])
    else:
        result['phase'] = 'published' if observed_ids == progress['uploaded'] else 'reconciliation_required'
        result['code'] = 'verified' if result['phase'] == 'published' else 'platform_images_mismatch'
    result.pop('nonce', None)
    result.pop('operation', None)
    result.pop('index', None)
    result.pop('dispatched', None)
    return result


def resolve_target(item, parent, item_id, site, seller_id, marketplace_seller_id, logistic_type, variant_id=None):
    if (not isinstance(item, dict) or not isinstance(parent, dict)
            or not isinstance(item_id, str) or not re.fullmatch(r'MLM[0-9]+', item_id)
            or item.get('id') != item_id or item.get('site_id') != site
            or str(item.get('seller_id')) != str(marketplace_seller_id)
            or not isinstance(item.get('tags'), list) or any(not isinstance(t, str) for t in item['tags'])
            or item.get('catalog_listing') is True):
        raise ValueError('无法确认站点商品归属或刊登模型')
    parent_id = item.get('cbt_item_id')
    if (not isinstance(parent_id, str) or not re.fullmatch(r'CBT[0-9]+', parent_id)
            or parent.get('id') != parent_id or parent.get('site_id') != 'CBT'
            or str(parent.get('seller_id')) != str(seller_id)):
        raise ValueError('无法确认 CBT 母商品归属')
    mappings = parent.get('marketplace_items')
    if not isinstance(mappings, list):
        raise ValueError('缺少母商品与站点商品映射')
    matches = [row for row in mappings if isinstance(row, dict) and row.get('item_id') == item_id
               and row.get('site_id') == site and str(row.get('user_id')) == str(marketplace_seller_id)
               and row.get('parent_id') == parent_id and str(row.get('parent_user_id')) == str(seller_id)
               and (not logistic_type or row.get('logistic_type') == logistic_type)]
    if len(matches) != 1:
        raise ValueError('无法唯一确认商品站点和物流映射')
    match = matches[0]
    if 'user_product_listing' in item['tags']:
        target = match.get('siteless_user_product_id')
        if (not isinstance(target, str) or not re.fullmatch(r'U[0-9]+', target)
                or not item.get('user_product_id') or item.get('user_product_id') != match.get('user_product_id')):
            raise ValueError('无法确认 User Product 标识')
        return {'model': 'user_product', 'target_id': target, 'parent_id': parent_id}
    variations = parent.get('variations')
    if not isinstance(variations, list) or len(variations) > 100:
        raise ValueError('传统商品规格范围无法确认')
    result = {'model':'traditional', 'target_id':parent_id, 'parent_id':parent_id}
    if not variations:
        if variant_id is not None:
            raise ValueError('商品没有可选择的规格')
        return result
    pictures = parent.get('pictures')
    if not isinstance(pictures, list) or any(not isinstance(p, dict) for p in pictures):
        raise ValueError('商品图片列表不完整')
    old = [p.get('id') for p in pictures]
    picture_update_payload(old)
    choices = []
    for entry in variations:
        if not isinstance(entry, dict) or type(entry.get('id')) not in (int, str):
            raise ValueError('商品规格标识无效')
        identifier = str(entry['id'])
        if not re.fullmatch(r'[0-9]{1,30}', identifier):
            raise ValueError('商品规格标识无效')
        refs = entry.get('picture_ids')
        picture_update_payload(refs)
        if any(ref not in old for ref in refs):
            raise ValueError('规格图片与商品图库不一致')
        sku = entry.get('seller_custom_field')
        raw_attributes = entry.get('attribute_combinations') or []
        if not isinstance(raw_attributes, list) or len(raw_attributes) > 30:
            raise ValueError('商品规格属性无效')
        attributes = []
        for attribute in raw_attributes:
            if not isinstance(attribute, dict):
                raise ValueError('商品规格属性无效')
            name, value = attribute.get('name') or attribute.get('id'), attribute.get('value_name')
            if isinstance(name, str) and isinstance(value, str) and name and value:
                attributes.append((name[:100] + ': ' + value[:100]))
        choices.append({'id':identifier,'picture_ids':refs,'sku':str(sku or '')[:200], 'attributes':attributes})
    if len({v['id'] for v in choices}) != len(choices):
        raise ValueError('商品规格标识重复')
    if variant_id is not None and (not isinstance(variant_id, str) or variant_id not in [v['id'] for v in choices]):
        raise ValueError('所选规格不存在')
    result.update(variants=choices, selected_variant_id=variant_id)
    return result


def collect_preflight(get, item_id, site, seller_id, marketplace_seller_id, logistic_type, variant_id=None):
    if not all(isinstance(v, str) and re.fullmatch(r'[1-9][0-9]{0,19}', v) for v in (seller_id, marketplace_seller_id)):
        raise ValueError('店铺身份无效')
    if not isinstance(item_id, str) or not re.fullmatch(r'MLM[0-9]+', item_id):
        raise ValueError('商品标识无效')
    identity, mapping = get('/users/me'), get('/marketplace/users/' + seller_id)
    if not isinstance(identity, dict) or str(identity.get('id')) != seller_id or not isinstance(mapping, dict) or str(mapping.get('user_id')) != seller_id:
        raise ValueError('授权账号身份不一致')
    markets = mapping.get('marketplaces')
    matches = [m for m in markets if isinstance(m, dict) and str(m.get('user_id')) == marketplace_seller_id
               and m.get('site_id') == site and (not logistic_type or m.get('logistic_type') == logistic_type)] if isinstance(markets, list) else []
    if len(matches) != 1:
        raise ValueError('授权账号站点映射无法确认')
    item = get('/marketplace/items/' + item_id)
    parent_id = item.get('cbt_item_id') if isinstance(item, dict) else None
    if not isinstance(parent_id, str) or not re.fullmatch(r'CBT[0-9]+', parent_id):
        raise ValueError('母商品标识无效')
    parent = get('/marketplace/items/' + parent_id)
    target = resolve_target(item, parent, item_id, site, seller_id, marketplace_seller_id, logistic_type, variant_id)
    category_id = parent.get('category_id')
    if not isinstance(category_id, str) or not re.fullmatch(r'CBT[0-9]+', category_id):
        raise ValueError('无法确认商品类别和图片数量限制')
    category = get('/categories/' + category_id)
    settings = category.get('settings') if isinstance(category, dict) and category.get('id') == category_id else None
    picture_limit = settings.get('max_pictures_per_item') if isinstance(settings, dict) else None
    if type(picture_limit) is not int or picture_limit <= 0:
        raise ValueError('无法确认商品类别的图片数量限制')
    if target.get('variants'):
        full = get('/items/' + parent_id)
        if not isinstance(full, dict) or any(full.get(k) != parent.get(k) for k in ('id','seller_id','category_id','site_id')):
            raise ValueError('无法确认完整母商品身份')
        def complete_scope(item):
            pictures, variations = item.get('pictures'), item.get('variations')
            if not isinstance(pictures, list) or not isinstance(variations, list):
                raise ValueError('完整商品图片或规格缺失')
            try:
                image_ids = [p['id'] for p in pictures]
                rows = [(str(v['id']), v['picture_ids'], sorted((a['id'], a.get('value_id'), a.get('value_name'))
                          for a in v.get('attribute_combinations', []))) for v in variations]
            except (KeyError, TypeError):
                raise ValueError('完整商品图片或规格无效') from None
            return image_ids, sorted(rows, key=lambda v: v[0])
        if complete_scope(full) != complete_scope(parent):
            raise ValueError('平台商品响应不完整或规格不一致')
        variant_limit = settings.get('max_pictures_per_item_var')
        if type(variant_limit) is not int or variant_limit <= 0:
            raise ValueError('无法确认规格的图片数量限制')
        target['variant_picture_limit'] = variant_limit
        from .image_batch_plan import picture_groups
        attrs = get('/categories/' + category_id + '/attributes')
        raw_variants = [dict(v, id=str(v['id'])) for v in parent['variations']]
        groups = picture_groups(attrs, raw_variants)
        target['picture_groups'] = groups
        if variant_id:
            matches = [g for g in groups if variant_id in g['variant_ids']]
            if len(matches) != 1:
                raise ValueError('无法确认所选图片变体')
            target['selected_group_variant_ids'] = matches[0]['variant_ids']
    if target['model'] == 'user_product':
        product = get('/user-products/' + target['target_id'])
        user_product_picture_ids(product, target['target_id'], seller_id)
    else:
        product = parent
    pictures = product.get('pictures')
    if not isinstance(pictures, list) or any(not isinstance(p, dict) for p in pictures):
        raise ValueError('平台图片列表不可用')
    picture_ids = [p.get('id') for p in pictures]
    picture_update_payload(picture_ids)
    displayed_pictures = []
    for picture in pictures:
        try:
            url = validate_url(picture.get('secure_url'))
        except ValueError:
            url = None
        displayed_pictures.append({'id':picture['id'], 'url':url})
    affected = []
    for row in parent['marketplace_items']:
        if not isinstance(row, dict) or not isinstance(row.get('item_id'), str) or not isinstance(row.get('site_id'), str):
            raise ValueError('关联站点信息不完整')
        if target['model'] == 'user_product':
            if not row.get('siteless_user_product_id'):
                raise ValueError('关联站点 User Product 信息不完整')
            if row['siteless_user_product_id'] != target['target_id']:
                continue
        affected.append({'item_id': row['item_id'], 'site_id': row['site_id'], 'logistic_type': row.get('logistic_type')})
    return dict(target, item_id=item_id, picture_ids=picture_ids, pictures=displayed_pictures, affected_sites=affected,
                category_id=category_id, picture_limit=picture_limit)

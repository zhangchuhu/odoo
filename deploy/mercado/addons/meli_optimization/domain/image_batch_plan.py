"""Pure, fail-closed planning helpers for traditional picture variations."""
import copy
import hashlib
import json
import re


def picture_groups(attributes, variants):
    if not isinstance(attributes, list) or not isinstance(variants, list) or not variants:
        raise ValueError('类别属性或规格缺失')
    defining = sorted(a['id'] for a in attributes if isinstance(a, dict) and isinstance(a.get('id'), str)
                      and isinstance(a.get('tags'), dict) and a['tags'].get('defines_picture') is True)
    if not defining or len(defining) != len(set(defining)):
        raise ValueError('图片定义属性无法确认')
    if any(not isinstance(v, dict) or not isinstance(v.get('attribute_combinations'), list)
           or any(not isinstance(a, dict) for a in v['attribute_combinations']) for v in variants):
        raise ValueError('规格属性缺失')
    used = {a.get('id') for v in variants for a in v['attribute_combinations']}
    defining = [attr for attr in defining if attr in used]
    if not defining:
        raise ValueError('商品未使用可识别的图片定义属性')
    seen, groups, names = set(), {}, {}
    for variant in variants:
        if not isinstance(variant, dict) or not isinstance(variant.get('id'), str) or not re.fullmatch(r'[0-9]+', variant['id']) or variant['id'] in seen:
            raise ValueError('规格身份无效')
        seen.add(variant['id'])
        values = variant.get('attribute_combinations')
        if not isinstance(values, list) or any(not isinstance(a, dict) for a in values):
            raise ValueError('规格属性缺失')
        by_id = {a.get('id'): a for a in values}
        if len(by_id) != len(values):
            raise ValueError('规格属性重复')
        key, labels = [], []
        for attr in defining:
            a = by_id.get(attr)
            if not isinstance(a, dict):
                raise ValueError('图片定义属性缺失')
            value_id, name = a.get('value_id'), a.get('value_name')
            if value_id is not None and (not isinstance(value_id, str) or not value_id.strip()):
                raise ValueError('图片属性值无效')
            if not value_id and (not isinstance(name, str) or not name.strip()):
                raise ValueError('图片属性值无法确认')
            identity = ('id', value_id) if value_id else ('name', name.strip().casefold())
            labels.append(attr + ': ' + (name.strip() if isinstance(name, str) and name.strip() else value_id))
            if isinstance(name, str) and name.strip():
                name_key = (attr, name.strip().casefold())
                if name_key in names and names[name_key] != identity:
                    raise ValueError('图片属性值无法唯一确认')
                names[name_key] = identity
            key.append((attr, identity))
        refs = variant.get('picture_ids')
        if not isinstance(refs, list) or not refs or any(not isinstance(x, str) or not x for x in refs) or len(refs) != len(set(refs)):
            raise ValueError('规格图片无效')
        key = tuple(key)
        group = groups.setdefault(key, {'key': key, 'labels':labels, 'variant_ids': [], 'picture_ids': list(refs)})
        if group['picture_ids'] != refs:
            raise ValueError('同组规格图片不一致')
        group['variant_ids'].append(variant['id'])
    return [dict(group, key=[[attribute, list(identity)] for attribute, identity in group['key']])
            for group in groups.values()]


def plan_batches(root, variants, selected_ids, new_ids, root_limit, variant_limit):
    """Plan link/update states only; actual platform validation is still required."""
    def valid_ids(value):
        return isinstance(value, list) and bool(value) and all(isinstance(x, str) and x for x in value) and len(value) == len(set(value))
    if (not valid_ids(root) or not valid_ids(new_ids) or not valid_ids(selected_ids)
            or not isinstance(variants, list) or not variants
            or type(root_limit) is not int or type(variant_limit) is not int
            or root_limit < 1 or variant_limit < 1 or set(root) & set(new_ids)):
        raise ValueError('发布输入或图片限制无效')
    mapping = {}
    for v in variants:
        if not isinstance(v, dict) or not isinstance(v.get('id'), str) or v['id'] in mapping or not valid_ids(v.get('picture_ids')) or not set(v['picture_ids']).issubset(root):
            raise ValueError('规格图片范围无效')
        mapping[v['id']] = list(v['picture_ids'])
    selected = set(selected_ids)
    if not selected.issubset(mapping):
        raise ValueError('目标规格不存在')
    target = list(mapping[selected_ids[0]])
    if any(mapping[x] != target for x in selected):
        raise ValueError('同组规格图片不一致')
    others = {pic for key, refs in mapping.items() if key not in selected for pic in refs}
    removable = set(target) - others
    final_root, inserted = [], False
    for image in root:
        if image in removable:
            if not inserted:
                final_root.extend(new_ids)
                inserted = True
        else:
            final_root.append(image)
    if not inserted:
        final_root.extend(new_ids)
    if len(final_root) > root_limit or len(new_ids) > variant_limit:
        raise ValueError('最终图片数量超限')
    if len(root) > root_limit or any(len(refs) > variant_limit for refs in mapping.values()):
        raise ValueError('现有图片数量超限')
    current_root, current_target, steps = list(root), list(target), []
    def record(kind, **extra):
        current = {k: (list(current_target) if k in selected else list(v)) for k,v in mapping.items()}
        steps.append({'kind':kind, 'root':list(current_root), 'variants':current, **extra})
    for image in new_ids:
        if len(current_root) >= root_limit:
            candidate = next((x for x in current_target if x in removable), None)
            if candidate is None:
                raise ValueError('无法安全释放图片空间')
            if len(current_target) == 1:
                replacement = next((x for x in new_ids if x in current_root and x not in current_target), None)
                if replacement is None:
                    raise ValueError('无法安全释放图片空间')
                current_target.append(replacement)
            current_target.remove(candidate)
            current_root.remove(candidate)
            removable.remove(candidate)
            record('update', removed=candidate)
        current_root.append(image)
        record('link', picture=image)
    current_target = list(new_ids)
    current_root = final_root
    record('update', final=True)
    return steps


def batch_update_payload(step):
    from .image_publish import picture_update_payload
    if not isinstance(step, dict) or step.get('kind') != 'update' or not isinstance(step.get('variants'), dict) or not step['variants']:
        raise ValueError('图片更新步骤无效')
    payload = picture_update_payload(step.get('root'))
    variations = []
    for identifier, refs in step['variants'].items():
        if not isinstance(identifier, str) or not re.fullmatch(r'[0-9]{1,30}', identifier):
            raise ValueError('规格标识无效')
        picture_update_payload(refs)
        if not set(refs).issubset(step['root']):
            raise ValueError('规格图片不属于商品图库')
        variations.append({'id':identifier, 'picture_ids':list(refs)})
    payload['variations'] = variations
    return payload


def plan_global_batches(root, variants, selected_ids, new_ids, root_limit, variant_limit):
    """Use the Global Selling update endpoint for every intermediate state."""
    def valid(value):
        return isinstance(value,list) and bool(value) and all(isinstance(x,str) and x for x in value) and len(value)==len(set(value))
    if (not valid(root) or not valid(selected_ids) or not valid(new_ids) or not isinstance(variants,list) or not variants
            or type(root_limit) is not int or type(variant_limit) is not int or min(root_limit,variant_limit)<1 or set(root)&set(new_ids)):
        raise ValueError('发布输入或图片限制无效')
    mapping = {}
    for v in variants:
        if not isinstance(v,dict) or not isinstance(v.get('id'),str) or v['id'] in mapping or not valid(v.get('picture_ids')) or not set(v['picture_ids']).issubset(root):
            raise ValueError('规格图片范围无效')
        mapping[v['id']] = list(v['picture_ids'])
    selected = set(selected_ids)
    if not selected.issubset(mapping): raise ValueError('目标规格不存在')
    target = list(mapping[selected_ids[0]])
    if any(mapping[i] != target for i in selected): raise ValueError('同组规格图片不一致')
    other_refs = {pic for key,refs in mapping.items() if key not in selected for pic in refs}
    exclusive = set(target)-other_refs
    final_root,inserted=[],False
    for pic in root:
        if pic in exclusive:
            if not inserted: final_root.extend(new_ids);inserted=True
        else: final_root.append(pic)
    if not inserted: final_root.extend(new_ids)
    if len(final_root)>root_limit or len(new_ids)>variant_limit: raise ValueError('最终图片数量超限')
    if len(root)>root_limit or any(len(refs)>variant_limit for refs in mapping.values()): raise ValueError('现有图片数量超限')
    current_root, current_target, steps = list(root), list(target), []
    for number,picture in enumerate(new_ids):
        if len(current_target)>=variant_limit:
            old=next((p for p in current_target if p in target),None)
            if old is None: raise ValueError('无法安全释放规格图片空间')
            current_target.remove(old)
        if len(current_root)>=root_limit:
            old=next((p for p in current_root if p in exclusive),None)
            if old is None: raise ValueError('无法安全释放图片空间')
            current_root.remove(old); exclusive.remove(old)
            if old in current_target: current_target.remove(old)
        current_root.append(picture); current_target.append(picture)
        final = number == len(new_ids)-1
        if final:
            current_root=list(final_root);current_target=list(new_ids)
        state={k:(list(current_target) if k in selected else list(v)) for k,v in mapping.items()}
        steps.append({'kind':'update','root':list(current_root),'variants':state,'final':final})
    return steps


def _digest_plan(plan, initial):
    return hashlib.sha256(json.dumps([plan, initial], sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _checked_progress(progress):
    p = copy.deepcopy(progress)
    if (not isinstance(p, dict) or not isinstance(p.get('plan'), list) or not p['plan']
            or p.get('digest') != _digest_plan(p['plan'], p.get('initial'))
            or type(p.get('index')) is not int or not 0 <= p['index'] <= len(p['plan'])):
        raise ValueError('发布计划已改变')
    return p


def start_plan(plan, initial):
    if not isinstance(plan, list) or not plan or not isinstance(initial, dict):
        raise ValueError('发布计划无效')
    plan, initial = copy.deepcopy(plan), copy.deepcopy(initial)
    return {'plan':plan, 'initial':initial, 'digest':_digest_plan(plan, initial), 'index':0, 'phase':'ready'}


def reserve_batch_step(progress, nonce):
    p = _checked_progress(progress)
    if p.get('phase') != 'ready' or p['index'] >= len(p['plan']) or not isinstance(nonce, str) or not re.fullmatch(r'[a-f0-9]{32}', nonce):
        raise ValueError('发布步骤无法领取')
    p.update(phase='in_flight', nonce=nonce, dispatched=False)
    return p


def dispatch_batch_step(progress, nonce):
    p = _checked_progress(progress)
    if p.get('phase') != 'in_flight' or p.get('nonce') != nonce or p.get('dispatched'):
        raise ValueError('发布步骤无法派发')
    p['dispatched'] = True
    return p


def finish_batch_step(progress, nonce, outcome):
    p = _checked_progress(progress)
    if p.get('phase') != 'in_flight' or p.get('nonce') != nonce or not p.get('dispatched') or outcome not in ('success', 'unknown', 'failed'):
        raise ValueError('发布结果无效')
    p.update(phase='reconciliation_required', outcome=outcome)
    return p


def reconcile_batch_step(progress, observed):
    p = _checked_progress(progress)
    if p.get('phase') != 'reconciliation_required':
        raise ValueError('当前步骤不需要核验')
    step = p['plan'][p['index']]
    if observed == {'root':step['root'], 'variants':step['variants']}:
        p['index'] += 1
        p['phase'] = 'published' if p['index'] == len(p['plan']) else 'ready'
        for key in ('nonce', 'dispatched', 'outcome'):
            p.pop(key, None)
    return p


def retry_batch_step(progress, observed):
    p = _checked_progress(progress)
    if p.get('phase') != 'reconciliation_required' or p['index'] >= len(p['plan']):
        raise ValueError('当前步骤无法重试')
    before = p['initial'] if p['index'] == 0 else {k:p['plan'][p['index'] - 1][k] for k in ('root','variants')}
    if observed != before:
        raise ValueError('平台状态已改变，不能重试')
    p['phase'] = 'ready'
    for key in ('nonce','dispatched','outcome'):
        p.pop(key,None)
    return p

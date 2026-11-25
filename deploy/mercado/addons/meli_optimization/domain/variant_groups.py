"""Conservative grouping of traditional listing variations by image attribute."""
import hashlib
import json
import re
from .snapshot_image_fetch import validate_url

def traditional_groups(parent):
    if not isinstance(parent,dict) or not isinstance(parent.get('variations'),list) or not isinstance(parent.get('pictures'),list):
        raise ValueError('平台变体或图片数据不完整')
    pictures={}
    for p in parent['pictures']:
        if not isinstance(p,dict) or not isinstance(p.get('id'),str) or p['id'] in pictures:
            raise ValueError('平台图库图片身份无效')
        try: url=validate_url(p.get('secure_url'))
        except ValueError: url=None
        pictures[p['id']]=url
    variations=parent['variations']
    if not variations:
        refs=list(pictures)
        return [{'key':'default','label':'默认商品','variant_ids':[],'sizes':[], 'stock':None,
                 'picture_ids':refs,'picture_urls':[pictures[x] for x in refs], 'selectable':bool(refs and all(pictures[x] for x in refs)),
                 'reason':None if refs and all(pictures[x] for x in refs) else '商品图片缺失'}]
    groups={};seen=set()
    for v in variations:
        if not isinstance(v,dict) or type(v.get('id')) not in (str,int) or not re.fullmatch(r'[0-9]{1,30}',str(v['id'])) or str(v['id']) in seen:
            raise ValueError('平台变体身份无效或重复')
        vid=str(v['id']);seen.add(vid);attrs=v.get('attribute_combinations')
        if not isinstance(attrs,list) or any(not isinstance(a,dict) for a in attrs):raise ValueError('平台变体属性不完整')
        attrs_by_id={a.get('id'):a for a in attrs if isinstance(a.get('id'),str)}
        if len(attrs_by_id)!=len(attrs): raise ValueError('平台变体属性身份缺失或重复')
        color=attrs_by_id.get('COLOR') or {}; name=color.get('value_name'); colorid=color.get('value_id')
        if not isinstance(name,str) or not name.strip():
            identity=('individual',vid);label='规格 '+vid
        else:
            extras=tuple(sorted((k,str(a.get('value_id') or '') or str(a.get('value_name') or '').strip().casefold()) for k,a in attrs_by_id.items() if k not in ('COLOR','SIZE')))
            identity=('color',str(colorid) if colorid else name.strip().casefold(),extras);label=name.strip()
        sizes=attrs_by_id.get('SIZE') or {};size=sizes.get('value_name')
        g=groups.setdefault(identity,{'label':label,'variant_ids':[],'sizes':[],'picture_ids':None,'picture_urls':[], 'stock':0,'selectable':True,'reason':None})
        g['variant_ids'].append(vid)
        if isinstance(size,str) and size and size not in g['sizes']:g['sizes'].append(size)
        qty=v.get('available_quantity')
        if type(qty) is not int or qty<0:g['stock']=None
        elif g['stock'] is not None:g['stock']+=qty
        refs=v.get('picture_ids')
        if not isinstance(refs,list) or not refs or len(refs)>50 or len(set(refs))!=len(refs) or any(not isinstance(x,str) or x not in pictures or not pictures[x] for x in refs):
            g['selectable']=False;g['reason']='变体图片缺失或无法验证';continue
        if g['picture_ids'] is None:g['picture_ids']=refs;g['picture_urls']=[pictures[x] for x in refs]
        elif g['picture_ids']!=refs:g['selectable']=False;g['reason']='同组规格的图片或顺序不一致'
    result=[]
    for identity,g in groups.items():
        g['variant_ids'].sort();g['picture_ids']=g['picture_ids'] or []
        g['key']=hashlib.sha256(json.dumps(g['variant_ids'],separators=(',',':')).encode()).hexdigest()
        g['attribute_signature']=hashlib.sha256(json.dumps(identity,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        result.append(g)
    return result

def bind_site_variations(groups, site_variations):
    """Match whole image groups, never assume site and CBT variation IDs coincide."""
    if not isinstance(groups,list) or not isinstance(site_variations,list):raise ValueError('变体列表缺失')
    if len({tuple(g.get('picture_ids') or []) for g in groups}) != len(groups):
        raise ValueError('母商品图片分组不唯一')
    used=set();result=[]
    for group in groups:
        refs=group.get('picture_ids')
        if not refs or not group.get('variant_ids'):raise ValueError('母商品图片分组不完整')
        members=[v for v in site_variations if isinstance(v,dict) and v.get('picture_ids')==refs]
        if len(members)!=len(group['variant_ids']):raise ValueError('站点与母商品变体组无法完整对应')
        ids=[];quantities=[]
        for v in members:
            vid=str(v.get('id') or '')
            if not re.fullmatch(r'[0-9]{1,30}',vid) or vid in used:raise ValueError('站点变体身份无效')
            used.add(vid);ids.append(vid);quantities.append(v.get('available_quantity'))
        result.append(dict(group,site_variant_ids=sorted(ids),stock=sum(quantities) if all(type(q) is int and q>=0 for q in quantities) else None))
    if len(used)!=len(site_variations):raise ValueError('站点存在未匹配的变体')
    return result

def publishable_variants(scope,current,target):
    groups=[g for g in current.get('groups',[]) if g.get('key')==scope.get('key') and g.get('variant_ids')==scope.get('variant_ids')]
    if (len(groups)!=1 or groups[0].get('attribute_signature')!=scope.get('attribute_signature')
            or not set(scope.get('site_variant_ids',[])).issubset(set(current.get('site_variation_ids',[])))):
        raise ValueError('平台变体或参考原图已变化，请重新核对')
    variants=target.get('variants') or []
    if not variants:
        if groups[0].get('picture_ids')!=scope.get('picture_ids'):raise ValueError('平台原图已变化，请重新核对')
        return []
    if not scope.get('variant_ids') or not set(scope['variant_ids']).issubset({v['id'] for v in variants}):
        raise ValueError('平台变体组成已变化，请重新核对')
    allowed=[v['id'] for v in variants if v['id'] in scope['variant_ids'] and v['picture_ids']==scope.get('picture_ids')]
    if not allowed or (target.get('selected_variant_id') and target['selected_variant_id'] not in allowed):
        raise ValueError('所选规格的原图已变化，请重新核对')
    return allowed

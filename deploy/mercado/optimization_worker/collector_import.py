"""Validate visible Chrome collector evidence without Feishu dependencies.

No browser activity or Odoo mutation occurs here. Callers must bind the immutable
plan and submit the result using the original host request and lease fence.
"""
import hashlib, json, re, unicodedata
from decimal import Decimal
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
from .domain import validate_result

_CARD={'query','rank','is_ad','title','url','product_id','thumbnail_url'}
_DETAIL={'identity','status','reason','detail_url','product_id','title','category','visual_category_code','sold_display','reviews_display','rating_display','match_level','visual_features'}

def valid_detail_fields(detail, *, allow_observed_at=False):
    if not isinstance(detail, dict): return False
    keys = set(detail)
    optional = {'price_observation'} | ({'observed_at'} if allow_observed_at else set())
    if not _DETAIL <= keys or keys - _DETAIL - optional: return False
    price = detail.get('price_observation')
    return (price is None or (isinstance(price, dict) and set(price) == {'amount','currency','source'}
        and isinstance(price['amount'], str) and bool(re.fullmatch(r'\d{1,10}(?:\.\d{1,2})?', price['amount']))
        and price['currency'] == 'MXN' and price['source'] == 'product_detail'))
_REASONS={'visual_category_mismatch','imagery_ambiguous_or_inaccessible','metric_missing_or_ambiguous','threshold_failure','identity_changed','detail_inaccessible'}
_CATEGORIES=frozenset(('dress','formal_gown','jumpsuit','overalls','womens_pants_set','womens_shorts_set','womens_skirt_set','womens_suit','top','blouse','shirt','vest','blazer','jacket','coat','sweater','cardigan','pants','shorts','skirt','shoes'))
_COLOR_SIZE = re.compile(r'(?<!\w)(?:aqua|beige|black|blue|brown|burgundy|champagne|coral|cream|cyan|fuchsia|gold|gray|green|grey|indigo|ivory|khaki|lavender|lime|magenta|maroon|mustard|navy|olive|orange|pink|purple|red|silver|tan|teal|turquoise|violet|white|yellow|amarillo|anaranjado|azul|blanco|blanca|cafe|caqui|celeste|cian|crema|dorado|fucsia|gris|lila|marron|morado|mostaza|naranja|negro|negra|oliva|plateado|purpura|rojo|roja|rosa|rosado|turquesa|verde|vino|violeta|petite|tall|curvy|maternity|chica|chico|extragrande|grande|maternal|unitalla|talla|tallas|tamano|size|xxs|xs|s|m|l|xl|xxl|xxxl|2xl|3xl|4xl|5xl)(?!\w)', re.I)
_CHINESE_COLOR_SIZE = re.compile(r'(?:黑白|粉白|红白|蓝白|蓝黑|黑红|灰白|黑灰|[黑白红蓝绿黄粉紫橙灰棕褐金银米咖青]色|藏青|卡其|驼色|肤色|杏色|酒红|深蓝|浅蓝|深绿|浅绿|尺码|尺寸|大码|小码|均码|加大码)')

def disallowed_term(value):
    normalized=unicodedata.normalize('NFKD',value)
    return any(unicodedata.category(c)=='Cf' for c in value) or bool(_CHINESE_COLOR_SIZE.search(value)) or bool(_COLOR_SIZE.search(''.join(c for c in normalized if not unicodedata.combining(c))))


def _identity(url, platform):
    if not isinstance(url,str) or len(url)>2000: raise ValueError('Invalid URL')
    p=urlsplit(url)
    hosts={'shein-us':{'us.shein.com'},'mercado-libre-mx':{'www.mercadolibre.com.mx','articulo.mercadolibre.com.mx','listado.mercadolibre.com.mx'}}
    if p.scheme!='https' or p.hostname not in hosts[platform] or p.username or p.password or p.port not in (None,443): raise ValueError('Invalid marketplace URL')
    match=re.search(r'-p-(\d+)(?:\.html)?(?:/|$)',p.path,re.I) if platform=='shein-us' else re.search(r'(?<![a-z0-9])(MLMU?)[-_ ]*(\d+)(?!\d)',p.path,re.I)
    if not match: raise ValueError('Product ID missing from URL')
    ident=match.group(1) if platform=='shein-us' else match.group(1).upper()+match.group(2)
    return ident,urlunsplit(('https',p.netloc,p.path,'',''))


def _number(value):
    if not isinstance(value,str): return None
    m=re.fullmatch(r'\s*(\+)?\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(k|mil)?\s*(\+)?\s*(?:sold|vendidos?)?\s*',value,re.I)
    if not m:return None
    if '.' in m.group(2) and not m.group(3): return None
    total=Decimal(m.group(2).replace(',','')) * (1000 if m.group(3) else 1)
    if total!=total.to_integral_value(): return None
    return int(total), bool(m.group(1) or m.group(3) or m.group(4))


def _observation_time(value):
    if not isinstance(value,str) or not value or value != value.strip():
        raise ValueError('Observation time must be an aware ISO timestamp')
    try:
        stamp=datetime.fromisoformat(value[:-1]+'+00:00' if value.endswith('Z') else value)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError('Missing time zone')
        return stamp.astimezone(timezone.utc)
    except (ValueError,OverflowError):
        raise ValueError('Observation time must be an aware ISO timestamp') from None


def ranked_pool(blocks, queries, platform, *, allow_variable=False, allow_short=False):
    pool={}
    if (not isinstance(blocks,list) or not isinstance(queries,list) or len(blocks)!=len(queries)
            or (not 1<=len(blocks)<=3 if allow_variable else len(blocks)!=3)):
        raise ValueError('Invalid blocks')
    for j,block in enumerate(blocks):
        if not isinstance(block,dict) or set(block)!={'query','observations'} or block['query']!=queries[j] or not isinstance(block['observations'],list) or not (0<=len(block['observations'])<=50 if allow_short else 30<=len(block['observations'])<=50): raise ValueError('Invalid block')
        for rank,card in enumerate(block['observations'],1):
            if not isinstance(card,dict) or set(card)!=_CARD or card['query']!=queries[j] or type(card['rank'])!=int or card['rank']!=rank or type(card['is_ad'])!=bool or not isinstance(card['title'],str) or not card['title'].strip(): raise ValueError('Invalid card')
            pid,canonical=_identity(card['url'],platform)
            if card['product_id']!=pid: raise ValueError('Card identity mismatch')
            info=pool.setdefault(pid,{'hits':set(),'organic':None,'ad':None,'card':card,'url':canonical})
            info['hits'].add(j); key='ad' if card['is_ad'] else 'organic'; info[key]=min(info[key] or rank,rank)
    recurrent=[pid for pid,p in pool.items() if len(p['hits'])>=2]
    recurrent.sort(key=lambda pid:(-len(pool[pid]['hits']),pool[pid]['organic'] is None,pool[pid]['organic'] or pool[pid]['ad'],platform+':'+pid))
    return pool, recurrent


def convert_evidence(data, request_id, platform, queries, category, observed_at, *, result_limit=15, min_sold=500,min_reviews=50,min_rating=4.1):
    return _validated_evidence(data, request_id, platform, queries, category, observed_at, result_limit,
                               min_sold, min_reviews, min_rating, require_complete=True)


def validate_evidence_prefix(data, request_id, platform, queries, category, observed_at, *, result_limit, min_sold, min_reviews, min_rating):
    _validated_evidence(data, request_id, platform, queries, category, observed_at, result_limit,
                        min_sold, min_reviews, min_rating, require_complete=False)


def convert_staged_evidence(data, request_id, platform, queries, extra_queries, category, observed_at,
                            *, result_limit=15, min_sold=500, min_reviews=50, min_rating=4.1, elapsed_seconds=0):
    """Validate a completed staged run; browser receipts must be checked by caller."""
    return _staged_evidence(data,request_id,platform,queries,extra_queries,category,observed_at,
                            result_limit,min_sold,min_reviews,min_rating,require_complete=True,elapsed_seconds=elapsed_seconds)


def validate_staged_evidence_prefix(data, request_id, platform, queries, extra_queries, category, observed_at,
                                    *, result_limit=15, min_sold=500, min_reviews=50, min_rating=4.1):
    return _staged_evidence(data,request_id,platform,queries,extra_queries,category,observed_at,
                            result_limit,min_sold,min_reviews,min_rating,require_complete=False)


def _staged_evidence(data, request_id, platform, queries, extra_queries, category, observed_at,
                     result_limit, min_sold, min_reviews, min_rating, *, require_complete, elapsed_seconds=0):
    from .collector_staged_policy import next_action
    if (not isinstance(queries, list) or len(queries) != 3 or not isinstance(extra_queries, list)
            or len(extra_queries) > 3
            or any(not isinstance(q, str) or not q.strip() or len(q) > 200 or disallowed_term(q) for q in queries + extra_queries)
            or len(set(queries + extra_queries)) != len(queries + extra_queries)):
        raise ValueError('Invalid staged queries')
    if not isinstance(data, dict) or not isinstance(data.get('queries'), list) or len(data['queries']) != 3 + len(extra_queries):
        raise ValueError('Invalid staged search observations')
    initial, _ = ranked_pool(data['queries'][:3], queries, platform)
    extra = None
    if extra_queries:
        extra, _ = ranked_pool(data['queries'][3:], extra_queries, platform, allow_variable=True,allow_short=True)
    details = data.get('details')
    if not isinstance(details, list):
        raise ValueError('Invalid staged details')
    reviewed, count, expansion_started = [], 0, False
    for detail in details:
        action = next_action(initial, reviewed, count, result_limit, extra)
        if extra is not None and action.get('stage') == 'expanded' and not expansion_started:
            if next_action(initial, reviewed, count, result_limit, None)['action'] != 'search':
                raise ValueError('Unexpected staged expansion')
            expansion_started = True
        if action['action'] != 'review' or not isinstance(detail, dict) or detail.get('identity') != platform + ':' + action['identity']:
            raise ValueError('Invalid staged detail order')
        reviewed.append((action['stage'], action['identity']))
        count += detail.get('status') == 'qualified'
    action = next_action(initial, reviewed, count, result_limit, extra, elapsed_seconds=elapsed_seconds if require_complete else 0)
    if extra is not None and not expansion_started and next_action(initial, reviewed, count, result_limit, None)['action'] != 'search':
        raise ValueError('Unexpected staged expansion')
    if require_complete and action['action'] == 'search':
        raise ValueError('Staged expansion required before completion')
    if require_complete and action['action'] != 'finish':
        raise ValueError('Staged detail review incomplete')
    pool = dict(initial)
    for key, value in (extra or {}).items():
        if key not in pool:
            pool[key] = dict(value, hits={i + 3 for i in value['hits']})
    return _validated_evidence(data, request_id, platform, queries + extra_queries, category, observed_at,
                               result_limit, min_sold, min_reviews, min_rating, require_complete=require_complete,
                               ranked_override=(pool, [identity for _, identity in reviewed]),
                               staged_reason=action.get('reason'))


def _validated_evidence(data, request_id, platform, queries, category, observed_at, result_limit,
                        min_sold, min_reviews, min_rating, *, require_complete, ranked_override=None, staged_reason=None):
    batch_time=_observation_time(observed_at)
    observed_at=batch_time.isoformat()
    if platform not in ('shein-us','mercado-libre-mx') or not isinstance(request_id,str) or not request_id: raise ValueError('Invalid request')
    if not isinstance(data,dict) or set(data)!={'task_record_id','platform','queries','details'} or data['task_record_id']!=request_id or data['platform']!=platform: raise ValueError('Request mismatch')
    if not isinstance(queries,list) or len(queries) not in ((3, 4, 5, 6) if ranked_override is not None else (3,)) or any(not isinstance(q,str) or not q.strip() for q in queries) or len(set(queries))!=len(queries): raise ValueError('Invalid query plan')
    if not isinstance(category,str) or category not in _CATEGORIES or type(result_limit)!=int or result_limit<1: raise ValueError('Invalid plan')
    pool, recurrent = ranked_override if ranked_override is not None else ranked_pool(data['queries'], queries, platform)
    details=data['details']; candidates=[]
    if not isinstance(details,list) or len(details)>len(recurrent): raise ValueError('Invalid recurring pool')
    for index,d in enumerate(details):
        if not isinstance(d,dict) or set(d) not in (_DETAIL,_DETAIL|{'observed_at'}):raise ValueError('Invalid detail')
        detail_time=_observation_time(d['observed_at']) if 'observed_at' in d else None
        if detail_time is not None and detail_time>batch_time:raise ValueError('Detail observation cannot follow batch observation')
        if d['visual_category_code'] is not None and (not isinstance(d['visual_category_code'],str) or d['visual_category_code'] not in _CATEGORIES):raise ValueError('Invalid visual category')
        pid=recurrent[index]
        if d['identity']!=platform+':'+pid or d['status'] not in ('qualified','rejected'):raise ValueError('Detail order/identity mismatch')
        detail_id=None
        if d['detail_url'] is not None:
            detail_id,_=_identity(d['detail_url'],platform)
            if d['product_id'] is not None and d['product_id']!=detail_id:raise ValueError('Detail URL and ID mismatch')
            if detail_id!=pid and not (d['status']=='rejected' and d['reason']=='identity_changed'):raise ValueError('Unexpected changed identity')
        elif d['product_id'] is not None:raise ValueError('Detail ID without URL')
        if d['status']=='rejected':
            if d['reason'] not in _REASONS: raise ValueError('Invalid rejection')
            if d['match_level'] is not None or d['visual_features'] is not None:raise ValueError('Rejected detail cannot contain match evidence')
            if d['reason']=='detail_inaccessible' and any(d[k] is not None for k in ('detail_url','product_id','title','category','visual_category_code','sold_display','reviews_display','rating_display')):raise ValueError('Inaccessible detail contains observations')
            if d['reason']=='identity_changed' and (detail_id is None or detail_id==pid):raise ValueError('Unproven changed identity')
            if d['reason']=='imagery_ambiguous_or_inaccessible' and (not d['detail_url'] or d['visual_category_code'] is not None):raise ValueError('Invalid ambiguous image evidence')
            if d['reason'] in ('visual_category_mismatch','metric_missing_or_ambiguous','threshold_failure') and (detail_id!=pid or not isinstance(d['title'],str) or not d['title'].strip()):raise ValueError('Rejection requires visible identity and title')
            if d['reason']=='visual_category_mismatch' and (not d['visual_category_code'] or d['visual_category_code']==category or (category=='womens_pants_set' and d['visual_category_code']=='womens_suit')): raise ValueError('Unproven category mismatch')
            if d['reason']=='metric_missing_or_ambiguous':
                sold=_number(d['sold_display']); reviews=_number(d['reviews_display'])
                try: rating=float(d['rating_display']) if isinstance(d['rating_display'],str) and re.fullmatch(r'\d(?:\.\d{1,2})?',d['rating_display'].strip()) else None
                except ValueError: rating=None
                if platform=='shein-us' and reviews and rating is not None and 0<=rating<=5:raise ValueError('Metrics are not missing')
                if platform=='mercado-libre-mx' and sold:raise ValueError('Sales are not missing')
            if d['reason']=='threshold_failure':
                sold=_number(d['sold_display']); reviews=_number(d['reviews_display'])
                try: rating=float(d['rating_display']) if isinstance(d['rating_display'],str) and re.fullmatch(r'\d(?:\.\d{1,2})?',d['rating_display'].strip()) else None
                except ValueError: rating=None
                if platform=='shein-us' and (not reviews or rating is None or not 0<=rating<=5 or (reviews[0]>=min_reviews and rating>=min_rating)): raise ValueError('Unproven threshold failure')
                if platform=='mercado-libre-mx' and (not sold or sold[0]>=min_sold): raise ValueError('Unproven threshold failure')
            continue
        ident,url=_identity(d['detail_url'],platform)
        if ident!=pid or d['reason'] is not None or d['visual_category_code']!=category or d['match_level'] not in ('同款','高度相似','类似竞品') or not isinstance(d['title'],str) or not d['title'].strip() or not isinstance(d['visual_features'],list) or not d['visual_features'] or any(not isinstance(v,str) or not v.strip() or disallowed_term(v) for v in d['visual_features']) or len(set(d['visual_features']))!=len(d['visual_features']):raise ValueError('Invalid qualifying evidence')
        sold=_number(d['sold_display']); reviews=_number(d['reviews_display'])
        try: rating=float(d['rating_display']) if isinstance(d['rating_display'],str) and re.fullmatch(r'\d(?:\.\d{1,2})?',d['rating_display'].strip()) else None
        except ValueError: rating=None
        if platform=='shein-us' and (not reviews or reviews[0]<min_reviews or rating is None or rating<min_rating or rating>5):raise ValueError('Insufficient SHEIN metrics')
        if platform=='mercado-libre-mx' and (not sold or sold[0]<min_sold):raise ValueError('Insufficient Mercado sales')
        c=pool[pid]; candidates.append({'platform':platform,'item_id':pid,'title':d['title'],'url':url,'raw_url':d['detail_url'],'query':queries[min(c['hits'])],'position':c['organic'] or c['ad'],'is_ad':c['organic'] is None,'sold_display':d['sold_display'],'sold_lower_bound':sold[0] if sold else None,'sold_upper_bound':None if not sold or sold[1] else sold[0],'review_count':reviews[0] if reviews and not reviews[1] else None,'reviews_display':d['reviews_display'],'rating':rating,'image_url':c['card']['thumbnail_url'],'visual_category':d['visual_category_code'],'visual_features':'；'.join(d['visual_features']),'observed_at':detail_time.isoformat() if detail_time is not None else observed_at,'observation_time_scope':'detail' if detail_time is not None else 'batch','selection_reason':d['match_level'],'evidence_ids':['collector']})
        if len(candidates)==result_limit and index!=len(details)-1:raise ValueError('Results exceed requested limit')
    if not require_complete:
        return
    if len(candidates)<result_limit and len(details)!=len(recurrent):raise ValueError('Incomplete detail review')
    digest=hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    status = ('complete' if len(candidates) >= result_limit else 'partial' if candidates else 'empty') if staged_reason is not None else ('complete' if candidates else 'empty')
    result={'schema_version':'1','status':status,'candidates':candidates,'evidence':[{'id':'collector','source':'collector:sha256:'+digest,'observed_at':observed_at}]}
    if status == 'partial':result['reason']=staged_reason
    if not candidates:result['reason']=staged_reason or 'No qualifying candidates in observed pool.'
    validate_result('research.bestseller',result)
    return result

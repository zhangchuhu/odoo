"""Pure selection rules for the versioned card-sales research strategy."""
import re
from decimal import Decimal, InvalidOperation


def parse_sales(text):
    """Return the provable integer lower bound of a product-sales label."""
    if not isinstance(text, str) or len(text)>100: return None
    match=re.fullmatch(r'\s*\+?\s*(\d{1,3}(?:\.\d{3})+|\d+(?:[.,]\d{1,2})?)(?:\s*(mil))?\s+vendidos?\s*',text,re.IGNORECASE)
    if not match:return None
    try:
        number=match.group(1)
        if re.fullmatch(r'\d{1,3}(?:\.\d{3})+',number):number=number.replace('.','')
        elif ',' in number or '.' in number:
            if not match.group(2):return None
            number=number.replace(',','.')
        value=Decimal(number)
        if match.group(2):value*=1000
        if value>1000000000:return None
        return int(value)
    except (InvalidOperation,ValueError):return None


def resolve_sales(labels, *, include_plus_hundred=False):
    if not isinstance(labels,list):raise ValueError('Sales observations required')
    intervals=[]
    for text in labels:
        value=parse_sales(text)
        if value is None:continue
        if include_plus_hundred and value==100 and is_plus_hundred(text):value=101
        lower_bound_only=text.lstrip().startswith('+') or bool(re.search(r'\bmil\b',text,re.IGNORECASE))
        intervals.append((value,None if lower_bound_only else value))
    if not intervals:return {'status':'missing','lower_bound':None}
    lower=max(item[0] for item in intervals)
    upper=min((item[1] for item in intervals if item[1] is not None),default=None)
    if upper is not None and lower>upper:return {'status':'conflict','lower_bound':None}
    return {'status':'known','lower_bound':intervals[-1][0]}


def is_plus_hundred(text):
    return isinstance(text,str) and bool(re.fullmatch(r'\s*\+\s*100\s+vendidos?\s*',text,re.IGNORECASE))


def rank_per_query(observations, limit=30, *, include_plus_hundred=False):
    if not isinstance(observations,list) or type(limit) is not int or not 1<=limit<=30:
        raise ValueError('Invalid observed candidates')
    unique={}
    for item in observations:
        if not isinstance(item,dict) or not isinstance(item.get('product_id'),str) or not item['product_id']:
            raise ValueError('Invalid product identity')
        sold=item.get('sales_lower_bound')
        if item.get('sales_status')!='known' or type(sold) is not int or sold<0:continue
        previous=unique.get(item['product_id'])
        if previous is None or _later(item,previous):
            unique[item['product_id']]=dict(item,first_seen=min(item['first_seen'],previous['first_seen']) if previous else item['first_seen'])
    return sorted((v for v in unique.values() if v['sales_lower_bound']>100 or (include_plus_hundred and v['sales_lower_bound']==100 and is_plus_hundred(v.get('sales_text')))),key=lambda x:(-x['sales_lower_bound'],x['first_seen'],x['product_id']))[:limit]


def merge_candidates(query_results):
    unique={}
    for query,rows in query_results:
        for row in rows:
            pid=row['product_id']
            if pid not in unique:unique[pid]=dict(row,search_sources=[])
            if query not in unique[pid]['search_sources']:unique[pid]['search_sources'].append(query)
            if _later(row,unique[pid]):
                previous=unique[pid];sources=previous['search_sources']
                unique[pid]=dict(row,search_sources=sources,first_seen=min(previous['first_seen'],row['first_seen']))
    return sorted(unique.values(),key=lambda x:(-x['sales_lower_bound'],x['first_seen'],x['product_id']))


def _later(current, previous):
    # Persisted row order is controlled by the backend, unlike browser clocks.
    a,b=current.get('observed_order'),previous.get('observed_order')
    if type(a) is int and type(b) is int:return a>b
    return current.get('observed_at','')>previous.get('observed_at','')


def classify_detail(sales_lower_bound, threshold, *, sales_text=None, include_plus_hundred=False):
    if type(threshold) is not int or threshold<0:raise ValueError('Invalid task threshold')
    plus_hundred=include_plus_hundred and sales_lower_bound==100 and is_plus_hundred(sales_text)
    if type(sales_lower_bound) is not int or (sales_lower_bound<=100 and not plus_hundred):return 'exclude'
    effective=101 if plus_hundred else sales_lower_bound
    return 'threshold_met' if effective>=threshold else 'supplemental'


def assess_visual(source, candidate, source_secondary, candidate_secondary, source_style, candidate_style):
    from .visual_v2_rules import REQUIRED, FLEXIBLE, _value, compare_primary
    fields=REQUIRED+FLEXIBLE
    if not isinstance(source,dict) or not isinstance(candidate,dict) or set(source)!=set(fields) or set(candidate)!=set(fields):
        raise ValueError('Complete visual attributes required')
    compare_primary(source,candidate)  # Reuse bounded attribute/type validation.
    for value in (source_secondary,candidate_secondary,source_style,candidate_style):
        if value is not None and (not isinstance(value,(list,tuple,set)) or any(not isinstance(x,str) or not x.strip() for x in value)):
            raise ValueError('Invalid visual evidence')
    matches=[];differences=[];unknown=[]
    for name in fields:
        a,b=_value(source[name]),_value(candidate[name])
        if isinstance(source[name],dict) and source[name].get('status')=='not_applicable':continue
        if a is None or b is None:unknown.append(name);continue
        if name=='components' and isinstance(a,list) and isinstance(b,list):a,b=sorted(a),sorted(b)
        (matches if a==b else differences).append(name)
    if any(x in differences for x in ('category','components')):level='reject'
    elif any(x in unknown for x in ('category','components')):level='review'
    else:
        primary_matches=sum(x in matches for x in fields if x not in ('category','components'))
        secondary_matches=len(set(source_secondary or ()) & set(candidate_secondary or ()))
        style_matches=len((set(source_style or ()) & set(candidate_style or ())) - {'modern','simple','all_occasions','everyday'})
        if not any(x in matches for x in ('length','shoulders','silhouette','fit','sleeve_length')) and unknown:level='review'
        elif primary_matches>=3 and secondary_matches:level='highly_similar'
        elif primary_matches>=1:level='competitor'
        else:level='broad_competitor'
    return dict(level=level,matches=matches,differences=differences,unknown=unknown,
                primary_matches=sum(x in matches for x in fields if x not in ('category','components')),
                secondary_matches=len(set(source_secondary or ()) & set(candidate_secondary or ())),
                style_matches=len((set(source_style or ()) & set(candidate_style or ())) - {'modern','simple','all_occasions','everyday'}),
                conflicts=len(differences))


def rank_visual(rows, *, expected):
    if not isinstance(rows,list) or type(expected) is not int or expected<0 or len(rows)!=expected or expected>30:
        raise ValueError('Visual decisions are incomplete')
    priority={'highly_similar':0,'competitor':1,'broad_competitor':2,'review':3,'reject':4}
    if any(row.get('level') not in priority for row in rows):raise ValueError('Invalid visual level')
    return sorted(rows,key=lambda x:(priority[x['level']],-x['primary_matches'],-x['secondary_matches'],
                   -x['style_matches'],x['conflicts'],-x['sales_lower_bound'],x['identity']))


def next_step(*,queries_complete,known,query_count,plan_exhausted,candidates_frozen,detail_remaining,visual_reserved,visual_decisions):
    if (any(type(x) is not bool for x in (queries_complete,plan_exhausted,candidates_frozen))
            or any(type(x) is not int or x<0 for x in (known,query_count,detail_remaining,visual_reserved,visual_decisions))
            or not 3<=query_count<=9 or visual_reserved>30 or visual_decisions>visual_reserved):
        raise ValueError('Invalid discovery state')
    if not candidates_frozen:
        if not queries_complete:return {'action':'search'}
        if known<100 and query_count<9 and not plan_exhausted:return {'action':'expand'}
        return {'action':'freeze'}
    if visual_decisions<visual_reserved:return {'action':'visual'}
    if visual_reserved<30 and detail_remaining:return {'action':'detail'}
    return {'action':'finish'}

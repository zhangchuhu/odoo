"""Conservative ranking of observed autocomplete terms for homepage research."""
import re
import unicodedata

from .query_terms import contains_color_or_size
from .visual_profile import _CATEGORY_ANCHORS

_CATEGORIES = {
    'womens_skirt_set': ({'conjunto', 'traje'}, {'falda'}),
    'womens_pants_set': ({'conjunto', 'traje'}, {'pantalon', 'pantalones','pants','trousers'}),
    'womens_shorts_set': ({'conjunto', 'traje'}, {'short', 'shorts'}),
    'dress': ({'vestido'}, set()),
}
_PRODUCT_TERMS={'falda','skirt','pantalon','pantalones','pants','trousers','short','shorts','vestido','dress','zapatos','shoes','abrigo','coat'}
_PRODUCT_ALIASES={'dress':{'dress'},'formal_gown':{'dress'},'pants':{'pants','trousers'},
                  'skirt':{'skirt'},'shoes':{'shoes'},'coat':{'coat'}}


def _normalized(value):
    if not isinstance(value,str) or not 3 <= len(value.strip()) <= 120:
        raise ValueError('Invalid search term')
    if any(ord(c)<32 or ord(c)==127 for c in value) or '://' in value or '/' in value or '\\' in value:
        raise ValueError('Invalid search term')
    value=unicodedata.normalize('NFKD',value.casefold())
    value=''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[^\W_]+',value,flags=re.UNICODE))


def rank_suggestions(category, observed, image_features):
    if not isinstance(observed,list) or not isinstance(image_features,list):
        raise ValueError('Invalid suggestion observations')
    if category in _CATEGORIES:core, component=_CATEGORIES[category]
    elif category in _CATEGORY_ANCHORS:core, component=_CATEGORY_ANCHORS[category][0],set()
    else:return []
    features=set(' '.join(_normalized(x) for x in image_features).split())
    seen=set(); ranked=[]
    for row in observed:
        if not isinstance(row,dict) or type(row.get('position')) is not int or row['position']<1:
            raise ValueError('Invalid suggestion position')
        text=row.get('text'); norm=_normalized(text); words=set(norm.split())
        if norm in seen:continue
        seen.add(norm)
        if not words & core or contains_color_or_size(text,'es-MX'):continue
        if words & (_PRODUCT_TERMS-component-core-_PRODUCT_ALIASES.get(category,set())):continue
        specificity=1 if component and words & component else 0
        score=len(words & features)
        ranked.append(((-specificity,-score,row['position']),{'text':text.strip(),'source':'platform_suggestion','position':row['position']}))
    return [row for _,row in sorted(ranked,key=lambda x:x[0])]


def rank_shein_suggestions(category, observed, image_features):
    """Rank visibly observed English suggestions without changing Mercado rules."""
    if not isinstance(observed,list) or not isinstance(image_features,list):raise ValueError('Invalid suggestion observations')
    if category not in _CATEGORY_ANCHORS:return []
    core=_CATEGORY_ANCHORS[category][1]
    components={'womens_skirt_set':{'skirt'},'womens_pants_set':{'pants','trousers'},'womens_shorts_set':{'short','shorts'}}
    component=components.get(category,set())
    features=set(' '.join(_normalized(x) for x in image_features).split())
    seen=set();ranked=[]
    for row in observed:
        if not isinstance(row,dict) or type(row.get('position')) is not int or row['position']<1:raise ValueError('Invalid suggestion position')
        text=row.get('text');norm=_normalized(text);words=set(norm.split())
        if norm in seen:continue
        seen.add(norm)
        if not words & core or contains_color_or_size(text,'en-US'):continue
        if words & (_PRODUCT_TERMS-component-core-_PRODUCT_ALIASES.get(category,set())):continue
        specificity=1 if component and words & component else 0
        ranked.append(((-specificity,-len(words & features),row['position']),{'text':text.strip(),'source':'platform_suggestion','position':row['position']}))
    return [row for _,row in sorted(ranked,key=lambda x:x[0])]


def initial_queries(anchor, ranked, fallback):
    if not isinstance(ranked,list) or not isinstance(fallback,list):raise ValueError('Invalid query sources')
    seen={_normalized(anchor)}; result=[{'text':anchor.strip(),'source':'category_anchor'}]
    for source,values in (('platform_suggestion',ranked),('image_fallback',fallback)):
        for value in values:
            if source=='platform_suggestion' and (not isinstance(value,dict) or value.get('source')!='platform_suggestion'
                    or type(value.get('position')) is not int or value['position']<1):
                raise ValueError('Verified ranked suggestion required')
            if source=='image_fallback' and not isinstance(value,str):raise ValueError('Invalid image fallback')
            text=value.get('text') if isinstance(value,dict) else value
            norm=_normalized(text)
            if norm in seen or contains_color_or_size(text,'es-MX'):continue
            seen.add(norm);item={'text':text.strip(),'source':source}
            if source=='platform_suggestion':item['position']=value['position']
            result.append(item)
            if len(result)==3:return result
    raise ValueError('Insufficient verified or source-derived search terms')


def verify_sealed_queries(selected,ranked,fallback):
    if (not isinstance(selected,list) or len(selected)!=3 or not isinstance(selected[0],dict)
            or set(selected[0])!={'text','source'} or selected[0].get('source')!='category_anchor'):
        return False
    try:return selected==initial_queries(selected[0]['text'],ranked,fallback)
    except (ValueError,TypeError,KeyError):return False

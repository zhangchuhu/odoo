"""Bounded message adapter; transport and OS-level isolation are required.

This module does not authenticate callers and must not be exposed directly.
"""
import base64
import json

_MAX_MESSAGE = 23 * 1024 * 1024
_REJECT = b'{"accepted":false}'
_ACCEPT = b'{"accepted":true}'
_PLAN_GUIDANCE = '检查 category 与 visual_profile.category_code 一致；queries 与 query_seeds 一致，三条分别使用具体子类、构造特征、风格或场景，不能包含颜色尺码；校验其他字段及枚举。'
_PLAN_REJECT = json.dumps({'accepted':False,'code':'invalid_plan','guidance':_PLAN_GUIDANCE}, ensure_ascii=False, separators=(',',':')).encode()


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out: raise ValueError('Duplicate field')
        out[key] = value
    return out


def _valid_plan_input(data):
    """Validate only caller-controlled syntax, before any fenced operation."""
    from .collector_import import _CATEGORIES, disallowed_term
    from .visual_profile import validate_profile
    required={'platform','queries','category','result_limit','source_artifact_id','viewed_source_sha256'}
    if set(data) not in (required,required | {'visual_profile'},required | {'visual_profile','strategy'},required | {'visual_profile','strategy','design_profile'}): return False
    if 'strategy' in data and (data['strategy'] not in ('staged-v1','visual-v2','card-visual-v3') or data.get('platform')!='mercado-libre-mx'): return False
    if ('design_profile' in data)!=(data.get('strategy') in ('visual-v2','card-visual-v3')): return False
    queries=data.get('queries')
    if (data.get('platform') not in ('shein-us','mercado-libre-mx') or data.get('category') not in _CATEGORIES
        or type(data.get('result_limit')) is not int or data['result_limit'] < 1
        or not isinstance(data.get('source_artifact_id'),str) or not isinstance(data.get('viewed_source_sha256'),str)
        or not isinstance(queries,list) or len(queries)!=3 or len(set(queries))!=3
        or any(not isinstance(q,str) or not q.strip() or len(q)>200 or disallowed_term(q) for q in queries)):
        return False
    if 'visual_profile' in data:
        try: profile=validate_profile(data['visual_profile'],data['platform'])
        except (TypeError,ValueError,KeyError): return False
        if profile != data['visual_profile'] or profile['category_code'] != data['category'] or profile['query_seeds'] != queries: return False
    if data.get('strategy') in ('visual-v2','card-visual-v3'):
        from .visual_v2_rules import validate_design_profile
        try: validate_design_profile(data['design_profile'],data['category'])
        except (TypeError,ValueError,KeyError): return False
    return True


def handle_message(bridge, raw):
    operation = None
    try:
        if type(raw) is not bytes or len(raw) > _MAX_MESSAGE:
            return _REJECT
        message = json.loads(raw, object_pairs_hook=_unique)
        if not isinstance(message,dict) or set(message)!={'operation','data'} or not isinstance(message['data'],dict):
            return _REJECT
        operation=message['operation']
        if operation not in ('visual_candidate_status','submit_visual_verdict','window_blocked','page_blocked','binding_status','seal_expansion','prior_lease_status','plan','submit','submit_staged','manifest','image','image_saved','finish','reconcile_image','reconcile_evidence'):
            return _REJECT
        data=dict(message['data'])
        if operation == 'plan' and not _valid_plan_input(data): return _PLAN_REJECT
        for key in ('file','screenshot'):
            if key in data:
                if not isinstance(data[key],str) or len(data[key]) > 12*1024*1024: return _REJECT
                data[key]=base64.b64decode(data[key],validate=True)
                if not 0 < len(data[key]) <= 8*1024*1024: return _REJECT
        result=bridge.call(bridge.request_id,operation,data)
        if operation=='prior_lease_status' and isinstance(result,dict) and set(result)=={'accepted','inactive'} and result['accepted'] is True and type(result['inactive']) is bool:
            return json.dumps(result,separators=(',',':')).encode()
        if operation=='binding_status' and isinstance(result,dict) and result.get('accepted') is True and isinstance(result.get('binding'),dict):
            return json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
        if operation in ('visual_candidate_status','submit_visual_verdict') and result.get('accepted') is True and isinstance(result.get('visual'),dict):
            return json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
        return _ACCEPT if result == {'accepted':True} else _REJECT
    except Exception:
        if operation == 'window_blocked':
            bridge.window_blocker_rejected = True
        return _REJECT

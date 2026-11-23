"""Experimental tab checks; NOT wired into the production collector.

The background Codex currently cannot access the Chrome browser inventory, so
neither a trusted session id nor a trustworthy window id is available here.
This module must not be used as proof of readiness or to enable automatic claims.
"""
from .domain import canonical_hash


def bind_tab(request_id, plan, observed):
    if (not isinstance(request_id, str) or not request_id or not isinstance(plan,dict)
            or not isinstance(observed,dict) or observed.get('platform') != plan.get('platform')
            or plan.get('platform') not in ('mercado-libre-mx','shein-us')
            or any(type(observed.get(k)) is not int or observed[k] <= 0 for k in ('tab_id','window_id'))):
        raise ValueError('Invalid observed tab binding')
    if not isinstance(observed.get('browser_session'),str) or not observed['browser_session'] or observed.get('collector_tab_id') != observed['tab_id']:
        raise ValueError('Collector tab or browser session mismatch')
    value={'request_id':request_id,'platform':plan['platform'],'tab_id':observed['tab_id'],
           'window_id':observed['window_id'],'plan_digest':canonical_hash(plan)}
    value['browser_session']=observed['browser_session']
    value['collector_tab_id']=observed['collector_tab_id']
    return {**value,'digest':canonical_hash(value)}


def verify_tab(binding, request_id, plan, observed, query_index):
    try:
        value={k:v for k,v in binding.items() if k != 'digest'}
        return (binding['digest']==canonical_hash(value) and value['request_id']==request_id
                and value['plan_digest']==canonical_hash(plan) and value['platform']==plan['platform']
                and type(query_index) is int and 0 <= query_index < len(plan['queries'])
                and observed['tab_id']==value['tab_id'] and observed['window_id']==value['window_id']
                and observed['platform']==value['platform'] and observed['query']==plan['queries'][query_index]
                and observed.get('browser_session')==value['browser_session'] and observed.get('collector_tab_id')==value['tab_id'])
    except (KeyError, TypeError, ValueError): return False

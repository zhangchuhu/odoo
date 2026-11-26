"""Deterministic, bounded candidate ordering for staged marketplace research.

This policy does not perform searches or attest that a candidate qualifies.
"""

import hashlib
import json
import re
from datetime import datetime, timezone

STAGE_SIZE = 20
TOTAL_DETAILS = 60


def elapsed_seconds(db, key, now=None):
    """Use the first verified search observation, including inherited progress."""
    now=now or datetime.now(timezone.utc)
    rows=db.execute('SELECT body FROM accepted_receipts WHERE request_id=? ORDER BY id',(key[0],)).fetchall()
    stamps=[]
    for (body,) in rows:
        receipt=json.loads(body)
        if receipt.get('plan_digest')==key[1] and receipt.get('lease_epoch')==key[2] and receipt.get('query_index')==0:
            stamps.append(receipt.get('observed_at'))
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inherited_query_cards'").fetchone():
        stamps += [row[0] for row in db.execute('SELECT observed_at FROM inherited_query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=0',key)]
    if not stamps:return 0
    try:
        parsed=[datetime.fromisoformat(s.replace('Z','+00:00')) for s in stamps]
        if any(t.utcoffset() is None or t>now for t in parsed): raise ValueError('Invalid collection start')
        return (now-min(parsed)).total_seconds()
    except (AttributeError,TypeError,ValueError) as error:
        raise ValueError('Invalid collection start') from error


def stage_allowances(repeated_used, single_used):
    if type(repeated_used) is not int or not 0 <= repeated_used <= STAGE_SIZE:
        raise ValueError('Invalid repeated candidate count')
    second = STAGE_SIZE + (STAGE_SIZE - repeated_used)
    if type(single_used) is not int or not 0 <= single_used <= second:
        raise ValueError('Invalid single candidate count')
    return STAGE_SIZE, second, TOTAL_DETAILS - repeated_used - single_used


def staged_order(pool, category_terms=()):
    if not isinstance(pool, dict):
        raise ValueError('Invalid candidate pool')
    terms = tuple(str(term).casefold().strip() for term in category_terms if isinstance(term, str) and term.strip())

    def order(entry):
        identity, value = entry
        hits, organic, ad = value['hits'], value['organic'], value['ad']
        if not isinstance(identity, str) or not isinstance(hits, set) or not hits:
            raise ValueError('Invalid candidate identity')
        rank = organic if organic is not None else ad
        if type(rank) is not int or rank < 1:
            raise ValueError('Invalid candidate rank')
        title = str(value.get('card', {}).get('title') or '').casefold()
        relevance = sum(term in title for term in terms)
        return (-len(hits), organic is None, rank, -relevance, identity)

    ordered = sorted(pool.items(), key=order)
    return ([key for key, value in ordered if len(value['hits']) >= 2],
            [key for key, value in ordered if len(value['hits']) == 1])


def next_action(initial_pool, reviewed, qualified, target, expanded_pool, *, elapsed_seconds=0):
    """Return one action from persisted observations; never mutate a checkpoint.

    The caller must authenticate every checkpoint and verify candidate evidence.
    ``expanded_pool=None`` means expansion has not yet been observed.
    """
    if (not isinstance(reviewed, list) or type(qualified) is not int or qualified < 0
            or qualified > len(reviewed) or type(target) is not int or target < 1
            or type(elapsed_seconds) not in (int,float) or not 0 <= elapsed_seconds < float('inf')):
        raise ValueError('Invalid progress')
    repeated, single = staged_order(initial_pool)
    first = repeated[:STAGE_SIZE]
    first_count = min(len(first), STAGE_SIZE)
    second_budget = STAGE_SIZE + STAGE_SIZE - first_count
    second = single[:second_budget]
    initial_order = [('repeated', x) for x in first] + [('single', x) for x in second]
    if reviewed[:min(len(reviewed), len(initial_order))] != initial_order[:len(reviewed)]:
        raise ValueError('Invalid initial checkpoint order')
    if len(reviewed) < len(initial_order):
        if qualified >= target:
            return {'stage': 'finished', 'action': 'finish', 'reason': 'target_reached'}
        if elapsed_seconds >= 3600:
            return {'stage': 'finished', 'action': 'finish', 'reason': 'time_budget_exhausted'}
        stage, identity = initial_order[len(reviewed)]
        return {'stage': stage, 'action': 'review', 'identity': identity}
    if expanded_pool is None:
        if len(reviewed) != len(initial_order):
            raise ValueError('Expansion observations missing')
        if qualified >= target:
            return {'stage': 'finished', 'action': 'finish', 'reason': 'target_reached'}
        if elapsed_seconds >= 3600:
            return {'stage': 'finished', 'action': 'finish', 'reason': 'time_budget_exhausted'}
        return {'stage': 'expanded', 'action': 'search'}
    repeated_expanded, single_expanded = staged_order(expanded_pool)
    seen = {identity for _, identity in initial_order}
    budget = TOTAL_DETAILS - len(initial_order)
    extra = [('expanded', identity) for identity in repeated_expanded + single_expanded if identity not in seen][:budget]
    remaining = reviewed[len(initial_order):]
    if remaining != extra[:len(remaining)]:
        raise ValueError('Invalid expanded checkpoint order')
    if qualified >= target:
        return {'stage': 'finished', 'action': 'finish', 'reason': 'target_reached'}
    if elapsed_seconds >= 3600:
        return {'stage': 'finished', 'action': 'finish', 'reason': 'time_budget_exhausted'}
    if len(remaining) < len(extra):
        return {'stage': 'expanded', 'action': 'review', 'identity': extra[len(remaining)][1]}
    reason = 'budget_exhausted' if len(initial_order) + len(extra) >= TOTAL_DETAILS else 'candidates_exhausted'
    return {'stage': 'finished', 'action': 'finish', 'reason': reason}


def initialize_expansions(db):
    db.execute('CREATE TABLE IF NOT EXISTS staged_expansions ('
               'request_id TEXT NOT NULL, plan_digest TEXT NOT NULL, lease_epoch INTEGER NOT NULL, '
               'body TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(request_id,plan_digest,lease_epoch))')


def save_expansion(db, key, initial_pool, reviewed, qualified, target, queries, initial_queries, *, fence):
    """Append one immutable expansion to a caller-owned transaction and lease."""
    from .collector_import import disallowed_term
    if (not isinstance(key, tuple) or len(key) != 3 or not isinstance(key[0], str) or not key[0]
            or not isinstance(key[1], str) or not re.fullmatch(r'[a-f0-9]{64}',key[1])
            or type(key[2]) is not int or key[2] < 1 or not callable(fence)):
        raise ValueError('Invalid expansion binding')
    if (not isinstance(initial_queries,list) or len(initial_queries)!=3 or not isinstance(queries,list)
            or not 1<=len(queries)<=3 or any(not isinstance(q,str) or not q.strip() or len(q)>200
                                             or disallowed_term(q) for q in initial_queries+queries)):
        raise ValueError('Invalid expansion queries')
    normalized=[' '.join(q.casefold().split()) for q in initial_queries+queries]
    if len(normalized)!=len(set(normalized)):
        raise ValueError('Duplicate expansion query')
    if next_action(initial_pool,reviewed,qualified,target,None).get('action')!='search':
        raise ValueError('Expansion is not ready')
    if fence() is not True:
        raise ValueError('Expansion lease fence failed')
    body=json.dumps({'queries':queries},ensure_ascii=False,sort_keys=True,separators=(',',':'))
    digest=hashlib.sha256(body.encode()).hexdigest()
    row=db.execute('SELECT body,digest FROM staged_expansions WHERE request_id=? AND plan_digest=? AND lease_epoch=?',key).fetchone()
    if row:
        if row != (body,digest): raise ValueError('Expansion plan already sealed')
    else:
        db.execute('INSERT INTO staged_expansions VALUES (?,?,?,?,?)',(*key,body,digest))
    return digest


def load_expansion(db, key, *, fence):
    if not callable(fence) or fence() is not True:
        raise ValueError('Expansion lease fence failed')
    row=db.execute('SELECT body,digest FROM staged_expansions WHERE request_id=? AND plan_digest=? AND lease_epoch=?',key).fetchone()
    if not row:
        return None
    body,digest=row
    if hashlib.sha256(body.encode()).hexdigest()!=digest:
        raise ValueError('Expansion checkpoint changed')
    result=json.loads(body)
    if not isinstance(result,dict) or set(result)!={'queries'} or not isinstance(result['queries'],list) or not 1<=len(result['queries'])<=3:
        raise ValueError('Invalid expansion checkpoint')
    return result


def inherit_expansion(db, old_key, new_key, *, fence, reconcile):
    """Transfer a checkpoint only after the caller verifies the previous lease."""
    if (not isinstance(old_key,tuple) or not isinstance(new_key,tuple) or len(old_key)!=3 or len(new_key)!=3
            or old_key[:2]!=new_key[:2] or type(old_key[2]) is not int or type(new_key[2]) is not int
            or old_key[2]<1 or new_key[2]<=old_key[2] or not callable(reconcile) or reconcile() is not True):
        raise ValueError('Prior expansion is not reconciled')
    value=load_expansion(db,old_key,fence=fence)
    if value is None: raise ValueError('Prior expansion missing')
    row=db.execute('SELECT body,digest FROM staged_expansions WHERE request_id=? AND plan_digest=? AND lease_epoch=?',old_key).fetchone()
    current=db.execute('SELECT body,digest FROM staged_expansions WHERE request_id=? AND plan_digest=? AND lease_epoch=?',new_key).fetchone()
    if current and current!=row: raise ValueError('Expansion recovery conflict')
    if not current: db.execute('INSERT INTO staged_expansions VALUES (?,?,?,?,?)',(*new_key,*row))
    return row[1]

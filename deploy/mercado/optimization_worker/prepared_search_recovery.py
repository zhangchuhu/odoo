"""Reuse confirmed marketplace observations under an independently authorized lease."""
import hashlib
import json
import sqlite3
import re
from pathlib import Path
from datetime import datetime, timezone
from contextlib import closing
from .domain import canonical_hash
from .collector_import import _identity, _observation_time, validate_evidence_prefix


def _validated_details(rows, groups, record, plan, observed):
    from .collector_cli import collector_id, task_config
    if not rows:
        return []
    now = datetime.now(timezone.utc)
    source = collector_id(record['request_id'])
    config = task_config(record['context']['snapshot'], plan)
    data = {'task_record_id': source, 'platform': plan['platform'], 'details': [],
            'queries': [dict(query=q, observations=cards) for q, cards in zip(plan['queries'], groups)]}
    accepted = []
    for index, body in rows:
        try:
            detail = json.loads(body)
            if index != len(accepted) or not isinstance(detail, dict):
                break
            # Legacy details have no timestamp. They become reachable only after
            # all three original groups were accepted, so the original query
            # observations give a conservative lower bound on their freshness.
            since = _observation_time(detail['observed_at']) if 'observed_at' in detail else min(map(_observation_time, observed))
            if not 0 <= (now - since).total_seconds() < 86400:
                break
            data['details'].append(detail)
            validate_evidence_prefix(data, source, queries=plan['queries'], category=plan['category'],
                                     observed_at=now.isoformat(), **config)
        except (AttributeError, TypeError, ValueError, KeyError):
            break
        accepted.append((index, body))
    return accepted


def read_prepared_search(ledger_path, record, claim):
    if not isinstance(record, dict) or not isinstance(claim, dict):
        raise ValueError('Original request and claim required')
    binding = record.get('collector_plan') or {}
    signed = dict(binding); digest = signed.pop('digest', None)
    plan = binding.get('plan') or {}; queries = plan.get('queries'); platform = plan.get('platform')
    rid = record.get('request_id'); epoch = claim.get('lease_epoch')
    manual_pause = (record.get('state') == 'waiting'
                    and record.get('reason') in ('captcha_required', 'login_required', 'page_unreadable')
                    and record.get('resume_state') in ('prepared', 'candidate_images_pending'))
    recoverable = (manual_pause or record.get('state') == 'prepared' or
                   (record.get('state') == 'unknown' and record.get('resume_state') == 'prepared'))
    retired_ok = not record.get('retired') or record.get('retirement_reason') == 'original_worker_lease_inactive'
    protected_writes = ('collector_pending','collector_evidence','collector_artifact_id','candidate_uploads','gallery_manifests','uploads','result')
    if (not recoverable or not retired_ok or (not manual_pause and any(record.get(k) for k in protected_writes)) or record.get('task_type') != 'research.bestseller'
        or claim.get('task_type') != 'research.bestseller' or record.get('payload') != claim.get('payload')
        or not isinstance(rid,str) or not rid or binding.get('request_id') != rid
        or digest != canonical_hash(signed) or any(binding.get(k) != claim.get(k) for k in ('company_id','snapshot_id','input_hash'))
        or type(epoch) is not int or epoch < 1 or platform not in ('mercado-libre-mx','shein-us')
        or not isinstance(queries,list) or len(queries)!=3 or any(not isinstance(q,str) or not q.strip() for q in queries)):
        raise ValueError('Original signed search plan mismatch')
    path=Path(ledger_path)
    if not path.is_file(): raise ValueError('Search ledger missing')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('BEGIN')
        receipts=[json.loads(r[0]) for r in db.execute('SELECT body FROM accepted_receipts WHERE request_id=? ORDER BY id',(rid,))]
        rows=db.execute('SELECT query_index,body FROM query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index',(rid,digest,epoch)).fetchall()
        current=[r for r in receipts if r.get('lease_epoch')==epoch and r.get('plan_digest')==digest]
        expected=list(range(len(rows)))
        if not rows or len(rows)>3 or [r.get('query_index') for r in current] not in (expected, list(range(min(3,len(rows)+1)))) or [i for i,_ in rows] != expected: raise ValueError('Incomplete or duplicate search checkpoints')
        identity_fields=('session_id','tab_id','window_id','collector_tab_id')
        first=current[0]
        if (any(not isinstance(first.get(k),int) or isinstance(first.get(k),bool) or first[k]<=0 for k in ('tab_id','window_id','collector_tab_id')) or first['tab_id']!=first['collector_tab_id'] or not isinstance(first.get('session_id'),str) or not re.fullmatch('[a-f0-9]{32}',first['session_id'])): raise ValueError('Invalid historical browser identity')
        groups=[]
        observed = []
        inherited = {}
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'inherited_query_cards' in tables:
            inherited = {idx: (stamp, json.loads(body)) for idx, stamp, body in db.execute(
                'SELECT query_index,observed_at,body FROM inherited_query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=?',
                (rid, digest, epoch))}

        for i,(_,body) in enumerate(rows):
            receipt=current[i]
            if receipt.get('request_id')!=rid or receipt.get('platform')!=platform or receipt.get('query')!=queries[i] or any(receipt.get(k)!=first.get(k) for k in identity_fields): raise ValueError('Historical receipt mismatch')
            cards=json.loads(body); seen=set()
            if not isinstance(cards,list) or not 30<=len(cards)<=50: raise ValueError('Invalid historical card count')
            for rank,card in enumerate(cards,1):
                if not isinstance(card,dict) or card.get('query')!=queries[i] or card.get('rank')!=rank or type(card.get('is_ad')) is not bool or not isinstance(card.get('title'),str) or not card['title']: raise ValueError('Invalid historical card')
                try: product,_=_identity(card.get('url'),platform)
                except Exception as exc: raise ValueError('Invalid historical product') from exc
                if product!=card.get('product_id') or product in seen: raise ValueError('Historical product mismatch')
                seen.add(product)
            groups.append(cards)
            source = inherited.get(i)
            observed.append(source[0] if source and cards == source[1] else receipt.get('observed_at'))
        details = []
        if len(groups) == 3 and 'collector_details' in tables:
            rows = db.execute('SELECT detail_index,body FROM collector_details WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY detail_index',
                              (rid, digest, epoch)).fetchall()
            details = _validated_details(rows, groups, record, plan, observed)
    raw=json.dumps(groups,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    return {'request_id':rid,'original_epoch':epoch,'plan_digest':digest,'cards':groups,'observed_at': observed, 'details': details, 'cards_digest':hashlib.sha256(raw).hexdigest(),'requires_new_browser_binding':True}


def restore_search_checkpoint(ledger, store, runs, client, request_id, claim, renew):
    """Import only business evidence. New-session receipts must still be collected."""
    from .collector_cli import bind_plan

    target = store.inspect(request_id)
    if target.get('retired') or target.get('state') != 'prepared' or claim['lease_epoch'] < 2:
        return False
    fixed_plan = (target.get('collector_plan') or {}).get('plan')
    candidates = []
    for run in runs:
        prior = run.get('claim', {})
        rid = run.get('request_id')
        if (not rid or rid == request_id or not run.get('done')
                or not 0 < prior.get('lease_epoch', 0) < claim['lease_epoch']
                or any(prior.get(k) != claim[k] for k in ('id', 'company_id', 'snapshot_id', 'input_hash', 'task_type'))):
            continue
        try:
            record = store.inspect(rid)
            old_plan = (record.get('collector_plan') or {}).get('plan')
            if fixed_plan is not None and fixed_plan != old_plan:
                continue
            evidence = read_prepared_search(ledger.path, record, prior)
            stamps = [datetime.fromisoformat(t.replace('Z', '+00:00')) for t in evidence['observed_at']]
            if any(not 0 <= (datetime.now(timezone.utc) - t).total_seconds() < 86400 for t in stamps):
                continue
        except (ValueError, AttributeError, TypeError):
            continue
        candidates.append((len(evidence['cards']), len(evidence['details']), prior['lease_epoch'], rid, old_plan, evidence))
    for _, _, source_epoch, rid, old_plan, evidence in sorted(candidates, key=lambda x: x[:3], reverse=True):
        params = {'task_id': claim['id'], 'lease_epoch': claim['lease_epoch'], 'request_id': request_id,
                  'source_request_id': rid, 'source_epoch': source_epoch}
        reply = client.request('POST', '/internal/meli-optimization/research-search-checkpoint',
                               dict(params, lease_token=claim['lease_token']))
        if reply == {'allowed': False}:
            continue
        if reply != dict(params, allowed=True, snapshot_id=claim['snapshot_id'], input_hash=claim['input_hash']):
            raise ValueError('Invalid checkpoint authorization receipt')
        digest = bind_plan(store, request_id, claim, old_plan)
        key = (request_id, digest, claim['lease_epoch'])
        with closing(sqlite3.connect(ledger.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT 1 FROM inherited_query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=?', key).fetchone()
            if existing:
                return True
            # Once collection starts its evidence takes precedence.
            if db.execute('SELECT 1 FROM accepted_receipts WHERE request_id=?', (request_id,)).fetchone():
                return False
            renew()
            for i, cards in enumerate(evidence['cards']):
                body = json.dumps(cards, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
                db.execute('INSERT INTO inherited_query_cards VALUES (?,?,?,?,?,?,?,?)',
                           (*key, i, rid, source_epoch, evidence['observed_at'][i], body))
            db.executemany('INSERT INTO inherited_details VALUES (?,?,?,?,?)',
                           [(*key, i, body) for i, body in evidence['details']])
        return True
    return False

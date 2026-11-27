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


def _validated_details(rows, groups, record, plan, observed, extra_queries=()):
    from .collector_cli import collector_id, task_config
    if not rows:
        return []
    now = datetime.now(timezone.utc)
    source = collector_id(record['request_id'])
    config = task_config(record['context']['snapshot'], plan)
    data = {'task_record_id': source, 'platform': plan['platform'], 'details': [],
            'queries': [dict(query=q, observations=cards) for q, cards in zip(plan['queries']+list(extra_queries), groups)]}
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
            if plan.get('strategy')=='staged-v1':
                from .collector_import import validate_staged_evidence_prefix
                try:
                    initial=dict(data,queries=data['queries'][:3])
                    validate_staged_evidence_prefix(initial,source,queries=plan['queries'],extra_queries=[],
                                                    category=plan['category'],observed_at=now.isoformat(),**config)
                except ValueError:
                    if not extra_queries: raise
                    validate_staged_evidence_prefix(data,source,queries=plan['queries'],extra_queries=list(extra_queries),
                                                    category=plan['category'],observed_at=now.isoformat(),**config)
            else:
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
                    and record.get('reason') in ('captcha_required', 'login_required', 'page_unreadable', 'browser_unavailable')
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
    if plan.get('strategy') == 'visual-v2':
        return _read_visual_v2(path, record, claim, binding)
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('BEGIN')
        staged=plan.get('strategy')=='staged-v1'
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        expansion=[]
        if staged and 'staged_expansions' in tables:
            from .collector_staged_policy import load_expansion
            saved=load_expansion(db,(rid,digest,epoch),fence=lambda:True)
            expansion=saved['queries'] if saved else []
        all_queries=queries+expansion
        receipts=[json.loads(r[0]) for r in db.execute('SELECT body FROM accepted_receipts WHERE request_id=? ORDER BY id',(rid,))]
        rows=db.execute('SELECT query_index,body FROM query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index',(rid,digest,epoch)).fetchall()
        current=[r for r in receipts if r.get('lease_epoch')==epoch and r.get('plan_digest')==digest]
        expected=list(range(len(rows)))
        if not rows or len(rows)>len(all_queries) or [r.get('query_index') for r in current] not in (expected, list(range(min(len(all_queries),len(rows)+1)))) or [i for i,_ in rows] != expected: raise ValueError('Incomplete or duplicate search checkpoints')
        identity_fields=('session_id','tab_id','window_id','collector_tab_id')
        first=current[0]
        if (any(not isinstance(first.get(k),int) or isinstance(first.get(k),bool) or first[k]<=0 for k in ('tab_id','window_id','collector_tab_id')) or first['tab_id']!=first['collector_tab_id'] or not isinstance(first.get('session_id'),str) or not re.fullmatch('[a-f0-9]{32}',first['session_id'])): raise ValueError('Invalid historical browser identity')
        groups=[]
        observed = []
        exhausted_groups=[]
        inherited = {}
        if 'inherited_query_cards' in tables:
            inherited = {idx: (stamp, json.loads(body)) for idx, stamp, body in db.execute(
                'SELECT query_index,observed_at,body FROM inherited_query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=?',
                (rid, digest, epoch))}

        for i,(_,body) in enumerate(rows):
            receipt=current[i]
            if receipt.get('request_id')!=rid or receipt.get('platform')!=platform or receipt.get('query')!=all_queries[i] or (staged and receipt.get('version')!=2) or any(receipt.get(k)!=first.get(k) for k in identity_fields): raise ValueError('Historical receipt mismatch')
            cards=json.loads(body); seen=set()
            if not isinstance(cards,list) or len(cards)>50: raise ValueError('Invalid historical card count')
            exhausted=False
            if len(cards)<30:
                meta=db.execute('SELECT query,exhausted FROM query_card_meta WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=?',(rid,digest,epoch,i)).fetchone() if 'query_card_meta' in tables else None
                if not staged or i<3 or meta!=(all_queries[i],1):raise ValueError('Historical short search is not verified')
                exhausted=True
            exhausted_groups.append(exhausted)
            for rank,card in enumerate(cards,1):
                if not isinstance(card,dict) or card.get('query')!=all_queries[i] or card.get('rank')!=rank or type(card.get('is_ad')) is not bool or not isinstance(card.get('title'),str) or not card['title']: raise ValueError('Invalid historical card')
                try: product,_=_identity(card.get('url'),platform)
                except Exception as exc: raise ValueError('Invalid historical product') from exc
                if product!=card.get('product_id') or product in seen: raise ValueError('Historical product mismatch')
                seen.add(product)
            groups.append(cards)
            source = inherited.get(i)
            observed.append(source[0] if source and cards == source[1] else receipt.get('observed_at'))
        details = []
        if len(groups) >= 3 and 'collector_details' in tables:
            rows = db.execute('SELECT detail_index,body FROM collector_details WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY detail_index',
                              (rid, digest, epoch)).fetchall()
            complete_expansion=expansion if len(groups)==len(all_queries) else []
            details = _validated_details(rows, groups if complete_expansion else groups[:3], record, plan, observed,complete_expansion)
        if expansion:
            from .collector_import import ranked_pool
            from .collector_staged_policy import staged_order, next_action
            from .collector_cli import task_config
            if len(groups)<3: raise ValueError('Expansion lacks initial searches')
            pool,_=ranked_pool([dict(query=q,observations=c) for q,c in zip(queries,groups[:3])],queries,platform)
            repeated,_=staged_order(pool)
            history=[]; qualified=0
            for _,body in details:
                detail=json.loads(body); pid=detail['identity'].split(':',1)[1]
                action=next_action(pool,history,qualified,task_config(record['context']['snapshot'],plan)['result_limit'],None)
                if action['action']=='search': break
                history.append(('repeated' if pid in repeated else 'single',pid))
                qualified+=detail['status']=='qualified'
            if next_action(pool,history,qualified,task_config(record['context']['snapshot'],plan)['result_limit'],None)['action']!='search':
                raise ValueError('Expansion was not authorized by the initial review')
    raw=json.dumps(groups,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    return {'request_id':rid,'original_epoch':epoch,'plan_digest':digest,'cards':groups,'observed_at': observed, 'details': details, 'expansion_queries':expansion, 'exhausted_groups':exhausted_groups, 'cards_digest':hashlib.sha256(raw).hexdigest(),'requires_new_browser_binding':True}


def _read_visual_v2(path, record, claim, binding):
    from .visual_v2_checkpoints import read_queries, load_query, query_status, candidate_pool, plan_finished
    from .visual_v2_policy import validate_queries
    from .visual_v2_outcomes import decisions
    from .visual_v2_rules import validate_design_profile, classify_visual
    rid, digest, epoch = record['request_id'], binding['digest'], claim['lease_epoch']
    plan = binding['plan']; key = (rid, digest, epoch)
    validate_design_profile(plan['design_profile'], plan['category'])
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('BEGIN')
        queries = read_queries(db,key); validate_queries(queries)
        if queries[:3] != plan['queries']: raise ValueError('Original query plan mismatch')
        receipts = [json.loads(r[0]) for r in db.execute('SELECT body FROM accepted_receipts WHERE request_id=? ORDER BY id',(rid,))]
        receipts = [r for r in receipts if r.get('lease_epoch')==epoch and r.get('plan_digest')==digest]
        if not receipts or len(receipts)>len(queries): raise ValueError('Missing historical search receipts')
        first=receipts[0]; fields=('session_id','tab_id','window_id','collector_tab_id')
        if (not re.fullmatch('[a-f0-9]{32}',str(first.get('session_id',''))) or
                any(type(first.get(k)) is not int or first[k]<1 for k in ('tab_id','window_id','collector_tab_id')) or
                first['tab_id']!=first['collector_tab_id']): raise ValueError('Invalid historical browser identity')
        groups=[]; stamps=[]; statuses=[]; batches=[]
        for i, receipt in enumerate(receipts):
            if (receipt.get('version')!=3 or receipt.get('query_index')!=i or receipt.get('query')!=queries[i]
                    or receipt.get('request_id')!=rid or receipt.get('platform')!=plan['platform']
                    or any(receipt.get(k)!=first.get(k) for k in fields)):
                raise ValueError('Historical receipt mismatch')
            stamp=_observation_time(receipt.get('observed_at'))
            if not 0 <= (datetime.now(timezone.utc)-stamp).total_seconds() < 86400:
                raise ValueError('Historical observation is stale')
            rows=db.execute('''SELECT batch_index,body FROM visual_v2_batches WHERE request_id=? AND plan_digest=?
                              AND lease_epoch=? AND query_id=? ORDER BY batch_index''',(*key,str(i))).fetchall()
            if [r[0] for r in rows]!=list(range(len(rows))): raise ValueError('Invalid batch sequence')
            cards=load_query(db,(*key,str(i))); seen=set()
            if len(cards)>100:raise ValueError('Historical card limit exceeded')
            for rank,card in enumerate(cards,1):
                if (card.get('query')!=queries[i] or card.get('rank')!=rank or type(card.get('is_ad')) is not bool
                        or not isinstance(card.get('title'),str) or not card['title'].strip()):raise ValueError('Invalid historical card')
                product,_=_identity(card.get('url'),plan['platform'])
                if product!=card.get('product_id') or product in seen:raise ValueError('Historical product mismatch')
                seen.add(product)
            status=query_status(db,(*key,str(i)))
            if status not in (None,'home_complete','card_limit_reached') or (status=='card_limit_reached' and len(cards)!=100):
                raise ValueError('Invalid historical query status')
            if i<len(receipts)-1 and not status:raise ValueError('Incomplete earlier query')
            groups.append(cards);stamps.append(stamp.isoformat());statuses.append(status);batches.append(rows)
        details=db.execute('''SELECT detail_index,body FROM collector_details WHERE request_id=? AND plan_digest=?
                              AND lease_epoch=? ORDER BY detail_index''',key).fetchall()
        if [i for i,_ in details]!=list(range(len(details))):raise ValueError('Invalid historical details')
        completed=[str(i) for i,status in enumerate(statuses) if status]
        pool=candidate_pool(db,key,completed)
        if len(details)>len(pool) or (details and len(completed)<3):raise ValueError('Invalid historical candidate scope')
        has_decisions=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='visual_v2_decisions'").fetchone()
        outcome_rows=decisions(db,key) if has_decisions else []; by_id={v['identity']:v for v in outcome_rows}
        for i,(_,body) in enumerate(details):
            item=json.loads(body); pid=pool[i]['product_id']
            if item.get('identity')!=plan['platform']+':'+pid or item.get('product_id')!=pid:
                raise ValueError('Historical candidate identity mismatch')
            if pid in by_id:
                v=by_id[pid]
                if v['source']!=plan['design_profile']['primary'] or classify_visual(v['source'],v['candidate'],*v['labels'],critical_conflict=v['critical_conflict'])!=v['outcome']:
                    raise ValueError('Historical visual decision mismatch')
        finished=plan_finished(db,key)
    return {'visual_v2':True,'cards':groups,'observed_at':stamps,'details':details,
            'queries':queries,'statuses':statuses,'batches':batches,'decisions':outcome_rows,
            'plan_finished':finished,'requires_new_browser_binding':True}


def restore_card_v3_checkpoint(ledger,store,runs,client,request_id,claim,renew):
    """Recover only recent, independently authorized v3 business checkpoints."""
    from .card_sales_v3_recovery import validate_source_receipts,restore_scope
    from .collector_cli import bind_plan
    target=store.inspect(request_id)
    if target.get('retired') or target.get('state')!='prepared' or claim.get('lease_epoch',0)<2:return False
    fixed=(target.get('collector_plan') or {}).get('plan');candidates=[]
    for run in runs:
        prior=run.get('claim',{});rid=run.get('request_id')
        if (not rid or rid==request_id or not run.get('done') or not 0<prior.get('lease_epoch',0)<claim['lease_epoch']
                or any(prior.get(k)!=claim.get(k) for k in ('id','company_id','snapshot_id','input_hash','task_type'))):continue
        try:
            record=store.inspect(rid);binding=record.get('collector_plan') or {};signed=dict(binding);digest=signed.pop('digest',None);plan=binding.get('plan') or {}
            manual_pause=(record.get('state')=='waiting' and record.get('reason') in ('captcha_required','login_required','page_unreadable','browser_unavailable')
                          and record.get('resume_state')=='prepared')
            recoverable=record.get('state')=='prepared' or manual_pause or (record.get('state')=='unknown' and record.get('resume_state')=='prepared')
            if (digest!=canonical_hash(signed) or binding.get('request_id')!=rid or plan.get('strategy') not in ('card-visual-v3','card-home-v4')
                    or any(binding.get(k)!=claim.get(k) for k in ('company_id','snapshot_id','input_hash'))
                    or record.get('task_type')!='research.bestseller' or record.get('payload')!=claim.get('payload')
                    or not recoverable or (record.get('retired') and record.get('retirement_reason')!='original_worker_lease_inactive')
                    or any(record.get(k) for k in ('collector_pending','collector_evidence','collector_artifact_id','result'))
                    or (fixed is not None and fixed!=plan)):continue
            scope=(rid,digest,prior['lease_epoch'])
            with closing(sqlite3.connect(ledger.path)) as db:
                from .visual_candidate import VisualCandidate
                review=VisualCandidate(ledger.path,scope,plan,None,prior)
                validate_source_receipts(db,scope,verify_photo=lambda filename,sha:review.photo(store.files,filename,sha),homepage_only=plan.get('strategy')=='card-home-v4')
                count=sum(db.execute('SELECT count(*) FROM '+table+' WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope).fetchone()[0]
                          for table in ('card_v3_pages','card_v3_batches','card_v3_details'))
            if count:candidates.append((count,prior['lease_epoch'],rid,digest,plan))
        except (ValueError,TypeError,KeyError,sqlite3.Error):continue
    for _,epoch,rid,source_digest,plan in sorted(candidates,key=lambda row:row[:2],reverse=True):
        params={'task_id':claim['id'],'lease_epoch':claim['lease_epoch'],'request_id':request_id,'source_request_id':rid,'source_epoch':epoch}
        reply=client.request('POST','/internal/meli-optimization/research-search-checkpoint',dict(params,lease_token=claim['lease_token']))
        if reply=={'allowed':False}:continue
        if reply!=dict(params,allowed=True,snapshot_id=claim['snapshot_id'],input_hash=claim['input_hash']):raise ValueError('Invalid checkpoint authorization receipt')
        if plan.get('strategy')=='card-home-v4':
            from .home_suggestion_bootstrap import restore_home_suggestions
            old_claim=next(run['claim'] for run in runs if run.get('request_id')==rid and run.get('claim',{}).get('lease_epoch')==epoch)
            with closing(sqlite3.connect(ledger.path)) as db,db:
                db.execute('BEGIN IMMEDIATE')
                restore_home_suggestions(store,rid,request_id,old_claim,claim,db,plan,authorize=lambda:True,fence=lambda:(renew() or True))
        digest=bind_plan(store,request_id,claim,plan,recovery=True);plan_hash=canonical_hash(plan)
        with closing(sqlite3.connect(ledger.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            restore_scope(db,(rid,source_digest,epoch),(request_id,digest,claim['lease_epoch']),plan_hash,plan_hash,
                          authorize=lambda:True,fence=lambda:(renew() or True))
        return True
    return False


def restore_search_checkpoint(ledger, store, runs, client, request_id, claim, renew):
    """Import only business evidence. New-session receipts must still be collected."""
    from .collector_cli import bind_plan

    target = store.inspect(request_id)
    if target.get('retired') or target.get('state') != 'prepared' or claim['lease_epoch'] < 2:
        return False
    if restore_card_v3_checkpoint(ledger,store,runs,client,request_id,claim,renew):return True
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
            if evidence.get('visual_v2'):
                from .visual_v2_checkpoints import initialize, seal_queries, append_batch, finish_query, finish_plan
                from .visual_v2_outcomes import _initialize
                from .collector_progress import initialize_details
                initialize(db); _initialize(db); initialize_details(db)
                existing=db.execute('SELECT 1 FROM visual_v2_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=?',key).fetchone()
                if existing:return True
                if db.execute('SELECT 1 FROM accepted_receipts WHERE request_id=?',(request_id,)).fetchone():return False
                def live():
                    value=renew()
                    return value is not False
                seal_queries(db,key,evidence['queries'],fence=live)
                for i,rows in enumerate(evidence['batches']):
                    for index,body in rows:
                        append_batch(db,(*key,str(i)),index,json.loads(body),fence=live)
                    if evidence['statuses'][i]:
                        finish_query(db,(*key,str(i)),evidence['statuses'][i],fence=live)
                    body=json.dumps(evidence['cards'][i],ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
                    db.execute('INSERT INTO inherited_query_cards VALUES (?,?,?,?,?,?,?,?)',
                               (*key,i,rid,source_epoch,evidence['observed_at'][i],body))
                for index,body in evidence['details']:
                    db.execute('INSERT INTO collector_details VALUES (?,?,?,?,?)',(*key,index,body))
                    db.execute('INSERT INTO inherited_details VALUES (?,?,?,?,?)',(*key,index,body))
                for decision in evidence['decisions']:
                    body=json.dumps(decision,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
                    db.execute('INSERT INTO visual_v2_decisions VALUES (?,?,?,?,?)',(*key,decision['identity'],body))
                if evidence['plan_finished']:finish_plan(db,key,fence=live)
                if not live():raise ValueError('Collection lease fence failed')
                return True
            existing = db.execute('SELECT 1 FROM inherited_query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=?', key).fetchone()
            if existing:
                return True
            # Once collection starts its evidence takes precedence.
            if db.execute('SELECT 1 FROM accepted_receipts WHERE request_id=?', (request_id,)).fetchone():
                return False
            renew()
            if evidence['expansion_queries']:
                from .collector_staged_policy import initialize_expansions
                initialize_expansions(db)
                body=json.dumps({'queries':evidence['expansion_queries']},ensure_ascii=False,sort_keys=True,separators=(',',':'))
                db.execute('INSERT INTO staged_expansions VALUES (?,?,?,?,?)',(*key,body,hashlib.sha256(body.encode()).hexdigest()))
            for i, cards in enumerate(evidence['cards']):
                body = json.dumps(cards, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
                db.execute('INSERT INTO inherited_query_cards VALUES (?,?,?,?,?,?,?,?)',
                           (*key, i, rid, source_epoch, evidence['observed_at'][i], body))
                if evidence['exhausted_groups'][i]:
                    query=(old_plan['queries']+evidence['expansion_queries'])[i]
                    db.execute('INSERT INTO inherited_query_meta VALUES (?,?,?,?,?,?)',(*key,i,query,1))
            db.executemany('INSERT INTO inherited_details VALUES (?,?,?,?,?)',
                           [(*key, i, body) for i, body in evidence['details']])
        return True
    return False

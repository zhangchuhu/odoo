"""Task-scoped identity for collecting suggestions before sealing a plan."""
import re

from .domain import canonical_hash
from .visual_profile import CATEGORY_CODES
from .home_suggestions import _normalized


def make_bootstrap(claim,request_id,source_sha,category,anchor):
    if (not isinstance(claim,dict) or claim.get('task_type')!='research.bestseller'
            or any(type(claim.get(k)) is not int or claim[k]<1 for k in ('id','company_id','snapshot_id','lease_epoch'))
            or not isinstance(claim.get('input_hash'),str) or not claim['input_hash']
            or not isinstance(request_id,str) or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',request_id)
            or not isinstance(source_sha,str) or not re.fullmatch(r'[0-9a-f]{64}',source_sha)
            or not isinstance(category,str) or category not in CATEGORY_CODES):
        raise ValueError('Invalid task-scoped suggestion bootstrap')
    _normalized(anchor)
    if category=='womens_skirt_set' and ' '.join(anchor.casefold().split())!='conjunto mujer falda':
        raise ValueError('Invalid skirt set category anchor')
    fields={k:claim[k] for k in ('id','company_id','snapshot_id','lease_epoch','input_hash','task_type')}
    fields.update(request_id=request_id,source_sha256=source_sha,category=category,anchor=anchor,version='card-home-v4')
    fields['digest']=canonical_hash(fields)
    return fields


def validate_bootstrap(draft,claim,request_id):
    if not isinstance(draft,dict):return False
    try:return draft==make_bootstrap(claim,request_id,draft['source_sha256'],draft['category'],draft['anchor'])
    except (KeyError,TypeError,ValueError):return False


def bind_bootstrap(store,request_id,claim,source_id,source_sha,category,anchor,*,fence):
    from .collector_cli import validate_source
    if not callable(fence) or fence() is not True:raise ValueError('Active lease required')
    draft=make_bootstrap(claim,request_id,source_sha,category,anchor)
    with store._db() as db:
        record=store._get(db,request_id)
        snapshot=record.get('context',{}).get('snapshot',{})
        if (record.get('retired') or record.get('state')!='prepared' or record.get('task_type')!='research.bestseller'
                or record.get('payload')!=claim.get('payload') or snapshot.get('snapshot_id')!=claim.get('snapshot_id')
                or record.get('collector_plan')):raise ValueError('Active unplanned research request required')
        validate_source(record,claim,{'source_artifact_id':source_id,'viewed_source_sha256':source_sha})
        existing=record.get('suggestion_bootstrap')
        if existing is not None and existing!=draft:raise ValueError('Suggestion bootstrap is immutable')
        if existing is None:
            if fence() is not True:raise ValueError('Active lease required')
            record['suggestion_bootstrap']=draft;store._put(db,record)
    return draft


def mark_suggestions_unavailable(store,request_id,claim,reason,*,fence):
    from datetime import datetime,timezone
    if reason not in ('browser_unavailable','page_unavailable','suggestions_unavailable') or not callable(fence) or fence() is not True:
        raise ValueError('Invalid suggestion unavailability report')
    with store._db() as db:
        record=store._get(db,request_id)
        if (record.get('retired') or record.get('state')!='prepared' or record.get('collector_plan')
                or not validate_bootstrap(record.get('suggestion_bootstrap'),claim,request_id)):
            raise ValueError('Current suggestion bootstrap required')
        prior=record.get('suggestion_unavailable')
        if prior:
            if prior.get('reason')!=reason or prior.get('source')!='host_report':raise ValueError('Unavailability report changed')
            return
        report={'reason':reason,'source':'host_report','observed_at':datetime.now(timezone.utc).isoformat()}
        if fence() is not True:raise ValueError('Active lease required')
        record['suggestion_unavailable']=report;store._put(db,record)


def seal_suggestion_plan(store,request_id,claim,db,profile,*,fence):
    from .home_suggestion_checkpoint import observations,reads
    from .home_suggestions import rank_suggestions,initial_queries
    from .visual_profile import validate_profile
    if not callable(fence) or fence() is not True:raise ValueError('Active lease required')
    checked=validate_profile(profile,'mercado-libre-mx')
    if checked!=profile:raise ValueError('Invalid source visual profile')
    with store._db() as host_db:
        record=store._get(host_db,request_id);draft=record.get('suggestion_bootstrap')
        if (record.get('retired') or record.get('state')!='prepared' or record.get('collector_plan')
                or not validate_bootstrap(draft,claim,request_id) or checked['category_code']!=draft['category']
                or checked['query_seeds'][0]!=draft['anchor']):raise ValueError('Current suggestion bootstrap required')
        scope=(request_id,draft['digest'],claim['lease_epoch'])
        read_rows=reads(db,scope);observed=observations(db,scope)
        if not read_rows and not isinstance(record.get('suggestion_unavailable'),dict):
            raise ValueError('Suggestion read required before using fallback')
        verified={(r['prefix'],r['evidence_sha256']) for r in read_rows if r['state']=='observed'}
        if any((r['prefix'],r['evidence_sha256']) not in verified for r in observed):
            raise ValueError('Suggestion observation lacks matching read evidence')
        features=checked['construction']+checked['selling_points']+checked['style']+checked['use_scene']
        ranked=rank_suggestions(draft['category'],observed,features)
        selected=initial_queries(draft['anchor'],ranked,checked['query_seeds'][1:])
        sealed={'profile':checked,'queries':selected}
        if record.get('suggestion_selection') is not None and record['suggestion_selection']!=sealed:
            raise ValueError('Suggestion selection is immutable')
        if fence() is not True:raise ValueError('Active lease required')
        record['suggestion_selection']=sealed;store._put(host_db,record)
    return selected


def available_expansion(store,request_id,claim,db,used,*,fence):
    """Return only task-scoped, evidenced suggestions not already searched."""
    from .home_suggestion_checkpoint import observations,reads
    from .home_suggestions import rank_suggestions
    if not callable(fence) or fence() is not True:raise ValueError('Active lease required')
    if not isinstance(used,list) or any(not isinstance(q,str) for q in used):raise ValueError('Current search terms required')
    record=store.inspect(request_id);draft=record.get('suggestion_bootstrap');selection=record.get('suggestion_selection')
    if (record.get('retired') or not validate_bootstrap(draft,claim,request_id) or not isinstance(selection,dict)
            or not isinstance(selection.get('profile'),dict)):
        raise ValueError('Current suggestion selection required')
    scope=(request_id,draft['digest'],claim['lease_epoch'])
    verified={(r['prefix'],r['evidence_sha256']) for r in reads(db,scope) if r['state']=='observed'}
    rows=observations(db,scope)
    if any((r['prefix'],r['evidence_sha256']) not in verified for r in rows):raise ValueError('Suggestion observation lacks matching read evidence')
    profile=selection['profile']
    features=profile['construction']+profile['selling_points']+profile['style']+profile['use_scene']
    seen={_normalized(q) for q in used}
    result=[row for row in rank_suggestions(draft['category'],rows,features) if _normalized(row['text']) not in seen]
    if fence() is not True:raise ValueError('Active lease required')
    return result


def restore_home_suggestions(store,old_request,new_request,old_claim,new_claim,db,plan,*,authorize,fence):
    """Copy evidenced suggestion observations to an authorized new lease."""
    import hashlib
    from datetime import datetime,timezone
    from pathlib import Path
    from .home_suggestion_checkpoint import initialize,observations,reads,save_observation,save_read
    if (not callable(authorize) or authorize() is not True or not callable(fence) or fence() is not True
            or old_request==new_request or type(old_claim.get('lease_epoch')) is not int
            or type(new_claim.get('lease_epoch')) is not int or not 0<old_claim['lease_epoch']<new_claim['lease_epoch']
            or any(old_claim.get(k)!=new_claim.get(k) for k in ('id','company_id','snapshot_id','input_hash','task_type','payload'))):
        raise ValueError('Suggestion recovery authorization mismatch')
    original=store.inspect(old_request);draft=original.get('suggestion_bootstrap');selection=original.get('suggestion_selection')
    if (not validate_bootstrap(draft,old_claim,old_request) or not isinstance(selection,dict)
            or selection.get('profile')!=plan.get('visual_profile')
            or [r.get('text') for r in selection.get('queries',[]) if isinstance(r,dict)]!=plan.get('queries')
            or (original.get('collector_plan') or {}).get('plan')!=plan):
        raise ValueError('Original suggestion selection mismatch')
    initialize(db);source=(old_request,draft['digest'],old_claim['lease_epoch'])
    read_rows=reads(db,source);rows=observations(db,source)
    unavailable=original.get('suggestion_unavailable')
    if not read_rows and (not isinstance(unavailable,dict) or unavailable.get('source')!='host_report'
                          or unavailable.get('reason') not in ('browser_unavailable','page_unavailable','suggestions_unavailable')):
        raise ValueError('Original suggestion evidence missing')
    verified={(r['prefix'],r['evidence_sha256']) for r in read_rows if r['state']=='observed'}
    if any((r['prefix'],r['evidence_sha256']) not in verified for r in rows):raise ValueError('Suggestion observation lacks matching evidence')
    def verify(sha):
        for suffix in ('.png','.jpg','.webp'):
            path=Path(store.files)/(sha+suffix)
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==sha:return True
        return False
    now=datetime.now(timezone.utc)
    for row in read_rows:
        stamp=datetime.fromisoformat(row['observed_at'].replace('Z','+00:00'))
        if stamp.utcoffset() is None or not 0<=(now-stamp).total_seconds()<86400 or not verify(row['evidence_sha256']):
            raise ValueError('Original suggestion evidence expired or missing')
    if not read_rows:
        try:stamp=datetime.fromisoformat(unavailable['observed_at'].replace('Z','+00:00'))
        except (KeyError,TypeError,ValueError) as exc:raise ValueError('Invalid unavailability report') from exc
        if stamp.utcoffset() is None or not 0<=(now-stamp).total_seconds()<86400:raise ValueError('Original suggestion report expired')
    target_record=store.inspect(new_request);target_draft=target_record.get('suggestion_bootstrap')
    if not validate_bootstrap(target_draft,new_claim,new_request):
        target_draft=bind_bootstrap(store,new_request,new_claim,plan['source_artifact_id'],plan['viewed_source_sha256'],draft['category'],draft['anchor'],fence=fence)
    target=(new_request,target_draft['digest'],new_claim['lease_epoch'])
    db.execute('SAVEPOINT home_suggestion_recovery')
    try:
        for row in read_rows:save_read(db,target,row,fence=fence,verify_evidence=verify)
        for row in rows:save_observation(db,target,row,fence=fence,verify_evidence=verify)
        with store._db() as host_db:
            record=store._get(host_db,new_request)
            if record.get('suggestion_selection') not in (None,selection) or fence() is not True:raise ValueError('Suggestion recovery conflict')
            if unavailable:
                if record.get('suggestion_unavailable') not in (None,unavailable):raise ValueError('Suggestion recovery conflict')
                record['suggestion_unavailable']=unavailable
            record['suggestion_selection']=selection;store._put(host_db,record)
        if fence() is not True:raise ValueError('Active lease required')
        db.execute('RELEASE home_suggestion_recovery')
    except Exception:
        db.execute('ROLLBACK TO home_suggestion_recovery');db.execute('RELEASE home_suggestion_recovery');raise

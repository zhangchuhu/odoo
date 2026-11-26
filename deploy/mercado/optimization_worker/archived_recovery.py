"""Explicit recovery of a fenced, previously retired research gallery checkpoint.

Caller must hold the global research service lock and the worker journal lock,
with the former executor stopped. This module never starts a browser or uploads.
"""
import base64
import fcntl
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from .collector_import import convert_evidence
from .research_upload import intent_id


def _revalidate_visual_v2(db,key,photo_scope,plan,config,collector_id,data,observed):
    from .visual_v2_finalize import snapshot_evidence,build_result
    if snapshot_evidence(db,key,photo_scope,plan,collector_id)!=data:
        raise ValueError('Visual evidence no longer matches its checkpoints')
    return build_result(db,key,photo_scope,plan,config,observed)


def _revalidate_staged_result(ledger, binding, claim, data, result, observed, config):
    from .collector_import import convert_staged_evidence
    params = binding['plan']
    key = (binding['request_id'], binding['digest'], claim['lease_epoch'])
    sealed = ledger.sealed_queries(*key, lambda: True)
    extra = sealed[:max(0, len(data['queries']) - 3)]
    if [item.get('query') for item in data['queries']] != params['queries'] + extra:
        raise ValueError('Staged recovery query plan mismatch')
    elapsed = ledger.budget_elapsed(*key, lambda: True) if result.get('reason') == 'time_budget_exhausted' else 0
    verified = convert_staged_evidence(data, binding['collector_id'], params['platform'], params['queries'],
        extra, params['category'], observed, result_limit=config['result_limit'], min_sold=config['min_sold'],
        min_reviews=config['min_reviews'], min_rating=config['min_rating'], elapsed_seconds=elapsed)
    if verified.get('reason') != result.get('reason'):
        raise ValueError('Staged recovery completion reason mismatch')
    return verified


def recover_archived(journal, store, client, request_id, *, service_lock):
    path = Path(service_lock)
    if not path.is_absolute() or not getattr(journal, 'lock', None):
        raise ValueError('Absolute service lock and locked journal required')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _recover_archived(journal, store, client, request_id)
    finally:
        os.close(fd)


def _recover_archived(journal, store, client, request_id, on_verified=None):
    rows = [json.loads(raw) for (raw,) in journal.db.execute('SELECT record FROM runs')]
    matches = [r for r in rows if r.get('request_id') == request_id]
    if len(matches) != 1:
        raise ValueError('Exactly one original journal record required')
    r = matches[0]
    if any(other is not r and not other.get('done') for other in rows):
        raise ValueError('Another journal run is active')
    claim = r.get('claim') or {}
    interrupted = (r.get('phase') == 'host_pending' and r.get('outcome') == 'lease_lost')
    waiting_unknown = (r.get('phase') == 'calling' and r.get('outcome') == 'waiting')
    if (claim.get('task_type') != 'research.bestseller'
            or r.get('pending') or (not r.get('recovery_pending') and
            (not r.get('done') or not (interrupted or waiting_unknown)))):
        raise ValueError('Only an interrupted durable research lease can be recovered')
    current = store.inspect(request_id)
    plan = current.get('collector_plan') or {}
    pending = current.get('collector_pending') or {}
    scoped = pending.get('ledger_v1') is True
    if scoped != (getattr(client, 'research_ledger_enabled', False) is True):
        raise ValueError('Original research ledger mode required')
    isolated = (scoped and (current.get('state'), current.get('reason')) in (('unknown','unknown'),('waiting','page_unreadable'))
                and current.get('retired') and current.get('resume_state') == 'candidate_images_pending')
    if ((current.get('state') != 'candidate_images_pending' and not isolated) or current.get('request_id') != request_id
            or current.get('payload') != claim.get('payload') or plan.get('request_id') != request_id
            or any(plan.get(k) != claim.get(k) for k in ('company_id', 'snapshot_id', 'input_hash'))
            or not str(pending.get('artifact_id', '')).isdigit()):
        raise ValueError('Original pending gallery evidence required')
    artifact = client.artifact(str(pending['artifact_id']))
    raw = base64.b64decode(artifact.get('data', ''), validate=True)
    canonical = json.dumps(pending.get('data'), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if (raw != canonical or artifact.get('digest') != pending.get('digest') or artifact.get('case_id') != plan.get('case_id')
            or artifact.get('company_id') != claim.get('company_id') or artifact.get('mime_type') != 'application/json'
            or hashlib.sha256(raw).hexdigest() != pending.get('digest')):
        raise ValueError('Original evidence receipt does not match')
    params = plan['plan']; config = plan['collection_config']; result = pending['result']
    observed = result['evidence'][0]['observed_at']
    if params.get('strategy') == 'card-visual-v3':
        from .visual_candidate import VisualCandidate
        from .visual_v3_finalize import build_result,snapshot_evidence
        from contextlib import closing
        import sqlite3
        journal_path=journal.db.execute('PRAGMA database_list').fetchone()[2]
        ledger_path=str(Path(journal_path).parent/'collector-bindings.sqlite')
        key=(request_id,plan['digest'],claim['lease_epoch'])
        review=VisualCandidate(ledger_path,key,params,config,claim)
        with closing(sqlite3.connect(ledger_path)) as db:
            verified=build_result(db,key,review.scope,params,config,observed,lambda filename,sha:review.photo(store.files,filename,sha))
            if snapshot_evidence(db,key,review.scope,params,verified,plan['collector_id'])!=pending['data']:
                raise ValueError('Original card evidence changed')
    elif params.get('strategy') == 'visual-v2':
        from .visual_candidate import VisualCandidate
        from contextlib import closing
        import sqlite3
        journal_path=journal.db.execute('PRAGMA database_list').fetchone()[2]
        ledger_path=str(Path(journal_path).parent/'collector-bindings.sqlite')
        key=(request_id,plan['digest'],claim['lease_epoch'])
        scope=VisualCandidate(ledger_path,key,params,config,claim).scope
        with closing(sqlite3.connect(ledger_path)) as db:
            verified=_revalidate_visual_v2(db,key,scope,params,config,plan['collector_id'],pending['data'],observed)
    elif params.get('strategy') == 'staged-v1':
        from .tab_receipt_store import TabReceiptStore
        journal_path = journal.db.execute('PRAGMA database_list').fetchone()[2]
        ledger = TabReceiptStore(str(Path(journal_path).parent / 'collector-bindings.sqlite'))
        verified = _revalidate_staged_result(ledger, plan, claim, pending['data'], result, observed, config)
    else:
        verified = convert_evidence(pending['data'], plan['collector_id'], params['platform'], params['queries'],
            params['category'], observed, result_limit=config['result_limit'], min_sold=config['min_sold'],
            min_reviews=config['min_reviews'], min_rating=config['min_rating'])
    from .research_result_candidates import all_candidates
    verified_items=all_candidates(verified) if params.get('strategy')=='card-visual-v3' else verified['candidates']
    result_items=all_candidates(result) if params.get('strategy')=='card-visual-v3' else result['candidates']
    if verified['status'] != result['status'] or len(verified_items) != len(result_items):
        raise ValueError('Candidate results do not match original evidence')
    if len(verified['candidates'])!=len(result['candidates']):raise ValueError('Candidate sales tiers changed')
    for expected, actual in zip(verified_items, result_items):
        for key, value in expected.items():
            if key == 'observation_time_scope' and key not in actual and value == 'batch':
                continue  # Older persisted records predate this explicit marker.
            if actual.get(key) != value:
                raise ValueError('Candidate results do not match original evidence')
        if set(actual) - set(expected) - {'image', 'images', 'gallery_image_urls'}:
            raise ValueError('Unexpected candidate fields')
    if scoped:
        return _recover_scoped_gallery(journal, store, client, r, current, on_verified)
    store.reactivate_research(request_id, claim, validate_only=True)
    if r.get('recovery_pending'):
        recovered = claim
        try:
            client.heartbeat(recovered)
        except Exception as exc:
            from .client import APIError
            if not isinstance(exc, APIError) or exc.status != 409:
                raise
            if not r.get('recovery_intent_id'):
                r['recovery_intent_id'] = uuid.uuid4().hex
                journal.save(r)
            recovered = client.recover_lease(claim, request_id, r['recovery_intent_id'])
            if (type(recovered.get('lease_epoch')) is not int or recovered['lease_epoch'] <= claim.get('lease_epoch', 0)
                    or any(recovered.get(k) != claim.get(k) for k in ('id', 'task_type', 'input_hash', 'snapshot_id', 'company_id', 'payload'))
                    or recovered.get('lease_token') == claim.get('lease_token')):
                raise ValueError('Recovery lease mismatch')
            journal.replace_claim(r, recovered)
            r.pop('recovery_intent_id', None)
            journal.save(r)
    else:
        if not r.get('recovery_intent_id'):
            r['recovery_intent_id'] = uuid.uuid4().hex
            journal.save(r)
        recovered = client.recover_lease(claim, request_id, r['recovery_intent_id'])
        fields = ('id', 'task_type', 'input_hash', 'snapshot_id', 'company_id', 'payload')
        if (any(recovered.get(k) != claim.get(k) for k in fields)
                or type(recovered.get('lease_epoch')) is not int or recovered['lease_epoch'] <= claim.get('lease_epoch', 0)
                or not recovered.get('lease_token') or recovered['lease_token'] == claim.get('lease_token')):
            raise ValueError('Recovery lease mismatch')
        r.pop('done', None);r.pop('outcome', None);r['phase'] = 'host_pending';r['recovery_pending'] = True
        journal.replace_claim(r, recovered)
        r.pop('recovery_intent_id', None)
        journal.save(r)
    client.heartbeat(recovered)
    store.reactivate_research(request_id, recovered)
    r.pop('recovery_pending', None)
    journal.save(r)
    return {'task_id': recovered['id'], 'request_id': request_id, 'state': 'host_pending'}


def resume_waiting_gallery(journal, store, client, dispatcher, task_id=None):
    """Under the service and journal locks, resume once per verified gallery step."""
    from .client import APIError
    from .domain import canonical_hash
    from .research_return_bridge import next_gallery_step
    with store._db() as db:
        requests = [r[0] for r in db.execute("""SELECT id FROM requests
            WHERE json_extract(record,'$.task_type')='research.bestseller'
              AND json_extract(record,'$.collector_pending.ledger_v1')=1
              AND (json_extract(record,'$.state')='candidate_images_pending'
                   OR (json_extract(record,'$.state')='unknown' AND json_extract(record,'$.resume_state')='candidate_images_pending')
                   OR (json_extract(record,'$.state')='waiting' AND json_extract(record,'$.reason')='page_unreadable'
                       AND json_extract(record,'$.resume_state')='candidate_images_pending'))
            ORDER BY rowid DESC""")]
    for request in requests:
        row = journal.db.execute("""SELECT record FROM runs WHERE json_extract(record,'$.request_id')=?
            AND json_extract(record,'$.done')=1 AND json_extract(record,'$.outcome')='waiting'
            AND json_extract(record,'$.phase')='calling'""", (request,)).fetchone()
        if not row:
            continue
        archived = json.loads(row[0])
        if task_id is not None and archived['claim']['id'] != task_id:
            continue
        if archived.get('gallery_retry_after',0) > time.time():
            continue
        host = store.inspect(request)
        if not host.get('retired') and not archived.get('recovery_intent_id'):
            continue
        pending = host['collector_pending']
        try:
            images = _confirmed_images(pending)
            step = next_gallery_step(dict(host,state='candidate_images_pending'))
            checkpoint = canonical_hash([pending['artifact_id'],pending['digest'],images,step])
        except (KeyError,TypeError,ValueError):
            continue
        if archived.get('gallery_resume_checkpoint') == checkpoint and not archived.get('gallery_resume_in_flight'):
            continue
        archived.update(gallery_resume_checkpoint=checkpoint,gallery_resume_in_flight=True)
        journal.save(archived)
        try:
            _recover_archived(journal,store,client,request,
                on_verified=lambda rid: dispatcher.acknowledge_reconciliation(rid, gallery_retry=True))
        except (APIError,ValueError) as exc:
            # Re-read to retain the idempotency intent journaled before the RPC.
            latest = journal.db.execute("SELECT record FROM runs WHERE json_extract(record,'$.request_id')=?",(request,)).fetchone()
            if latest:
                saved = json.loads(latest[0])
                if isinstance(exc,APIError) and exc.transient:
                    saved['gallery_retry_after'] = time.time()+60
                else:
                    saved.pop('gallery_resume_in_flight',None)
                journal.save(saved)
            continue
        return True
    return False


def _confirmed_images(pending):
    """Enumerate exactly the local image intents confirmed against the candidates."""
    uploads = pending.get('candidate_uploads', {})
    manifests = pending.get('gallery_manifests', {})
    if not isinstance(uploads, dict) or not isinstance(manifests, dict):
        raise ValueError('Malformed gallery checkpoint')
    confirmed = {}
    from .research_result_candidates import all_candidates
    for c in all_candidates(pending['result']):
        identity = c['platform'] + ':' + c['item_id']
        manifest = manifests.get(identity)
        if manifest and manifest.get('detail_url') != c['url']:
            raise ValueError('Gallery manifest belongs to another candidate')
        for image in c.get('images', []):
            order = image.get('order')
            key = f'{identity}:{order}'
            upload = uploads.get(key)
            if (not manifest or type(order) is not int or not 0 <= order < len(manifest['images'])
                    or not isinstance(upload,dict) or upload.get('ledger_v1') is not True or upload.get('state') != 'confirmed'
                    or upload.get('order') != order or image.get('source') != manifest['images'][order]
                    or upload.get('source') != image['source'] or image.get('artifact_id') != upload.get('artifact_id')
                    or not re.fullmatch(r'[1-9][0-9]*',str(upload.get('artifact_id')))
                    or image.get('sha256') != upload.get('digest') or not re.fullmatch(r'[0-9a-f]{64}',str(upload.get('digest')))
                    or key in confirmed):
                raise ValueError('Image receipts require reconciliation')
            confirmed[key] = {'artifact_id': str(upload['artifact_id']), 'digest': upload['digest']}
    if uploads.keys() != confirmed.keys():
        raise ValueError('Outstanding image intents require reconciliation')
    return {intent_id('image', key): proof for key, proof in confirmed.items()}


def _recover_scoped_gallery(journal, store, client, row, host, on_verified):
    from .domain import canonical_hash
    from .research_return_bridge import next_gallery_step
    request = row['request_id']
    claim = row['claim']
    pending = host['collector_pending']
    source = f"odoo-artifact:{pending['artifact_id']}:sha256:{pending['digest']}"
    if (host.get('collector_evidence') != pending['data'] or host.get('collector_artifact_id') != pending['artifact_id']
            or pending['result']['evidence'][0].get('source') != source
            or host.get('binding') != canonical_hash({'kind': claim['task_type'], 'payload': claim['payload'], 'context': row['context']})
            or next_gallery_step(dict(host,state='candidate_images_pending'))['stage'] not in ('manifest','image','finish')):
        raise ValueError('Original confirmed gallery evidence required')
    images = _confirmed_images(pending)
    store.reactivate_research(request, claim, validate_only=True, verified_gallery=True)
    if not row.get('recovery_intent_id'):
        row['recovery_intent_id'] = uuid.uuid4().hex
        journal.save(row)
    proof = client.continue_registered_research(claim, request, row['recovery_intent_id'],
        evidence_artifact_id=pending['artifact_id'], evidence_digest=pending['digest'], confirmed_images=images)
    recovered = proof.get('claim') if isinstance(proof, dict) else None
    fields = ('id','company_id','snapshot_id','input_hash','task_type','payload','lease_epoch')
    if (not isinstance(recovered, dict) or proof.get('request_id') != request
            or any(k not in claim or claim[k] != recovered.get(k) for k in fields)
            or not isinstance(recovered.get('lease_token'),str) or not recovered['lease_token']
            or recovered['lease_token'] == claim['lease_token']):
        raise ValueError('Scoped gallery continuation changed original identity')
    client.heartbeat(recovered)
    # Keep the old credential and intent until both the remote receipt and local
    # checkpoint are durable. A crash before this final save replays the same intent.
    store.reactivate_research(request, recovered, verified_gallery=True)
    if on_verified:
        on_verified(request)
    row.update(claim=recovered, phase='host_pending')
    for key in ('done','outcome','recovery_pending','recovery_intent_id','scoped_continue_intent','gallery_resume_in_flight','gallery_retry_after'):
        row.pop(key,None)
    journal.save(row)
    return {'task_id': recovered['id'], 'request_id': request, 'state': 'host_pending'}


def main():
    import argparse
    from .client import Client
    from .host_session import SessionStore
    from .journal import Journal
    parser = argparse.ArgumentParser(description='Recover an original retired research gallery checkpoint')
    parser.add_argument('--request-id', required=True)
    args = parser.parse_args()
    journal_path = os.environ['OPTIMIZATION_JOURNAL']
    host_path = os.environ['OPTIMIZATION_HOST_SESSION']
    lock_path = os.environ['OPTIMIZATION_RESEARCH_LOCK']
    if any(not Path(p).is_absolute() for p in (journal_path, host_path, lock_path)):
        raise ValueError('Absolute research state paths required')
    client = Client(os.environ['ODOO_OPTIMIZATION_URL'], os.environ['ODOO_API_KEY'],
                    research_ledger_enabled=os.environ.get('OPTIMIZATION_RESEARCH_LEDGER_V1') == '1')
    journal = Journal(journal_path, client.identity)
    try:
        print(json.dumps(recover_archived(journal, SessionStore(host_path, client.identity), client,
                                          args.request_id, service_lock=lock_path)))
    finally:
        journal.close()


if __name__ == '__main__':
    main()

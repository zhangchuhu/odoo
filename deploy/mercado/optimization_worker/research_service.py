"""Opt-in research service composition; missing live adapters fail closed.

Adapters are trusted executables, not arbitrary model-supplied code. They must
perform the operation for real and return nonce-bound JSON; config alone is not
proof. This entrypoint is not installed or started by the project.
"""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing

from .research_capability_probe import check_research_capabilities, SingleFlightReadiness

class CapabilityDiagnostics:
    """Report probe classes only, without model outputs or credentials."""
    def __init__(self, emit):
        self.emit=emit;self.previous=None
    def report(self,result):
        failed=tuple(name for name in ('chrome','vision','callback') if name in result.failed)
        state=tuple(name + ' (expired)' if name in result.expired else name for name in failed) if not result.ready else ()
        if state!=self.previous:
            self.emit('Research readiness unavailable: '+','.join(state) if state else 'Research readiness restored')
            self.previous=state
        return result

class UnknownRetryPoll:
    """Bound retry requests; server provides scoped, idempotent batch recovery."""
    def __init__(self, request, *, clock=time.monotonic, interval=60):
        self.request, self.clock, self.interval = request, clock, interval
        self.next_poll = float('-inf')
        self.ready = False

    def tick(self):
        now = self.clock()
        if now < self.next_poll:
            return self.ready
        self.next_poll = now + self.interval
        try:
            result = self.request()
            if not isinstance(result, dict) or not isinstance(result.get('retried'), list):
                raise ValueError('Invalid retry response')
            failed = result.get('failed', [])
            if not isinstance(failed, list):
                raise ValueError('Invalid retry failure response')
            if failed:
                ids = [str(row['task_id']) for row in failed
                       if isinstance(row, dict) and type(row.get('task_id')) is int]
                print('Unknown retry failed for tasks: ' + ','.join(ids), file=sys.stderr, flush=True)
            self.ready = True
        except Exception:
            self.ready = False
        return self.ready

class ResearchService:
    def __init__(self, host, lock_path, validate=None):
        self.host=host
        self.lock_path=lock_path;self.lock=None;self.validate=validate

    @classmethod
    def from_host(cls, host, lock_path, validate=None):
        return cls(host,lock_path,validate)

    def __enter__(self):
        if self.lock_path is not None:
            fd=os.open(self.lock_path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
            try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BaseException: os.close(fd);raise
            self.lock=fd
        try:
            if self.validate is not None: self.validate()
        except BaseException:
            if self.lock is not None: os.close(self.lock);self.lock=None
            raise
        return self

    def tick(self):
        result=self.host.tick()
        return 'waiting_for_host' if result == 'waiting' else result

    def close(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        self.host.stop_new_claims()
        worker=self.host.worker_thread
        if worker: worker.join(timeout)
        if worker and worker.is_alive(): return False
        if deadline is None:
            self.host.tick_lock.acquire()
        elif not self.host.tick_lock.acquire(timeout=max(0,deadline-time.monotonic())):
            return False
        self.host.tick_lock.release()
        return True

    def __exit__(self,*args):
        self.close()
        if self.lock is not None:
            fcntl.flock(self.lock,fcntl.LOCK_UN);os.close(self.lock);self.lock=None


def adapter(path, *, timeout=120):
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError('Invalid probe timeout')
    if not path: return None
    p=Path(path)
    if not p.is_absolute() or not p.is_file() or not os.access(p,os.X_OK): return None
    def run(nonce):
        env={k:os.environ[k] for k in ('PATH','HOME','TMPDIR','LANG','LC_ALL') if k in os.environ}
        proc=subprocess.run([str(p),nonce],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                            env=env,timeout=timeout,check=False)
        if proc.returncode or len(proc.stdout)>4096: raise ValueError('Probe unavailable')
        return json.loads(proc.stdout)
    return run


def timing_config(env):
    ttl=float(env.get('OPTIMIZATION_PROBE_CACHE_TTL','30'))
    idle=float(env.get('OPTIMIZATION_IDLE_POLL_SECONDS','60'))
    if not math.isfinite(ttl) or not 0 < ttl <= 30 or not math.isfinite(idle) or not 5 <= idle <= 300:
        raise ValueError('Invalid service timing')
    return ttl,idle


def build_live_adapters(config, client, timeouts, *, case_id=None, now=time.time):
    """Live local observations plus isolated transport and rollback checks.

    Probe-table roundtrips are transport evidence only and cannot establish
    business writeback readiness by themselves.
    """
    from .chrome_live_probe import probe_chrome
    from .multimodal_probe import probe_multimodal
    from .callback_transport_probe import probe_diagnostics
    def chrome(nonce):
        ok=probe_chrome(config['home'],config['cwd'],config['computer_use_cwd'],config['proxy'],timeout=timeouts['chrome']) is True
        return {'nonce':nonce,'observed_at':now(),'ok':ok}
    def vision(nonce):
        ok=probe_multimodal(config['home'],config['proxy'],timeout=timeouts['vision']) is True
        return {'nonce':nonce,'observed_at':now(),'ok':ok}
    def callback(nonce):
        if type(case_id) is not int or case_id <= 0:
            return {'nonce':nonce,'observed_at':now(),'ok':False,'task_completion_verified':False}
        result=probe_diagnostics(client,nonce,case_id)
        return {'nonce':nonce,'observed_at':now(),
                'ok':result.transport_verified is True and result.artifact_orm_write_read_verified is True,
                'transport_verified':result.transport_verified,
                'artifact_orm_write_read_verified':result.artifact_orm_write_read_verified,
                'task_completion_verified':False}
    return {'chrome':chrome,'vision':vision,'callback':callback}


def isolate_prepared_research(store, request, diagnostic=None):
    """Atomically isolate an interrupted invocation without touching its evidence."""
    from .domain import canonical_hash
    from .research_bridge_protocol import _valid_plan_input
    if not isinstance(request,str) or not request: return False
    with store._db() as db:
        record=store._get(db,request)
        if record.get('retired') or record.get('task_type') != 'research.bestseller': return False
        if any(record.get(k) for k in ('collector_pending','candidate_uploads','gallery_manifests','uploads','result')): return False
        if record.get('state') == 'unknown': return True
        if record.get('state') != 'prepared': return False
        plan=record.get('collector_plan')
        if plan is not None:
            if not isinstance(plan,dict) or not plan: return False
            signed=dict(plan); digest=signed.pop('digest',None)
            snapshot=record.get('context',{}).get('snapshot',{})
            try:
                if (digest != canonical_hash(signed) or signed.get('request_id') != request
                    or signed.get('company_id') != snapshot.get('company_id')
                    or signed.get('case_id') != snapshot.get('case_id')
                    or signed.get('snapshot_id') != snapshot.get('snapshot_id')
                    or signed.get('input_hash') != canonical_hash(record['payload'])
                    or not _valid_plan_input(signed.get('plan',{}))): return False
            except (KeyError,TypeError,ValueError): return False
        record.update(state='unknown',reason='unknown',resume_state='prepared')
        if diagnostic:
            record['dispatch_diagnostic'] = diagnostic
        store._put(db,record)
        return True


def settle_interrupted_upload(store, client, journal, request):
    """Read back an original receipt before retrying interrupted gallery work."""
    from .candidate_image_upload import reconcile
    from .client import APIError
    from .collector_cli import reconcile_evidence
    from .host_session import fence, worker_records
    from .research_return_bridge import next_gallery_step

    record = store.inspect(request)
    state = record.get('state')
    if state not in ('collector_upload_unknown', 'candidate_images_pending') or record.get('retired'):
        return False
    active = [row['claim'] for row in worker_records(journal)
              if not row.get('done') and not row.get('recovery_pending') and row.get('request_id') == request]
    if len(active) != 1:
        return False
    def renew():
        fence(client, journal, request)
    try:
        renew()
        if state == 'collector_upload_unknown':
            reconcile_evidence(store, request, active[0], record['collector_pending'].get('artifact_id'), client)
            return True
        step = next_gallery_step(record)
        if step and step['stage'] == 'reconcile_image':
            reconcile(store, request, active[0], step['identity'], None, client, renew, order=step['order'])
            return True
    except APIError as exc:
        if exc.transient:
            raise  # Recheck this exact upload when Odoo is reachable.
        return False
    except ValueError:
        # Keep the original intent, digest and receipt in the retired record.
        return False
    return False


def validate_targeted_state(journal, host, task_id):
    """Do not dispatch another task from a reused durable session."""
    if type(task_id) is not int or task_id <= 0: raise ValueError('Invalid task ID')
    ids=set()
    for path, table in ((journal,'runs'),(host,'requests')):
        p=Path(path)
        if not p.exists() or p.stat().st_size == 0: continue
        with closing(sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone(): continue
            rows=db.execute('SELECT id,record FROM '+table).fetchall()
            if table=='runs':
                for _,raw in rows:
                    record=json.loads(raw)
                    if record.get('claim',{}).get('id') != task_id: raise ValueError('Foreign task in journal')
                    if record.get('request_id'): ids.add(record['request_id'])
            elif any(row[0] not in ids for row in rows): raise ValueError('Foreign request in session')

def recover_expired_original(client, record, save):
    claim=record.get('claim') or {}
    request=record.get('request_id')
    if record.get('done') or record.get('recovery_pending') or not request: raise ValueError('Original active request required')
    receipt=client.retry_expired_research(claim,request)
    if (not isinstance(receipt,dict) or receipt.get('source_task_id')!=claim.get('id') or
            type(receipt.get('task_id')) is not int or receipt['task_id']!=claim.get('id') or receipt.get('state') not in ('queued', 'waiting')):
        raise ValueError('Invalid retry receipt')
    retired=dict(record,done=True,outcome='expired_unknown_retried' if receipt['state']=='queued' else 'waiting',replacement_task_id=receipt['task_id'])
    save(retired)
    return True

def validate_scoped_preflight(client, task_id, case_id, records, preflight):
    active=[r for r in records if not r.get('done')]
    if len(active)>1: raise ValueError('Ambiguous scoped request')
    resume=None
    if active:
        row=active[0]; claim=row.get('claim') or {}; original=claim.get('id')
        if type(original) is not int or original<=0 or (task_id is not None and original!=task_id) or not row.get('request_id'):
            raise ValueError('Original scoped request required')
        task_id=original
        resume={'request_id':row['request_id'],'lease_token':claim.get('lease_token'),'lease_epoch':claim.get('lease_epoch')}
    if not preflight(client,task_id,resume=resume,case_id=case_id): raise ValueError('Scoped identity or task not verified')


def continue_scoped_startup(client, task_id, store, journal, dispatcher):
    from .domain import canonical_hash
    from .runtime import REGISTERED_RUN_PHASES, continue_registered_run

    active = journal.active()
    if len(active) != 1:
        return False
    row = active[0]
    claim = row['claim']
    if task_id is not None and claim['id'] != task_id:
        raise ValueError('Foreign task in journal')
    if (row.get('phase') not in REGISTERED_RUN_PHASES
            or row.get('pending') or row.get('recovery_pending')):
        return False
    request = row['request_id']
    if request not in store.request_ids():
        return False
    host = store.inspect(request)
    if (host.get('retired') or host.get('request_id') != request or claim['task_type'] != 'research.bestseller'
            or host.get('binding') != canonical_hash({'kind': claim['task_type'], 'payload': claim['payload'], 'context': row['context']})):
        raise ValueError('Original live local research request required')
    identity = client.request('POST', '/internal/meli-optimization/scoped-preflight', {'task_id': claim['id']})
    if (not isinstance(identity, dict) or identity.get('task_id') != claim['id']
            or not all(identity.get(k) is True for k in ('scoped_worker','non_admin','legacy_artifact_denied'))):
        raise ValueError('Scoped worker identity not verified')
    continue_registered_run(client, row, journal)
    # After the server and the exclusive local service lock establish ownership,
    # retire only the stale invocation of a stage with no unacknowledged write.
    pending = host.get('collector_pending') or {}
    if host.get('state') == 'prepared':
        replayable = not any(host.get(k) for k in ('collector_pending', 'collector_evidence', 'collector_artifact_id',
                                                  'candidate_uploads', 'gallery_manifests', 'uploads', 'result'))
    elif host.get('state') == 'candidate_images_pending' and isinstance(pending, dict):
        uploads = pending.get('candidate_uploads', {})
        replayable = (pending.get('ledger_v1') is True and host.get('collector_artifact_id')
                      and pending.get('artifact_id') == host['collector_artifact_id']
                      and isinstance(uploads, dict) and all(isinstance(u, dict) and u.get('state') == 'confirmed'
                                                              and u.get('artifact_id') for u in uploads.values()))
    else:
        replayable = False
    if replayable:
        dispatcher.acknowledge_reconciliation(request)
    return True


def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=('once','loop'));parser.add_argument('--task-id',type=int);args=parser.parse_args()
    try:
        from .client import Client, APIError
        from .host_session import SessionStore, fence, worker_records
        from .cooperative_research_host import ClaimGate, GatedClient, CooperativeResearchHost
        from .host_session import SessionProvider
        from .runtime import Worker
        from .interactive_dispatcher import Dispatcher, ALLOWED_ACTIONS
        from .research_return_bridge import build_bridge
        journal=os.environ['OPTIMIZATION_JOURNAL'];host=os.environ['OPTIMIZATION_HOST_SESSION']
        dispatch_journal=os.environ['OPTIMIZATION_DISPATCH_JOURNAL'];lock=os.environ['OPTIMIZATION_RESEARCH_LOCK']
        paths=[journal,host,dispatch_journal,lock]
        if any(not Path(p).is_absolute() for p in paths): raise ValueError('Absolute state paths required')
        if args.task_id is not None: validate_targeted_state(journal,host,args.task_id)
        config={'home':os.environ['OPTIMIZATION_INTERACTIVE_CODEX_HOME'],
                'cwd':os.environ['OPTIMIZATION_WORKSPACE'],
                'computer_use_cwd':os.environ['OPTIMIZATION_COMPUTER_USE_CWD'],
                'proxy':os.environ.get('OPTIMIZATION_CODEX_PROXY','')}
        kinds=('chrome','vision','callback')
        timeouts={kind:float(os.environ.get('OPTIMIZATION_PROBE_'+kind.upper()+'_TIMEOUT','120')) for kind in kinds}
        max_age=float(os.environ.get('OPTIMIZATION_PROBE_MAX_AGE','30'))
        client=Client(os.environ.get('ODOO_OPTIMIZATION_URL','http://127.0.0.1:8069'),os.environ.get('ODOO_API_KEY',''),research_ledger_enabled=os.environ.get('OPTIMIZATION_RESEARCH_LEDGER_V1') == '1')
        if os.environ.get('OPTIMIZATION_USE_LIVE_ADAPTERS') == '1':
            if timeouts['chrome']>120 or timeouts['vision']>120: raise ValueError('Local probe timeout exceeds bound')
            raw_case=os.environ.get('OPTIMIZATION_PROBE_CASE_ID','')
            case_id=int(raw_case) if raw_case.isascii() and raw_case.isdigit() and int(raw_case)>0 else None
            probes=build_live_adapters(config,client,timeouts,case_id=case_id)
        else:
            probes={kind:adapter(os.environ.get('OPTIMIZATION_PROBE_'+kind.upper()),timeout=timeouts[kind]) for kind in kinds}
        diagnostics=CapabilityDiagnostics(lambda message: print(message,file=sys.stderr,flush=True))
        def observe(): return diagnostics.report(check_research_capabilities(probes,max_duration=max(timeouts.values()),max_age=max_age))
        cache_ttl,idle_poll=timing_config(os.environ)
        probe_ready=SingleFlightReadiness(observe,ttl=cache_ttl).ready
        retry_poll = UnknownRetryPoll(client.retry_unknown_research_batch) if args.task_id is None else None
        ready = probe_ready
        store=SessionStore(host,client.identity)
        gate=ClaimGate(ready)
        gated_client=GatedClient(client,gate,queue_check=client.has_claimable_research if args.task_id is None else None)
        worker=Worker(gated_client,journal,providers={'research.bestseller':SessionProvider(store)},
                      capabilities=('research.bestseller',),task_id=args.task_id,single_active=True)
        def receipt_progress(request):
            from .tab_receipt_store import TabReceiptStore
            records=[r for r in worker_records(journal) if r.get('request_id') == request and not r.get('done')]
            if len(records) != 1: raise ValueError('Active lease required')
            bound=store.inspect(request).get('collector_plan') or {}
            plan=bound.get('plan') or {}
            queries=plan.get('queries')
            if not isinstance(queries,list) or len(queries)!=3: return 0
            ledger=Path(journal).parent/'collector-bindings.sqlite'
            c = records[0]['claim']
            key = (request,bound.get('digest'),c['lease_epoch'])
            receipts=TabReceiptStore(str(ledger));staged=plan.get('strategy')=='staged-v1'
            if plan.get('strategy') in ('card-visual-v3','card-home-v4','shein-home-v1'):
                import sqlite3
                from contextlib import closing
                from .visual_candidate import VisualCandidate
                if plan.get('strategy') in ('card-home-v4','shein-home-v1'):
                    options={'platform':'shein-us','threshold':bound['collection_config']['min_sold']} if plan.get('strategy')=='shein-home-v1' else {}
                    state=receipts.card_home_state(*key,queries,lambda:(fence(client,journal,request) or True),**options)
                    groups=sum(state['progress']['completed_pages'])
                else:
                    state=receipts.card_v3_state(*key,queries,lambda:(fence(client,journal,request) or True))
                    groups=receipts.resume_index(*key[:2],state['queries'],key[2],card_v3=True)
                with closing(sqlite3.connect(str(ledger))) as db:
                    steps=sum(db.execute('SELECT count(*) FROM '+table+' WHERE request_id=? AND plan_digest=? AND lease_epoch=?',key).fetchone()[0]
                              for table in ('card_v3_batches','card_v3_pages','card_v3_details','card_v3_visual_evidence'))
                details=state['progress']['detail_count'] if groups>=3 else 0
                photos=VisualCandidate(ledger,key,plan,None,c).durable_photo_count()
                return groups,details,photos,5+len(state['queries']),steps
            if plan.get('strategy')=='visual-v2':
                from .visual_candidate import VisualCandidate
                declared=receipts.visual_v2_queries(*key,lambda: (fence(client,journal,request) or True))
                if not declared:return (0,0,0,8)
                groups=receipts.next_query_index(*key[:2],declared,key[2],visual_v2=True)
                details=receipts.detail_status(*key)['saved']
                photos=VisualCandidate(ledger,key,plan,None,c).durable_photo_count()
                return groups,details,photos,5+len(declared)
            groups, details = receipts.collection_progress(*key, **({'staged':True} if staged else {}))
            if plan.get('platform') == 'mercado-libre-mx':
                from .visual_candidate import VisualCandidate
                photos=VisualCandidate(ledger,key,plan,None,c).durable_photo_count()
                if staged:
                    extra=receipts.sealed_queries(*key,lambda: (fence(client,journal,request) or True))
                    return groups,details,photos,1+len(extra)
                return groups, details, photos
            return groups, details
        dispatcher=Dispatcher(dispatch_journal,store,before=lambda request:fence(client,journal,request), progress_probe=receipt_progress,
                              bridge_factory=lambda request:build_bridge(request,client,store,journal),config=config,host_probe=ready)
        from .archived_recovery import resume_waiting_gallery
        from .home_verified_recovery import resume_verified_waiting
        def resume_gallery(locked_journal):
            try:
                resume_verified_waiting(locked_journal, store, client, Path(journal).parent/'collector-bindings.sqlite', args.task_id)
            except (APIError, ValueError, OSError, sqlite3.Error):
                print('Verified browser recovery could not be completed; task remains waiting.', file=sys.stderr)
            return resume_waiting_gallery(locked_journal,store,client,dispatcher,args.task_id)
        if client.research_ledger_enabled:
            worker.resume_waiting = resume_gallery
        stop=threading.Event()
        for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,lambda *_:stop.set())
        def isolate_unknown():
            request = dispatcher.last_unknown_request
            diagnostic = dispatcher.failure_diagnostic(request)
            if settle_interrupted_upload(store, client, journal, request):
                dispatcher.acknowledge_reconciliation(request)
                return True
            if isolate_prepared_research(store, request, diagnostic):
                return True
            with store._db() as db:
                record = store._get(db, request)
                if (record.get('retired') or record.get('task_type') != 'research.bestseller'
                        or record.get('state') not in ('collector_upload_unknown', 'candidate_images_pending')):
                    return False
                record.update(state='unknown', resume_state=record['state'], reason='unknown',
                              dispatch_diagnostic=diagnostic)
                store._put(db, record)
            return True
        def dispatch_needed():
            if not Path(journal).exists():
                return False
            try:
                records = worker_records(journal)
            except sqlite3.OperationalError as exc:
                if 'no such table: runs' in str(exc):
                    return False
                raise
            active = [r for r in records if not r.get('done')]
            if len(active) > 1:
                raise ValueError('Multiple active research requests require reconciliation')
            return bool(active and not active[0].get('recovery_pending') and store.next(kinds={'research.bestseller'},request_id=active[0]['request_id']).get('action') in ALLOWED_ACTIONS)
        host_runner=CooperativeResearchHost(gate,worker,dispatcher.tick,idle_poll_seconds=idle_poll, isolate_unknown=isolate_unknown,
            dispatch_needed=dispatch_needed, status_provider=gated_client.queue_status)
        def locked_validation():
            if args.task_id is not None: validate_targeted_state(journal,host,args.task_id)
            if client.research_ledger_enabled:
                from .research_launchd import scoped_identity_preflight
                raw_case=os.environ.get('OPTIMIZATION_PROBE_CASE_ID','')
                scoped_case=int(raw_case) if raw_case.isascii() and raw_case.isdigit() and int(raw_case)>0 else None
                records=[]
                if Path(journal).exists() and Path(journal).stat().st_size:
                    try: records=worker_records(journal)
                    except sqlite3.OperationalError as exc:
                        if 'no such table: runs' not in str(exc): raise
                if records:
                    from .journal import Journal
                    active_journal = Journal(journal, client.identity)
                    try:
                        if not active_journal.active() and resume_gallery(active_journal):
                            return
                        if continue_scoped_startup(client,args.task_id,store,active_journal,dispatcher):
                            return
                    finally:
                        active_journal.close()
                try:
                    validate_scoped_preflight(client,args.task_id,scoped_case,records,scoped_identity_preflight)
                except ValueError:
                    active=[r for r in records if not r.get('done')]
                    if args.task_id is not None or len(active)!=1: raise
                    # Verify identity independently; the server itself checks the
                    # original owner, request and expired lease before retrying.
                    if not scoped_identity_preflight(client,None,case_id=scoped_case): raise
                    from .journal import Journal
                    with_journal=Journal(journal,client.identity)
                    try: recover_expired_original(client,active[0],with_journal.save)
                    finally: with_journal.close()
                    validate_scoped_preflight(client,None,scoped_case,worker_records(journal),scoped_identity_preflight)
        with ResearchService.from_host(host_runner,lock_path=lock,validate=locked_validation) as service:
            while not stop.is_set():
                if retry_poll is not None:
                    retry_poll.tick()
                print(service.tick(),flush=True)
                if args.mode=='once': break
                stop.wait(5)
        return 0
    except Exception as error:
        import traceback
        frame = traceback.extract_tb(error.__traceback__)[-1]
        print(f'Research service unavailable: stage={frame.name} code={type(error).__name__} '
              f'location={Path(frame.filename).name}:{frame.lineno} '
              f'status={getattr(error, "status", None)}', file=sys.stderr)
        return 1

if __name__=='__main__': raise SystemExit(main())

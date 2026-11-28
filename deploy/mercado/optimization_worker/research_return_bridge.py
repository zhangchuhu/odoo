"""In-process, request-scoped collector return bridge; no network listener.

The embedding process owns the Odoo credential. This class does not isolate a
same-user Codex process from that credential; use a separate OS security boundary.
"""
import hashlib
from .research_result_candidates import all_candidates
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
_FIELDS = {'prepare_suggestions': {'source_artifact_id','viewed_source_sha256','category','anchor'}, 'record_suggestions': {'page_url','prefix','visible_input','state','suggestions','screenshot'}, 'suggestions_unavailable': {'reason'}, 'seal_suggestions': {'profile'}, 'home_expansion_options': set(), 'reserve_captcha_attempt': {'stage','action_id'}, 'arm_home_verification': {'stage'}, 'visual_candidate_status': set(), 'submit_visual_verdict': {'submission_id','receipts','status','reason','category','match_level','features'}, 'page_blocked': {'code'}, 'window_blocked': {'code'}, 'binding_status': set(), 'seal_expansion': {'queries'}, 'prior_lease_status': {'request_id'}, 'plan': None, 'submit': None, 'submit_staged': {'observed_at'}, 'manifest': {'identity','manifest','screenshot'},
           'image_saved': {'identity','order'}, 'image': {'identity','file','screenshot','source','order'}, 'finish': set(),
           'reconcile_image': {'identity','artifact_id','order'}, 'reconcile_evidence': {'artifact_id'}}
_PAGE_DIAGNOSTIC = {
    'stage': {'search','pagination','detail','visual','gallery','binding','unknown'},
    'symptom': {'results_not_visible','search_input_missing','page_loading','page_error','access_blocked','captcha','login','tab_mismatch','extension_error','navigation_failed','computer_browser_enumeration_unavailable','unknown'},
    'page_type': {'search','detail','extension','other','unknown'},
    'tab_match': {'yes','no','unknown'},
}
_EXTENSION_DIAGNOSTIC = {
    'extension_stage': {'binding_tab','binding_session','binding_status','binding_anchor','binding_select','binding_confirm','binding_guard','binding_wait','binding_activate','binding_window','binding_task_tab','query_challenge','query_tab_get','query_page_extract','query_receipt','query_submit','collector_action','unknown'},
    'extension_code': {'session_timeout','status_timeout','select_timeout','anchor_recovery_timeout','anchor_recovery_unavailable','anchor_rebind_pending','anchor_select_pending','broker_identity_unavailable','window_timeout','tab_timeout','session_changed_reopen','task_tab_landing_timeout','guard_ack_timeout','guard_binding_changed','receipt_query_mismatch','receipt_page_url_mismatch','receipt_page_extract_error','content_receiver_missing','evidence_not_staged','error','type_error','other_error','unknown'},
}

def window_blocker_safe(supervisor, broker=None):
    """Permit a known pause only before any target tab has been observed."""
    if supervisor is None:
        return False
    broker = broker or supervisor.active_broker
    if not broker or broker.initial_tab is not None or broker.done or broker.conflict or broker.candidates:
        return False
    try:
        if broker.fence() is not True:
            return False
        anchor_guard = getattr(broker, 'anchor_guard_alive', None)
        if callable(anchor_guard) and anchor_guard():
            return False
    except Exception:
        return False
    if broker.window is None and broker.session is None:
        return True
    if type(broker.window) is not int or not broker.session:
        return False
    import time
    return time.monotonic() - broker.started >= 15

def close_for_window_blocker(supervisor):
    if supervisor is None:
        return None
    broker = supervisor.active_broker
    if not window_blocker_safe(supervisor, broker):
        return None
    # Join the broker before rechecking; late observations cannot cross this fence.
    supervisor.close()
    return broker if window_blocker_safe(supervisor, broker) else None


def next_gallery_step(record):
    """Describe the next gallery operation using only committed host receipts."""
    state = record.get('state')
    if state == 'resolved':
        return {'stage': 'complete'}
    if state != 'candidate_images_pending':
        return None
    pending = record['collector_pending']
    uploads = pending.get('candidate_uploads', {})
    manifests = pending.get('gallery_manifests', {})
    from .research_result_candidates import all_candidates
    for candidate in all_candidates(pending['result']):
        identity = candidate['platform'] + ':' + candidate['item_id']
        target = {'identity': identity, 'detail_url': candidate['url']}
        manifest = manifests.get(identity)
        if manifest is None:
            return dict(target, stage='manifest')
        for order, source in enumerate(manifest['images']):
            upload = uploads.get(f'{identity}:{order}')
            if upload is None:
                return dict(target, stage='image', order=order, source=source)
            if upload.get('state') != 'confirmed':
                return dict(target, stage='reconcile_image', order=order)
    return {'stage': 'finish'}


def gallery_save_path(directory, step):
    key = json.dumps([step['identity'], step['order'], step['source']], ensure_ascii=False).encode()
    suffix = Path(urlsplit(step['source']).path).suffix.lower()
    suffix = suffix if suffix in ('.png', '.jpg', '.jpeg', '.webp') else '.jpg'
    return Path(directory) / ('original-' + hashlib.sha256(key).hexdigest()[:24] + suffix)


def read_saved_original(path):
    # Do not follow a redirected filename or block on a special file.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or not 0 < info.st_size <= 8 * 1024 * 1024):
            raise ValueError('Invalid saved original')
        raw = stream.read(8 * 1024 * 1024 + 1)
        if len(raw) != info.st_size or os.fstat(fd).st_mtime_ns != info.st_mtime_ns:
            raise ValueError('Saved original changed during read')
        return raw


class ReturnBridge:
    def __init__(self, request_id, claim_loader, fence, handlers, write_guard=None):
        if not isinstance(request_id,str) or not _UUID.fullmatch(request_id): raise ValueError('Invalid request')
        self.request_id,self.claim_loader,self.fence,self.handlers = request_id,claim_loader,fence,dict(handlers)
        self._close = lambda: None
        self.write_guard = write_guard
        self.save_directory = None

    def close(self):
        self._close()

    def call(self, request_id, operation, data):
        if request_id != self.request_id or operation not in _FIELDS or operation not in self.handlers or not isinstance(data,dict):
            raise ValueError('Unauthorized operation')
        fields=_FIELDS[operation]
        visual_v2_fields={'submission_id','receipts','candidate_primary','secondary','styles','occasions','critical_conflict','features'}
        visual_v3_fields=visual_v2_fields-{'critical_conflict'}
        if fields is not None and set(data)!=fields and not (operation=='submit_visual_verdict' and set(data) in (visual_v2_fields,visual_v3_fields)) and not (operation=='page_blocked' and set(data)=={'code','diagnostic'}):
            raise ValueError('Invalid fields')
        if operation == 'window_blocked' and data.get('code') != 'window_registration_failed': raise ValueError('Invalid blocker')
        if operation == 'page_blocked' and data['code'] not in ('captcha_required', 'login_required', 'page_unreadable', 'browser_unavailable'):
            raise ValueError('Invalid page blocker')
        if operation == 'page_blocked' and 'diagnostic' in data:
            diag=data['diagnostic']
            if (not isinstance(diag,dict) or set(diag) not in (set(_PAGE_DIAGNOSTIC), set(_PAGE_DIAGNOSTIC)|set(_EXTENSION_DIAGNOSTIC))
                    or any(not isinstance(diag[k],str) or diag[k] not in choices for k,choices in _PAGE_DIAGNOSTIC.items())
                    or any(k in diag and (not isinstance(diag[k],str) or diag[k] not in choices) for k,choices in _EXTENSION_DIAGNOSTIC.items())):
                raise ValueError('Invalid page diagnostic')
        if 'artifact_id' in data and not (operation in ('reconcile_image', 'reconcile_evidence') and data['artifact_id'] is None) and (not isinstance(data['artifact_id'],str) or not data['artifact_id'].isdigit()):
            raise ValueError('Invalid artifact identifier')
        if operation=='prior_lease_status' and (not isinstance(data['request_id'],str) or not _UUID.fullmatch(data['request_id']) or data['request_id']==self.request_id):
            raise ValueError('Distinct prior request required')
        self.fence()
        claim=self.claim_loader()
        if not isinstance(claim,dict) or claim.get('task_type')!='research.bestseller': raise ValueError('Inactive research lease')
        payload=dict(data)
        for key in ('file','screenshot'):
            if key in payload and (type(payload[key]) is not bytes or not 0<len(payload[key])<=8*1024*1024):
                raise ValueError('Invalid image bytes')
        if operation in ('plan','submit') and len(json.dumps(data,ensure_ascii=False,allow_nan=False).encode()) > (65536 if operation=='plan' else 5*1024*1024):
            raise ValueError('Input too large')
        with tempfile.TemporaryDirectory(prefix='research-bridge-') as tmp:
            for key in ('file','screenshot'):
                if key in payload:
                    path=Path(tmp)/key
                    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(fd,'wb') as f: f.write(payload[key])
                    payload[key]=str(path)
            if operation in ('submit','submit_staged','manifest','image','image_saved','finish') and self.write_guard is not None:
                self.write_guard()
            result=self.handlers[operation](claim,payload)
        if operation in ('visual_candidate_status','submit_visual_verdict'):
            if not isinstance(result,dict): raise ValueError('Invalid visual response')
            return {'accepted':True,'visual':result}
        if operation=='seal_suggestions':
            if not isinstance(result,dict) or set(result)!={'queries'} or not isinstance(result['queries'],list) or len(result['queries'])!=3:
                raise ValueError('Invalid suggestion selection')
            return {'accepted':True,'selection':result}
        if operation=='home_expansion_options':
            if not isinstance(result,dict) or set(result)!={'queries'} or not isinstance(result['queries'],list) or len(result['queries'])>3 or any(not isinstance(q,str) or not q.strip() for q in result['queries']):
                raise ValueError('Invalid expansion options')
            return {'accepted':True,'expansion':result}
        if operation=='reserve_captcha_attempt':
            if not isinstance(result,dict) or set(result)!={'attempt'} or type(result['attempt']) is not int or result['attempt'] not in (1,2):raise ValueError('Invalid verification reservation')
            return {'accepted':True,'captcha':result}
        if operation=='arm_home_verification':
            if not isinstance(result,dict) or set(result)!={'stage'} or result['stage'] not in ('search','detail'):raise ValueError('Invalid verification watch')
            return {'accepted':True,'watch':result}
        if operation=='binding_status':
            if not isinstance(result,dict): raise ValueError('Invalid binding status')
            return {'accepted':True,'binding':result}
        if operation=='prior_lease_status':
            if not isinstance(result,dict) or set(result)!={'inactive'} or type(result['inactive']) is not bool: raise ValueError('Invalid status')
            return {'accepted':True,'inactive':result['inactive']}
        return {'accepted':True}


def build_bridge(request_id, client, store, journal, *, ledger_path_override=None):
    from .host_session import worker_records, fence
    from .collector_cli import bind_plan, import_evidence, reconcile_evidence
    from .candidate_image_upload import bind_gallery, send, finish, reconcile
    from .tab_receipt_store import TabReceiptStore
    from .tab_receipt_factory import build_native_broker
    from .tab_receipt_supervisor import NativeSupervisor,CardPageSupervisor
    from .native_collector_host import trace_supervisor
    private = Path(__file__).resolve().parents[3] / '.local/optimization-codex-worker'
    ledger_path = Path(ledger_path_override) if ledger_path_override is not None else private / 'collector-bindings.sqlite'
    socket_path = private / 'collector-native.sock'
    supervisor = None
    last_progress = None
    def renew(): fence(client,journal,request_id)
    def prepare_suggestions(c,data):
        from .home_suggestion_bootstrap import bind_bootstrap
        record=store.inspect(request_id)
        config=record.get('context',{}).get('snapshot',{}).get('payload',{}).get('collection_config',{})
        if not isinstance(config,dict) or config.get('strategy')!='card-home-v4':
            raise ValueError('Homepage suggestion strategy required')
        bind_bootstrap(store,request_id,c,data['source_artifact_id'],data['viewed_source_sha256'],
                       data['category'],data['anchor'],fence=lambda:(renew() or True))
    def seal_suggestions(c,data):
        from contextlib import closing
        import sqlite3
        from .home_suggestion_bootstrap import seal_suggestion_plan
        from .home_suggestion_checkpoint import initialize
        with closing(sqlite3.connect(str(ledger_path))) as db,db:
            initialize(db)
            selected=seal_suggestion_plan(store,request_id,c,db,data['profile'],fence=lambda:(renew() or True))
        return {'queries':selected}
    def suggestions_unavailable(c,data):
        from .home_suggestion_bootstrap import mark_suggestions_unavailable
        mark_suggestions_unavailable(store,request_id,c,data['reason'],fence=lambda:(renew() or True))
    def record_suggestions(c,data):
        from contextlib import closing
        import sqlite3
        from .home_suggestion_bootstrap import validate_bootstrap
        from .home_suggestion_checkpoint import initialize,reads
        from .home_suggestion_ingest import ingest
        record=store.inspect(request_id);draft=record.get('suggestion_bootstrap')
        if record.get('state')!='prepared' or record.get('retired') or not validate_bootstrap(draft,c,request_id):
            raise ValueError('Current suggestion bootstrap required')
        if data.get('state')=='blocked':raise ValueError('Blocked page evidence must not be stored as suggestions')
        raw=Path(data['screenshot']).read_bytes();sha=hashlib.sha256(raw).hexdigest()
        stored=Path(store._file(raw))
        body={k:v for k,v in data.items() if k!='screenshot'}
        with closing(sqlite3.connect(str(ledger_path))) as db,db:
            initialize(db)
            scope=(request_id,draft['digest'],c['lease_epoch'])
            previous=next((row for row in reads(db,scope) if row['prefix']==body.get('prefix')),None)
            stamp=previous['observed_at'] if previous and previous['evidence_sha256']==sha else datetime.now(timezone.utc).isoformat()
            ingest(db,scope,body,sha,stamp,
                   fence=lambda:(renew() or True),verify=lambda digest:digest==sha and stored.is_file() and hashlib.sha256(stored.read_bytes()).hexdigest()==digest)
    def claim():
        rows=[r for r in worker_records(journal) if r.get('request_id')==request_id and not r.get('done')]
        if len(rows)!=1: raise ValueError('Active lease required')
        return rows[0]['claim']
    def ensure_supervisor():
        nonlocal supervisor
        if supervisor is not None: return
        renew()
        current=claim()
        record=store.inspect(request_id)
        binding=record.get('collector_plan') or {}
        plan=binding.get('plan') or {}
        from .research_result_candidates import all_candidates
        direct_gallery = (record.get('state')=='candidate_images_pending' and getattr(client,'research_ledger_enabled',False) is True
                          and all_candidates(record.get('collector_pending',{}).get('result') or {'candidates':[]}))
        ledger=TabReceiptStore(str(ledger_path))
        staged=plan.get('strategy')=='staged-v1'
        visual=plan.get('strategy')=='visual-v2'
        card_v3=plan.get('strategy')=='card-visual-v3'
        card_home=plan.get('strategy')=='card-home-v4'
        if card_home and not direct_gallery:
            ledger.card_home_state(request_id,binding['digest'],current['lease_epoch'],plan['queries'],lambda:(renew() or True))
            def home_page_state():
                state=ledger.card_home_state(request_id,binding['digest'],current['lease_epoch'],plan['queries'],lambda:(renew() or True))
                if state['next_page'] is None and not ledger.current_binding(request_id):return (len(state['queries'])-1,0)
                return state['next_page']
            def home_rebind_step():
                state=ledger.card_home_state(request_id,binding['digest'],current['lease_epoch'],plan['queries'],lambda:(renew() or True))
                return (len(state['queries'])-1,0)
            def home_retry(index,clear=False):
                import sqlite3
                from contextlib import closing
                from .home_retry_checkpoint import initialize,reserve_retry,clear_retry
                with closing(sqlite3.connect(str(ledger_path))) as db,db:
                    db.execute('BEGIN IMMEDIATE');initialize(db)
                    scope=(request_id,binding['digest'],current['lease_epoch'])
                    return (clear_retry if clear else reserve_retry)(db,scope,index,fence=lambda:(renew() or True))
            supervisor=CardPageSupervisor(lambda number,rebind=False,watch_only=False:build_native_broker(socket_path,ledger_path,request_id,number,store,client,journal,rebind=rebind,watch_only=watch_only,dedicated_required=True),home_page_state,
                diagnostic=lambda phase,code,exception:trace_supervisor(private/'collector-window-trace.jsonl',request_id,phase,code,exception),homepage=True,rebind_step=home_rebind_step,
                reserve_retry=lambda index:home_retry(index),clear_retry=lambda index:home_retry(index,clear=True))
            supervisor.start()
            return
        if card_v3 and not direct_gallery:
            ledger.card_v3_state(request_id,binding['digest'],current['lease_epoch'],plan['queries'],lambda:(renew() or True))
            def page_state():
                return ledger.card_v3_resume_step(request_id,binding['digest'],current['lease_epoch'],plan['queries'])
            supervisor=CardPageSupervisor(lambda number,rebind=False,watch_only=False:build_native_broker(socket_path,ledger_path,request_id,number,store,client,journal,rebind=rebind,watch_only=watch_only,dedicated_required=True),page_state,
                diagnostic=lambda phase,code,exception:trace_supervisor(private/'collector-window-trace.jsonl',request_id,phase,code,exception))
            supervisor.start()
            return
        def expansion_state():
            extra=ledger.sealed_queries(request_id,binding['digest'],current['lease_epoch'],lambda: (renew() or True))
            if not extra:return None
            queries=plan['queries']+extra
            index=ledger.next_query_index(request_id,binding['digest'],queries,current['lease_epoch'],staged=True)
            if index>=3 and ledger.budget_elapsed(request_id,binding['digest'],current['lease_epoch'],lambda: (renew() or True))>=3600:
                return index,index
            return len(queries),index
        def visual_state():
            queries=ledger.visual_v2_queries(request_id,binding['digest'],current['lease_epoch'],lambda: (renew() or True))
            if not queries:
                queries=ledger.seal_visual_v2_queries(request_id,binding['digest'],current['lease_epoch'],plan['queries'],lambda: (renew() or True))
            index=ledger.next_query_index(request_id,binding['digest'],queries,current['lease_epoch'],visual_v2=True)
            return len(queries),index
        if direct_gallery:
            index=3
        elif visual:
            index=visual_state()[1]
        elif staged:
            state=expansion_state()
            index=state[1] if state else ledger.next_query_index(request_id,binding.get('digest'),plan['queries'],current.get('lease_epoch'),staged=True)
        else:
            index=ledger.next_query_index(request_id,binding.get('digest'),plan['queries'],current.get('lease_epoch'))
        options={'max_queries':9} if visual else {}
        supervisor=NativeSupervisor(lambda number, rebind=False, watch_only=False: build_native_broker(socket_path,ledger_path,request_id,number,store,client,journal,rebind=rebind,watch_only=watch_only,dedicated_required=True),start_index=index,diagnostic=lambda phase,code,exception: trace_supervisor(private/'collector-window-trace.jsonl',request_id,phase,code,exception),expansion_state=(visual_state if visual else expansion_state if staged else None) if not direct_gallery else None,**options)
        supervisor.start()
    def submit(c,data):
        # Caller must supply its actual observation timestamp in the evidence.
        if supervisor is None or not supervisor.fresh_confirmation():
            raise ValueError('Current browser session requires fresh verification')
        if set(data)!={'evidence','observed_at'} or not isinstance(data['observed_at'],str): raise ValueError('Observation time required')
        observed=datetime.fromisoformat(data['observed_at'].replace('Z','+00:00'))
        now=datetime.now(timezone.utc)
        if observed.utcoffset()!=timedelta(0) or not now-timedelta(hours=24)<=observed<=now: raise ValueError('Invalid observation time')
        receipts = TabReceiptStore(str(ledger_path))
        binding = store.inspect(request_id).get('collector_plan') or {}
        strategy=(binding.get('plan') or {}).get('strategy')
        if strategy not in ('visual-v2','card-visual-v3','card-home-v4'):
            receipts.require_checkpoint_evidence(request_id, binding.get('digest'), c['lease_epoch'], data['evidence'],staged=strategy=='staged-v1')
        if store.inspect(request_id).get('state')=='candidate_images_pending':
            raise ValueError('Research evidence is already archived for this request')
        count=import_evidence(store,request_id,c,data['evidence'],observed.isoformat(),client,renew,tab_receipts=receipts)
        if count == 0 and getattr(client,'research_ledger_enabled',False) is True:
            require_live_guard()
            return finish(store,request_id,c,renew,require_manifest=True,client=client)
        if count and getattr(client,'research_ledger_enabled',False) is True:
            after_reconcile()
        return count
    def plan(c,data):
        result=bind_plan(store,request_id,c,data)
        ensure_supervisor()
        return result
    def submit_staged(c,data):
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(str(ledger_path))) as db:
            row=db.execute('SELECT body FROM staged_evidence WHERE request_id=? AND lease_epoch=?',(request_id,c['lease_epoch'])).fetchone()
        if not row: raise ValueError('No evidence staged for current lease')
        return submit(c,{'evidence':json.loads(row[0]),'observed_at':data['observed_at']})
    def seal_expansion(c,data):
        from .collector_progress import handle_progress
        broker=supervisor.active_broker if supervisor else None
        if not broker or not broker.watch_only or not supervisor.fresh_confirmation():
            raise ValueError('Current browser session requires fresh verification')
        event=dict(operation='seal_expansion',request_id=request_id,window_id=broker.window,
                   session_id=broker.session,tab_id=broker.tab,native_origin=broker.anchor_origin,queries=data['queries'])
        return handle_progress(broker,event)
    def home_expansion_options(c,data):
        broker=supervisor.active_broker if supervisor else None
        if not broker or not broker.watch_only or not supervisor.fresh_confirmation() or not callable(getattr(broker,'home_expansion_candidates',None)):
            raise ValueError('Current homepage browser session required')
        record=store.inspect(request_id);binding=record.get('collector_plan') or {};plan=binding.get('plan') or {}
        if plan.get('strategy')!='card-home-v4':raise ValueError('Homepage strategy required')
        state=TabReceiptStore(str(ledger_path)).card_home_state(request_id,binding['digest'],c['lease_epoch'],plan['queries'],lambda:(renew() or True))
        if state['next_page'] is not None or state['known_products']>=100 or len(state['queries'])>=9:
            raise ValueError('Expansion not needed or not ready')
        return {'queries':broker.home_expansion_candidates(state['queries'])[:min(3,9-len(state['queries']))]}
    def reserve_captcha_attempt(c,data):
        from contextlib import closing
        import sqlite3
        from .captcha_attempt_checkpoint import initialize,reserve_attempt
        record=store.inspect(request_id);config=record.get('context',{}).get('snapshot',{}).get('payload',{}).get('collection_config',{})
        if (record.get('retired') or record.get('state') not in ('prepared','candidate_images_pending')
                or not isinstance(config,dict) or config.get('strategy')!='card-home-v4'
                or data['stage'] not in ('suggestions','search','detail','visual','gallery')):
            raise ValueError('Active homepage verification required')
        task=(c['company_id'],c['id'],c['snapshot_id'])
        # A stable task-wide budget also prevents a changed reported stage from resetting attempts.
        challenge=hashlib.sha256(('home-captcha:'+':'.join(map(str,task))).encode()).hexdigest()
        with closing(sqlite3.connect(str(ledger_path))) as db,db:
            db.execute('BEGIN IMMEDIATE');initialize(db)
            attempt=reserve_attempt(db,task,challenge,data['action_id'],fence=lambda:(renew() or True))
        return {'attempt':attempt}
    def arm_home_verification(c,data):
        import sqlite3
        from contextlib import closing
        from .home_verification_watch import initialize,arm
        broker=supervisor.active_broker if supervisor else None
        stage=data['stage']
        if (stage not in ('search','detail') or not broker or broker.challenge.get('version')!=5
                or not broker.anchor_guard_alive() or broker.fence() is not True):raise ValueError('Current homepage browser required')
        if stage=='search':
            if not broker.query_verified or broker.watch_only:raise ValueError('Verified current search required')
            expected={'query':broker.challenge['query']}
        else:
            from .collector_progress import handle_progress
            if not broker.watch_only or not broker.guard_alive(broker.session):raise ValueError('Verified current detail required')
            result=handle_progress(broker,dict(operation='next_candidate',request_id=request_id,window_id=broker.window,
                session_id=broker.session,tab_id=broker.tab,native_origin=broker.anchor_origin))
            candidate=result.get('candidate') or {};identity=candidate.get('identity')
            if result.get('stage') not in ('detail','visual') or not isinstance(identity,str) or not identity.startswith('mercado-libre-mx:'):
                raise ValueError('Verified current detail required')
            expected={'product_id':identity.split(':',1)[1]}
        task=(c['company_id'],c['id'],c['snapshot_id'],c['lease_epoch'])
        browser={'tab_id':broker.tab,'window_id':broker.window,'session_id':broker.session}
        with closing(sqlite3.connect(str(ledger_path))) as db,db:
            db.execute('BEGIN IMMEDIATE');initialize(db);arm(db,request_id,task,stage,expected,browser,fence=lambda:(renew() or True))
        return {'stage':stage}
    def binding_status(c,data):
        nonlocal last_progress
        current_record = store.inspect(request_id)
        item=current_record.get('collector_plan') or {}
        plan=item.get('plan') or {};queries=plan.get('queries')
        ledger=TabReceiptStore(str(ledger_path));binding=ledger.current_binding(request_id)
        staged=plan.get('strategy')=='staged-v1'
        visual=plan.get('strategy')=='visual-v2'
        card_v3=plan.get('strategy')=='card-visual-v3'
        card_home=plan.get('strategy')=='card-home-v4'
        card_state=None
        if card_home and isinstance(queries,list) and len(queries)==3:
            card_state=ledger.card_home_state(request_id,item['digest'],c['lease_epoch'],queries,lambda:(renew() or True))
            queries=card_state['queries'];completed=sum(card_state['progress']['completed_pages'])
            card_state['resume_step']=card_state['next_page'] or ((len(queries)-1,0) if not binding else None)
            card_state['resume_url']=None
        elif card_v3 and isinstance(queries,list) and len(queries)==3:
            card_state=ledger.card_v3_state(request_id,item['digest'],c['lease_epoch'],queries,lambda:(renew() or True))
            queries=card_state['queries']
            completed=ledger.resume_index(request_id,item['digest'],queries,c['lease_epoch'],card_v3=True)
            card_state['resume_step']=ledger.card_v3_resume_step(request_id,item['digest'],c['lease_epoch'],plan['queries'])
            card_state['resume_url']=ledger.card_v3_resume_url(request_id,item['digest'],c['lease_epoch'],card_state['resume_step'])
        elif visual and isinstance(queries,list) and len(queries)==3:
            queries=ledger.visual_v2_queries(request_id,item['digest'],c['lease_epoch'],lambda: (renew() or True)) or queries
            completed=ledger.resume_index(request_id,item.get('digest'),queries,c.get('lease_epoch'),visual_v2=True)
        elif staged and isinstance(queries,list) and len(queries)==3:
            queries=queries+ledger.sealed_queries(request_id,item['digest'],c['lease_epoch'],lambda: (renew() or True))
            completed=ledger.resume_index(request_id,item.get('digest'),queries,c.get('lease_epoch'),staged=True)
        else:
            completed=ledger.resume_index(request_id,item.get('digest'),queries,c.get('lease_epoch')) if isinstance(queries,list) and len(queries)==3 else 0
        matches=bool(binding and completed>0 and binding.get('plan_digest')==item.get('digest'))
        selection=supervisor.window_selection_status() if supervisor else {'registered':False,'window_id':None,'observation_seconds_remaining':0}
        gallery_step = next_gallery_step(current_record)
        if (current_record.get('state')=='candidate_images_pending' and getattr(client,'research_ledger_enabled',False) is True
                and all_candidates(current_record.get('collector_pending',{}).get('result') or {'candidates':[]})):
            active = supervisor.active_broker if supervisor else None
            gallery = getattr(active,'gallery_binding',None)
            matches = bool(gallery and gallery.tab_matches(active) is True)
            binding = dict(tab_id=active.tab,window_id=active.window) if matches else None
        if gallery_step and gallery_step['stage'] == 'image' and bridge.save_directory:
            gallery_step['save_path'] = str(gallery_save_path(bridge.save_directory, gallery_step))
        collection_stage=None
        active=supervisor.active_broker if supervisor else None
        if (staged or visual or card_v3 or card_home) and active and active.watch_only and supervisor.fresh_confirmation():
            from .collector_progress import handle_progress, CollectorProgressError
            try:
                progress=handle_progress(active,dict(operation='next_candidate',request_id=request_id,
                    window_id=active.window,session_id=active.session,tab_id=active.tab,native_origin=active.anchor_origin))
                collection_stage={k:progress[k] for k in ('stage','action','reason','requires_expansion') if k in progress}
            except (CollectorProgressError, ValueError):
                pass
        if card_state:
            cp=card_state['progress']
            details={'saved':cp['detail_count'],'qualified':cp['threshold_met'],'supplemental':cp['supplemental']}
            saved_groups=sum(value is not None for value in cp['selected_per_query'])
        else:
            details=ledger.detail_status(request_id,item.get('digest'),c.get('lease_epoch'))
            saved_groups=(ledger.next_query_index(request_id,item.get('digest'),queries,c.get('lease_epoch'),visual_v2=True)
                      if visual else ledger.completed_card_index(request_id,item.get('digest'),c.get('lease_epoch'),**({'staged':True} if staged else {})))
        if staged or visual:
            stage=(collection_stage or {}).get('stage') or ('expanded' if staged and len(queries)>3 and saved_groups>=3 else 'search')
            progress=dict(stage=stage,queries=saved_groups,reviewed=details['saved'],qualified=details['qualified'],target=plan['result_limit'])
            if progress != last_progress:
                try:
                    client.research_progress(c,request_id,progress)
                    last_progress=progress
                except Exception:
                    pass # Display-only progress must not interrupt authenticated collection.
        elif card_state:
            cp=card_state['progress'];stage=(collection_stage or {}).get('stage')
            stage='finished' if stage=='finished' else 'visual' if stage in ('visual','detail') else 'search'
            keys=('known_products','completed_pages','selected_per_query','threshold_met','supplemental','excluded','visual_required','visual_done','visual_threshold_passed','visual_supplemental_passed')
            progress=dict(stage=stage,queries=saved_groups,reviewed=cp['detail_count'],qualified=min(cp['visual_threshold_passed'],plan['result_limit']),target=plan['result_limit'],card_v3={k:cp[k] for k in keys})
            if card_home:
                selected=(current_record.get('suggestion_selection') or {}).get('queries')
                if (not isinstance(selected,list) or len(selected)!=3 or any(not isinstance(row,dict) for row in selected)
                        or [row.get('text') for row in selected]!=plan['queries']
                        or any(row.get('source') not in ('category_anchor','platform_suggestion','image_fallback') for row in selected)):
                    raise ValueError('Original search sources required')
                progress['strategy']='card-home-v4'
                progress['end_reason']=card_state['end_reason']
                progress['search_terms']=[{'text':row['text'],'source':row['source']} for row in selected]
                progress['search_terms'] += [{'text':term,'source':'platform_suggestion'} for term in queries[3:]]
            if progress!=last_progress:
                try:
                    client.research_progress(c,request_id,progress);last_progress=progress
                except Exception:
                    pass
        failure=getattr(supervisor,'failure_code',None) if supervisor else None
        return {'tab_id':binding.get('tab_id') if matches else None,'window_id':binding.get('window_id') if matches else None,
                'binding_matches_plan':matches,'completed_queries':completed,'queries':queries if isinstance(queries,list) else [],
                'window_selection':selection,
                'anchor_required':True,'anchor_active':bool(supervisor and supervisor.anchor_active() is True),
                'gallery_step': gallery_step,
                'details': details,
                'saved_query_groups': saved_groups,'card_v3':card_state,
                'guard_active':bool(supervisor and supervisor.fresh_confirmation()),'collection_stage':collection_stage,
                'failure_code':failure if failure=='browser_unavailable' else None}
    def after_reconcile():
        nonlocal supervisor
        if not callable(getattr(store,'inspect',None)): return
        current=store.inspect(request_id)
        if current.get('state') not in ('prepared','candidate_images_pending'): return
        pending=current.get('collector_pending') or {}
        uploads=pending.get('candidate_uploads') or {}
        if not isinstance(uploads,dict) or any(not isinstance(v,dict) or v.get('state')!='confirmed' for v in uploads.values()): return
        if supervisor is not None:
            supervisor.close()
            supervisor=None
        ensure_supervisor()
    def reconcile_original_evidence(c,d):
        result=reconcile_evidence(store,request_id,c,d['artifact_id'],client)
        after_reconcile()
        return result
    def reconcile_original_image(c,d):
        result=reconcile(store,request_id,c,d['identity'],d['artifact_id'],client,renew,order=d['order'])
        after_reconcile()
        return result
    def window_blocked(c, data):
        nonlocal supervisor
        current=store.inspect(request_id)
        if current.get('state') != 'prepared' or current.get('collector_pending') or current.get('collector_artifact_id'):
            raise ValueError('Existing research progress requires reconciliation')
        old_supervisor=supervisor
        try:
            broker=close_for_window_blocker(old_supervisor)
        finally:
            # Never retain a stopped broker as if it were active. A rejected
            # blocker preserves the request as unknown for reconciliation.
            if old_supervisor is not None and old_supervisor.stop.is_set():
                supervisor=None
        if broker is None or TabReceiptStore(str(ledger_path)).current_binding(request_id):
            raise ValueError('Window registration status changed; reconcile original request')
        with store._db() as db:
            record=store._get(db, request_id)
            if record.get('retired') or record.get('state') != 'prepared': raise ValueError('Request is not prepared')
            record.update(state='waiting', reason='window_registration_failed', resume_state='prepared')
            store._put(db, record)
    def image_saved(c, data):
        current = store.inspect(request_id)
        step = next_gallery_step(current)
        if (not bridge.save_directory or not step or step['stage'] != 'image'
                or type(data['order']) is not int or data['order'] != step['order'] or data['identity'] != step['identity']):
            raise ValueError('Original does not match the next gallery image')
        raw = read_saved_original(gallery_save_path(bridge.save_directory, step))
        manifest = current['collector_pending']['gallery_manifests'][step['identity']]
        screenshot = manifest['screenshot_path']
        if hashlib.sha256(Path(screenshot).read_bytes()).hexdigest() != manifest['screenshot_sha256']:
            raise ValueError('Original gallery screenshot changed')
        with tempfile.TemporaryDirectory(prefix='research-original-') as directory:
            source_file = Path(directory) / 'image'
            source_file.write_bytes(raw)
            return send(store, request_id, c, step['identity'], source_file, screenshot, client, renew,
                        source=step['source'], order=step['order'])

    def page_blocked(c, data):
        with store._db() as db:
            record = store._get(db, request_id)
            state = record.get('state')
            if record.get('retired') or state not in ('prepared', 'candidate_images_pending'):
                raise ValueError('An active collection stage is required')
            pending = record.get('collector_pending') or {}
            if any(upload.get('state') != 'confirmed' for upload in pending.get('candidate_uploads', {}).values()):
                raise ValueError('Pending image receipt requires reconciliation')
            record.update(state='waiting', reason=data['code'], resume_state=state)
            record['page_block_diagnostic']={'timestamp':datetime.now(timezone.utc).isoformat(),
                'reported':data.get('diagnostic'), 'prior_state':state,
                'plan_bound':bool(record.get('collector_plan')), 'source':'host_report'}
            store._put(db, record)

    def require_gallery_identity(data):
        record = store.inspect(request_id)
        if (record.get('state')=='candidate_images_pending' and (record.get('collector_pending') or {}).get('ledger_v1') is True
                and getattr(client,'research_ledger_enabled',False) is True):
            active = supervisor.active_broker if supervisor else None
            gallery = getattr(active,'gallery_binding',None)
            if not gallery or not gallery.ready(active) or gallery.confirmed[0] != data['identity']:
                raise ValueError('Current verified gallery candidate required')

    def manifest(c,d):
        require_gallery_identity(d)
        return bind_gallery(store,request_id,c,d['identity'],d['manifest'],d['screenshot'],renew)

    def image(c,d):
        require_gallery_identity(d)
        return send(store,request_id,c,d['identity'],d['file'],d['screenshot'],client,renew,source=d['source'],order=d['order'])

    def visual(c, data):
        from .collector_cli import task_config
        from .visual_candidate import VisualCandidate
        record = store.inspect(request_id)
        binding = record.get('collector_plan') or {}
        plan = binding.get('plan') or {}
        if record.get('state') != 'prepared' or record.get('retired') or plan.get('platform') != 'mercado-libre-mx':
            raise ValueError('Active Mercado candidate review required')
        key = (request_id,binding['digest'],c['lease_epoch'])
        review = VisualCandidate(ledger_path,key,plan,task_config(record['context']['snapshot'],plan),c)
        if data:
            return review.submit(data,store,renew)
        if not bridge.save_directory:
            raise ValueError('Missing private image directory')
        return review.status(store,bridge.save_directory)

    handlers={
        'prepare_suggestions':prepare_suggestions, 'record_suggestions':record_suggestions, 'suggestions_unavailable':suggestions_unavailable, 'seal_suggestions':seal_suggestions, 'home_expansion_options':home_expansion_options, 'reserve_captcha_attempt':reserve_captcha_attempt, 'arm_home_verification':arm_home_verification, 'window_blocked':window_blocked, 'page_blocked': page_blocked,
        'binding_status':binding_status,
        'seal_expansion':seal_expansion,
        'visual_candidate_status':visual,
        'submit_visual_verdict':visual,
        'prior_lease_status':lambda c,d: client.request('POST','/internal/meli-optimization/prior-research-lease',{'task_id':c['id'],'lease_token':c['lease_token'],'lease_epoch':c['lease_epoch'],'old_request_id':d['request_id']}),
        'plan': plan,
        'submit':submit,
        'submit_staged':submit_staged, 'image_saved':image_saved,
        'manifest':manifest,
        'image':image,
        'finish':lambda c,d: finish(store,request_id,c,renew,require_manifest=True,client=client),
        'reconcile_evidence':reconcile_original_evidence,
        'reconcile_image':reconcile_original_image,
    }
    def require_live_guard():
        if supervisor is None or not supervisor.fresh_confirmation():
            raise ValueError('Current browser session requires fresh verification')
        current=store.inspect(request_id)
        if current.get('state')=='collector_upload_unknown':
            raise ValueError('Evidence upload requires reconciliation')
        pending=current.get('collector_pending') or {}
        uploads=pending.get('candidate_uploads') or {}
        if not isinstance(uploads,dict) or any(not isinstance(v,dict) or v.get('state')=='unknown' for v in uploads.values()):
            raise ValueError('Image upload requires reconciliation')
    bridge=ReturnBridge(request_id,claim,renew,handlers,write_guard=require_live_guard)
    bridge._close=lambda: supervisor.close() if supervisor is not None else None
    if getattr(client, 'research_ledger_enabled', False) is True:
        active_claim = claim()
        if active_claim['lease_epoch'] > 1:
            from .prepared_search_recovery import restore_search_checkpoint
            restored = restore_search_checkpoint(TabReceiptStore(str(ledger_path)), store,
                worker_records(journal), client, request_id, active_claim, renew)
            if restored:
                bridge.restored_plan = store.inspect(request_id)['collector_plan']
    if callable(getattr(store,'inspect',None)) and store.inspect(request_id).get('collector_plan'):
        ensure_supervisor()
    return bridge

"""Request-scoped native collector broker; same-UID callers are not isolated."""
import os
import json
from pathlib import Path
import socket
import stat
import time
import sqlite3
import threading
import fcntl
import errno
import secrets
from urllib.parse import urlsplit
from contextlib import closing, suppress
from datetime import datetime, timezone

from .client import APIError
from .native_collector_host import read_message, write_message, trace_native
from .tab_receipt_store import TabReceiptStore
from .collector_progress import initialize_details, handle_progress, CollectorProgressError, checked_detail_identity, verify_checkpoint_evidence

GUARD_TIMEOUT_SECONDS = 15

class Broker:
    def __init__(self, path, ledger, challenge, expected_tab_id, fence, *, expected_window_id=None, expected_session=None, settle_seconds=15, reconcile=None, rebind_check=None, watch_only=False, anchor_origin=None, peer_origin=None, progress_plan=None, gallery_capture=None, gallery_binding=None, gallery_downloads=None):
        self.path=Path(path); self.store=TabReceiptStore(str(ledger)); self.challenge=challenge
        if ((expected_tab_id is not None and (type(expected_tab_id) is not int or expected_tab_id <= 0)) or not callable(fence)
                or (expected_window_id is not None and (type(expected_window_id) is not int or expected_window_id <= 0)) or settle_seconds <= 0): raise ValueError('Observed target tab and fence required')
        self.tab=expected_tab_id;self.initial_tab=expected_tab_id;self.window=expected_window_id;self.fence=fence;self.reconcile=reconcile;self.rebind_check=rebind_check;self.rebind_requested=False;self.done=False;self.sock=None
        self.started=time.monotonic();self.settle_seconds=settle_seconds;self.candidates=set();self.conflict=False;self.session=expected_session;self.watch_only=watch_only
        with closing(sqlite3.connect(self.store.path)) as db:
            receipts=[json.loads(row[0]) for row in db.execute('SELECT body FROM accepted_receipts WHERE request_id=?',(challenge['request_id'],))]
        self.query_verified=any(all(r.get(k)==challenge.get(k) for k in ('version','platform','lease_epoch','plan_digest','query_index','query')) and r.get('tab_id')==expected_tab_id and r.get('window_id')==expected_window_id and r.get('session_id')==expected_session for r in receipts)
        self._guard_lock=threading.Lock();self._guard=None;self._guard_socket=None;self._guard_last_seen=None;self._lock_fd=None;self._socket_inode=None
        self.progress_plan = progress_plan
        self.gallery_binding = gallery_binding
        self.gallery_downloads = gallery_downloads
        self.gallery_capture = gallery_capture
        self._activation=None
        self._anchor_lock=threading.Lock();self._anchor_guard=None;self._anchor_seen=None
        from .window_restart_registry import WindowRestartRegistry
        self.anchor_origin=anchor_origin
        self.peer_origin=peer_origin;self._peer_pending=None;self._peer_verified=None
        self.anchor_registry=WindowRestartRegistry(str(ledger))
        from .task_tab_challenge import TaskTabChallenge
        self.tab_challenge=TaskTabChallenge(fence)
        self.peer_tokens=TaskTabChallenge(fence)
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS tab_observations (request_id TEXT, nonce TEXT, tab_id INTEGER, session_id TEXT, window_id INTEGER, observed_at TEXT, UNIQUE(request_id,nonce,tab_id,session_id))')
            db.execute('CREATE TABLE IF NOT EXISTS selected_windows (request_id TEXT, nonce TEXT UNIQUE, tab_id INTEGER, window_id INTEGER, session_id TEXT, observed_at TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS receipt_recoveries (request_id TEXT, lease_epoch INTEGER, query_index INTEGER, PRIMARY KEY(request_id,lease_epoch,query_index))')
            db.execute('''CREATE TABLE IF NOT EXISTS confirmed_task_tabs (
                request_id TEXT, plan_digest TEXT, lease_epoch INTEGER, platform TEXT, marker TEXT,
                anchor_session TEXT, window_id INTEGER NOT NULL, tab_id INTEGER NOT NULL,
                PRIMARY KEY (request_id,plan_digest,lease_epoch,platform,marker,anchor_session))''')
            initialize_details(db)
            db.execute('CREATE TABLE IF NOT EXISTS staged_evidence (request_id TEXT, lease_epoch INTEGER, body TEXT, PRIMARY KEY(request_id,lease_epoch))')

    def anchor_challenge(self, event):
        if (not self.anchor_origin or set(event)!={'operation','request_id','session_id','native_origin'}
                or event.get('native_origin') != self.anchor_origin or event.get('request_id') != self.challenge['request_id']): return None
        try: return self.anchor_registry.challenge(event['request_id'],self.challenge['lease_epoch'],event['session_id'],self.fence)
        except (ValueError,TypeError): return None

    def anchor_confirm(self, event):
        if (not self.anchor_origin or set(event)!={'operation','request_id','epoch','session_id','window_id','marker','token','native_origin'}
                or event.get('native_origin') != self.anchor_origin or event.get('request_id') != self.challenge['request_id']
                or event.get('epoch') != self.challenge['lease_epoch']): return False
        proof={'request':event['request_id'],'epoch':event['epoch'],'session':event['session_id'],
               'window':event['window_id'],'marker':event['marker'],'token':event['token']}
        if self.initial_tab is not None or self.tab is not None or self.candidates:
            old=self.anchor_registry.current()
            if not old or old[2]!=event['marker']: return False
            same_anchor = old[:2] == (event['window_id'],event['session_id']) and self._anchor_matches_binding(*old[:2])
            if not same_anchor and (not callable(self.rebind_check) or self.rebind_check() is not True): return False
            if not self.anchor_registry.confirm(proof,lambda _:event['native_origin']==self.anchor_origin,self.fence): return False
            if not same_anchor: self.rebind_requested=True
            return True
        if not self.anchor_registry.confirm(proof,lambda _:event['native_origin']==self.anchor_origin,self.fence): return False
        self.window=event['window_id'];self.session=event['session_id'];self.started=time.monotonic()
        return True

    def _anchor_matches_binding(self, window, session):
        # SHEIN proves its extension-local session separately via the anchor.
        return self.window == window and (self.challenge['platform'] == 'shein-us' or self.session == session)

    def open_anchor_guard(self,event):
        self.anchor_guard_failure=None
        if (not isinstance(event,dict) or not self.anchor_origin or set(event)!={'operation','request_id','session_id','window_id','marker','native_origin'}
                or event.get('native_origin')!=self.anchor_origin or event.get('request_id')!=self.challenge['request_id']):
            self.anchor_guard_failure='request_invalid';return None
        if self.rebind_requested:
            self.anchor_guard_failure='rebind_pending';return None
        if not self._anchor_matches_binding(event.get('window_id'),event.get('session_id')):
            self.anchor_guard_failure='binding_mismatch';return None
        if self.anchor_registry.current()!=(event.get('window_id'),event.get('session_id'),event.get('marker')):
            self.anchor_guard_failure='anchor_mismatch';return None
        if self.fence() is not True:
            self.anchor_guard_failure='lease_invalid';return None
        with self._anchor_lock:
            if self._live_anchor_connection():
                self.anchor_guard_failure='guard_already_active';return None
            token=(event['window_id'],event['session_id'],event['marker'])
            self._anchor_guard=token;self._anchor_seen=time.monotonic();return token

    def _live_anchor_connection(self):
        token=self._anchor_guard
        return bool(token and not self.rebind_requested and self._anchor_seen is not None
                    and 0 <= time.monotonic()-self._anchor_seen <= GUARD_TIMEOUT_SECONDS
                    and self._anchor_matches_binding(*token[:2]) and self.anchor_registry.current()==token)

    def _anchor_connected(self):
        with self._anchor_lock:
            return self._live_anchor_connection()

    def anchor_guard_alive(self):
        return self._anchor_connected() and self.fence() is True

    def close_anchor_guard(self,token):
        with self._anchor_lock:
            if self._anchor_guard is token:self._anchor_guard=None;self._anchor_seen=None

    def _anchor_guard_loop(self,conn,token):
        try:
            conn.settimeout(None)
            with conn,conn.makefile('rwb') as stream:
                while (msg:=read_message(stream)) is not None:
                    with self._anchor_lock:
                        now=time.monotonic()
                        ok=(msg=={'operation':'guard_ping'} and self._anchor_guard is token and self._live_anchor_connection() and self.fence() is True)
                        if ok:self._anchor_seen=now
                    write_message(stream,{'ok':ok})
                    if not ok:break
        except (OSError,ValueError,TypeError): pass
        finally:self.close_anchor_guard(token)

    def _task_anchor(self, event):
        if (not self.anchor_origin or event.get('native_origin') != self.anchor_origin
                or event.get('request_id') != self.challenge['request_id'] or self.conflict or self.rebind_requested
                or type(event.get('window_id')) is not int or event['window_id'] != self.window
                or not self.anchor_guard_alive()):
            return None
        anchor = self.anchor_registry.current()
        if not anchor or anchor[:2] != (event['window_id'], event.get('session_id')):
            return None
        return (self.challenge['request_id'], self.challenge['plan_digest'], self.challenge['lease_epoch'],
                self.challenge['platform'], anchor[2], anchor[1])

    def task_tab_challenge(self, event):
        if set(event) != {'operation','request_id','session_id','window_id','native_origin'}:
            return None
        key = self._task_anchor(event)
        if not key:
            return None
        with closing(sqlite3.connect(self.store.path)) as db, db:
            proof = db.execute("""SELECT window_id,tab_id FROM confirmed_task_tabs WHERE
                request_id=? AND plan_digest=? AND lease_epoch=? AND platform=? AND marker=? AND anchor_session=?""",
                               key).fetchone()
            if not proof and self.tab is not None and not self.gallery_binding:
                # A registration made before confirmation persistence can be recovered
                # only from an accepted search receipt and its still-current binding.
                row = db.execute("""SELECT b.body,a.body FROM bindings b JOIN accepted_receipts a
                    USING(request_id) WHERE b.request_id=? ORDER BY a.id DESC LIMIT 1""", (key[0],)).fetchone()
                if row:
                    binding, receipt = map(json.loads, row)
                    identity = dict(session_id=self.session, window_id=self.window, tab_id=self.tab,
                                    plan_digest=key[1], platform=key[3])
                    if (binding == identity and all(receipt.get(k) == v for k,v in identity.items())
                            and receipt.get('request_id') == key[0] and receipt.get('lease_epoch') == key[2]
                            and receipt.get('collector_tab_id') == self.tab
                            and (self.session == key[5] if key[3]=='mercado-libre-mx'
                                 else self.peer_session_valid(self.session, self.window, self.tab))):
                        proof = (self.window,self.tab)
                        db.execute('INSERT INTO confirmed_task_tabs VALUES (?,?,?,?,?,?,?,?)', (*key,*proof))
        if proof:
            window, tab = proof
            if (window != self.window or (self.tab is not None and self.tab != tab)
                    or any(observed != tab for observed, _ in self.candidates)):
                return None
            if self.tab is None:
                self.tab = tab
                self.candidates.add((tab,self.session))
            if self.gallery_binding:
                self.gallery_binding.proven_tab = (window,key[5],key[4],tab)
            return dict(ok=True, request_id=key[0], platform=key[3], epoch=key[2], session_id=key[5],
                        window_id=window, bound_tab_id=tab)
        if self.initial_tab is not None or self.tab is not None:
            return None
        try:
            return self.tab_challenge.issue(key[0],key[2],key[5],self.window,keep_pending=True) or {
                'ok':False,'error':'task_tab_pending'}
        except (ValueError,TypeError): return None

    def task_tab_confirm(self,event):
        if (set(event) != {'operation','request_id','session_id','window_id','native_origin','epoch','token','tab_id'}
                or type(event.get('epoch')) is not int or event['epoch'] != self.challenge['lease_epoch']
                or self.tab is not None or (self.candidates and (event.get('tab_id'),event.get('session_id')) not in self.candidates)):
            return False
        key = self._task_anchor(event)
        if not key or not self.tab_challenge.confirm(event['token'],key[0],key[2],key[5],self.window,event['tab_id']):
            return False
        with closing(sqlite3.connect(self.store.path)) as db, db:
            try:
                db.execute('INSERT INTO confirmed_task_tabs VALUES (?,?,?,?,?,?,?,?)', (*key,self.window,event['tab_id']))
            except sqlite3.IntegrityError:
                return False
        self.tab=event['tab_id'];self.candidates.add((self.tab,event['session_id']))
        if self.gallery_binding:
            self.gallery_binding.proven_tab = (self.window,key[5],key[4],self.tab)
        return True

    def peer_challenge(self,event):
        if (not self.peer_origin or set(event)!={'operation','request_id','session_id','window_id','tab_id','native_origin'}
                or event.get('native_origin')!=self.peer_origin or event.get('request_id')!=self.challenge['request_id']
                or self.challenge['platform']!='shein-us' or not self.anchor_guard_alive()
                or event.get('window_id')!=self.window or type(event.get('tab_id')) is not int or event['tab_id']<=0
                or event['tab_id']!=self.tab): return None
        try:
            token=self.peer_tokens.issue(event['request_id'],self.challenge['lease_epoch'],event['session_id'],event['window_id'])
            self._peer_pending=(token,event['session_id'],event['window_id'],event['tab_id']);return token
        except (ValueError,TypeError):return None

    def peer_confirm(self,event):
        fields={'operation','request_id','epoch','session_id','window_id','tab_id','token','peer_id','native_origin'}
        if (not self.anchor_origin or not self.peer_origin or set(event)!=fields or event.get('native_origin')!=self.anchor_origin
                or event.get('peer_id')!=self.peer_origin.removeprefix('chrome-extension://').removesuffix('/')
                or event.get('request_id')!=self.challenge['request_id'] or event.get('epoch')!=self.challenge['lease_epoch']
                or not self.anchor_guard_alive() or self._peer_pending!=(event.get('token'),event.get('session_id'),event.get('window_id'),event.get('tab_id'))):return False
        if not self.peer_tokens.confirm(event['token'],event['request_id'],event['epoch'],event['session_id'],event['window_id'],event['tab_id']):return False
        self._peer_pending=None;self._peer_verified=(event['session_id'],event['window_id'],event['tab_id']);return True

    def peer_session_valid(self,session,window,tab):
        return self.anchor_guard_alive() and self._peer_verified==(session,window,tab)

    def issue_activation(self, event):
        self.activation_error = 'activation_binding_invalid'
        fields={'operation','request_id','tab_id','window_id','session_id'}
        if (not isinstance(event,dict) or set(event) not in (fields, fields | {'detail_url'}) or event['operation']!='activation_challenge'
            or event['request_id']!=self.challenge['request_id'] or event['tab_id']!=self.tab
            or event['window_id']!=self.window or event['session_id']!=self.session
            or type(self.tab) is not int or self.tab<=0 or type(self.window) is not int or self.window<=0
            or not isinstance(self.session,str) or len(self.session)!=32 or self.fence() is not True): return None
        nonce=secrets.token_urlsafe(32)
        selected=dict(self.challenge,nonce=nonce)
        if 'detail_url' in event:
            if not self.watch_only or self.progress_plan is None:
                self.activation_error = 'detail_phase_unavailable'
                return None
            if not self.guard_alive(self.session):
                self.activation_error = 'detail_guard_pending'
                return None
            identity = checked_detail_identity(self, event['detail_url'])
            if identity is None:
                self.activation_error = 'detail_candidate_unverified'
                return None
            self._activation = (nonce, time.monotonic(), identity, event['detail_url'])
            self._activation_started_at=time.time()
            return dict(selected, detail_identity=identity, detail_url=event['detail_url'])
        index=self.challenge['query_index']
        if self.watch_only and self.challenge.get('version')==5:
            scope=tuple(self.challenge[k] for k in ('request_id','plan_digest','lease_epoch'))
            with closing(sqlite3.connect(self.store.path)) as db:
                rows=[json.loads(row[0]) for row in db.execute('SELECT body FROM accepted_receipts WHERE request_id=? ORDER BY id DESC',(scope[0],))]
                current=[r for r in rows if r.get('version')==5 and all(r.get(k)==self.challenge.get(k) for k in ('request_id','lease_epoch','plan_digest','platform'))
                         and r.get('tab_id')==self.tab and r.get('window_id')==self.window and r.get('session_id')==self.session]
                receipt=next((r for r in current if db.execute('''SELECT 1 FROM card_v3_pages p JOIN card_v3_queries q
                    USING(request_id,plan_digest,lease_epoch,query_index) JOIN card_v3_page_evidence e
                    USING(request_id,plan_digest,lease_epoch,query_index,page) WHERE p.request_id=? AND p.plan_digest=? AND p.lease_epoch=?
                    AND p.query_index=? AND p.page=1 AND p.status='home_complete' AND q.query=?''',(*scope,r.get('query_index'),r.get('query'))).fetchone()),None)
            if receipt is None:return None
            selected['query']=receipt['query'];selected['query_index']=receipt['query_index']
        elif self.watch_only or (index>0 and not self.query_verified):
            prior_index=2 if self.watch_only else index-1
            with closing(sqlite3.connect(self.store.path)) as db:
                if self.challenge.get('version') == 3:
                    from .visual_v2_checkpoints import load_query, query_status
                    key=(self.challenge['request_id'],self.challenge['plan_digest'],self.challenge['lease_epoch'],str(prior_index))
                    complete=query_status(db,key)
                    cards=(json.dumps(load_query(db,key)),) if complete else None
                else:
                    cards=db.execute('SELECT body FROM query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=?', (self.challenge['request_id'],self.challenge['plan_digest'],self.challenge['lease_epoch'],prior_index)).fetchone()
                rows=[json.loads(row[0]) for row in db.execute('SELECT body FROM accepted_receipts WHERE request_id=?',(self.challenge['request_id'],))]
            previous=[r for r in rows if r.get('query_index')==prior_index and all(r.get(k)==self.challenge.get(k) for k in ('request_id','lease_epoch','plan_digest','platform')) and r.get('tab_id')==self.tab and r.get('window_id')==self.window and r.get('session_id')==self.session]
            if cards and len(previous)==1:
                try: saved=json.loads(cards[0])
                except (ValueError,TypeError): saved=None
                short=(self.challenge.get('version')==2 and prior_index>=3 and isinstance(saved,list) and len(saved)<30
                       and self.store.query_exhausted(self.challenge['request_id'],self.challenge['plan_digest'],self.challenge['lease_epoch'],prior_index))
                visual=(self.challenge.get('version')==3 and isinstance(saved,list) and len(saved)<=100)
                if isinstance(saved,list) and (30<=len(saved)<=50 or short or visual) and all(isinstance(c,dict) and c.get('query')==previous[0].get('query') for c in saved):
                    selected['query']=previous[0]['query']; selected['query_index']=prior_index
                elif self.watch_only:return None
            elif self.watch_only:return None
        self._activation=(nonce,time.monotonic(),selected['query'])
        return selected

    def confirm_activation(self, event):
        fields={'operation','request_id','tab_id','window_id','session_id','nonce','url','query','observed_at'}
        if (not isinstance(event,dict) or set(event)!=fields or event['operation']!='activation_confirm'
            or not self._activation or event['nonce']!=self._activation[0]
            or time.monotonic()-self._activation[1]>10 or event['request_id']!=self.challenge['request_id']
            or event['tab_id']!=self.tab or event['window_id']!=self.window or event['session_id']!=self.session
            or event['query']!=(self._activation[2] if len(self._activation)>2 else self.challenge['query']) or self.fence() is not True): return False
        try:
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(event['observed_at'].replace('Z','+00:00'))).total_seconds()
            url=urlsplit(event['url'])
            if len(self._activation) == 4:
                if (not 0 <= age <= 10 or event['url'] != self._activation[3]
                        or checked_detail_identity(self, event['url'],activation_started=getattr(self,'_activation_started_at',None)) != event['query']):
                    return False
                if self.progress_plan and self.progress_plan[0].get('strategy')=='staged-v1':
                    from .collector_progress import reserve_staged_detail
                    with closing(sqlite3.connect(self.store.path)) as db, db:
                        db.execute('BEGIN IMMEDIATE')
                        if self.fence() is not True: return False
                        reserve_staged_detail(db,tuple(self.challenge[k] for k in ('request_id','plan_digest','lease_epoch')),event['query'],self._activation_started_at)
                self._activation = None
                return True
            host='us.shein.com' if self.challenge['platform']=='shein-us' else 'listado.mercadolibre.com.mx'
            if not 0<=age<=10 or url.scheme!='https' or url.hostname!=host or url.port or url.username or url.password: return False
        except (ValueError,TypeError,AttributeError): return False
        self._activation=None
        return True

    def stage_evidence(self, event):
        from .collector_cli import collector_id
        fields={'operation','request_id','tab_id','window_id','session_id','evidence'}
        if (self.gallery_binding or not isinstance(event,dict) or set(event)!=fields or event['operation']!='stage_evidence' or not self.watch_only
                or event['request_id']!=self.challenge['request_id'] or event['tab_id']!=self.initial_tab
                or event['window_id']!=self.window or event['session_id']!=self.session or self.fence() is not True
                or not self.guard_alive(self.session) or not isinstance(event['evidence'],dict)
                or event['evidence'].get('task_record_id')!=collector_id(event['request_id'])): return False
        if self.progress_plan is not None and self.progress_plan[0].get('strategy') in ('visual-v2','card-visual-v3','card-home-v4'):
            plan,config=self.progress_plan;v3=plan.get('strategy') in ('card-visual-v3','card-home-v4')
            if v3:from .visual_v3_finalize import prepare_stage
            else:from .visual_v2_finalize import prepare_stage
            scope=getattr(self,'visual_v3_scope' if v3 else 'visual_v2_scope',None)
            if not scope:return False
            try:
                with closing(sqlite3.connect(self.store.path)) as db, db:
                    db.execute('BEGIN IMMEDIATE')
                    key=tuple(self.challenge[k] for k in ('request_id','plan_digest','lease_epoch'))
                    args=(db,key,scope,plan,config,collector_id(event['request_id']),datetime.now(timezone.utc).isoformat())
                    if v3:
                        verify=getattr(self,'visual_v3_verify_photo',None)
                        if not callable(verify):return False
                        args+=(verify,)
                    evidence=prepare_stage(*args)
                    body=json.dumps(evidence,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))
                    if self.fence() is not True:return False
                    old=db.execute('SELECT body FROM staged_evidence WHERE request_id=? AND lease_epoch=?',(event['request_id'],self.challenge['lease_epoch'])).fetchone()
                    if old:return old[0]==body
                    db.execute('INSERT INTO staged_evidence VALUES (?,?,?)',(event['request_id'],self.challenge['lease_epoch'],body))
                    return True
            except (ValueError,TypeError,sqlite3.Error):return False
        body=json.dumps(event['evidence'],sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        if len(body.encode())>900000: return False
        if self.progress_plan is not None:
            from .collector_import import convert_evidence, convert_staged_evidence
            plan, config = self.progress_plan
            try:
                staged=plan.get('strategy')=='staged-v1'
                sealed=(self.store.sealed_queries(event['request_id'],self.challenge['plan_digest'],self.challenge['lease_epoch'],self.fence)
                        if staged and len(event['evidence'].get('queries',[]))>3 else [])
                extra=sealed[:max(0,len(event['evidence'].get('queries',[]))-3)]
                args=(event['evidence'],collector_id(event['request_id']),plan['platform'],plan['queries'])
                if staged: args += (extra,)
                args += (plan['category'],datetime.now(timezone.utc).isoformat())
                kwargs={k: config[k] for k in ('result_limit','min_sold','min_reviews','min_rating')}
                if staged: kwargs['elapsed_seconds']=self.store.budget_elapsed(event['request_id'],self.challenge['plan_digest'],self.challenge['lease_epoch'],self.fence)
                (convert_staged_evidence if staged else convert_evidence)(*args,**kwargs)
            except (TypeError, ValueError):
                return False
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            staged=self.progress_plan is not None and self.progress_plan[0].get('strategy')=='staged-v1'
            if self.progress_plan is not None and not verify_checkpoint_evidence(db, self.challenge, event['evidence'], allow_previous_stage=not staged,staged=staged):
                return False
            if staged:
                from .collector_progress import _candidate_order, staged_in_flight
                from .collector_staged_policy import elapsed_seconds
                key=tuple(self.challenge[k] for k in ('request_id','plan_digest','lease_epoch'))
                try:
                    _,ordered,reviewed,finished=_candidate_order(db,key,*self.progress_plan)
                    if not finished and reviewed<len(ordered) and staged_in_flight(db,key,plan['platform']+':'+ordered[reviewed],elapsed_seconds(db,key)):
                        return False
                except (ValueError,CollectorProgressError,sqlite3.Error):
                    return False
            old=db.execute('SELECT body FROM staged_evidence WHERE request_id=? AND lease_epoch=?',(event['request_id'],self.challenge['lease_epoch'])).fetchone()
            if old: return old[0]==body
            db.execute('INSERT INTO staged_evidence VALUES (?,?,?)',(event['request_id'],self.challenge['lease_epoch'],body))
        return True

    def select_window(self, event):
        if (not isinstance(event,dict)
                or set(event)!={'operation','request_id','tab_id','window_id','session_id','observed_at'} or event['operation']!='select_window'
                or event['request_id'] != self.challenge['request_id']
                or any(type(event[k]) is not int or event[k]<=0 for k in ('tab_id','window_id'))
                or not isinstance(event['session_id'],str) or len(event['session_id'])!=32 or self.fence() is not True): return False
        try:
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(event['observed_at'].replace('Z','+00:00'))).total_seconds()
            if not 0<=age<=15: return False
        except (ValueError,TypeError,AttributeError): return False
        if self.anchor_origin:
            anchor=self.anchor_registry.current()
            # Other extensions do not share chrome.storage.session. Until a
            # cross-extension generation proof exists, fail closed for SHEIN.
            if self.challenge['platform']=='shein-us':
                if not self.peer_session_valid(event['session_id'],event['window_id'],event['tab_id']):return False
                if self.initial_tab is None:
                    if self.tab!=event['tab_id']:return False
                    self.session=event['session_id'];self.candidates={(self.tab,self.session)}
            elif not self.anchor_guard_alive() or not anchor or anchor[:2] != (event['window_id'],event['session_id']):return False
        if self.initial_tab is not None:
            if event['session_id'] != self.session and callable(self.rebind_check) and self.rebind_check() is True:
                self.rebind_requested=True
                return False
            matched=(event['tab_id']==self.initial_tab and event['window_id']==self.window and event['session_id']==self.session)
            if matched and self.watch_only and not self.gallery_binding: self.done=True
            return matched
        if self.window is not None:
            return self.session is not None and event['window_id']==self.window and event['session_id']==self.session
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute('INSERT INTO selected_windows VALUES (?,?,?,?,?,?)',(self.challenge['request_id'],self.challenge['nonce'],event['tab_id'],event['window_id'],event['session_id'],event['observed_at']))
        self.window=event['window_id'];self.session=event['session_id'];self.started=time.monotonic()
        return True

    def observe(self, event):
        fields={'operation','request_id','nonce','lease_epoch','plan_digest','tab_id','window_id','session_id','observed_at'}
        if (not isinstance(event,dict) or set(event)!=fields or event['operation']!='observe_tab'
                or any(event[k]!=self.challenge[k] for k in ('request_id','nonce','lease_epoch','plan_digest'))
                or event['window_id']!=self.window or type(event['tab_id']) is not int or event['tab_id']<=0
                or not isinstance(event['session_id'],str) or len(event['session_id'])!=32 or (self.session is not None and event['session_id']!=self.session) or self.fence() is not True): return False
        try:
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(event['observed_at'].replace('Z','+00:00'))).total_seconds()
            if not 0<=age<=15 or time.monotonic()-self.started>15: return False
        except (ValueError,TypeError,AttributeError): return False
        self.candidates.add((event['tab_id'],event['session_id']))
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute('INSERT OR IGNORE INTO tab_observations VALUES (?,?,?,?,?,?)',(event['request_id'],event['nonce'],event['tab_id'],event['session_id'],event['window_id'],event['observed_at']))
        if len(self.candidates)>1: self.conflict=True
        return not self.conflict

    def poll(self, tab_id):
        if self.done or self.query_verified or self.watch_only or self.conflict or self.fence() is not True: return None
        if self.tab is None:
            if time.monotonic()-self.started < max(15,self.settle_seconds) or len(self.candidates)!=1: return None
            self.tab=next(iter(self.candidates))[0]
        return self.challenge if tab_id==self.tab else None

    def _card_v3_identity(self,event):
        return (self.challenge.get('version') in (4,5) and self.anchor_origin and event.get('native_origin')==self.anchor_origin
                and event.get('request_id')==self.challenge['request_id'] and event.get('tab_id')==self.tab
                and event.get('window_id')==self.window and event.get('session_id')==self.session
                and self.query_verified and self.anchor_guard_alive() and self.fence() is True)

    def home_tab_missing(self,event):
        fields={'operation','request_id','tab_id','window_id','session_id','native_origin'}
        if (not isinstance(event,dict) or set(event)!=fields or event.get('operation')!='home_tab_missing'
                or self.challenge.get('version')!=5 or not self.progress_plan or self.progress_plan[0].get('strategy')!='card-home-v4'
                or self.gallery_binding or event.get('native_origin')!=self.anchor_origin
                or event.get('request_id')!=self.challenge['request_id'] or event.get('tab_id')!=self.tab
                or event.get('window_id')!=self.window or event.get('session_id')!=self.session
                or not self.anchor_guard_alive() or not callable(self.rebind_check) or self.rebind_check() is not True
                or self.fence() is not True):return False
        try:
            with closing(sqlite3.connect(self.store.path)) as db,db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM confirmed_task_tabs WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND tab_id=?',
                           (self.challenge['request_id'],self.challenge['plan_digest'],self.challenge['lease_epoch'],self.tab))
                if self.fence() is not True:raise ValueError('Active lease required')
            self.rebind_requested=True
            return True
        except (ValueError,sqlite3.Error):return False

    def card_v3_progress(self,event):
        fields={'operation','request_id','tab_id','window_id','session_id','native_origin'}
        if not isinstance(event,dict) or set(event)!=fields or event['operation']!='card_v3_progress' or not self._card_v3_identity(event):return None
        try:
            from .card_sales_v3_checkpoints import initialize
            from .card_sales_v3_pipeline import initialize_pipeline,progress
            with closing(sqlite3.connect(self.store.path)) as db,db:
                initialize(db);initialize_pipeline(db)
                result=progress(db,(event['request_id'],self.challenge['plan_digest'],self.challenge['lease_epoch']))
                if not self.anchor_guard_alive() or self.fence() is not True:return None
                return result
        except (ValueError,TypeError,sqlite3.Error):return None

    @staticmethod
    def _fresh_card_v3_proofs(db, expected):
        now=datetime.now(timezone.utc);matches=[]
        for body, issued_at in db.execute('SELECT body,issued_at FROM card_v3_receipt_challenges WHERE used=1'):
            try:
                proof=json.loads(body);age=(now-datetime.fromisoformat(issued_at)).total_seconds()
                if 0<=age<=15 and all(proof.get(k)==v for k,v in expected.items()):matches.append(proof)
            except (TypeError,ValueError,KeyError):continue
        return matches

    def issue_card_v3_page(self,event):
        fields={'operation','request_id','tab_id','window_id','session_id','query_index','query','page','native_origin'}
        if (not isinstance(event,dict) or set(event)!=fields or event['operation']!='card_v3_page_challenge'
                or not self._card_v3_identity(event) or event['query_index']!=self.challenge['query_index']
                or event['query']!=self.challenge['query'] or type(event['page']) is not int
                or event['page']!=abs(getattr(self,'requested_page',event['page']))):return None
        if self.progress_plan and self.progress_plan[0].get('strategy')=='card-home-v4' and event['page']!=1:return None
        try:
            from .card_sales_v3_receipts import PageReceiptGuard
            with closing(sqlite3.connect(self.store.path)) as db,db:
                db.execute('BEGIN IMMEDIATE')
                rows=[json.loads(row[0]) for row in db.execute('SELECT body FROM accepted_receipts WHERE request_id=?',(event['request_id'],))]
                keys=('request_id','plan_digest','lease_epoch','query_index','query','tab_id','window_id','session_id')
                expected=dict(request_id=event['request_id'],plan_digest=self.challenge['plan_digest'],lease_epoch=self.challenge['lease_epoch'],
                              query_index=event['query_index'],query=event['query'],tab_id=self.tab,window_id=self.window,session_id=self.session)
                matches=[row for row in rows if row.get('version')==self.challenge['version'] and row.get('platform')=='mercado-libre-mx' and all(row.get(k)==expected[k] for k in keys)]
                if not matches or (self.challenge['version']==4 and len(matches)!=1):return None
                return PageReceiptGuard(db).issue_for_page(matches[-1],event['page'],fence=self.fence)
        except (ValueError,TypeError,KeyError,sqlite3.Error):return None

    def confirm_card_v3_page(self,event):
        fields={'operation','request_id','tab_id','window_id','session_id','receipt','native_origin'}
        if (not isinstance(event,dict) or set(event)!=fields or event['operation']!='card_v3_page_confirm'
                or not self._card_v3_identity(event) or not isinstance(event['receipt'],dict)):return False
        receipt=event['receipt'];expected=dict(request_id=event['request_id'],plan_digest=self.challenge['plan_digest'],lease_epoch=self.challenge['lease_epoch'],
            query_index=self.challenge['query_index'],query=self.challenge['query'],tab_id=self.tab,window_id=self.window,session_id=self.session)
        if type(receipt.get('page')) is not int or receipt['page']!=abs(getattr(self,'requested_page',receipt['page'])):return False
        if any(receipt.get(k)!=v for k,v in expected.items()):return False
        try:
            from .card_sales_v3_receipts import PageReceiptGuard
            with closing(sqlite3.connect(self.store.path)) as db,db:
                db.execute('BEGIN IMMEDIATE');confirmed=PageReceiptGuard(db).confirm(receipt,fence=self.fence)
            if confirmed and getattr(self,'requested_page',1)<0:self.done=True
            return confirmed
        except (ValueError,TypeError,KeyError,sqlite3.Error):return False

    def save_card_v3_batch(self,event):
        fields={'operation','request_id','tab_id','window_id','session_id','query_index','query','page','page_url','batch_index','cards','screenshot','native_origin'}
        if (not isinstance(event,dict) or set(event)!=fields or event['operation']!='card_v3_batch'
                or self.challenge.get('version') not in (4,5) or not self.anchor_origin or event['native_origin']!=self.anchor_origin
                or event['request_id']!=self.challenge['request_id'] or event['tab_id']!=self.tab
                or event['window_id']!=self.window or event['session_id']!=self.session
                or event['query_index']!=self.challenge['query_index'] or event['query']!=self.challenge['query']
                or type(event['page']) is not int or not 1<=event['page']<=3 or event['page']!=getattr(self,'requested_page',event['page']) or not isinstance(event['cards'],list)
                or not 1<=len(event['cards'])<=100 or not self.query_verified or not self.anchor_guard_alive() or self.fence() is not True):
            return False
        if self.progress_plan and self.progress_plan[0].get('strategy')=='card-home-v4' and event['page']!=1:return False
        try:
            import base64,hashlib,io
            from PIL import Image
            from .card_sales_v3_checkpoints import append_page_batch,initialize
            if not callable(getattr(self,'card_v3_store_screenshot',None)) or not isinstance(event['screenshot'],str) or not 0<len(event['screenshot'])<850000:return False
            raw=base64.b64decode(event['screenshot'],validate=True)
            with Image.open(io.BytesIO(raw)) as shot:
                width,height=shot.size
                if shot.format!='JPEG' or min(width,height)<100 or width*height>30000000:return False
                shot.verify()
            for card in event['cards']:
                box=card.get('bbox') if isinstance(card,dict) else None
                if (not isinstance(box,list) or len(box)!=4 or any(type(v) is not int for v in box)
                        or box[0]<0 or box[1]<0 or box[2]<=0 or box[3]<=0 or box[0]+box[2]>width or box[1]+box[3]>height):return False
            screenshot_sha=hashlib.sha256(raw).hexdigest();screenshot_file=self.card_v3_store_screenshot(raw)
            if not isinstance(screenshot_file,str) or not screenshot_file:return False
            with closing(sqlite3.connect(self.store.path)) as db,db:
                db.execute('BEGIN IMMEDIATE');initialize(db)
                expected=dict(request_id=event['request_id'],plan_digest=self.challenge['plan_digest'],lease_epoch=self.challenge['lease_epoch'],
                              query_index=event['query_index'],query=event['query'],page=event['page'],page_url=event['page_url'],
                              tab_id=self.tab,window_id=self.window,session_id=self.session)
                if not self._fresh_card_v3_proofs(db,expected):return False
                scope=(event['request_id'],self.challenge['plan_digest'],self.challenge['lease_epoch'])
                append_page_batch(db,scope,event['query_index'],event['page'],event['batch_index'],event['cards'],fence=self.fence)
                key=(*scope,event['query_index'],event['page'],event['batch_index'])
                old=db.execute('SELECT screenshot_sha,screenshot_file,width,height FROM card_v3_batch_evidence WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? AND page=? AND batch_index=?',key).fetchone()
                evidence=(screenshot_sha,screenshot_file,width,height)
                if old and old!=evidence:raise ValueError('Batch screenshot changed')
                if not old:db.execute('INSERT INTO card_v3_batch_evidence VALUES (?,?,?,?,?,?,?,?,?,?)',(*key,*evidence))
                if not self.anchor_guard_alive() or self.fence() is not True:raise ValueError('Lost browser guard')
            return True
        except (ValueError,TypeError,sqlite3.Error,OSError):return False

    def finish_card_v3_page(self,event):
        fields={'operation','request_id','tab_id','window_id','session_id','query_index','query','page','page_url','status','next_url','end_checks','end_evidence','native_origin'}
        home=bool(self.progress_plan and self.progress_plan[0].get('strategy')=='card-home-v4')
        if (not isinstance(event,dict) or set(event)!=fields or event['operation']!='card_v3_page_finish'
                or not self._card_v3_identity(event) or event['query_index']!=self.challenge['query_index'] or event['query']!=self.challenge['query']
                or type(event['page']) is not int or not 1<=event['page']<=3 or event['page']!=getattr(self,'requested_page',event['page']) or type(event['end_checks']) is not int or event['end_checks']<3):return False
        if home:
            if event['page']!=1 or event['next_url'] is not None or (event['status'],event['end_evidence']) not in (('home_complete','scroll_end'),('end_of_results','empty_results')):return False
        elif (event['status'] not in ('has_next','end_of_results','page_limit') or
                (event['status']=='has_next' and event['end_evidence']!='next_link') or
                (event['status']=='end_of_results' and event['end_evidence'] not in ('disabled_next','empty_results')) or
                (event['status']=='page_limit' and event['end_evidence'] not in ('page_limit','empty_results'))):return False
        try:
            from .card_sales_v3_checkpoints import finish_page,initialize
            with closing(sqlite3.connect(self.store.path)) as db,db:
                db.execute('BEGIN IMMEDIATE');initialize(db)
                scope=(event['request_id'],self.challenge['plan_digest'],self.challenge['lease_epoch'])
                expected=dict(request_id=event['request_id'],plan_digest=scope[1],lease_epoch=scope[2],query_index=event['query_index'],
                              query=event['query'],page=event['page'],page_url=event['page_url'],tab_id=self.tab,window_id=self.window,session_id=self.session)
                matches=self._fresh_card_v3_proofs(db,expected)
                if not matches:return False
                count=db.execute('SELECT count(*) FROM card_v3_batches WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? AND page=?',(*scope,event['query_index'],event['page'])).fetchone()[0]
                if not count and event['end_evidence']!='empty_results':return False
                if count and event['end_evidence']=='empty_results':return False
                if home:
                    from .home_collection_checkpoint import finish_homepage
                    data=dict(url=event['page_url'],receipt_nonce=matches[-1]['nonce'],end_checks=event['end_checks'],no_results=not count)
                    finish_homepage(db,scope,event['query_index'],data,fence=self.fence,verify=lambda _:bool(matches) and self.anchor_guard_alive())
                else:
                    finish_page(db,scope,event['query_index'],event['page'],event['status'],next_url=event['next_url'],fence=self.fence)
                    key=(*scope,event['query_index'],event['page'])
                    saved=db.execute('SELECT page_url,end_evidence,end_checks FROM card_v3_page_evidence WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? AND page=?',key).fetchone()
                    expected_evidence=(event['page_url'],event['end_evidence'],event['end_checks'])
                    if saved and saved!=expected_evidence:raise ValueError('Page evidence changed')
                    if not saved:
                        db.execute('INSERT INTO card_v3_page_evidence VALUES (?,?,?,?,?,?,?,?,?)',(*key,event['page_url'],matches[-1]['nonce'],event['end_evidence'],event['end_checks']))
                if not self.anchor_guard_alive() or self.fence() is not True:raise ValueError('Lost browser guard')
            if home or getattr(self,'requested_page',None) is not None or event['status'] in ('end_of_results','page_limit'):self.done=True
            return True
        except (ValueError,TypeError,sqlite3.Error):return False

    def save_visual_batch(self, event):
        fields={'operation','request_id','tab_id','window_id','session_id','query_index','query','batch_index','cards','native_origin'}
        if (not isinstance(event,dict) or set(event)!=fields or event['operation']!='visual_v2_batch'
                or self.challenge.get('version')!=3 or not self.anchor_origin or event['native_origin']!=self.anchor_origin
                or event['request_id']!=self.challenge['request_id'] or event['tab_id']!=self.tab
                or event['window_id']!=self.window or event['session_id']!=self.session
                or event['query_index']!=self.challenge['query_index'] or event['query']!=self.challenge['query']
                or not self.query_verified or not self.anchor_guard_alive() or self.fence() is not True):
            return False
        try:
            self.store.record_visual_v2_batch(event['request_id'],self.challenge['plan_digest'],self.challenge['lease_epoch'],
                event['query_index'],event['query'],event['batch_index'],event['cards'],self.fence)
        except (ValueError,TypeError,sqlite3.Error):
            return False
        return True

    def finish_visual_query(self, event):
        fields={'operation','request_id','tab_id','window_id','session_id','query_index','query','status','end_checks','no_results','native_origin'}
        if (not isinstance(event,dict) or set(event)!=fields or event['operation']!='visual_v2_finish'
                or self.challenge.get('version')!=3 or not self.anchor_origin or event['native_origin']!=self.anchor_origin
                or event['request_id']!=self.challenge['request_id'] or event['tab_id']!=self.tab
                or event['window_id']!=self.window or event['session_id']!=self.session
                or event['query_index']!=self.challenge['query_index'] or event['query']!=self.challenge['query']
                or event['status'] not in ('home_complete','card_limit_reached')
                or type(event['end_checks']) is not int or type(event['no_results']) is not bool
                or (event['status']=='home_complete' and event['end_checks'] < 3)
                or not self.query_verified or not self.anchor_guard_alive() or self.fence() is not True):
            return False
        try:
            self.store.finish_visual_v2_query(event['request_id'],self.challenge['plan_digest'],self.challenge['lease_epoch'],
                event['query_index'],event['query'],event['status'],self.fence,no_results=event['no_results'])
        except (ValueError,TypeError,sqlite3.Error):
            return False
        self.done=True
        return True

    def complete_query_cards(self,event):
        fields={'operation','request_id','tab_id','window_id','session_id','query_index','cards'}
        short=isinstance(event,dict) and event.get('exhausted') is True
        if (not isinstance(event,dict) or set(event)!=(fields|{'query','exhausted','native_origin'} if short else fields) or event['request_id']!=self.challenge['request_id']
            or event['tab_id']!=self.tab or event['window_id']!=self.window or event['session_id']!=self.session
            or type(event['query_index']) is not int or not 0<=event['query_index']<(6 if self.challenge.get('version')==2 else 3)
            or (self.anchor_origin and not self.anchor_guard_alive())
            or (short and (self.challenge.get('version')!=2 or event['query_index']<3 or event['native_origin']!=self.anchor_origin))
            or self.fence() is not True): return False
        index=event['query_index']; cards=event['cards']
        if not isinstance(cards,list) or (not cards and not short) or (cards and not isinstance(cards[0],dict)): return False
        query=event['query'] if short else cards[0].get('query')
        if not isinstance(query,str) or not query:return False
        if index==self.challenge['query_index'] and not self.watch_only and (not self.query_verified or query!=self.challenge['query']): return False
        try: self.store.record_cards(event['request_id'],self.challenge['plan_digest'],self.challenge['lease_epoch'],index,query,cards,self.fence,exhausted=short)
        except (ValueError,TypeError,sqlite3.Error): return False
        if index==self.challenge['query_index']: self.done=True
        return True

    def recover_receipt(self, event):
        fields={'operation','request_id','tab_id','window_id','session_id','query_index','query'}
        if (not isinstance(event,dict) or set(event)!=fields or event['operation']!='recover_receipt'
                or event['request_id']!=self.challenge['request_id'] or event['tab_id']!=self.tab or self.tab is None
                or event['window_id']!=self.window or event['session_id']!=self.session
                or type(event['query_index']) is not int or not 0<=event['query_index']<(9 if self.challenge.get('version')==3 else 6 if self.challenge.get('version')==2 else 3)
                or not isinstance(event['query'],str) or not event['query'] or self.fence() is not True): return None
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            rows=[json.loads(r[0]) for r in db.execute('SELECT body FROM accepted_receipts WHERE request_id=? ORDER BY id',(event['request_id'],))]
            matches=[r for r in rows if all(r.get(k)==event[k] for k in ('request_id','tab_id','window_id','session_id','query_index','query'))
                     and r.get('lease_epoch')==self.challenge['lease_epoch'] and r.get('plan_digest')==self.challenge['plan_digest']
                     and r.get('platform')==self.challenge['platform'] and r.get('collector_tab_id')==event['tab_id']]
            if len(matches)!=1: return None
            db.execute('INSERT OR IGNORE INTO receipt_recoveries VALUES (?,?,?)',(event['request_id'],self.challenge['lease_epoch'],event['query_index']))
            return matches[0]

    def _tab_guard_connected(self, session_id=None):
        with self._guard_lock:
            age=time.monotonic()-self._guard_last_seen if self._guard_last_seen is not None else -1
            return bool(self._guard and 0<=age<=GUARD_TIMEOUT_SECONDS and (session_id is None or session_id==self._guard[1]))

    def guard_alive(self, session_id=None):
        return (not self.rebind_requested and self._tab_guard_connected(session_id)
                and (not self.anchor_origin or self._anchor_connected()) and self.fence() is True
                and (not self.gallery_binding or self.gallery_binding.ready(self)))

    def _guard_loop(self, conn, token):
        try:
            conn.settimeout(None)
            with conn, conn.makefile('rwb') as stream:
                while True:
                    msg=read_message(stream)
                    if msg is None: break
                    with self._guard_lock:
                        now=time.monotonic()
                        age=now-self._guard_last_seen if self._guard_last_seen is not None else -1
                        ok=(msg=={'operation':'guard_ping'} and self._guard is token and 0<=age<=GUARD_TIMEOUT_SECONDS and self.fence() is True)
                        if ok: self._guard_last_seen=now
                    write_message(stream,{'ok':ok})
                    if not ok: break
        except (OSError,ValueError,TypeError): pass
        finally:
            with self._guard_lock:
                if self._guard is token: self._guard=None;self._guard_socket=None;self._guard_last_seen=None

    def __enter__(self):
        parent=self.path.parent; info=parent.lstat()
        if not self.path.is_absolute() or parent.is_symlink() or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode)!=0o700: raise ValueError('Private socket required')
        lock_path=parent/(self.path.name+'.lock')
        fd=os.open(lock_path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            ls=os.fstat(fd)
            if not stat.S_ISREG(ls.st_mode) or ls.st_uid!=os.getuid() or stat.S_IMODE(ls.st_mode)!=0o600 or ls.st_nlink!=1: raise ValueError('Unsafe lock')
            try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError as exc: raise ValueError('Broker already running') from exc
            if os.path.lexists(self.path):
                old=self.path.lstat()
                if not stat.S_ISSOCK(old.st_mode) or old.st_uid!=os.getuid() or stat.S_IMODE(old.st_mode)!=0o600: raise ValueError('Unsafe existing socket')
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as probe:
                    probe.settimeout(.5)
                    try: probe.connect(str(self.path))
                    except OSError as exc:
                        if exc.errno!=errno.ECONNREFUSED: raise ValueError('Socket state uncertain') from exc
                    else: raise ValueError('Broker already running')
                current=self.path.lstat()
                if (old.st_dev,old.st_ino)!=(current.st_dev,current.st_ino): raise ValueError('Socket changed')
                self.path.unlink()
            self._lock_fd=fd
        except BaseException:
            os.close(fd);raise
        try:
            if not self.gallery_binding:
                self.store.issue(self.challenge)
            self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            self.sock.bind(str(self.path));bound=self.path.lstat();self._socket_inode=(bound.st_dev,bound.st_ino)
            os.chmod(self.path,0o600);self.sock.listen(16);self.sock.settimeout(.2)
        except BaseException:
            if self.sock: self.sock.close();self.sock=None
            self._remove_own_socket()
            os.close(self._lock_fd);self._lock_fd=None
            raise
        return self

    def serve_once(self):
        try: conn,_=self.sock.accept()
        except socket.timeout: return False
        # A timed-out Chrome caller can close the socket before the reply.
        # Isolate that connection, including any buffered flush on close.
        with suppress(BrokenPipeError, ConnectionResetError, TimeoutError):
            self._serve_client(conn)
        return True

    def _serve_client(self, conn):
        with conn:
            conn.settimeout(5)
            with conn.makefile('rwb') as stream:
                op = None
                try:
                    message=read_message(stream)
                    op=message.get('operation') if isinstance(message,dict) else None
                    trace_native(self.path.parent/'collector-window-trace.jsonl',op,'broker_received',self.challenge.get('request_id'))
                    if isinstance(message,dict) and set(message)=={'operation','tab_id','window_id','session_id'} and message.get('operation')=='binding_status':
                        target=self.tab
                        fenced=self.fence() is True
                        valid=fenced and not self.rebind_requested and message['session_id']==self.session and message['window_id']==self.window
                        matches=bool(valid and target is not None and message['tab_id']==target)
                        gallery_ready = bool(self.gallery_binding and matches and self.gallery_binding.ready(self))
                        guard_required = bool(self.watch_only and matches and (not self.gallery_binding or gallery_ready))
                        anchor_active=bool(fenced and self.anchor_origin and self._anchor_connected())
                        result={'ok':valid,'request_id':self.challenge['request_id'] if fenced else None,'current_tab_id':message['tab_id'],'bound_tab_id':target if valid else None,'matches':matches,'guard_required':guard_required,
                                'anchor_required':bool(self.anchor_origin),'anchor_active':anchor_active,
                                'guard_active':guard_required and (not self.anchor_origin or anchor_active) and self._tab_guard_connected(self.session)}
                        if self.gallery_binding and fenced:
                            result.update(gallery_phase=True,gallery_binding_required=bool(matches and not gallery_ready))
                        if matches and getattr(self, 'task_id', None) is not None:
                            result['task_id'] = self.task_id
                        if fenced and self.anchor_origin:
                            result.update(platform=self.challenge['platform'], anchor_extension_id=urlsplit(self.anchor_origin).hostname,
                                          peer_extension_id=urlsplit(self.peer_origin).hostname if self.peer_origin else None)
                        write_message(stream,result)
                        return True
                    elif isinstance(message,dict) and message.get('operation')=='anchor_guard_open':
                        token=self.open_anchor_guard(message)
                        trace_native(self.path.parent/'collector-window-trace.jsonl',op,'broker_result',self.challenge.get('request_id'),code='ok' if token else self.anchor_guard_failure or 'rejected')
                        try:
                            write_message(stream,{'ok':bool(token)})
                        except OSError:
                            if token:
                                self.close_anchor_guard(token)
                            raise
                        if token:
                            threading.Thread(target=self._anchor_guard_loop,args=(conn.dup(),token),daemon=True).start()
                        return True
                    elif isinstance(message,dict) and message.get('operation')=='guard_open':
                        fields = {'operation','tab_id','window_id','session_id'}
                        scoped = set(message) == fields | {'request_id'} and message['request_id'] == self.challenge['request_id'] and self.watch_only
                        target = self.tab if self.gallery_binding else self.initial_tab
                        ok=((set(message)==fields or scoped) and (not self.gallery_binding or (scoped and self.gallery_binding.ready(self)))
                            and type(target) is int and type(message['tab_id']) is int and message['tab_id']==target and message['window_id']==self.window and message['session_id']==self.session and self.fence() is True)
                        expired_socket=None
                        with self._guard_lock:
                            if self._guard is not None and self._guard_last_seen is not None and time.monotonic()-self._guard_last_seen>GUARD_TIMEOUT_SECONDS:
                                expired_socket=self._guard_socket;self._guard=None;self._guard_socket=None;self._guard_last_seen=None
                            if self._guard is not None: ok=False
                            if ok:
                                token=(object(),message['session_id']);self._guard=token;self._guard_last_seen=time.monotonic()
                        if expired_socket:
                            try: expired_socket.shutdown(socket.SHUT_RDWR)
                            except OSError: pass
                        trace_native(self.path.parent/'collector-window-trace.jsonl',op,'broker_result',self.challenge.get('request_id'),code='ok' if ok else 'guard_open_rejected')
                        try:
                            write_message(stream,{'ok':ok} if ok else {'ok':False,'error':'guard_open_rejected'})
                        except OSError:
                            if ok:
                                with self._guard_lock:
                                    if self._guard is token:
                                        self._guard=None;self._guard_last_seen=None
                            raise
                        if ok:
                            duplicate=conn.dup()
                            with self._guard_lock: self._guard_socket=duplicate
                            threading.Thread(target=self._guard_loop,args=(duplicate,token),daemon=True).start()
                        return True
                    if isinstance(message,dict) and message.get('operation')=='peer_challenge':
                        token=self.peer_challenge(message)
                        result={'ok':True,'token':token,'epoch':self.challenge['lease_epoch'],'anchor_id':self.anchor_origin.removeprefix('chrome-extension://').removesuffix('/'),'peer_id':self.peer_origin.removeprefix('chrome-extension://').removesuffix('/')} if token else {'ok':False}
                    elif isinstance(message,dict) and message.get('operation')=='peer_confirm': result={'ok':self.peer_confirm(message)}
                    elif isinstance(message,dict) and message.get('operation')=='task_tab_challenge':
                        offer=self.task_tab_challenge(message)
                        if isinstance(offer,dict):
                            result=offer
                        else:
                            result={'ok':True,'token':offer,'epoch':self.challenge['lease_epoch'],'platform':self.challenge['platform']} if offer else {'ok':False}
                        code = ('task_tab_reused' if 'bound_tab_id' in result else 'ok') if result['ok'] else result.get('error','rejected')
                        trace_native(self.path.parent/'collector-window-trace.jsonl',op,'broker_result',self.challenge.get('request_id'),code=code)
                    elif isinstance(message,dict) and message.get('operation')=='task_tab_confirm':
                        result={'ok':self.task_tab_confirm(message)}
                        trace_native(self.path.parent/'collector-window-trace.jsonl',op,'broker_result',self.challenge.get('request_id'),code='ok' if result['ok'] else 'rejected')
                    elif isinstance(message,dict) and message.get('operation')=='anchor_challenge':
                        token=self.anchor_challenge(message)
                        result={'ok':True,'token':token,'epoch':self.challenge['lease_epoch']} if token else {'ok':False}
                        trace_native(self.path.parent/'collector-window-trace.jsonl',op,'broker_result',self.challenge.get('request_id'),code='ok' if token else 'rejected')
                    elif isinstance(message,dict) and message.get('operation')=='anchor_confirm':
                        accepted=self.anchor_confirm(message)
                        result={'ok':accepted,'rebind_required':bool(accepted and self.rebind_requested)}
                        code='rebind_required' if result['rebind_required'] else ('ok' if accepted else 'rejected')
                        trace_native(self.path.parent/'collector-window-trace.jsonl',op,'broker_result',self.challenge.get('request_id'),code=code)
                    elif isinstance(message,dict) and message.get('operation')=='activation_challenge':
                        result={'challenge':self.issue_activation(message)}
                        if not result['challenge']:
                            result['error'] = self.activation_error
                        trace_native(self.path.parent/'collector-window-trace.jsonl',op,'broker_result',self.challenge.get('request_id'),code=result.get('error','ok'))
                    elif isinstance(message,dict) and message.get('operation')=='activation_confirm':
                        result={'ok':self.confirm_activation(message)}
                        trace_native(self.path.parent/'collector-window-trace.jsonl',op,'broker_result',self.challenge.get('request_id'),code='ok' if result['ok'] else 'rejected')
                    elif op in ('visual_photo_start','visual_photo_saved'):
                        if not getattr(self,'visual_photo',None):
                            raise CollectorProgressError('visual_photo_unavailable','本次任务未启用候选首图')
                        result=self.visual_photo(self,message)
                    elif op in ('gallery_download_next','gallery_download_saved'):
                        if not self.gallery_binding or not self.gallery_downloads:
                            raise CollectorProgressError('gallery_phase_invalid','当前任务无已确认图库')
                        result=self.gallery_downloads.handle(self,message)
                    elif op in ('gallery_binding_challenge','gallery_binding_confirm'):
                        if not self.gallery_binding:
                            raise CollectorProgressError('gallery_phase_invalid','当前任务未进入图库恢复阶段')
                        result=self.gallery_binding.handle(self,message)
                    elif self.gallery_binding and op in ('stage_evidence','submit','poll','watch','observe_tab','recover_receipt',
                                                        'query_cards_complete','visual_v2_batch','visual_v2_finish','card_v3_page_challenge','card_v3_page_confirm','card_v3_batch','card_v3_page_finish','card_v3_progress','collector_progress','checkpoint_detail','next_candidate','seal_expansion'):
                        result={'ok':False,'error':'gallery_phase_invalid'}
                    elif op == 'home_tab_missing':
                        result={'ok':self.home_tab_missing(message)}
                    elif op in ('collector_progress', 'checkpoint_detail', 'next_candidate', 'gallery_capture', 'seal_expansion'):
                        result = handle_progress(self, message)
                    elif op == 'card_v3_page_challenge':
                        result={'challenge':self.issue_card_v3_page(message)}
                    elif op == 'card_v3_page_confirm':
                        result={'ok':self.confirm_card_v3_page(message)}
                    elif op == 'card_v3_batch':
                        result={'ok':self.save_card_v3_batch(message)}
                    elif op == 'card_v3_page_finish':
                        result={'ok':self.finish_card_v3_page(message)}
                    elif op == 'card_v3_progress':
                        data=self.card_v3_progress(message);result={'ok':data is not None,'progress':data}
                    elif isinstance(message,dict) and message.get('operation')=='visual_v2_batch':
                        result={'ok':self.save_visual_batch(message)}
                    elif isinstance(message,dict) and message.get('operation')=='visual_v2_finish':
                        result={'ok':self.finish_visual_query(message)}
                    elif isinstance(message,dict) and message.get('operation')=='query_cards_complete':
                        result={'ok':self.complete_query_cards(message)}
                    elif isinstance(message,dict) and message.get('operation')=='stage_evidence':
                        result={'ok':self.stage_evidence(message)}
                    elif isinstance(message,dict) and message.get('operation')=='select_window':
                        accepted=self.select_window(message)
                        result={'ok':accepted,'error':'session_changed_reopen'} if self.rebind_requested else {'ok':accepted}
                    elif isinstance(message,dict) and set(message)=={'operation','request_id','tab_id'} and message['operation']=='poll': result={'challenge':self.poll(message['tab_id'])} if message['request_id']==self.challenge['request_id'] else {'challenge':None,'error':'request_changed'}
                    elif isinstance(message,dict) and message.get('operation')=='recover_receipt': result={'receipt':self.recover_receipt(message)}
                    elif isinstance(message,dict) and message.get('operation')=='watch':
                        result={'challenge':self.challenge if self.initial_tab is None and message.get('window_id')==self.window and time.monotonic()-self.started<=15 and self.fence() is True else None}
                    elif isinstance(message,dict) and message.get('operation')=='observe_tab': result={'ok':self.observe(message)}
                    elif isinstance(message,dict) and set(message)=={'operation','receipt'} and message['operation']=='submit' and not self.conflict and message['receipt'].get('tab_id')==self.tab and (self.session is None or message['receipt'].get('session_id')==self.session) and (self.initial_tab is not None or (message['receipt'].get('tab_id'),message['receipt'].get('session_id')) in self.candidates):
                        self.store.accept(message['receipt'],self.fence,reconcile=self.reconcile);self.query_verified=True;result={'ok':True}
                        if self.challenge.get('version') in (4,5) and getattr(self,'requested_page',1)==0:self.done=True
                    else: result={'ok':False}
                except CollectorProgressError as error:
                    result = {'ok': False, 'error': error.code, 'message': str(error)}
                except APIError:
                    result = {'ok': False, 'error': 'checkpoint_binding_invalid', 'message': '宿主无法确认该任务租约'}
                except (ValueError,TypeError,KeyError,OSError,sqlite3.Error): result={'ok':False}
                if op in ('gallery_capture','gallery_binding_challenge','gallery_binding_confirm','gallery_download_next',
                          'gallery_download_saved','visual_photo_start','visual_photo_saved'):
                    trace_native(self.path.parent/'collector-window-trace.jsonl', op, 'broker_result',
                                 self.challenge['request_id'], code='ok' if result.get('ok') else result.get('error', 'rejected'),
                                 message=result.get('message', ''))
                try: write_message(stream,result)
                except OSError: pass
        return True

    def _remove_own_socket(self):
        try: current=self.path.lstat()
        except FileNotFoundError: return
        if self._socket_inode==(current.st_dev,current.st_ino) and stat.S_ISSOCK(current.st_mode): self.path.unlink()

    def __exit__(self,*args):
        with self._anchor_lock:self._anchor_guard=None;self._anchor_seen=None
        with self._guard_lock:
            self._guard=None;self._guard_last_seen=None
            guarded=self._guard_socket;self._guard_socket=None
        if guarded:
            try: guarded.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        if self.sock: self.sock.close()
        self._remove_own_socket()
        if self._lock_fd is not None: os.close(self._lock_fd);self._lock_fd=None

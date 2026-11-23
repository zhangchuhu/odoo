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
        self.query_verified=any(all(r.get(k)==challenge.get(k) for k in ('lease_epoch','plan_digest','query_index','query')) and r.get('tab_id')==expected_tab_id and r.get('window_id')==expected_window_id and r.get('session_id')==expected_session for r in receipts)
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
        if (not self.anchor_origin or set(event)!={'operation','request_id','session_id','window_id','marker','native_origin'}
                or event.get('native_origin')!=self.anchor_origin or event.get('request_id')!=self.challenge['request_id']
                or self.rebind_requested or not self._anchor_matches_binding(event.get('window_id'), event.get('session_id'))
                or self.anchor_registry.current()!=(event.get('window_id'),event.get('session_id'),event.get('marker'))
                or self.fence() is not True): return None
        with self._anchor_lock:
            if self._live_anchor_connection(): return None
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
            return dict(selected, detail_identity=identity, detail_url=event['detail_url'])
        index=self.challenge['query_index']
        if self.watch_only or (index>0 and not self.query_verified):
            prior_index=2 if self.watch_only else index-1
            with closing(sqlite3.connect(self.store.path)) as db:
                cards=db.execute('SELECT body FROM query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=?', (self.challenge['request_id'],self.challenge['plan_digest'],self.challenge['lease_epoch'],prior_index)).fetchone()
                rows=[json.loads(row[0]) for row in db.execute('SELECT body FROM accepted_receipts WHERE request_id=?',(self.challenge['request_id'],))]
            previous=[r for r in rows if r.get('query_index')==prior_index and all(r.get(k)==self.challenge.get(k) for k in ('request_id','lease_epoch','plan_digest','platform')) and r.get('tab_id')==self.tab and r.get('window_id')==self.window and r.get('session_id')==self.session]
            if cards and len(previous)==1:
                try: saved=json.loads(cards[0])
                except (ValueError,TypeError): saved=None
                if isinstance(saved,list) and 30<=len(saved)<=50 and all(isinstance(c,dict) and c.get('query')==previous[0].get('query') for c in saved):
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
                        or checked_detail_identity(self, event['url']) != event['query']):
                    return False
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
        body=json.dumps(event['evidence'],sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        if len(body.encode())>900000: return False
        if self.progress_plan is not None:
            from .collector_import import convert_evidence
            plan, config = self.progress_plan
            try:
                convert_evidence(event['evidence'], collector_id(event['request_id']), plan['platform'],
                                 plan['queries'], plan['category'], datetime.now(timezone.utc).isoformat(),
                                 **{k: config[k] for k in ('result_limit', 'min_sold', 'min_reviews', 'min_rating')})
            except (TypeError, ValueError):
                return False
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if self.progress_plan is not None and not verify_checkpoint_evidence(db, self.challenge, event['evidence'], allow_previous_stage=True):
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

    def complete_query_cards(self,event):
        fields={'operation','request_id','tab_id','window_id','session_id','query_index','cards'}
        if (not isinstance(event,dict) or set(event)!=fields or event['request_id']!=self.challenge['request_id']
            or event['tab_id']!=self.tab or event['window_id']!=self.window or event['session_id']!=self.session
            or type(event['query_index']) is not int or not 0<=event['query_index']<3
            or (self.anchor_origin and not self.anchor_guard_alive())
            or self.fence() is not True): return False
        index=event['query_index']; cards=event['cards']
        if not isinstance(cards,list) or not cards or not isinstance(cards[0],dict): return False
        query=cards[0].get('query')
        if index==self.challenge['query_index'] and not self.watch_only and (not self.query_verified or query!=self.challenge['query']): return False
        try: self.store.record_cards(event['request_id'],self.challenge['plan_digest'],self.challenge['lease_epoch'],index,query,cards,self.fence)
        except (ValueError,TypeError,sqlite3.Error): return False
        if index==self.challenge['query_index']: self.done=True
        return True

    def recover_receipt(self, event):
        fields={'operation','request_id','tab_id','window_id','session_id','query_index','query'}
        if (not isinstance(event,dict) or set(event)!=fields or event['operation']!='recover_receipt'
                or event['request_id']!=self.challenge['request_id'] or event['tab_id']!=self.tab or self.tab is None
                or event['window_id']!=self.window or event['session_id']!=self.session
                or type(event['query_index']) is not int or not 0<=event['query_index']<3
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
                        if fenced and self.anchor_origin:
                            result.update(platform=self.challenge['platform'], anchor_extension_id=urlsplit(self.anchor_origin).hostname,
                                          peer_extension_id=urlsplit(self.peer_origin).hostname if self.peer_origin else None)
                        write_message(stream,result)
                        return True
                    elif isinstance(message,dict) and message.get('operation')=='anchor_guard_open':
                        token=self.open_anchor_guard(message)
                        trace_native(self.path.parent/'collector-window-trace.jsonl',op,'broker_result',self.challenge.get('request_id'),code='ok' if token else 'rejected')
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
                                                        'query_cards_complete','collector_progress','checkpoint_detail','next_candidate'):
                        result={'ok':False,'error':'gallery_phase_invalid'}
                    elif op in ('collector_progress', 'checkpoint_detail', 'next_candidate', 'gallery_capture'):
                        result = handle_progress(self, message)
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

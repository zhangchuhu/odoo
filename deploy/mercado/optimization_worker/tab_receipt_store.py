"""Local receipt ledger. Callers must authenticate the native host before accept.

This is not a network endpoint and does not authenticate extension origins.
Session changes fail closed until a separate, verified rebind is implemented.
"""
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlsplit


class TabReceiptStore:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS challenges (nonce TEXT PRIMARY KEY, body TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0)')
            db.execute('CREATE TABLE IF NOT EXISTS bindings (request_id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS binding_history (id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS accepted_receipts (id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS query_cards (request_id TEXT, plan_digest TEXT, lease_epoch INTEGER, query_index INTEGER, body TEXT NOT NULL, PRIMARY KEY(request_id,plan_digest,lease_epoch,query_index))')

            db.execute("""CREATE TABLE IF NOT EXISTS inherited_query_cards (
                request_id TEXT, plan_digest TEXT, lease_epoch INTEGER, query_index INTEGER,
                source_request TEXT NOT NULL, source_epoch INTEGER NOT NULL,
                observed_at TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(request_id, plan_digest, lease_epoch, query_index))""")

            db.execute("""CREATE TABLE IF NOT EXISTS inherited_details (
                request_id TEXT, plan_digest TEXT, lease_epoch INTEGER, detail_index INTEGER,
                body TEXT NOT NULL, PRIMARY KEY(request_id, plan_digest, lease_epoch, detail_index))""")

    @staticmethod
    def _restore_checkpoint_details(db, current):
        key = 'request_id=? AND plan_digest=? AND lease_epoch=?'
        rows = db.execute(f'SELECT detail_index,body FROM inherited_details WHERE {key} ORDER BY detail_index', current).fetchall()
        if not rows:
            return
        origin = db.execute(f'SELECT body,observed_at FROM inherited_query_cards WHERE {key} ORDER BY query_index', current).fetchall()
        saved = db.execute(f'SELECT body FROM query_cards WHERE {key} ORDER BY query_index', current).fetchall()
        if len(saved) != 3 or [r[0] for r in saved] != [r[0] for r in origin]:
            return
        from .collector_import import _observation_time
        now = datetime.now(timezone.utc)
        stamps = [_observation_time(stamp) for _, stamp in origin]
        if any(not 0 <= (now - stamp).total_seconds() < 86400 for stamp in stamps):
            return
        for index, body in rows:
            detail = json.loads(body)
            if 'observed_at' in detail and not 0 <= (now - _observation_time(detail['observed_at'])).total_seconds() < 86400:
                break
            db.execute('INSERT INTO collector_details(request_id,plan_digest,lease_epoch,detail_index,body) VALUES (?,?,?,?,?)',
                       (*current, index, body))

    def read_inherited_cards(self, request_id, plan_digest, lease_epoch, query_index):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT body, observed_at FROM inherited_query_cards WHERE request_id=? "
                "AND plan_digest=? AND lease_epoch=? AND query_index=?",
                (request_id, plan_digest, lease_epoch, query_index),
            ).fetchone()
        if row:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(row[1])).total_seconds()
            if 0 <= age < 86400:
                return json.loads(row[0])
        return None

    def completed_card_index(self, request_id, plan_digest, lease_epoch):
        with closing(sqlite3.connect(self.path)) as db:
            rows=[row[0] for row in db.execute('SELECT query_index FROM query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index',(request_id,plan_digest,lease_epoch))]
        if rows!=list(range(len(rows))) or len(rows)>3: raise ValueError('Inconsistent card progress')
        return len(rows)

    def collection_progress(self, request_id, plan_digest, lease_epoch):
        from .collector_progress import initialize_details
        key = (request_id, plan_digest, lease_epoch)
        with closing(sqlite3.connect(self.path)) as db, db:
            initialize_details(db)
            db.execute('BEGIN')
            groups = [r[0] for r in db.execute(
                'SELECT query_index FROM query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index', key)]
            details = [r[0] for r in db.execute(
                'SELECT detail_index FROM collector_details WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY detail_index', key)]
            if groups != list(range(len(groups))) or len(groups) > 3 or details != list(range(len(details))):
                raise ValueError('Nonconsecutive collection checkpoints')
            return len(groups), len(details)

    def detail_status(self, request_id, plan_digest, lease_epoch):
        from .collector_progress import initialize_details
        key = (request_id, plan_digest, lease_epoch)
        with closing(sqlite3.connect(self.path)) as db, db:
            initialize_details(db)
            db.execute('BEGIN')
            rows = db.execute(
                'SELECT d.detail_index,d.body,h.body FROM collector_details d LEFT JOIN inherited_details h '
                'USING (request_id,plan_digest,lease_epoch,detail_index) '
                'WHERE d.request_id=? AND d.plan_digest=? AND d.lease_epoch=? ORDER BY d.detail_index', key).fetchall()
            inherited = sum(saved == original for _, saved, original in rows)
            source = db.execute(
                'SELECT source_request,source_epoch FROM inherited_query_cards '
                'WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=0', key).fetchone() if inherited else None
        return {'saved': len(rows), 'restored': inherited,
                'source_request': source[0] if source else None, 'source_epoch': source[1] if source else None,
                'last_identity': json.loads(rows[-1][1])['identity'] if rows else None}

    def require_checkpoint_evidence(self, request_id, plan_digest, lease_epoch, evidence):
        from .collector_progress import verify_checkpoint_evidence
        challenge = dict(request_id=request_id, plan_digest=plan_digest, lease_epoch=lease_epoch)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('BEGIN')
            if not verify_checkpoint_evidence(db, challenge, evidence, allow_previous_stage=True):
                raise ValueError('Evidence differs from acknowledged collector progress')

    def next_query_index(self, request_id, plan_digest, queries, lease_epoch):
        # A restarted browser needs fresh receipts, even if all cards are durable.
        verified = self.resume_index(request_id, plan_digest, queries, lease_epoch)
        saved = self.completed_card_index(request_id, plan_digest, lease_epoch)
        return min(verified, saved)

    def record_cards(self, request_id, plan_digest, lease_epoch, query_index, query, cards, fence):
        if not callable(fence) or type(query_index) is not int or not 0<=query_index<3 or not isinstance(cards,list) or not 30<=len(cards)<=50: raise ValueError('Invalid card progress')
        if any(not isinstance(c,dict) or c.get('query')!=query or c.get('rank')!=i+1 or type(c.get('is_ad')) is not bool or not isinstance(c.get('title'),str) or not c['title'] or not isinstance(c.get('url'),str) or not isinstance(c.get('product_id'),str) or not c['product_id'] for i,c in enumerate(cards)): raise ValueError('Invalid card observations')
        body=json.dumps(cards,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        if len(body.encode())>900000: raise ValueError('Card progress too large')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            receipts=[json.loads(row[0]) for row in db.execute('SELECT body FROM accepted_receipts WHERE request_id=?',(request_id,))]
            matching=[r for r in receipts if r.get('plan_digest')==plan_digest and r.get('lease_epoch')==lease_epoch and r.get('query_index')==query_index and r.get('query')==query]
            if not matching or fence() is not True: raise ValueError('Verified receipt required')
            from .collector_import import _identity
            platform=matching[-1].get('platform'); seen=set()
            for card in cards:
                identity,_=_identity(card['url'],platform)
                if identity!=card['product_id'] or identity in seen: raise ValueError('Card identity mismatch')
                seen.add(identity)
            old=db.execute('SELECT body FROM query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=?',(request_id,plan_digest,lease_epoch,query_index)).fetchone()
            if old:
                if old[0]!=body: raise ValueError('Card progress differs')
                return
            indices=[r[0] for r in db.execute('SELECT query_index FROM query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index',(request_id,plan_digest,lease_epoch))]
            if indices!=list(range(query_index)): raise ValueError('Card progress out of order')
            db.execute('INSERT INTO query_cards VALUES (?,?,?,?,?)',(request_id,plan_digest,lease_epoch,query_index,body))
            if query_index == 2:
                self._restore_checkpoint_details(db, (request_id, plan_digest, lease_epoch))

    def issue(self, request):
        if (not isinstance(request, dict) or set(request) != {'version','request_id','nonce','lease_epoch','plan_digest','platform','query_index','query'}
                or request['version'] != 1 or not isinstance(request['request_id'],str) or not request['request_id']
                or not isinstance(request['nonce'],str) or not re.fullmatch('[A-Za-z0-9_-]{32,128}',request['nonce'])
                or type(request['lease_epoch']) is not int or request['lease_epoch'] < 1
                or not isinstance(request['plan_digest'],str) or not re.fullmatch('[a-f0-9]{64}',request['plan_digest'])
                or request['platform'] not in ('mercado-libre-mx','shein-us') or type(request['query_index']) is not int or not 0 <= request['query_index'] < 3
                or not isinstance(request['query'],str) or not request['query'].strip()):
            raise ValueError('Invalid challenge')
        with closing(sqlite3.connect(self.path)) as db, db:
            try: db.execute('INSERT INTO challenges(nonce,body) VALUES (?,?)',(request['nonce'],json.dumps(request,sort_keys=True)))
            except sqlite3.IntegrityError: raise ValueError('Challenge already exists') from None

    def accept(self, receipt, fence, *, reconcile=None):
        if not isinstance(receipt,dict) or not callable(fence): raise ValueError('Invalid receipt')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT body,used FROM challenges WHERE nonce=?',(receipt.get('nonce'),)).fetchone()
            if not row or row[1]: raise ValueError('Challenge missing or consumed')
            request=json.loads(row[0])
            if any(receipt.get(k)!=v for k,v in request.items()) or set(receipt) != set(request)|{'session_id','tab_id','window_id','collector_tab_id','url','observed_at'}:
                raise ValueError('Receipt differs from challenge')
            url=urlsplit(receipt['url']) if isinstance(receipt['url'],str) else None
            allowed = ('listado.mercadolibre.com.mx',) if request['platform']=='mercado-libre-mx' else ('us.shein.com',)
            try:
                stamp=datetime.fromisoformat(receipt['observed_at'].replace('Z','+00:00'))
                age=(datetime.now(timezone.utc)-stamp).total_seconds()
                if not 0 <= age <= 120: raise ValueError('Stale observation')
            except (TypeError, ValueError, AttributeError): raise ValueError('Invalid observation time') from None
            if (not url or url.scheme!='https' or url.hostname not in allowed or url.username or url.password or url.port
                    or not isinstance(receipt['session_id'],str) or not re.fullmatch('[a-f0-9]{32}',receipt['session_id'])
                    or any(type(receipt[k]) is not int or receipt[k] <= 0 for k in ('tab_id','window_id'))
                    or receipt['collector_tab_id'] != receipt['tab_id']):
                raise ValueError('Invalid browser identity')
            identity={k:receipt[k] for k in ('session_id','tab_id','window_id','plan_digest','platform')}
            existing=db.execute('SELECT body FROM bindings WHERE request_id=?',(request['request_id'],)).fetchone()
            old=json.loads(existing[0]) if existing else None
            changed=bool(old and old != identity)
            if changed and (not callable(reconcile) or old['plan_digest'] != identity['plan_digest'] or old['platform'] != identity['platform'] or old['session_id'] == identity['session_id'] or reconcile() is not True):
                raise ValueError('Browser binding changed; reconcile before rebind')
            if fence() is not True: raise ValueError('Lease fence failed')
            prior=[json.loads(r[0]) for r in db.execute('SELECT body FROM accepted_receipts WHERE request_id=? ORDER BY id',(request['request_id'],))]
            same=[r for r in prior if all(r[k]==receipt[k] for k in ('session_id','tab_id','window_id','plan_digest','lease_epoch'))]
            if request['query_index'] != len(same) or len(same)>2: raise ValueError('Queries must be observed in order on the same lease and tab')
            if not old or changed:
                body=json.dumps(identity,sort_keys=True)
                db.execute('INSERT OR REPLACE INTO bindings VALUES (?,?)',(request['request_id'],body))
                db.execute('INSERT INTO binding_history(request_id,body) VALUES (?,?)',(request['request_id'],body))
            db.execute('UPDATE challenges SET used=1 WHERE nonce=?',(request['nonce'],))
            db.execute('INSERT INTO accepted_receipts(request_id,body) VALUES (?,?)',(request['request_id'],json.dumps(receipt,sort_keys=True)))

    def rebind(self, receipt, fence, reconcile):
        """Only a trusted caller that checked the prior invocation may rebind."""
        return self.accept(receipt, fence, reconcile=reconcile)

    def history(self, request_id):
        with closing(sqlite3.connect(self.path)) as db, db:
            return [json.loads(row[0]) for row in db.execute('SELECT body FROM binding_history WHERE request_id=? ORDER BY id',(request_id,))]

    def current_binding(self, request_id):
        with closing(sqlite3.connect(self.path)) as db, db:
            row=db.execute('SELECT body FROM bindings WHERE request_id=?',(request_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def verified_queries(self, request_id, plan_digest, queries, lease_epoch):
        if type(lease_epoch) is not int or lease_epoch<1 or not isinstance(queries,list) or len(queries)!=3 or any(not isinstance(q,str) or not q for q in queries): return False
        with closing(sqlite3.connect(self.path)) as db, db:
            receipts=[json.loads(row[0]) for row in db.execute('SELECT body FROM accepted_receipts WHERE request_id=? ORDER BY id',(request_id,))]
        if len(receipts)<3: return False
        last=receipts[-3:]; first=last[0]
        return all(r.get('lease_epoch')==lease_epoch and r.get('plan_digest')==plan_digest and r.get('query_index')==i and r.get('query')==queries[i]
                   and all(r.get(k)==first.get(k) for k in ('session_id','tab_id','window_id','collector_tab_id','lease_epoch')) for i,r in enumerate(last))

    def resume_index(self, request_id, plan_digest, queries, lease_epoch):
        if type(lease_epoch) is not int or lease_epoch<1 or not isinstance(queries,list) or len(queries)!=3: raise ValueError('Invalid plan')
        binding=self.current_binding(request_id)
        if not binding: return 0
        with closing(sqlite3.connect(self.path)) as db, db:
            receipts=[json.loads(r[0]) for r in db.execute('SELECT body FROM accepted_receipts WHERE request_id=? ORDER BY id',(request_id,))]
        current=[r for r in receipts if r.get('lease_epoch')==lease_epoch and r.get('plan_digest')==plan_digest and all(r.get(k)==binding.get(k) for k in ('session_id','tab_id','window_id'))]
        if len(current)>3 or any(r.get('query_index')!=i or r.get('query')!=queries[i] for i,r in enumerate(current)): raise ValueError('Inconsistent receipt history')
        return len(current)

"""Local receipt ledger. Callers must authenticate the native host before accept.

This is not a network endpoint and does not authenticate extension origins.
Session changes fail closed until a separate, verified rebind is implemented.
"""
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlsplit


class TabReceiptStore:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            if path != ':memory:':os.chmod(path,0o600)
            db.execute('CREATE TABLE IF NOT EXISTS challenges (nonce TEXT PRIMARY KEY, body TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0)')
            db.execute('CREATE TABLE IF NOT EXISTS bindings (request_id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS binding_history (id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS accepted_receipts (id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS query_cards (request_id TEXT, plan_digest TEXT, lease_epoch INTEGER, query_index INTEGER, body TEXT NOT NULL, PRIMARY KEY(request_id,plan_digest,lease_epoch,query_index))')
            db.execute('CREATE TABLE IF NOT EXISTS query_card_meta (request_id TEXT, plan_digest TEXT, lease_epoch INTEGER, query_index INTEGER, query TEXT NOT NULL, exhausted INTEGER NOT NULL, PRIMARY KEY(request_id,plan_digest,lease_epoch,query_index))')
            db.execute('CREATE TABLE IF NOT EXISTS inherited_query_meta (request_id TEXT, plan_digest TEXT, lease_epoch INTEGER, query_index INTEGER, query TEXT NOT NULL, exhausted INTEGER NOT NULL, PRIMARY KEY(request_id,plan_digest,lease_epoch,query_index))')

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
        if len(saved) < 3 or len(saved) != len(origin) or [r[0] for r in saved] != [r[0] for r in origin]:
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

    def query_exhausted(self,request_id,plan_digest,lease_epoch,query_index, *, inherited=False):
        table='inherited_query_meta' if inherited else 'query_card_meta'
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT exhausted FROM '+table+' WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=?',(request_id,plan_digest,lease_epoch,query_index)).fetchone()
        return bool(row and row[0]==1)

    def completed_card_index(self, request_id, plan_digest, lease_epoch, *, staged=False):
        with closing(sqlite3.connect(self.path)) as db:
            rows=[row[0] for row in db.execute('SELECT query_index FROM query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index',(request_id,plan_digest,lease_epoch))]
        if rows!=list(range(len(rows))) or len(rows)>(6 if staged else 3): raise ValueError('Inconsistent card progress')
        return len(rows)

    def collection_progress(self, request_id, plan_digest, lease_epoch, *, staged=False):
        from .collector_progress import initialize_details
        key = (request_id, plan_digest, lease_epoch)
        with closing(sqlite3.connect(self.path)) as db, db:
            initialize_details(db)
            db.execute('BEGIN')
            groups = [r[0] for r in db.execute(
                'SELECT query_index FROM query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index', key)]
            details = [r[0] for r in db.execute(
                'SELECT detail_index FROM collector_details WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY detail_index', key)]
            if groups != list(range(len(groups))) or len(groups) > (6 if staged else 3) or details != list(range(len(details))):
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
                'qualified': sum(json.loads(row[1]).get('status')=='qualified' for row in rows),
                'source_request': source[0] if source else None, 'source_epoch': source[1] if source else None,
                'last_identity': json.loads(rows[-1][1])['identity'] if rows else None}

    def require_checkpoint_evidence(self, request_id, plan_digest, lease_epoch, evidence, *, staged=False):
        from .collector_progress import verify_checkpoint_evidence
        challenge = dict(request_id=request_id, plan_digest=plan_digest, lease_epoch=lease_epoch)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('BEGIN')
            if not verify_checkpoint_evidence(db, challenge, evidence, allow_previous_stage=not staged, staged=staged):
                raise ValueError('Evidence differs from acknowledged collector progress')

    def next_query_index(self, request_id, plan_digest, queries, lease_epoch, *, staged=False, visual_v2=False):
        # A restarted browser needs fresh receipts, even if all cards are durable.
        verified = self.resume_index(request_id, plan_digest, queries, lease_epoch, staged=staged,visual_v2=visual_v2)
        if visual_v2:
            from .visual_v2_checkpoints import initialize, query_status
            with closing(sqlite3.connect(self.path)) as db, db:
                initialize(db)
                saved=0
                for index in range(len(queries)):
                    if not query_status(db,(request_id,plan_digest,lease_epoch,str(index))): break
                    saved+=1
        else:
            saved = self.completed_card_index(request_id, plan_digest, lease_epoch, staged=staged)
        return min(verified, saved)

    def sealed_queries(self, request_id, plan_digest, lease_epoch, fence):
        from .collector_staged_policy import initialize_expansions, load_expansion
        with closing(sqlite3.connect(self.path)) as db, db:
            initialize_expansions(db)
            value=load_expansion(db,(request_id,plan_digest,lease_epoch),fence=fence)
        return value['queries'] if value else []

    def seal_visual_v2_queries(self, request_id, plan_digest, lease_epoch, queries, fence):
        from .visual_v2_checkpoints import initialize, seal_queries, read_queries
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            initialize(db)
            key=(request_id,plan_digest,lease_epoch)
            seal_queries(db,key,queries,fence=fence)
            return read_queries(db,key)

    def visual_v2_queries(self, request_id, plan_digest, lease_epoch, fence):
        from .visual_v2_checkpoints import initialize, read_queries
        if not callable(fence) or fence() is not True: raise ValueError('Collection lease fence failed')
        with closing(sqlite3.connect(self.path)) as db, db:
            initialize(db)
            return read_queries(db,(request_id,plan_digest,lease_epoch))

    def card_home_state(self,request_id,plan_digest,lease_epoch,initial_queries,fence,*,platform='mercado-libre-mx',threshold=None):
        """Read homepage checkpoints without requiring historical tab receipts."""
        from .card_sales_v3_checkpoints import initialize,seal_query,known_count
        from .card_sales_v3_pipeline import initialize_pipeline,progress,freeze_queue
        from .home_collection_policy import next_home_query,record_end_reason,end_reason,shein_known_count
        if not isinstance(initial_queries,list) or len(initial_queries)!=3 or not callable(fence) or fence() is not True:
            raise ValueError('Current homepage plan required')
        scope=(request_id,plan_digest,lease_epoch)
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE');initialize(db);initialize_pipeline(db)
            for index,query in enumerate(initial_queries):seal_query(db,scope,index,query,fence=fence)
            queries=[row[0] for row in db.execute('SELECT query FROM card_v3_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index',scope)]
            if queries[:3]!=initial_queries:raise ValueError('Original homepage plan changed')
            page=next_home_query(db,scope);known=shein_known_count(db,scope) if platform=='shein-us' else known_count(db,scope,include_plus_hundred=True)
            if page is None and (known>=100 or len(queries)>=9):
                freeze_queue(db,scope,plan_exhausted=len(queries)>=9,fence=fence,homepage_only=True,platform=platform,threshold=threshold)
                record_end_reason(db,scope,'sales_target_reached' if known>=100 else 'query_limit',fence=fence)
            state=dict(queries=queries,next_page=page,known_products=known,
                       progress=progress(db,scope,homepage_only=True,platform=platform,threshold=threshold),end_reason=end_reason(db,scope))
            if fence() is not True:raise ValueError('Current lease required')
            return state

    def card_v3_state(self,request_id,plan_digest,lease_epoch,initial_queries,fence):
        from .card_sales_v3_checkpoints import initialize,seal_query,next_page,known_count
        from .card_sales_v3_pipeline import initialize_pipeline,progress,freeze_queue
        if not isinstance(initial_queries,list) or len(initial_queries)!=3 or not callable(fence) or fence() is not True:
            raise ValueError('Current card plan required')
        scope=(request_id,plan_digest,lease_epoch)
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE');initialize(db);initialize_pipeline(db)
            for index,query in enumerate(initial_queries):seal_query(db,scope,index,query,fence=fence)
            queries=[row[0] for row in db.execute('SELECT query FROM card_v3_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index',scope)]
            page=next_page(db,scope);known=known_count(db,scope)
            if page is None and (known>=100 or len(queries)>=9):freeze_queue(db,scope,plan_exhausted=len(queries)>=9,fence=fence)
            next_url=None
            if page and page[1]>1:
                row=db.execute('SELECT next_url FROM card_v3_navigation WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? AND page=?',(*scope,page[0],page[1]-1)).fetchone()
                if not row:raise ValueError('Verified next-page link missing')
                next_url=row[0]
            state=dict(queries=queries,next_page=page,next_url=next_url,known_products=known,progress=progress(db,scope))
            if fence() is not True:raise ValueError('Current lease required')
            return state

    def card_v3_next_page(self,request_id,plan_digest,lease_epoch):
        from .card_sales_v3_checkpoints import next_page
        with closing(sqlite3.connect(self.path)) as db:
            return next_page(db,(request_id,plan_digest,lease_epoch))

    def card_v3_resume_step(self,request_id,plan_digest,lease_epoch,initial_queries):
        """Reconfirm restored query/page identities in this browser before continuing."""
        from .card_sales_v3_checkpoints import next_page
        scope=(request_id,plan_digest,lease_epoch)
        with closing(sqlite3.connect(self.path)) as db:
            queries=[r[0] for r in db.execute('SELECT query FROM card_v3_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index',scope)]
            if queries[:3]!=initial_queries:raise ValueError('Original card plan changed')
            pending=next_page(db,scope)
            used=[row[0] for row in db.execute('SELECT query_index FROM card_v3_pages WHERE request_id=? AND plan_digest=? AND lease_epoch=? UNION SELECT query_index FROM card_v3_batches WHERE request_id=? AND plan_digest=? AND lease_epoch=?',(*scope,*scope))]
            verified=self.resume_index(request_id,plan_digest,queries,lease_epoch,card_v3=True)
            if used and verified<max(used)+1:return verified,0
            if pending and pending[1]>1:
                binding=self.current_binding(request_id)
                if not binding:raise ValueError('Current browser binding required')
                exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='card_v3_receipt_challenges'").fetchone()
                proofs=[json.loads(row[0]) for row in db.execute('SELECT body FROM card_v3_receipt_challenges WHERE used=1')] if exists else []
                for page in range(1,pending[1]):
                    if not any(p.get('request_id')==request_id and p.get('plan_digest')==plan_digest and p.get('lease_epoch')==lease_epoch
                               and p.get('query_index')==pending[0] and p.get('page')==page
                               and p.get('query')==queries[pending[0]]
                               and all(p.get(k)==binding.get(k) for k in ('session_id','tab_id','window_id')) for p in proofs):
                        return pending[0],-page
            return pending

    def card_v3_resume_url(self,request_id,plan_digest,lease_epoch,step):
        if not isinstance(step,tuple) or len(step)!=2 or type(step[0]) is not int or type(step[1]) is not int or step[1]>=0:return None
        index,page=step[0],-step[1];scope=(request_id,plan_digest,lease_epoch)
        with closing(sqlite3.connect(self.path)) as db:
            if page>1:
                row=db.execute('SELECT next_url FROM card_v3_navigation WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? AND page=?',(*scope,index,page-1)).fetchone()
                return row[0] if row else None
            binding=self.current_binding(request_id)
            if not binding:return None
            receipts=[json.loads(row[0]) for row in db.execute('SELECT body FROM accepted_receipts WHERE request_id=? ORDER BY id',(request_id,))]
            matches=[row for row in receipts if row.get('version')==4 and row.get('plan_digest')==plan_digest and row.get('lease_epoch')==lease_epoch
                     and row.get('query_index')==index and all(row.get(k)==binding.get(k) for k in ('session_id','tab_id','window_id'))]
            return matches[-1].get('url') if matches else None

    def budget_elapsed(self, request_id, plan_digest, lease_epoch, fence):
        from .collector_staged_policy import elapsed_seconds
        if not callable(fence) or fence() is not True: raise ValueError('Collection lease fence failed')
        with closing(sqlite3.connect(self.path)) as db:
            return elapsed_seconds(db,(request_id,plan_digest,lease_epoch))

    def record_visual_v2_batch(self, request_id, plan_digest, lease_epoch, query_index, query, batch_index, cards, fence):
        """Store a batch only for a native-verified query and active lease."""
        from .collector_import import _identity
        from .visual_v2_checkpoints import initialize, append_batch
        if (type(query_index) is not int or not 0 <= query_index < 9 or not isinstance(query, str)
                or not query.strip() or not callable(fence) or not isinstance(cards, list) or not cards):
            raise ValueError('Invalid query batch')
        if any(not isinstance(c, dict) or c.get('query') != query or type(c.get('rank')) is not int
               or c['rank'] < 1 or type(c.get('is_ad')) is not bool or not isinstance(c.get('title'), str)
               or not c['title'].strip() or not isinstance(c.get('url'), str) for c in cards):
            raise ValueError('Invalid query observations')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            receipts = [json.loads(row[0]) for row in db.execute(
                'SELECT body FROM accepted_receipts WHERE request_id=?', (request_id,))]
            matching = [r for r in receipts if r.get('plan_digest') == plan_digest
                        and r.get('lease_epoch') == lease_epoch and r.get('query_index') == query_index
                        and r.get('query') == query and r.get('version') == 3]
            if not matching or fence() is not True:
                raise ValueError('Verified receipt required')
            for card in cards:
                identity, _ = _identity(card['url'], matching[-1]['platform'])
                if identity != card.get('product_id'):
                    raise ValueError('Card identity mismatch')
            initialize(db)
            append_batch(db, (request_id, plan_digest, lease_epoch, str(query_index)), batch_index, cards, fence=fence)

    def finish_visual_v2_query(self, request_id, plan_digest, lease_epoch, query_index, query, status, fence, *, no_results=False):
        from .visual_v2_checkpoints import initialize, finish_query, load_query
        if type(query_index) is not int or not 0 <= query_index < 9 or not callable(fence):
            raise ValueError('Invalid query completion')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            receipts=[json.loads(row[0]) for row in db.execute(
                'SELECT body FROM accepted_receipts WHERE request_id=?',(request_id,))]
            if not any(r.get('version') == 3 and r.get('plan_digest') == plan_digest
                       and r.get('lease_epoch') == lease_epoch and r.get('query_index') == query_index
                       and r.get('query') == query for r in receipts) or fence() is not True:
                raise ValueError('Verified receipt required')
            initialize(db)
            key=(request_id,plan_digest,lease_epoch,str(query_index))
            if status == 'home_complete' and not load_query(db,key) and no_results is not True:
                raise ValueError('Empty query requires explicit no-results observation')
            finish_query(db, key, status, fence=fence)

    def record_cards(self, request_id, plan_digest, lease_epoch, query_index, query, cards, fence, *, exhausted=False):
        if not callable(fence) or type(query_index) is not int or not 0<=query_index<6 or not isinstance(cards,list) or type(exhausted) is not bool or not (30<=len(cards)<=50 or (query_index>=3 and exhausted and len(cards)<30)): raise ValueError('Invalid card progress')
        if any(not isinstance(c,dict) or c.get('query')!=query or c.get('rank')!=i+1 or type(c.get('is_ad')) is not bool or not isinstance(c.get('title'),str) or not c['title'] or not isinstance(c.get('url'),str) or not isinstance(c.get('product_id'),str) or not c['product_id'] for i,c in enumerate(cards)): raise ValueError('Invalid card observations')
        body=json.dumps(cards,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        if len(body.encode())>900000: raise ValueError('Card progress too large')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            receipts=[json.loads(row[0]) for row in db.execute('SELECT body FROM accepted_receipts WHERE request_id=?',(request_id,))]
            matching=[r for r in receipts if r.get('plan_digest')==plan_digest and r.get('lease_epoch')==lease_epoch and r.get('query_index')==query_index and r.get('query')==query]
            if not matching or fence() is not True: raise ValueError('Verified receipt required')
            if query_index >= 3 and matching[-1].get('version') != 2: raise ValueError('Staged receipt required')
            if exhausted and (query_index<3 or matching[-1].get('version')!=2): raise ValueError('Staged expansion receipt required')
            from .collector_import import _identity
            platform=matching[-1].get('platform'); seen=set()
            for card in cards:
                identity,_=_identity(card['url'],platform)
                if identity!=card['product_id'] or identity in seen: raise ValueError('Card identity mismatch')
                seen.add(identity)
            old=db.execute('SELECT body FROM query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=?',(request_id,plan_digest,lease_epoch,query_index)).fetchone()
            if old:
                meta=db.execute('SELECT query,exhausted FROM query_card_meta WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=?',(request_id,plan_digest,lease_epoch,query_index)).fetchone()
                if old[0]!=body or bool(meta and meta[1])!=exhausted or (meta and meta[0]!=query): raise ValueError('Card progress differs')
                return
            indices=[r[0] for r in db.execute('SELECT query_index FROM query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index',(request_id,plan_digest,lease_epoch))]
            if indices!=list(range(query_index)): raise ValueError('Card progress out of order')
            db.execute('INSERT INTO query_cards VALUES (?,?,?,?,?)',(request_id,plan_digest,lease_epoch,query_index,body))
            if exhausted:db.execute('INSERT INTO query_card_meta VALUES (?,?,?,?,?,?)',(request_id,plan_digest,lease_epoch,query_index,query,1))
            if query_index >= 2:
                self._restore_checkpoint_details(db, (request_id, plan_digest, lease_epoch))

    def issue(self, request):
        if (not isinstance(request, dict) or set(request) != {'version','request_id','nonce','lease_epoch','plan_digest','platform','query_index','query'}
                or type(request['version']) is not int or request['version'] not in (1,2,3,4,5,6) or not isinstance(request['request_id'],str) or not request['request_id']
                or not isinstance(request['nonce'],str) or not re.fullmatch('[A-Za-z0-9_-]{32,128}',request['nonce'])
                or type(request['lease_epoch']) is not int or request['lease_epoch'] < 1
                or not isinstance(request['plan_digest'],str) or not re.fullmatch('[a-f0-9]{64}',request['plan_digest'])
                or request['platform'] not in ('mercado-libre-mx','shein-us') or (request['version'] in (3,4,5) and request['platform']!='mercado-libre-mx') or (request['version']==6 and request['platform']!='shein-us') or type(request['query_index']) is not int or not 0 <= request['query_index'] < ({1:3,2:6,3:9,4:9,5:9,6:9}[request['version']])
                or not isinstance(request['query'],str) or not request['query'].strip()):
            raise ValueError('Invalid challenge')
        with closing(sqlite3.connect(self.path)) as db, db:
            if request['version'] in (4,5,6):
                from .card_sales_v3_checkpoints import initialize
                initialize(db)
                row = db.execute('SELECT query FROM card_v3_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=?',
                                 (request['request_id'], request['plan_digest'], request['lease_epoch'], request['query_index'])).fetchone()
                if not row or row[0] != request['query']:
                    raise ValueError('Query is not in sealed card plan')
            elif request['version'] == 3:
                from .visual_v2_checkpoints import initialize, read_queries
                initialize(db)
                queries = read_queries(db, (request['request_id'], request['plan_digest'], request['lease_epoch']))
                if request['query_index'] >= len(queries) or queries[request['query_index']] != request['query']:
                    raise ValueError('Query is not in sealed visual plan')
            elif request['query_index'] >= 3:
                from .collector_staged_policy import initialize_expansions, load_expansion
                initialize_expansions(db)
                expansion = load_expansion(db, (request['request_id'], request['plan_digest'], request['lease_epoch']), fence=lambda: True)
                index = request['query_index'] - 3
                if not expansion or index >= len(expansion['queries']) or expansion['queries'][index] != request['query']:
                    raise ValueError('Query is not in sealed expansion')
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
            if changed and (not callable(reconcile) or old['plan_digest'] != identity['plan_digest'] or old['platform'] != identity['platform'] or (request['version'] not in (5,6) and old['session_id'] == identity['session_id']) or reconcile() is not True):
                raise ValueError('Browser binding changed; reconcile before rebind')
            if fence() is not True: raise ValueError('Lease fence failed')
            prior=[json.loads(r[0]) for r in db.execute('SELECT body FROM accepted_receipts WHERE request_id=? ORDER BY id',(request['request_id'],))]
            same=[r for r in prior if all(r[k]==receipt[k] for k in ('session_id','tab_id','window_id','plan_digest','lease_epoch'))]
            if request['version'] in (5,6):
                from .home_collection_policy import next_home_query
                scope=(request['request_id'],request['plan_digest'],request['lease_epoch'])
                pending=next_home_query(db,scope)
                if pending is None:
                    last=db.execute('SELECT max(query_index) FROM card_v3_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope).fetchone()[0]
                    if request['query_index']!=last:raise ValueError('Last completed homepage required')
                elif pending!=(request['query_index'],1):raise ValueError('Current unfinished homepage required')
            elif (request['query_index'] != len(same) or len(same) >= ({1:3,2:6,3:9,4:9}[request['version']])
                    or any(r.get('version') != request['version'] for r in same)):
                raise ValueError('Queries must be observed in order on the same lease and tab')
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

    def verified_queries(self, request_id, plan_digest, queries, lease_epoch, *, staged=False):
        if type(lease_epoch) is not int or lease_epoch<1 or not isinstance(queries,list) or not (3<=len(queries)<=6 if staged else len(queries)==3) or any(not isinstance(q,str) or not q for q in queries): return False
        with closing(sqlite3.connect(self.path)) as db, db:
            receipts=[json.loads(row[0]) for row in db.execute('SELECT body FROM accepted_receipts WHERE request_id=? ORDER BY id',(request_id,))]
        if staged:
            binding=self.current_binding(request_id)
            if not binding:return False
            receipts=[r for r in receipts if r.get('lease_epoch')==lease_epoch and r.get('plan_digest')==plan_digest
                      and all(r.get(k)==binding.get(k) for k in ('session_id','tab_id','window_id'))]
            receipts=receipts[:len(queries)]
        if len(receipts)<len(queries): return False
        last=receipts[-len(queries):]; first=last[0]
        return all(r.get('version') == (2 if staged else 1) and r.get('lease_epoch')==lease_epoch and r.get('plan_digest')==plan_digest and r.get('query_index')==i and r.get('query')==queries[i]
                   and all(r.get(k)==first.get(k) for k in ('session_id','tab_id','window_id','collector_tab_id','lease_epoch')) for i,r in enumerate(last))

    def resume_index(self, request_id, plan_digest, queries, lease_epoch, *, staged=False, visual_v2=False, card_v3=False):
        if sum(bool(x) for x in (staged, visual_v2, card_v3)) > 1: raise ValueError('Conflicting receipt versions')
        if type(lease_epoch) is not int or lease_epoch<1 or not isinstance(queries,list) or not (3<=len(queries)<=9 if visual_v2 or card_v3 else 3<=len(queries)<=6 if staged else len(queries)==3): raise ValueError('Invalid plan')
        binding=self.current_binding(request_id)
        if not binding: return 0
        with closing(sqlite3.connect(self.path)) as db, db:
            receipts=[json.loads(r[0]) for r in db.execute('SELECT body FROM accepted_receipts WHERE request_id=? ORDER BY id',(request_id,))]
        current=[r for r in receipts if r.get('lease_epoch')==lease_epoch and r.get('plan_digest')==plan_digest and all(r.get(k)==binding.get(k) for k in ('session_id','tab_id','window_id'))]
        if len(current)>len(queries) or any(r.get('query_index')!=i or r.get('query')!=queries[i] or r.get('version') != (4 if card_v3 else 3 if visual_v2 else 2 if staged else 1) for i,r in enumerate(current)): raise ValueError('Inconsistent receipt history')
        return len(current)

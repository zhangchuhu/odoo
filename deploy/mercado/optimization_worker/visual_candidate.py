"""Persist candidate photos separately from the browser and commit bound visual decisions."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import secrets
import sqlite3
from urllib.parse import urlsplit

from PIL import Image

from .collector_cli import collector_id
from .collector_import import _DETAIL, _identity, _number, validate_evidence_prefix
from .collector_progress import _candidate_order, CollectorProgressError
from .domain import canonical_hash


def fail(code, message):
    raise CollectorProgressError(code, message)


class VisualCandidate:
    def __init__(self, ledger, key, plan, config, claim):
        self.ledger, self.key, self.plan, self.config, self.claim = ledger, key, plan, config, claim
        self.scope = canonical_hash([claim[k] for k in ('id', 'snapshot_id', 'company_id', 'input_hash')] + [plan])

    def open(self):
        db = sqlite3.connect(self.ledger)
        db.execute("""CREATE TABLE IF NOT EXISTS visual_photos (
            id TEXT PRIMARY KEY, scope TEXT, ordinal INTEGER, photo INTEGER, metadata TEXT,
            download TEXT, sha TEXT, file TEXT, observed_at TEXT, download_id INTEGER, final_url TEXT, UNIQUE(scope, ordinal, photo))""")
        db.execute("""CREATE TABLE IF NOT EXISTS visual_verdicts (
            scope TEXT, ordinal INTEGER, submission TEXT, digest TEXT, receipt TEXT,
            PRIMARY KEY(scope, ordinal), UNIQUE(scope, submission))""")
        return db

    def current(self, db=None):
        if self.plan['platform'] != 'mercado-libre-mx':
            raise ValueError('Mercado Libre visual review required')
        if db is None:
            with closing(self.open()) as existing:
                return self.current(existing)
        if self.plan.get('strategy') in ('card-visual-v3','card-home-v4'):
            rows=[json.loads(row[0]) for row in db.execute('SELECT body FROM card_v3_details WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY ordinal',self.key)]
            eligible=[row for row in rows if row.get('status') in ('threshold_met','supplemental')]
            ordinal=db.execute('SELECT count(*) FROM card_v3_visual_evidence WHERE request_id=? AND plan_digest=? AND lease_epoch=?',self.key).fetchone()[0]
            if ordinal>=len(eligible) or ordinal>=30:raise ValueError('No pending candidate')
            return ordinal,self.plan['platform']+':'+eligible[ordinal]['identity']
        if self.plan.get('strategy') == 'visual-v2':
            from .visual_v2_checkpoints import read_queries
            from .tab_receipt_store import TabReceiptStore
            table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inherited_query_cards'").fetchone()
            inherited=db.execute('SELECT 1 FROM inherited_query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=? LIMIT 1',self.key).fetchone() if table else None
            if inherited:
                queries=read_queries(db,self.key)
                if TabReceiptStore(str(self.ledger)).resume_index(*self.key[:2],queries,self.key[2],visual_v2=True)<len(queries):
                    raise ValueError('Recovered searches require current browser verification')
        _, recurrent, ordinal, finished = _candidate_order(db, self.key, self.plan, self.config)
        if finished or ordinal >= len(recurrent):
            raise ValueError('No pending candidate')
        if self.plan.get('strategy')=='staged-v1':
            from .collector_staged_policy import elapsed_seconds
            from .collector_progress import staged_in_flight
            elapsed=elapsed_seconds(db,self.key)
            if elapsed>=3600 and not staged_in_flight(db,self.key,self.plan['platform']+':'+recurrent[ordinal],elapsed):
                raise ValueError('Collection time budget exhausted')
        return ordinal, self.plan['platform'] + ':' + recurrent[ordinal]

    def native(self, broker, event, store, download_root):
        from .gallery_download import read_slot, supported_url

        start = event['operation'] == 'visual_photo_start'
        base = {'operation','request_id','window_id','tab_id','session_id','native_origin'}
        fields = base | ({'detail','source','variant','photo'} if start else
                         {'receipt','download_id','final_url','size'})
        def live():
            return (broker.watch_only and not broker.gallery_binding and not broker.conflict and
                    not broker.rebind_requested and broker.fence() and broker.anchor_guard_alive() and
                    broker.guard_alive(broker.session))
        if (set(event) != fields or not broker.anchor_origin or event['native_origin'] != broker.anchor_origin or
                (event['request_id'],event['tab_id'],event['window_id'],event['session_id']) !=
                (self.key[0],broker.tab,broker.window,broker.session) or not live()):
            fail('visual_photo_binding_invalid','需要当前候选的有效浏览器绑定')
        with closing(self.open()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            ordinal, identity = self.current(db)
            if start:
                detail, source, variant, photo = (event[k] for k in ('detail','source','variant','photo'))
                if (not isinstance(detail,dict) or set(detail) != _DETAIL or not self.valid_detail(detail,identity)
                        or not supported_url(source,self.plan['platform'])):
                    fail('visual_photo_candidate_invalid','商品身份、销量或图片来源未通过核验')
                if type(photo) is not int or not 1 <= photo <= 100 or not isinstance(variant,str) or not 1 <= len(variant) <= 3500:
                    fail('visual_photo_candidate_invalid','规格或图片位置无效')
                prefix = db.execute('SELECT metadata FROM visual_photos WHERE scope=? AND ordinal=? AND photo=1',
                                    (self.scope,ordinal)).fetchone()
                if (photo != 1 and not prefix) or (prefix and json.loads(prefix[0])['variant'] != variant):
                    fail('visual_photo_variant_invalid','先确认同一规格的首图')
                metadata = json.dumps(dict(detail=detail,source=source,variant=variant,photo=photo),sort_keys=True,ensure_ascii=False)
                old = db.execute('SELECT id,metadata,download,sha FROM visual_photos WHERE scope=? AND ordinal=? AND photo=?',
                                 (self.scope,ordinal,photo)).fetchone()
                if old:
                    receipt, prior, filename, sha = old
                    if prior != metadata:
                        fail('visual_photo_conflict','该位置已有不同的图片凭证')
                else:
                    receipt = secrets.token_hex(16)
                    suffix = Path(urlsplit(source).path).suffix.lower()
                    suffix = suffix if suffix in ('.png','.jpg','.jpeg','.webp') else '.jpg'
                    filename = f'Odoo-Research/{self.key[0]}/{self.key[2]}/{receipt}{suffix}'
                    db.execute('''INSERT INTO visual_photos (id,scope,ordinal,photo,metadata,download,observed_at)
                               VALUES (?,?,?,?,?,?,?)''',
                               (receipt,self.scope,ordinal,photo,metadata,filename,datetime.now(timezone.utc).isoformat()))
                    sha = None
                return dict(ok=True,receipt=receipt,confirmed=bool(sha),source=source,filename=filename,token=receipt)
            receipt = event['receipt']
            row = db.execute('SELECT metadata,download,sha FROM visual_photos WHERE id=? AND scope=? AND ordinal=?',
                             (receipt,self.scope,ordinal)).fetchone()
            if (not row or type(event['download_id']) is not int or event['download_id'] < 0 or
                    type(event['size']) is not int or not supported_url(event['final_url'],self.plan['platform'])):
                fail('visual_photo_download_invalid','原下载回执不可核验')
            if row[2]:
                return dict(ok=True,receipt=receipt,sha256=row[2])
            raw = read_slot(download_root,row[1].split('/'),event['size'])
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ('JPEG','PNG','WEBP') or min(image.size) < 100 or image.width*image.height > 30_000_000:
                    fail('visual_photo_download_invalid','原图格式或尺寸无效')
                image.load()
            file = store._file(raw)
            sha = hashlib.sha256(raw).hexdigest()
            if not live():
                fail('visual_photo_binding_invalid','浏览器绑定已失效')
            db.execute('UPDATE visual_photos SET sha=?,file=?,download_id=?,final_url=? WHERE id=?',
                       (sha,Path(file).name,event['download_id'],event['final_url'],receipt))
            return dict(ok=True,receipt=receipt,sha256=sha)

    def valid_detail(self, detail, identity):
        try:
            product, _ = _identity(detail['detail_url'],self.plan['platform'])
            if self.plan.get('strategy') in ('card-visual-v3','card-home-v4'):
                from .card_sales_v3 import parse_sales, classify_detail
                sold=parse_sales(detail['sold_display']);threshold_ok=classify_detail(sold,self.config['min_sold'],sales_text=detail['sold_display'],include_plus_hundred=self.plan.get('strategy')=='card-home-v4')!='exclude'
            else:
                sold=_number(detail['sold_display']);threshold_ok=sold and sold[0]>=self.config['min_sold']
            return (detail.get('identity') == identity == self.plan['platform'] + ':' + product and
                    detail.get('product_id') == product and isinstance(detail.get('title'),str) and detail['title'].strip() and
                    bool(threshold_ok))
        except (TypeError,KeyError,ValueError):
            return False

    def photo(self, directory, filename, sha):
        from .gallery_download import read_slot
        if not re.fullmatch(r'[0-9a-f]{64}\.(?:jpg|png|webp)',filename) or not filename.startswith(sha+'.'):
            raise ValueError('Invalid photo receipt')
        raw = read_slot(directory,(filename,))
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError('Photo digest changed')
        return raw

    def status(self, store, directory):
        with closing(self.open()) as db:
            ordinal, identity = self.current(db)
            rows = db.execute('SELECT id,metadata,sha,file FROM visual_photos WHERE scope=? AND ordinal=? AND sha IS NOT NULL ORDER BY photo',
                              (self.scope,ordinal)).fetchall()
        images = []
        for receipt, raw, sha, file in rows:
            meta = json.loads(raw)
            if not self.valid_detail(meta['detail'],identity):
                raise ValueError('Stored evidence belongs to another candidate')
            data = self.photo(store.files,file,sha)
            target = Path(directory) / ('candidate-'+file)
            try:
                with target.open('xb') as stream:
                    stream.write(data)
            except FileExistsError:
                if target.is_symlink() or target.read_bytes() != data:
                    raise ValueError('Staged photo changed')
            images.append(dict(receipt=receipt,sha256=sha,path=str(target),source=meta['source'],photo=meta['photo']))
        return dict(identity=identity,ordinal=ordinal,images=images)

    def durable_photo_count(self):
        with closing(self.open()) as db:
            return db.execute('SELECT COUNT(*) FROM visual_photos WHERE scope=? AND sha IS NOT NULL',
                              (self.scope,)).fetchone()[0]

    def reuse_first_photo(self, store, identity, source, variant):
        with closing(self.open()) as db:
            rows = db.execute('''SELECT p.metadata,p.sha,p.file,v.receipt
                FROM visual_photos p JOIN visual_verdicts v ON p.scope=v.scope AND p.ordinal=v.ordinal
                WHERE p.scope=? AND p.photo=1 AND p.sha IS NOT NULL''', (self.scope,)).fetchall()
        for raw, sha, file, receipt in rows:
            meta = json.loads(raw)
            if (json.loads(receipt)['status']=='qualified' and meta['detail']['identity']==identity
                    and meta['source']==source and meta['variant']==variant):
                try:
                    return self.photo(store.files,file,sha)
                except (OSError,ValueError):
                    return None
        return None

    def submit(self, data, store, fence):
        if self.plan.get('strategy') in ('card-visual-v3','card-home-v4'):
            return self.submit_card_v3(data,store,fence)
        if self.plan.get('strategy') == 'visual-v2':
            return self.submit_visual_v2(data,store,fence)
        keys = {'submission_id','receipts','status','reason','category','match_level','features'}
        if (set(data) != keys or not isinstance(data['submission_id'],str) or
                not re.fullmatch(r'[A-Za-z0-9_-]{12,80}',data['submission_id']) or
                not isinstance(data['receipts'],list) or not 1 <= len(data['receipts']) <= 8 or
                any(not isinstance(r,str) or not re.fullmatch(r'[0-9a-f]{32}',r) for r in data['receipts']) or
                len(set(data['receipts'])) != len(data['receipts']) or
                len(json.dumps(data,ensure_ascii=False,allow_nan=False).encode()) > 12000):
            raise ValueError('Invalid visual verdict')
        digest = canonical_hash(data)
        with closing(self.open()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT digest,receipt FROM visual_verdicts WHERE scope=? AND submission=?',
                               (self.scope,data['submission_id'])).fetchone()
            if prior:
                if prior[0] != digest:
                    raise ValueError('Visual decision submission conflict')
                replay = json.loads(prior[1])
                row = db.execute('''SELECT body FROM collector_details
                    WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND detail_index=?''',
                    (*self.key,replay['ordinal'])).fetchone()
                if not row or canonical_hash(json.loads(row[0])) != replay['detail_digest']:
                    raise ValueError('Original decision has not been restored to the current lease')
                return replay
            ordinal, identity = self.current(db)
            if db.execute('SELECT 1 FROM visual_verdicts WHERE scope=? AND ordinal=?',(self.scope,ordinal)).fetchone():
                raise ValueError('Candidate already has a submitted verdict')
            metadata = []
            photo_receipts = []
            for receipt in data['receipts']:
                row = db.execute('SELECT metadata,sha,file FROM visual_photos WHERE id=? AND scope=? AND ordinal=? AND sha IS NOT NULL',
                                 (receipt,self.scope,ordinal)).fetchone()
                if not row:
                    raise ValueError('Unverified or unrelated photo')
                self.photo(store.files,row[2],row[1])
                meta = json.loads(row[0])
                metadata.append(meta)
                photo_receipts.append(dict(receipt=receipt,source=meta['source'],sha256=row[1]))
            detail = metadata[0]['detail'].copy()
            if metadata[0]['photo'] != 1 or not self.valid_detail(detail,identity) or any(
                    m['detail'] != metadata[0]['detail'] or m['variant'] != metadata[0]['variant'] for m in metadata):
                raise ValueError('Visual review requires the verified first photo of the candidate')
            if data['status'] == 'qualified':
                if data['reason'] is not None:
                    raise ValueError('Qualified candidate cannot have a rejection reason')
            elif data['status'] != 'rejected' or data['reason'] not in ('visual_category_mismatch','imagery_ambiguous_or_inaccessible'):
                raise ValueError('Invalid visual outcome')
            detail.update(status=data['status'],reason=data['reason'],visual_category_code=data['category'],
                          match_level=data['match_level'],visual_features=data['features'])
            where = ' WHERE request_id=? AND plan_digest=? AND lease_epoch=?'
            cards = [json.loads(b) for b, in db.execute('SELECT body FROM query_cards'+where+' ORDER BY query_index',self.key)]
            details = [json.loads(b) for b, in db.execute('SELECT body FROM collector_details'+where+' ORDER BY detail_index',self.key)]
            task = collector_id(self.key[0])
            staged=self.plan.get('strategy')=='staged-v1'
            if staged:
                from .collector_progress import _expanded_queries
                from .collector_import import validate_staged_evidence_prefix
                extra=_expanded_queries(db,self.key,cards)
            else: extra=[]
            evidence = dict(task_record_id=task,platform=self.plan['platform'],details=details+[detail],
                            queries=[dict(query=q,observations=c) for q,c in zip(self.plan['queries']+extra,cards)])
            args=(evidence,task,self.plan['platform'],self.plan['queries'])
            if staged:args+=(extra,)
            args+=(self.plan['category'],datetime.now(timezone.utc).isoformat())
            (validate_staged_evidence_prefix if staged else validate_evidence_prefix)(*args,**{k:self.config[k] for k in
                                     ('result_limit','min_sold','min_reviews','min_rating')})
            if len(json.dumps(evidence,ensure_ascii=False).encode()) > 900000:
                raise ValueError('Evidence exceeds stage limit')
            fence()
            receipt = dict(submission_id=data['submission_id'],identity=identity,ordinal=ordinal,status=data['status'],
                           detail_digest=canonical_hash(detail),images=photo_receipts,
                           saved_at=datetime.now(timezone.utc).isoformat())
            db.execute('INSERT INTO collector_details VALUES (?,?,?,?,?)',(*self.key,ordinal,json.dumps(detail,ensure_ascii=False)))
            if staged and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='staged_detail_reservations'").fetchone():
                db.execute('DELETE FROM staged_detail_reservations'+where,self.key)
            db.execute('INSERT INTO visual_verdicts VALUES (?,?,?,?,?)',
                       (self.scope,ordinal,data['submission_id'],digest,json.dumps(receipt)))
            return receipt

    def submit_card_v3(self,data,store,fence):
        from .collector_cli import validate_source
        from .collector_import import disallowed_term
        from .card_sales_v3_pipeline import record_visual_observation
        from .visual_v2_rules import REQUIRED,FLEXIBLE,validate_design_profile
        fields={'submission_id','receipts','candidate_primary','secondary','styles','occasions','features'}
        if (not isinstance(data,dict) or set(data)!=fields or not isinstance(data['submission_id'],str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{12,80}',data['submission_id'])
                or not isinstance(data['receipts'],list) or not 1<=len(data['receipts'])<=8
                or any(not isinstance(token,str) or not re.fullmatch(r'[0-9a-f]{32}',token) for token in data['receipts'])
                or len(set(data['receipts']))!=len(data['receipts']) or not isinstance(data['candidate_primary'],dict)
                or set(data['candidate_primary'])!=set(REQUIRED+FLEXIBLE)
                or any(not isinstance(data[k],list) or len(data[k])>30 or any(not isinstance(v,str) or not v.strip() or len(v)>100 or disallowed_term(v) for v in data[k]) for k in ('secondary','styles','occasions','features'))
                or len(json.dumps(data,ensure_ascii=False,allow_nan=False).encode())>12000):raise ValueError('Invalid card-v3 verdict')
        design=self.plan.get('design_profile');validate_design_profile(design,self.plan['category'])
        validate_source(store.inspect(self.key[0]),self.claim,self.plan)
        digest=canonical_hash(data)
        def live():return fence() is not False
        with closing(self.open()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT digest,receipt FROM visual_verdicts WHERE scope=? AND submission=?',(self.scope,data['submission_id'])).fetchone()
            if prior:
                if prior[0]!=digest or not live():raise ValueError('Visual decision conflict')
                saved=json.loads(prior[1])
                evidence=db.execute('SELECT body FROM card_v3_visual_evidence WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND ordinal=?',(*self.key,saved['ordinal'])).fetchone()
                if not evidence or json.loads(evidence[0]).get('identity')!=saved['identity'].split(':',1)[1]:
                    raise ValueError('Visual decision has not been restored')
                for image in saved['images']:
                    row=db.execute('SELECT sha,file FROM visual_photos WHERE id=? AND scope=? AND ordinal=?',(image['receipt'],self.scope,saved['ordinal'])).fetchone()
                    if not row or row[0]!=image['sha256']:raise ValueError('Visual photo receipt missing')
                    self.photo(store.files,row[1],row[0])
                return saved
            ordinal,identity=self.current(db)
            metadata=[];photo_receipts=[]
            for token in data['receipts']:
                row=db.execute('SELECT metadata,sha,file FROM visual_photos WHERE id=? AND scope=? AND ordinal=? AND sha IS NOT NULL',(token,self.scope,ordinal)).fetchone()
                if not row:raise ValueError('Unverified candidate photo')
                self.photo(store.files,row[2],row[1]);meta=json.loads(row[0]);metadata.append(meta)
                photo_receipts.append(dict(receipt=token,source=meta['source'],sha256=row[1]))
            detail=metadata[0]['detail']
            if metadata[0]['photo']!=1 or not self.valid_detail(detail,identity) or any(m['detail']!=detail or m['variant']!=metadata[0]['variant'] for m in metadata):
                raise ValueError('Visual review requires verified candidate photo')
            saved=[json.loads(r[0]) for r in db.execute('SELECT body FROM card_v3_details WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY ordinal',self.key)]
            eligible=[row for row in saved if row['status']!='exclude']
            if ordinal>=len(eligible) or eligible[ordinal]['identity']!=identity.split(':',1)[1] or eligible[ordinal]['sales_text']!=detail['sold_display']:
                raise ValueError('Candidate detail differs from confirmed observation')
            index=next(i for i,row in enumerate(saved) if row['identity']==eligible[ordinal]['identity'])
            original=db.execute('SELECT body FROM card_v3_detail_observations WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND ordinal=?',(*self.key,index)).fetchone()
            if not original or json.loads(original[0])!=detail:raise ValueError('Candidate page evidence differs')
            source_style=list(dict.fromkeys((design['styles'] or [])+(design['occasions'] or [])))
            candidate_style=list(dict.fromkeys(data['styles']+data['occasions']))
            outcome=record_visual_observation(db,self.key,ordinal,eligible[ordinal]['identity'],design['primary'],data['candidate_primary'],
                design['secondary'],data['secondary'],source_style,candidate_style,fence=live)
            if not live():raise ValueError('Current lease required')
            receipt=dict(submission_id=data['submission_id'],identity=identity,ordinal=ordinal,classification=outcome['level'],
                         status='qualified' if outcome['level'] in ('highly_similar','competitor','broad_competitor') else 'rejected',images=photo_receipts,
                         saved_at=datetime.now(timezone.utc).isoformat())
            db.execute('INSERT INTO visual_verdicts VALUES (?,?,?,?,?)',(self.scope,ordinal,data['submission_id'],digest,json.dumps(receipt)))
            if not live():raise ValueError('Current lease required')
            return receipt

    def submit_visual_v2(self, data, store, fence):
        from .collector_import import disallowed_term
        from .visual_v2_checkpoints import read_queries
        from .visual_v2_outcomes import save_decision
        from .visual_v2_rules import REQUIRED, FLEXIBLE
        keys={'submission_id','receipts','candidate_primary','secondary','styles','occasions','critical_conflict','features'}
        if (not isinstance(data,dict) or set(data)!=keys or not isinstance(data['submission_id'],str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{12,80}',data['submission_id'])
                or not isinstance(data['receipts'],list) or not 1<=len(data['receipts'])<=8
                or any(not isinstance(r,str) or not re.fullmatch(r'[0-9a-f]{32}',r) for r in data['receipts'])
                or len(set(data['receipts']))!=len(data['receipts']) or not isinstance(data['candidate_primary'],dict)
                or set(data['candidate_primary'])!=set(REQUIRED+FLEXIBLE)
                or type(data['critical_conflict']) is not bool or not isinstance(data['features'],list)
                or any(not isinstance(x,str) or not x.strip() or disallowed_term(x) for x in data['features'])
                or len(json.dumps(data,ensure_ascii=False,allow_nan=False).encode())>12000):
            raise ValueError('Invalid visual-v2 verdict')
        digest=canonical_hash(data)
        def live(): return fence() is not False
        with closing(self.open()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT digest,receipt FROM visual_verdicts WHERE scope=? AND submission=?',
                             (self.scope,data['submission_id'])).fetchone()
            if prior:
                if prior[0]!=digest: raise ValueError('Visual decision submission conflict')
                receipt=json.loads(prior[1])
                row=db.execute('SELECT body FROM collector_details WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND detail_index=?',
                               (*self.key,receipt['ordinal'])).fetchone()
                if not row or canonical_hash(json.loads(row[0]))!=receipt['detail_digest']:
                    raise ValueError('Original decision has not been restored')
                if not live():raise ValueError('Current decision fence required')
                return receipt
            ordinal,identity=self.current(db)
            metadata=[];photo_receipts=[]
            for token in data['receipts']:
                row=db.execute('SELECT metadata,sha,file FROM visual_photos WHERE id=? AND scope=? AND ordinal=? AND sha IS NOT NULL',
                               (token,self.scope,ordinal)).fetchone()
                if not row:raise ValueError('Unverified or unrelated photo')
                self.photo(store.files,row[2],row[1]);meta=json.loads(row[0]);metadata.append(meta)
                photo_receipts.append(dict(receipt=token,source=meta['source'],sha256=row[1]))
            detail=metadata[0]['detail'].copy()
            if metadata[0]['photo']!=1 or not self.valid_detail(detail,identity) or any(
                    m['detail']!=metadata[0]['detail'] or m['variant']!=metadata[0]['variant'] for m in metadata):
                raise ValueError('Visual review requires the verified first photo of the candidate')
            design=self.plan['design_profile'];queries=read_queries(db,self.key)
            outcome=save_decision(db,self.key,[str(i) for i in range(len(queries))],identity.split(':',1)[1],
                design['primary'],data['candidate_primary'],design['secondary'],data['secondary'],design['styles'],data['styles'],
                design['occasions'],data['occasions'],fence=live,critical_conflict=data['critical_conflict'])
            qualified=outcome in ('highly_similar','competitor')
            if qualified and not data['features']: raise ValueError('Qualifying evidence requires visual features')
            detail.update(status='qualified' if qualified else 'rejected',reason=None if qualified else
                          'visual_review_required' if outcome=='review' else 'visual_mismatch',
                          visual_category_code=self.plan['category'] if qualified else None,
                          match_level=('高度相似' if outcome=='highly_similar' else '类似竞品') if qualified else None,
                          visual_features=data['features'] if qualified else None)
            if not live():raise ValueError('Current decision fence required')
            receipt=dict(submission_id=data['submission_id'],identity=identity,ordinal=ordinal,status=detail['status'],
                         classification=outcome,detail_digest=canonical_hash(detail),images=photo_receipts,
                         saved_at=datetime.now(timezone.utc).isoformat())
            db.execute('INSERT INTO collector_details VALUES (?,?,?,?,?)',(*self.key,ordinal,json.dumps(detail,ensure_ascii=False)))
            db.execute('INSERT INTO visual_verdicts VALUES (?,?,?,?,?)',(self.scope,ordinal,data['submission_id'],digest,json.dumps(receipt)))
            return receipt

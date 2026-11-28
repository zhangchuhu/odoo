"""Copy authorized durable business observations, never browser receipts."""
import hashlib
import json
import sqlite3
from datetime import datetime,timezone

TABLES=('card_v3_queries','card_v3_batches','card_v3_batch_evidence','card_v3_pages','card_v3_navigation','card_v3_page_evidence','card_v3_queue','card_v3_details','card_v3_detail_observations',
        'card_v3_visual','card_v3_meta','card_v3_visual_source','card_v3_visual_evidence','home_collection_end')


def validate_source_receipts(db,scope,*,verify_photo=None,homepage_only=False,platform='mercado-libre-mx',_lineage=None,_verified=None):
    """Require recent historical query and page proofs before copying business data."""
    if type(homepage_only) is not bool or platform not in ('mercado-libre-mx','shein-us') or (platform=='shein-us' and not homepage_only):raise ValueError('Invalid collection strategy')
    lineage=set() if _lineage is None else set(_lineage)
    verified=set() if _verified is None else _verified
    if scope in lineage or len(lineage)>=32:raise ValueError('Invalid recovery lineage')
    if scope in verified:return True
    lineage.add(scope)
    now=datetime.now(timezone.utc)
    queries=dict(db.execute('SELECT query_index,query FROM card_v3_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope))
    try:
        receipts=[json.loads(row[0]) for row in db.execute('SELECT body FROM accepted_receipts WHERE request_id=?',(scope[0],))]
        proofs=[(nonce,json.loads(body),datetime.fromisoformat(stamp)) for nonce,body,stamp in db.execute('SELECT nonce,body,issued_at FROM card_v3_receipt_challenges WHERE used=1')]
    except (sqlite3.Error,ValueError,TypeError) as exc:raise ValueError('Historical browser evidence missing') from exc
    pages=set(db.execute('SELECT DISTINCT query_index,page FROM card_v3_batches WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope))
    pages.update(db.execute('SELECT query_index,page FROM card_v3_pages WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope))
    if homepage_only:
        if any(page!=1 for _,page in pages):raise ValueError('Homepage recovery cannot include later pages')
        if db.execute("SELECT 1 FROM card_v3_pages WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND status!='home_complete'",scope).fetchone():raise ValueError('Invalid homepage history')
    batches=db.execute('''SELECT e.screenshot_sha,e.screenshot_file FROM card_v3_batches b LEFT JOIN card_v3_batch_evidence e
        USING (request_id,plan_digest,lease_epoch,query_index,page,batch_index)
        WHERE b.request_id=? AND b.plan_digest=? AND b.lease_epoch=?''',scope).fetchall()
    for sha,filename in batches:
        if not sha or not filename or not callable(verify_photo):raise ValueError('Historical card screenshot missing')
        verify_photo(filename,sha)
    for index,page in pages:
        query=queries.get(index)
        version=6 if platform=='shein-us' else 5 if homepage_only else 4
        matching=[r for r in receipts if r.get('version')==version and r.get('platform')==platform and r.get('request_id')==scope[0] and r.get('plan_digest')==scope[1] and r.get('lease_epoch')==scope[2] and r.get('query_index')==index and r.get('query')==query]
        accepted=[]
        for r in matching:
            try:
                stamp=datetime.fromisoformat(r['observed_at'].replace('Z','+00:00'))
                if 0<=(now-stamp).total_seconds()<86400:accepted.append(r)
            except (TypeError,ValueError,KeyError):continue
        def valid(proof,stamp):
            try:age=(now-stamp).total_seconds()
            except TypeError:return False
            return (0<=age<86400 and proof.get('request_id')==scope[0] and proof.get('plan_digest')==scope[1]
                    and proof.get('lease_epoch')==scope[2] and proof.get('query_index')==index and proof.get('page')==page
                    and proof.get('query')==query and any(all(proof.get(k)==r.get(k) for k in ('tab_id','window_id','session_id')) for r in accepted))
        current=[(nonce,proof) for nonce,proof,stamp in proofs if valid(proof,stamp)]
        if not current and homepage_only:
            # Restored checkpoints deliberately have no reusable browser receipts.
            # Follow their authorized lineage and require the copied page to be
            # byte-for-byte identical to the independently verified source.
            try:
                source=db.execute('SELECT source_request,source_digest,source_epoch FROM card_v3_lineage WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope).fetchone()
            except sqlite3.Error:source=None
            if not source or type(source[2]) is not int or not 0<source[2]<scope[2]:
                raise ValueError('Historical page proof missing or stale')
            original_query=db.execute('SELECT query FROM card_v3_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=?',(*source,index)).fetchone()
            if not original_query or original_query[0]!=query:raise ValueError('Recovered page query changed')
            for table in ('card_v3_batches','card_v3_batch_evidence','card_v3_pages','card_v3_page_evidence'):
                columns=[row[1] for row in db.execute('PRAGMA table_info('+table+')')][3:]
                fields=','.join(columns)
                where=' WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? AND page=? ORDER BY rowid'
                if db.execute('SELECT '+fields+' FROM '+table+where,(*scope,index,page)).fetchall()!=db.execute('SELECT '+fields+' FROM '+table+where,(*source,index,page)).fetchall():
                    raise ValueError('Recovered page evidence changed')
            validate_source_receipts(db,tuple(source),verify_photo=verify_photo,homepage_only=True,platform=platform,_lineage=lineage,_verified=verified)
            continue
        if not current:raise ValueError('Historical page proof missing or stale')
        evidence=db.execute('SELECT page_url,receipt_nonce FROM card_v3_page_evidence WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? AND page=?',(*scope,index,page)).fetchone()
        ended=db.execute('SELECT 1 FROM card_v3_pages WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? AND page=?',(*scope,index,page)).fetchone()
        if ended and (not evidence or not any(nonce==evidence[1] and proof.get('page_url')==evidence[0] for nonce,proof in current)):
            raise ValueError('Historical page end proof missing')
    verified.add(scope)
    return True


def restore_scope(db,source,target,source_plan_hash,target_plan_hash,*,authorize,fence):
    if (not isinstance(source,tuple) or not isinstance(target,tuple) or len(source)!=3 or len(target)!=3
            or source==target or type(source[2]) is not int or type(target[2]) is not int or not 0<source[2]<target[2]
            or not source_plan_hash or source_plan_hash!=target_plan_hash or not callable(authorize) or authorize() is not True
            or not callable(fence) or fence() is not True):raise ValueError('Recovery authorization mismatch')
    from .home_collection_policy import initialize_end_reason
    initialize_end_reason(db)
    db.execute('''CREATE TABLE IF NOT EXISTS card_v3_lineage (request_id TEXT,plan_digest TEXT,lease_epoch INTEGER,
        source_request TEXT,source_digest TEXT,source_epoch INTEGER,content_hash TEXT,
        PRIMARY KEY(request_id,plan_digest,lease_epoch))''')
    export={}
    for table in TABLES:
        columns=[r[1] for r in db.execute('PRAGMA table_info('+table+')')]
        if columns[:3]!=['request_id','plan_digest','lease_epoch']:raise ValueError('Recovery schema missing')
        export[table]=db.execute('SELECT * FROM '+table+' WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY rowid',source).fetchall()
    if not export['card_v3_queries']:raise ValueError('Recovery source is empty')
    digest=hashlib.sha256(json.dumps(export,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    prior=db.execute('SELECT source_request,source_digest,source_epoch,content_hash FROM card_v3_lineage WHERE request_id=? AND plan_digest=? AND lease_epoch=?',target).fetchone()
    if prior:
        if prior!=(*source,digest):raise ValueError('Recovery lineage changed')
        return {'restored':True,'requires_browser_reconfirmation':True}
    if (db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='accepted_receipts'").fetchone()
            and db.execute('SELECT 1 FROM accepted_receipts WHERE request_id=? LIMIT 1',(target[0],)).fetchone()):
        raise ValueError('Target browser session already started')
    for table in TABLES:
        if db.execute('SELECT 1 FROM '+table+' WHERE request_id=? AND plan_digest=? AND lease_epoch=? LIMIT 1',target).fetchone():
            raise ValueError('Target has existing progress')
    db.execute('SAVEPOINT card_v3_recovery')
    try:
        for table,rows in export.items():
            for row in rows:
                if fence() is not True:raise ValueError('Active lease required')
                db.execute('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in row)+')',(*target,*row[3:]))
        if fence() is not True:raise ValueError('Active lease required')
        db.execute('INSERT INTO card_v3_lineage VALUES (?,?,?,?,?,?,?)',(*target,*source,digest))
    except Exception:
        db.execute('ROLLBACK TO card_v3_recovery');db.execute('RELEASE card_v3_recovery');raise
    db.execute('RELEASE card_v3_recovery')
    return {'restored':True,'requires_browser_reconfirmation':True}

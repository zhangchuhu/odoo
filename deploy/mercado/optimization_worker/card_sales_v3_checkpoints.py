"""Fenced page observations for card-sales-v3; caller authenticates browser evidence."""
import json
from urllib.parse import urlsplit
from .card_sales_v3 import parse_sales, rank_per_query, resolve_sales


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS card_v3_queries (request_id TEXT,plan_digest TEXT,lease_epoch INTEGER,
        query_index INTEGER,query TEXT NOT NULL,PRIMARY KEY(request_id,plan_digest,lease_epoch,query_index))''')
    db.execute('''CREATE TABLE IF NOT EXISTS card_v3_batches (request_id TEXT,plan_digest TEXT,lease_epoch INTEGER,
        query_index INTEGER,page INTEGER,batch_index INTEGER,body TEXT NOT NULL,
        PRIMARY KEY(request_id,plan_digest,lease_epoch,query_index,page,batch_index))''')
    db.execute('''CREATE TABLE IF NOT EXISTS card_v3_batch_evidence (request_id TEXT,plan_digest TEXT,lease_epoch INTEGER,
        query_index INTEGER,page INTEGER,batch_index INTEGER,screenshot_sha TEXT NOT NULL,screenshot_file TEXT NOT NULL,width INTEGER NOT NULL,height INTEGER NOT NULL,
        PRIMARY KEY(request_id,plan_digest,lease_epoch,query_index,page,batch_index))''')
    db.execute('''CREATE TABLE IF NOT EXISTS card_v3_pages (request_id TEXT,plan_digest TEXT,lease_epoch INTEGER,
        query_index INTEGER,page INTEGER,status TEXT NOT NULL,
        PRIMARY KEY(request_id,plan_digest,lease_epoch,query_index,page))''')
    db.execute('''CREATE TABLE IF NOT EXISTS card_v3_navigation (request_id TEXT,plan_digest TEXT,lease_epoch INTEGER,
        query_index INTEGER,page INTEGER,next_url TEXT NOT NULL,
        PRIMARY KEY(request_id,plan_digest,lease_epoch,query_index,page))''')
    db.execute('''CREATE TABLE IF NOT EXISTS card_v3_page_evidence (request_id TEXT,plan_digest TEXT,lease_epoch INTEGER,
        query_index INTEGER,page INTEGER,page_url TEXT NOT NULL,receipt_nonce TEXT NOT NULL,end_evidence TEXT NOT NULL,end_checks INTEGER NOT NULL,
        PRIMARY KEY(request_id,plan_digest,lease_epoch,query_index,page))''')


def _fence(fence):
    if not callable(fence) or fence() is not True:raise ValueError('Active lease required')


def _frozen(db,scope):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='card_v3_meta'").fetchone():return False
    return bool(db.execute('SELECT 1 FROM card_v3_meta WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope).fetchone())


def seal_query(db,scope,index,query,*,fence):
    _fence(fence)
    if len(scope)!=3 or type(index) is not int or not 0<=index<9 or not isinstance(query,str) or not query.strip():
        raise ValueError('Invalid search query')
    rows=db.execute('SELECT query_index,query FROM card_v3_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index',scope).fetchall()
    if index<len(rows):
        if rows[index]!=(index,query):raise ValueError('Confirmed query cannot change')
        return
    if _frozen(db,scope):raise ValueError('Candidate plan already frozen')
    if index!=len(rows) or any(q.casefold().strip()==query.casefold().strip() for _,q in rows):raise ValueError('Invalid query sequence')
    _fence(fence);db.execute('INSERT INTO card_v3_queries VALUES (?,?,?,?,?)',(*scope,index,query))


def _page_ready(db,scope,index,page):
    if type(page) is not int or not 1<=page<=3:raise ValueError('Invalid page')
    q=db.execute('SELECT query FROM card_v3_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=?',(*scope,index)).fetchone()
    if not q:raise ValueError('Unsealed query')
    if page>1:
        row=db.execute('SELECT status FROM card_v3_pages WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? AND page=?',(*scope,index,page-1)).fetchone()
        if row!=('has_next',):raise ValueError('Previous page not confirmed')
    return q[0]


def append_page_batch(db,scope,index,page,batch_index,cards,*,fence,platform='mercado-libre-mx'):
    from .collector_import import _identity
    _fence(fence);query=_page_ready(db,scope,index,page)
    if platform not in ('mercado-libre-mx','shein-us'):raise ValueError('Invalid platform')
    if platform=='shein-us' and page!=1:raise ValueError('SHEIN homepage required')
    if type(batch_index) is not int or batch_index<0 or not isinstance(cards,list) or not cards:raise ValueError('Invalid card batch')
    if any(not isinstance(c,dict) or c.get('query')!=query or not isinstance(c.get('product_id'),str) or not c['product_id']
           or type(c.get('rank')) is not int or c['rank']<1 for c in cards):
        raise ValueError('Invalid card')
    for card in cards:
        try:pid,_=_identity(card.get('url'),platform)
        except (TypeError,ValueError,AttributeError) as exc:raise ValueError('Invalid card URL') from exc
        if pid!=card['product_id']:raise ValueError('Card identity mismatch')
    key=(*scope,index,page)
    rows=db.execute('SELECT batch_index,body FROM card_v3_batches WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? AND page=? ORDER BY batch_index',key).fetchall()
    body=json.dumps(cards,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    if batch_index<len(rows):
        if rows[batch_index]!=(batch_index,body):raise ValueError('Confirmed batch cannot change')
        return
    if _frozen(db,scope):raise ValueError('Candidate plan already frozen')
    if batch_index!=len(rows) or db.execute('SELECT 1 FROM card_v3_pages WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? AND page=?',key).fetchone():raise ValueError('Invalid page progress')
    _fence(fence);db.execute('INSERT INTO card_v3_batches VALUES (?,?,?,?,?,?,?)',(*key,batch_index,body))


def finish_page(db,scope,index,page,status,*,fence,next_url=None):
    _fence(fence);_page_ready(db,scope,index,page)
    if status not in ('has_next','end_of_results','page_limit') or (status=='page_limit' and page!=3) or (status=='has_next' and page==3):raise ValueError('Invalid page end')
    if status=='has_next':
        try:
            url=urlsplit(next_url)
            if (url.scheme!='https' or url.hostname!='listado.mercadolibre.com.mx' or url.port or url.username or url.password):raise ValueError('Invalid next page')
        except (TypeError,AttributeError) as exc:raise ValueError('Invalid next page') from exc
    elif next_url is not None:raise ValueError('Unexpected next page')
    key=(*scope,index,page);prior=db.execute('SELECT status FROM card_v3_pages WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? AND page=?',key).fetchone()
    if prior:
        if prior!=(status,):raise ValueError('Confirmed page cannot change')
        nav=db.execute('SELECT next_url FROM card_v3_navigation WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? AND page=?',key).fetchone()
        if (nav[0] if nav else None)!=next_url:raise ValueError('Confirmed next page cannot change')
        return
    if _frozen(db,scope):raise ValueError('Candidate plan already frozen')
    db.execute('SAVEPOINT card_v3_page_finish')
    try:
        _fence(fence);db.execute('INSERT INTO card_v3_pages VALUES (?,?,?,?,?,?)',(*key,status))
        if next_url:db.execute('INSERT INTO card_v3_navigation VALUES (?,?,?,?,?,?)',(*key,next_url))
        _fence(fence)
    except Exception:
        db.execute('ROLLBACK TO card_v3_page_finish');db.execute('RELEASE card_v3_page_finish');raise
    db.execute('RELEASE card_v3_page_finish')


def _observations(db,scope,index=None):
    sql='SELECT query_index,body FROM card_v3_batches WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY rowid'
    sequence=0
    for query_index,body in db.execute(sql,scope):
        for card in json.loads(body):
            sequence+=1
            if index is not None and query_index!=index:continue
            claimed=card.get('sales_status')
            value=parse_sales(card.get('sales_text')) if claimed not in ('ambiguous','missing','conflict') else None
            status='known' if value is not None else claimed if claimed in ('ambiguous','conflict') else 'missing'
            yield dict(card,first_seen=sequence,observed_order=sequence,sales_status=status,sales_lower_bound=value)


def query_candidates(db,scope,index):
    rows=db.execute('SELECT page,status FROM card_v3_pages WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? ORDER BY page',(*scope,index)).fetchall()
    if not rows or (rows[-1][0]<3 and rows[-1][1]!='end_of_results'):raise ValueError('Search pages incomplete')
    all_cards=list(_observations(db,scope)); grouped={}
    for card in all_cards:grouped.setdefault(card['product_id'],[]).append(card.get('sales_text') if card['sales_status']=='known' else None)
    conflicts={pid for pid,labels in grouped.items() if resolve_sales(labels)['status']=='conflict'}
    return rank_per_query([c for c in _observations(db,scope,index) if c['product_id'] not in conflicts])


def known_count(db,scope,*,include_plus_hundred=False):
    grouped={}
    for card in _observations(db,scope):grouped.setdefault(card['product_id'],[]).append(card.get('sales_text') if card['sales_status']=='known' else None)
    return sum(resolve_sales(labels,include_plus_hundred=include_plus_hundred)['status']=='known' for labels in grouped.values())


def next_page(db,scope):
    """Resume sealed queries in page-major order, including unfinished pages."""
    queries=db.execute('SELECT query_index FROM card_v3_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index',scope).fetchall()
    indices=[row[0] for row in queries]
    if len(indices)<3 or indices!=list(range(len(indices))) or len(indices)>9:
        raise ValueError('Invalid sealed query plan')
    pages=db.execute('SELECT query_index,page,status FROM card_v3_pages WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope).fetchall()
    confirmed={(index,page):status for index,page,status in pages}
    for index,page,status in pages:
        if index not in indices or page not in (1,2,3) or status not in ('has_next','end_of_results','page_limit'):
            raise ValueError('Invalid page history')
        if (page==3 and status=='has_next') or (page<3 and status=='page_limit'):
            raise ValueError('Invalid page history')
        if page>1 and confirmed.get((index,page-1))!='has_next':
            raise ValueError('Nonconsecutive page history')
    for page in (1,2,3):
        for index in indices:
            if (index,page) in confirmed:continue
            if page==1 or confirmed.get((index,page-1))=='has_next':return index,page
    return None

"""Scheduling policy for a versioned homepage-only collection."""


def initialize_end_reason(db):
    db.execute('''CREATE TABLE IF NOT EXISTS home_collection_end (
        request_id TEXT NOT NULL,plan_digest TEXT NOT NULL,lease_epoch INTEGER NOT NULL,
        reason TEXT NOT NULL,PRIMARY KEY(request_id,plan_digest,lease_epoch))''')


def record_end_reason(db,scope,reason,*,fence):
    if reason not in ('sales_target_reached','query_limit','terms_exhausted') or not callable(fence) or fence() is not True:
        raise ValueError('Invalid homepage completion reason')
    initialize_end_reason(db)
    row=db.execute('SELECT reason FROM home_collection_end WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope).fetchone()
    if row:
        if row[0]!=reason:raise ValueError('Homepage completion reason changed')
        return
    db.execute('INSERT INTO home_collection_end VALUES (?,?,?,?)',(*scope,reason))
    if fence() is not True:raise ValueError('Active lease required')


def end_reason(db,scope):
    initialize_end_reason(db)
    row=db.execute('SELECT reason FROM home_collection_end WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope).fetchone()
    return row[0] if row else None


def next_home_query(db,scope):
    if not isinstance(scope,tuple) or len(scope)!=3:raise ValueError('Invalid collection scope')
    pages=db.execute('SELECT query_index,page,status FROM card_v3_pages WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope).fetchall()
    if any(page!=1 or status!='home_complete' for _,page,status in pages):
        raise ValueError('Invalid homepage completion')
    queries=db.execute('SELECT query_index FROM card_v3_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index',scope).fetchall()
    if [row[0] for row in queries]!=list(range(len(queries))):raise ValueError('Invalid query sequence')
    if any(index not in range(len(queries)) for index,_,_ in pages):raise ValueError('Unknown homepage query')
    done={index for index,_,_ in pages}
    for index, in queries:
        if index not in done:return (index,1)
    return None


def home_query_candidates(db,scope,index):
    from .card_sales_v3_checkpoints import _observations
    from .card_sales_v3 import rank_per_query, resolve_sales
    row=db.execute('SELECT status FROM card_v3_pages WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? AND page=1',(*scope,index)).fetchone()
    if not row or row[0]!='home_complete':
        raise ValueError('Homepage incomplete')
    if db.execute('SELECT 1 FROM card_v3_pages WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND page<>1',scope).fetchone():
        raise ValueError('Invalid later page')
    all_cards=list(_observations(db,scope)); grouped={}
    for card in all_cards:grouped.setdefault(card['product_id'],[]).append(card.get('sales_text') if card['sales_status']=='known' else None)
    conflicts={pid for pid,labels in grouped.items() if resolve_sales(labels,include_plus_hundred=True)['status']=='conflict'}
    return rank_per_query([c for c in _observations(db,scope,index) if c['product_id'] not in conflicts],include_plus_hundred=True)


def next_home_action(completed, known_products, terms_exhausted):
    if (not isinstance(completed,list) or not 3<=len(completed)<=9
            or any(type(x) is not bool for x in completed)
            or type(known_products) is not int or known_products<0
            or type(terms_exhausted) is not bool):
        raise ValueError('Invalid homepage collection state')
    for index,done in enumerate(completed):
        if not done:return {'action':'collect','query_index':index,'page':1}
    if known_products>=100:return {'action':'freeze','reason':'sales_target_reached'}
    if len(completed)>=9:return {'action':'freeze','reason':'query_limit'}
    if terms_exhausted:return {'action':'freeze','reason':'terms_exhausted'}
    return {'action':'expand','remaining':9-len(completed)}

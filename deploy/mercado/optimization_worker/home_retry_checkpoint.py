"""Fenced retry budget for a specific task lease and homepage."""

def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS home_retry_state (
        request_id TEXT NOT NULL,plan_digest TEXT NOT NULL,lease_epoch INTEGER NOT NULL,
        query_index INTEGER NOT NULL,attempts INTEGER NOT NULL,
        PRIMARY KEY(request_id,plan_digest,lease_epoch,query_index))''')


def _check(scope,index,fence):
    if (not isinstance(scope,tuple) or len(scope)!=3 or any(not isinstance(x,str) or not x for x in scope[:2])
            or type(scope[2]) is not int or scope[2]<1 or type(index) is not int or not 0<=index<9
            or not callable(fence) or fence() is not True):raise ValueError('Current retry scope required')


def reserve_retry(db,scope,index,*,fence):
    _check(scope,index,fence)
    row=db.execute('SELECT attempts FROM home_retry_state WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=?',(*scope,index)).fetchone()
    attempts=row[0] if row else 0
    if attempts>=3:raise ValueError('browser_unavailable')
    if fence() is not True:raise ValueError('Current retry scope required')
    db.execute('INSERT INTO home_retry_state VALUES (?,?,?,?,?) ON CONFLICT(request_id,plan_digest,lease_epoch,query_index) DO UPDATE SET attempts=excluded.attempts',(*scope,index,attempts+1))
    if fence() is not True:raise ValueError('Current retry scope required')
    return (2,5,10)[attempts]


def clear_retry(db,scope,index,*,fence):
    _check(scope,index,fence)
    db.execute('DELETE FROM home_retry_state WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=?',(*scope,index))
    if fence() is not True:raise ValueError('Current retry scope required')

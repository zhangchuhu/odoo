"""Task-scoped, non-secret evidence that a verification page became readable."""
import json
import re
import time

from .home_resume_policy import resume_page_matches


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS home_verification_watch (
        request_id TEXT NOT NULL,company_id INTEGER NOT NULL,task_id INTEGER NOT NULL,
        snapshot_id INTEGER NOT NULL,lease_epoch INTEGER NOT NULL,stage TEXT NOT NULL,
        expected TEXT NOT NULL,binding TEXT NOT NULL,created REAL NOT NULL,status TEXT NOT NULL,verified_at REAL,
        PRIMARY KEY(request_id,company_id,task_id,snapshot_id,lease_epoch))''')
    if 'verified_at' not in {row[1] for row in db.execute('PRAGMA table_info(home_verification_watch)')}:
        db.execute('ALTER TABLE home_verification_watch ADD COLUMN verified_at REAL')


def _key(request,task):
    if (not isinstance(request,str) or not request or not isinstance(task,tuple) or len(task)!=4
            or any(type(x) is not int or x<1 for x in task)):raise ValueError('Invalid verification scope')
    return (request,*task)


def _binding(value):
    if (not isinstance(value,dict) or set(value)!={'tab_id','window_id','session_id'}
            or any(type(value[k]) is not int or value[k]<1 for k in ('tab_id','window_id'))
            or not isinstance(value['session_id'],str) or not re.fullmatch('[a-f0-9]{32}',value['session_id'])):
        raise ValueError('Invalid browser identity')
    return json.dumps(value,sort_keys=True,separators=(',',':'))


def arm(db,request,task,stage,expected,binding,*,fence):
    key=_key(request,task);ident=_binding(binding)
    if (not callable(fence) or fence() is not True or stage not in ('search','detail')
            or not isinstance(expected,dict) or set(expected)!=( {'query'} if stage=='search' else {'product_id'})
            or not isinstance(next(iter(expected.values())),str) or not next(iter(expected.values())).strip()):
        raise ValueError('Invalid verification watch')
    body=json.dumps(expected,sort_keys=True,separators=(',',':'))
    prior=db.execute('SELECT stage,expected,binding FROM home_verification_watch WHERE request_id=? AND company_id=? AND task_id=? AND snapshot_id=? AND lease_epoch=?',key).fetchone()
    if prior:
        if prior!=(stage,body,ident):raise ValueError('Verification watch changed')
        return
    if fence() is not True:raise ValueError('Active lease required')
    db.execute('INSERT INTO home_verification_watch (request_id,company_id,task_id,snapshot_id,lease_epoch,stage,expected,binding,created,status) VALUES (?,?,?,?,?,?,?,?,?,?)',(*key,stage,body,ident,time.time(),'armed'))


def observe(db,request,task,binding,observed,*,authorize):
    key=_key(request,task);ident=_binding(binding)
    if not callable(authorize) or authorize() is not True:raise ValueError('Waiting task required')
    row=db.execute('SELECT stage,expected,binding,created,status FROM home_verification_watch WHERE request_id=? AND company_id=? AND task_id=? AND snapshot_id=? AND lease_epoch=?',key).fetchone()
    if (not row or row[2]!=ident or row[4] not in ('armed','ready') or not 0<=time.time()-row[3]<86400
            or not resume_page_matches(row[0],json.loads(row[1]),observed)):
        raise ValueError('Verification page identity mismatch')
    if authorize() is not True:raise ValueError('Waiting task required')
    db.execute("UPDATE home_verification_watch SET status='ready',verified_at=? WHERE request_id=? AND company_id=? AND task_id=? AND snapshot_id=? AND lease_epoch=?",(time.time(),*key))
    return True


def ready(db,request,task):
    row=db.execute("SELECT stage,created,verified_at FROM home_verification_watch WHERE request_id=? AND company_id=? AND task_id=? AND snapshot_id=? AND lease_epoch=? AND status='ready'",_key(request,task)).fetchone()
    return {'stage':row[0]} if row and row[2] is not None and 0<=time.time()-row[1]<86400 and 0<=time.time()-row[2]<600 else None


def consume(db,request,task):
    db.execute("UPDATE home_verification_watch SET status='consumed' WHERE request_id=? AND company_id=? AND task_id=? AND snapshot_id=? AND lease_epoch=? AND status='ready'",_key(request,task))


def armed_info(db,request,binding):
    ident=_binding(binding)
    rows=db.execute('''SELECT company_id,task_id,snapshot_id,lease_epoch,stage,expected,created,status,binding
        FROM home_verification_watch WHERE request_id=? AND status IN ('armed','ready')''',(request,)).fetchall()
    rows=[row for row in rows if row[8]==ident and 0<=time.time()-row[6]<86400]
    if len(rows)!=1:raise ValueError('Current verification watch required')
    row=rows[0]
    return {'task':tuple(row[:4]),'stage':row[4],'expected':json.loads(row[5]),'status':row[7]}

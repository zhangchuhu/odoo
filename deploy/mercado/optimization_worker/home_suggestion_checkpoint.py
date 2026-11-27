"""Lease-fenced storage for observed autocomplete suggestions."""
import json
import re
from datetime import datetime


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS home_suggestion_observations (
        request_id TEXT NOT NULL, plan_digest TEXT NOT NULL, lease_epoch INTEGER NOT NULL,
        prefix TEXT NOT NULL, position INTEGER NOT NULL, body TEXT NOT NULL,
        PRIMARY KEY(request_id,plan_digest,lease_epoch,prefix,position))''')
    db.execute('''CREATE TABLE IF NOT EXISTS home_suggestion_reads (
        request_id TEXT NOT NULL, plan_digest TEXT NOT NULL, lease_epoch INTEGER NOT NULL,
        prefix TEXT NOT NULL, body TEXT NOT NULL,
        PRIMARY KEY(request_id,plan_digest,lease_epoch,prefix))''')


def _validate_scope(scope):
    if (not isinstance(scope,tuple) or len(scope)!=3 or any(not isinstance(v,str) or not v for v in scope[:2])
            or type(scope[2]) is not int or scope[2]<1):raise ValueError('Invalid suggestion scope')


def save_observation(db,scope,row,*,fence,verify_evidence):
    _validate_scope(scope)
    if not callable(fence) or fence() is not True:raise ValueError('Active lease required')
    if (not isinstance(row,dict) or set(row)!={'prefix','text','position','observed_at','evidence_sha256'}
            or any(not isinstance(row[k],str) or not 1<=len(row[k].strip())<=120 for k in ('prefix','text'))
            or type(row['position']) is not int or not 1<=row['position']<=100
            or not isinstance(row['observed_at'],str)
            or not isinstance(row['evidence_sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',row['evidence_sha256'])):
        raise ValueError('Invalid suggestion observation')
    if not callable(verify_evidence) or verify_evidence(row['evidence_sha256']) is not True:
        raise ValueError('Verified suggestion evidence required')
    try: stamp=datetime.fromisoformat(row['observed_at'].replace('Z','+00:00'))
    except ValueError as exc:raise ValueError('Invalid observation time') from exc
    if stamp.utcoffset() is None:raise ValueError('Observation timezone required')
    body=json.dumps(row,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    key=(*scope,row['prefix'],row['position'])
    prior=db.execute('SELECT body FROM home_suggestion_observations WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND prefix=? AND position=?',key).fetchone()
    if prior:
        if prior[0]!=body:raise ValueError('Suggestion observation conflict')
        return
    if fence() is not True:raise ValueError('Active lease required')
    db.execute('INSERT INTO home_suggestion_observations VALUES (?,?,?,?,?,?)',(*key,body))


def observations(db,scope):
    _validate_scope(scope)
    rows=db.execute('SELECT body FROM home_suggestion_observations WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY rowid',scope)
    return [json.loads(row[0]) for row in rows]


def save_read(db,scope,row,*,fence,verify_evidence):
    _validate_scope(scope)
    if not callable(fence) or fence() is not True:raise ValueError('Active lease required')
    if (not isinstance(row,dict) or set(row)!={'prefix','state','observed_at','evidence_sha256'}
            or not isinstance(row['prefix'],str) or not 1<=len(row['prefix'].strip())<=120
            or row['state'] not in ('observed','unavailable','blocked','unverified')
            or not isinstance(row['evidence_sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',row['evidence_sha256'])
            or not isinstance(row['observed_at'],str)):
        raise ValueError('Invalid suggestion read')
    try:stamp=datetime.fromisoformat(row['observed_at'].replace('Z','+00:00'))
    except ValueError as exc:raise ValueError('Invalid observation time') from exc
    if stamp.utcoffset() is None or not callable(verify_evidence) or verify_evidence(row['evidence_sha256']) is not True:
        raise ValueError('Verified suggestion evidence required')
    body=json.dumps(row,sort_keys=True,ensure_ascii=False,separators=(',',':'));key=(*scope,row['prefix'])
    prior=db.execute('SELECT body FROM home_suggestion_reads WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND prefix=?',key).fetchone()
    if prior:
        if prior[0]!=body:raise ValueError('Suggestion read conflict')
        return
    if fence() is not True:raise ValueError('Active lease required')
    db.execute('INSERT INTO home_suggestion_reads VALUES (?,?,?,?,?)',(*key,body))


def reads(db,scope):
    _validate_scope(scope)
    return [json.loads(row[0]) for row in db.execute('SELECT body FROM home_suggestion_reads WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY rowid',scope)]

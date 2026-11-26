"""Durable visual decisions for already observed candidates.

Caller must separately verify source and candidate photo receipts and own the
transaction; this module cannot attest the accuracy of visual observations.
"""
import json

from .visual_v2_checkpoints import candidate_pool
from .visual_v2_rules import classify_visual, compare_primary, validate_source_profile, _value


def _initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS visual_v2_decisions (
        request_id TEXT NOT NULL, plan_digest TEXT NOT NULL, lease_epoch INTEGER NOT NULL,
        identity TEXT NOT NULL, body TEXT NOT NULL,
        PRIMARY KEY(request_id,plan_digest,lease_epoch,identity))''')


def decisions(db, scope):
    _initialize(db)
    return [json.loads(row[0]) for row in db.execute('''SELECT body FROM visual_v2_decisions
        WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY rowid''',scope)]


def save_decision(db, scope, query_ids, identity, source, candidate, source_features, candidate_features,
                  source_styles, candidate_styles, source_occasions, candidate_occasions, *, fence, critical_conflict=False):
    if not callable(fence) or fence() is not True or type(critical_conflict) is not bool:
        raise ValueError('Current decision fence required')
    validate_source_profile(source, _value(source.get('category')))
    if not isinstance(identity,str) or identity not in {item['product_id'] for item in candidate_pool(db,scope,query_ids)}:
        raise ValueError('Candidate was not observed')
    fields=(source_features,candidate_features,source_styles,candidate_styles,source_occasions,candidate_occasions)
    if any(value is not None and (not isinstance(value,(set,list,tuple)) or any(not isinstance(v,str) or not v.strip() for v in value)) for value in fields):
        raise ValueError('Invalid visual labels')
    labels=[None if value is None else sorted(set(value)) for value in fields]
    primary=compare_primary(source,candidate)
    outcome=classify_visual(source,candidate,*labels,critical_conflict=critical_conflict)
    value=dict(identity=identity,source=source,candidate=candidate,labels=labels,primary=primary,
               critical_conflict=critical_conflict,outcome=outcome)
    body=json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    _initialize(db)
    row=db.execute('''SELECT body FROM visual_v2_decisions WHERE request_id=? AND plan_digest=?
                       AND lease_epoch=? AND identity=?''',(*scope,identity)).fetchone()
    if row:
        if row[0]!=body:raise ValueError('Confirmed decision cannot be replaced')
        return outcome
    if fence() is not True:raise ValueError('Current decision fence required')
    db.execute('INSERT INTO visual_v2_decisions VALUES (?,?,?,?,?)',(*scope,identity,body))
    return outcome

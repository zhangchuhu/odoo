"""Append-only visual-v2 query batches, scoped to one authenticated lease.

The caller must validate native browser receipts and hold the transaction and
lease fence before writing. This module does not attest platform observations.
"""

import json


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS visual_v2_batches (
        request_id TEXT NOT NULL, plan_digest TEXT NOT NULL, lease_epoch INTEGER NOT NULL,
        query_id TEXT NOT NULL, batch_index INTEGER NOT NULL, body TEXT NOT NULL,
        PRIMARY KEY(request_id,plan_digest,lease_epoch,query_id,batch_index))''')
    db.execute('''CREATE TABLE IF NOT EXISTS visual_v2_queries (
        request_id TEXT NOT NULL, plan_digest TEXT NOT NULL, lease_epoch INTEGER NOT NULL,
        query_index INTEGER NOT NULL, query TEXT NOT NULL,
        PRIMARY KEY(request_id,plan_digest,lease_epoch,query_index))''')
    db.execute('''CREATE TABLE IF NOT EXISTS visual_v2_query_status (
        request_id TEXT NOT NULL, plan_digest TEXT NOT NULL, lease_epoch INTEGER NOT NULL,
        query_id TEXT NOT NULL, status TEXT NOT NULL,
        PRIMARY KEY(request_id,plan_digest,lease_epoch,query_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS visual_v2_plan_status (
        request_id TEXT NOT NULL, plan_digest TEXT NOT NULL, lease_epoch INTEGER NOT NULL,
        PRIMARY KEY(request_id,plan_digest,lease_epoch))''')


def plan_finished(db, scope):
    return bool(db.execute('SELECT 1 FROM visual_v2_plan_status WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope).fetchone())


def finish_plan(db, scope, *, fence):
    if not callable(fence) or fence() is not True or len(read_queries(db,scope))<3:
        raise ValueError('Valid current query plan required')
    db.execute('INSERT OR IGNORE INTO visual_v2_plan_status VALUES (?,?,?)',scope)


def query_status(db, key):
    row = db.execute('''SELECT status FROM visual_v2_query_status WHERE request_id=? AND plan_digest=?
                        AND lease_epoch=? AND query_id=?''', key).fetchone()
    return row[0] if row else None


def finish_query(db, key, status, *, fence, max_cards=100):
    if status not in ('home_complete', 'card_limit_reached') or not callable(fence) or fence() is not True:
        raise ValueError('Invalid query completion')
    previous = query_status(db, key)
    if previous:
        if previous != status:
            raise ValueError('Query completion cannot change')
        return
    if status == 'card_limit_reached' and len(load_query(db, key)) != max_cards:
        raise ValueError('Card limit not reached')
    if fence() is not True:
        raise ValueError('Collection lease fence failed')
    db.execute('INSERT INTO visual_v2_query_status VALUES (?,?,?,?,?)', (*key, status))


def read_queries(db, scope):
    return [row[0] for row in db.execute('''SELECT query FROM visual_v2_queries WHERE request_id=?
        AND plan_digest=? AND lease_epoch=? ORDER BY query_index''', scope)]


def seal_queries(db, scope, queries, *, fence):
    from .visual_v2_policy import validate_queries
    validate_queries(queries)
    if (not isinstance(scope, tuple) or len(scope) != 3 or not scope[0] or not scope[1]
            or type(scope[2]) is not int or scope[2] < 1 or not callable(fence) or fence() is not True):
        raise ValueError('Invalid query scope')
    current = read_queries(db, scope)
    if queries[:len(current)] != current:
        raise ValueError('Previously sealed queries cannot change')
    if len(queries)>len(current) and plan_finished(db,scope):
        raise ValueError('Finished query plan cannot be expanded')
    if fence() is not True:
        raise ValueError('Collection lease fence failed')
    db.executemany('INSERT INTO visual_v2_queries VALUES (?,?,?,?,?)',
                   [(*scope, index, query) for index, query in enumerate(queries) if index >= len(current)])


def load_query(db, key):
    rows = db.execute('''SELECT body FROM visual_v2_batches WHERE request_id=? AND plan_digest=?
                         AND lease_epoch=? AND query_id=? ORDER BY batch_index''', key)
    seen, result = set(), []
    for (body,) in rows:
        for card in json.loads(body):
            identity = card['product_id']
            if identity not in seen:
                seen.add(identity)
                result.append(card)
    return result


def candidate_pool(db, scope, query_ids):
    if (not isinstance(scope, tuple) or len(scope) != 3 or not isinstance(query_ids, list)
            or len(query_ids) > 9 or len(set(query_ids)) != len(query_ids)
            or any(not isinstance(q, str) or not q for q in query_ids)):
        raise ValueError('Invalid candidate scope')
    candidates = {}
    for query_id in query_ids:
        for card in load_query(db, (*scope, query_id)):
            identity = card['product_id']
            if identity not in candidates:
                candidates[identity] = {'product_id': identity, 'card': card, 'queries': []}
            candidates[identity]['queries'].append(query_id)
    return list(candidates.values())


def append_batch(db, key, index, cards, *, fence, max_cards=100):
    if (not isinstance(key, tuple) or len(key) != 4 or not key[0] or not key[1]
            or type(key[2]) is not int or key[2] < 1 or not key[3]
            or type(index) is not int or index < 0 or not isinstance(cards, list)
            or not cards or not callable(fence) or type(max_cards) is not int or max_cards < 1
            or any(not isinstance(c, dict) or not isinstance(c.get('product_id'), str)
                   or not c['product_id'] for c in cards)):
        raise ValueError('Invalid query batch')
    if fence() is not True:
        raise ValueError('Collection lease fence failed')
    body = json.dumps(cards, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    rows = db.execute('''SELECT batch_index,body FROM visual_v2_batches WHERE request_id=? AND plan_digest=?
                         AND lease_epoch=? AND query_id=? ORDER BY batch_index''', key).fetchall()
    if index < len(rows):
        if rows[index] != (index, body):
            raise ValueError('Confirmed batch cannot be replaced')
        return
    if query_status(db, key):
        raise ValueError('Completed query cannot be extended')
    if index != len(rows):
        raise ValueError('Query batches must be consecutive')
    ids = {c['product_id'] for c in load_query(db, key)} | {c['product_id'] for c in cards}
    if len(ids) > max_cards:
        raise ValueError('Query card limit exceeded')
    if fence() is not True:
        raise ValueError('Collection lease fence failed')
    db.execute('INSERT INTO visual_v2_batches VALUES (?,?,?,?,?,?)', (*key, index, body))

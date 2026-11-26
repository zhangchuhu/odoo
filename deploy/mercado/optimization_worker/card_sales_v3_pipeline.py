"""Durable selection pipeline; caller authenticates the page and image evidence."""
import json
from .card_sales_v3 import merge_candidates, parse_sales, classify_detail, rank_visual
from .card_sales_v3_checkpoints import known_count,query_candidates


def initialize_pipeline(db):
    db.execute('''CREATE TABLE IF NOT EXISTS card_v3_queue (request_id TEXT,plan_digest TEXT,lease_epoch INTEGER,
      ordinal INTEGER,product_id TEXT,body TEXT,PRIMARY KEY(request_id,plan_digest,lease_epoch,ordinal))''')
    db.execute('''CREATE TABLE IF NOT EXISTS card_v3_details (request_id TEXT,plan_digest TEXT,lease_epoch INTEGER,
      ordinal INTEGER,product_id TEXT,body TEXT,PRIMARY KEY(request_id,plan_digest,lease_epoch,ordinal))''')
    db.execute('''CREATE TABLE IF NOT EXISTS card_v3_detail_observations (request_id TEXT,plan_digest TEXT,lease_epoch INTEGER,
      ordinal INTEGER,body TEXT NOT NULL,PRIMARY KEY(request_id,plan_digest,lease_epoch,ordinal))''')
    db.execute('''CREATE TABLE IF NOT EXISTS card_v3_visual (request_id TEXT,plan_digest TEXT,lease_epoch INTEGER,
      ordinal INTEGER,product_id TEXT,body TEXT,PRIMARY KEY(request_id,plan_digest,lease_epoch,ordinal))''')
    db.execute('''CREATE TABLE IF NOT EXISTS card_v3_meta (request_id TEXT,plan_digest TEXT,lease_epoch INTEGER,
      threshold INTEGER,PRIMARY KEY(request_id,plan_digest,lease_epoch))''')
    db.execute('''CREATE TABLE IF NOT EXISTS card_v3_visual_source (request_id TEXT,plan_digest TEXT,lease_epoch INTEGER,
      body TEXT NOT NULL,PRIMARY KEY(request_id,plan_digest,lease_epoch))''')
    db.execute('''CREATE TABLE IF NOT EXISTS card_v3_visual_evidence (request_id TEXT,plan_digest TEXT,lease_epoch INTEGER,
      ordinal INTEGER,body TEXT NOT NULL,PRIMARY KEY(request_id,plan_digest,lease_epoch,ordinal))''')


def _fence(fence):
    if not callable(fence) or fence() is not True:raise ValueError('Active lease required')


def _body(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)


def freeze_queue(db,scope,*,plan_exhausted,fence):
    _fence(fence)
    if type(plan_exhausted) is not bool:raise ValueError('Plan status required')
    existing=db.execute('SELECT product_id FROM card_v3_queue WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY ordinal',scope).fetchall()
    frozen=db.execute('SELECT 1 FROM card_v3_meta WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope).fetchone()
    if frozen:return [row[0] for row in existing]
    queries=db.execute('SELECT query_index,query FROM card_v3_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index',scope).fetchall()
    if len(queries)<3 or len(queries)>9 or [x[0] for x in queries]!=list(range(len(queries))):raise ValueError('Invalid search plan')
    if known_count(db,scope)<100 and len(queries)<9 and not plan_exhausted:raise ValueError('Search expansion required')
    merged=merge_candidates([(q,query_candidates(db,scope,i)) for i,q in queries])
    db.execute('SAVEPOINT card_v3_freeze')
    try:
        _fence(fence)
        db.executemany('INSERT INTO card_v3_queue VALUES (?,?,?,?,?,?)',[( *scope,i,row['product_id'],_body(row)) for i,row in enumerate(merged)])
        # A meta row records a frozen empty queue too.
        db.execute('INSERT OR IGNORE INTO card_v3_meta VALUES (?,?,?,NULL)',scope)
        _fence(fence)
    except Exception:
        db.execute('ROLLBACK TO card_v3_freeze');db.execute('RELEASE card_v3_freeze');raise
    db.execute('RELEASE card_v3_freeze')
    return [row['product_id'] for row in merged]


def record_detail(db,scope,index,identity,sales_text,threshold,*,fence):
    _fence(fence)
    meta=db.execute('SELECT threshold FROM card_v3_meta WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope).fetchone()
    if not meta or type(index) is not int or index<0 or type(threshold) is not int or threshold<0:raise ValueError('Frozen task threshold required')
    if meta[0] is not None and meta[0]!=threshold:raise ValueError('Task threshold changed')
    queue=db.execute('SELECT product_id FROM card_v3_queue WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND ordinal=?',(*scope,index)).fetchone()
    if not queue or queue[0]!=identity:raise ValueError('Wrong candidate')
    value=parse_sales(sales_text);status=classify_detail(value,threshold)
    body=_body(dict(identity=identity,sales_text=sales_text,sales_lower_bound=value,status=status))
    rows=db.execute('SELECT ordinal,body FROM card_v3_details WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY ordinal',scope).fetchall()
    if index<len(rows):
        if rows[index]!=(index,body):raise ValueError('Confirmed detail cannot change')
        return status
    if index!=len(rows):raise ValueError('Details out of order')
    eligible=sum(json.loads(row[1])['status']!='exclude' for row in rows)
    if eligible>=30:raise ValueError('Visual candidate limit reached')
    db.execute('SAVEPOINT card_v3_detail_write')
    try:
        _fence(fence);db.execute('UPDATE card_v3_meta SET threshold=? WHERE request_id=? AND plan_digest=? AND lease_epoch=?',(threshold,*scope))
        db.execute('INSERT INTO card_v3_details VALUES (?,?,?,?,?,?)',(*scope,index,identity,body))
        _fence(fence)
    except Exception:
        db.execute('ROLLBACK TO card_v3_detail_write');db.execute('RELEASE card_v3_detail_write');raise
    db.execute('RELEASE card_v3_detail_write')
    return status


def record_visual(db,scope,ordinal,identity,decision,*,fence):
    _fence(fence)
    details=[json.loads(r[0]) for r in db.execute('SELECT body FROM card_v3_details WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY ordinal',scope)]
    selected=[d for d in details if d['status']!='exclude'][:30]
    if type(ordinal) is not int or not 0<=ordinal<len(selected) or selected[ordinal]['identity']!=identity:raise ValueError('Wrong visual candidate')
    fields={'level','primary_matches','secondary_matches','style_matches','conflicts'}
    if not isinstance(decision,dict) or set(decision)!=fields or decision['level'] not in ('highly_similar','competitor','broad_competitor','review','reject') or any(type(decision[k]) is not int or decision[k]<0 for k in fields-{'level'}):raise ValueError('Invalid visual decision')
    body=_body(decision);rows=db.execute('SELECT ordinal,body FROM card_v3_visual WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY ordinal',scope).fetchall()
    if ordinal<len(rows):
        if rows[ordinal]!=(ordinal,body):raise ValueError('Confirmed visual decision cannot change')
        return
    if ordinal!=len(rows):raise ValueError('Visual decisions out of order')
    _fence(fence);db.execute('INSERT INTO card_v3_visual VALUES (?,?,?,?,?,?)',(*scope,ordinal,identity,body))


def record_visual_observation(db,scope,ordinal,identity,source,candidate,source_secondary,candidate_secondary,source_style,candidate_style,*,fence):
    from .card_sales_v3 import assess_visual
    result=assess_visual(source,candidate,source_secondary,candidate_secondary,source_style,candidate_style)
    _fence(fence)
    source_body=_body(dict(source=source,secondary=source_secondary,style=source_style))
    evidence_body=_body(dict(identity=identity,candidate=candidate,secondary=candidate_secondary,style=candidate_style,result=result))
    prior=db.execute('SELECT body FROM card_v3_visual_source WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope).fetchone()
    if prior and prior[0]!=source_body:raise ValueError('Source visual evidence cannot change')
    previous=db.execute('SELECT body FROM card_v3_visual_evidence WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND ordinal=?',(*scope,ordinal)).fetchone()
    if previous and previous[0]!=evidence_body:raise ValueError('Visual evidence cannot change')
    fields=('level','primary_matches','secondary_matches','style_matches','conflicts')
    db.execute('SAVEPOINT card_v3_visual_write')
    try:
        record_visual(db,scope,ordinal,identity,{k:result[k] for k in fields},fence=fence)
        _fence(fence)
        db.execute('INSERT OR IGNORE INTO card_v3_visual_source VALUES (?,?,?,?)',(*scope,source_body))
        db.execute('INSERT OR IGNORE INTO card_v3_visual_evidence VALUES (?,?,?,?,?)',(*scope,ordinal,evidence_body))
        _fence(fence)
    except Exception:
        db.execute('ROLLBACK TO card_v3_visual_write');db.execute('RELEASE card_v3_visual_write')
        raise
    db.execute('RELEASE card_v3_visual_write')
    return result


def selection(db,scope,result_limit):
    from .card_sales_v3 import assess_visual
    if type(result_limit) is not int or result_limit<1:raise ValueError('Invalid result target')
    meta=db.execute('SELECT 1 FROM card_v3_meta WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope).fetchone()
    if not meta:raise ValueError('Candidates not frozen')
    queue=db.execute('SELECT count(*) FROM card_v3_queue WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope).fetchone()[0]
    details=[json.loads(r[0]) for r in db.execute('SELECT body FROM card_v3_details WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY ordinal',scope)]
    eligible=[d for d in details if d['status']!='exclude'][:30]
    if len(details)<queue and len(eligible)<30:raise ValueError('Detail review incomplete')
    visual=db.execute('SELECT product_id,body FROM card_v3_visual WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY ordinal',scope).fetchall()
    if len(visual)!=len(eligible):raise ValueError('Visual review incomplete')
    source_row=db.execute('SELECT body FROM card_v3_visual_source WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope).fetchone()
    if visual and not source_row:raise ValueError('Visual source evidence missing')
    source=json.loads(source_row[0]) if source_row else None
    rows=[]
    for ordinal,((pid,body),detail) in enumerate(zip(visual,eligible)):
        row=dict(json.loads(body),identity=pid,sales_lower_bound=detail['sales_lower_bound'],sales_tier=detail['status'])
        proof=db.execute('SELECT body FROM card_v3_visual_evidence WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND ordinal=?',(*scope,ordinal)).fetchone()
        if not proof:raise ValueError('Visual observation evidence missing')
        observed=json.loads(proof[0])
        if observed['identity']!=pid:raise ValueError('Visual observation identity mismatch')
        comparison=assess_visual(source['source'],observed['candidate'],source['secondary'],observed['secondary'],source['style'],observed['style'])
        if comparison!=observed['result'] or any(row[k]!=comparison[k] for k in ('level','primary_matches','secondary_matches','style_matches','conflicts')):
            raise ValueError('Visual decision evidence mismatch')
        row['visual_comparison']=comparison
        rows.append(row)
    ranked=rank_visual(rows,expected=len(eligible));passed=[r for r in ranked if r['level'] in ('highly_similar','competitor','broad_competitor')]
    return {'threshold_met':[r for r in passed if r['sales_tier']=='threshold_met'][:result_limit],
            'supplemental':[r for r in passed if r['sales_tier']=='supplemental'],
            'review':[r for r in ranked if r['level']=='review']}


def progress(db,scope):
    """Derive counts from confirmed checkpoints, never from browser claims."""
    queries=db.execute('SELECT query_index,query FROM card_v3_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index',scope).fetchall()
    if [index for index,_ in queries]!=list(range(len(queries))):raise ValueError('Invalid query history')
    selected=[];pages=[]
    for index,_ in queries:
        history=db.execute('SELECT page,status FROM card_v3_pages WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? ORDER BY page',(*scope,index)).fetchall()
        pages.append(len(history))
        try:selected.append(len(query_candidates(db,scope,index)))
        except ValueError:selected.append(None)
    details=[json.loads(row[0]) for row in db.execute('SELECT body FROM card_v3_details WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY ordinal',scope)]
    statuses=[item['status'] for item in details]
    visual_done=db.execute('SELECT count(*) FROM card_v3_visual_evidence WHERE request_id=? AND plan_digest=? AND lease_epoch=?',scope).fetchone()[0]
    eligible=[row for row in details if row['status']!='exclude']
    decisions=[json.loads(row[0]) for row in db.execute('SELECT body FROM card_v3_visual WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY ordinal',scope)]
    if len(decisions)>len(eligible):raise ValueError('Invalid visual progress')
    passed={'threshold_met':0,'supplemental':0}
    for detail,decision in zip(eligible,decisions):
        if decision.get('level') in ('highly_similar','competitor','broad_competitor'):passed[detail['status']]+=1
    return dict(known_products=known_count(db,scope),selected_per_query=selected,completed_pages=pages,
                detail_count=len(details),threshold_met=statuses.count('threshold_met'),supplemental=statuses.count('supplemental'),
                excluded=statuses.count('exclude'),visual_required=statuses.count('threshold_met')+statuses.count('supplemental'),visual_done=visual_done,
                visual_threshold_passed=passed['threshold_met'],visual_supplemental_passed=passed['supplemental'])

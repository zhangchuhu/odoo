"""Rebuild visual-v2 results from durable, fenced browser checkpoints."""
import hashlib
import json
from datetime import datetime

from .collector_import import _DETAIL, _identity, _number
from .domain import canonical_hash, validate_result
from .visual_v2_checkpoints import read_queries, query_status, candidate_pool, plan_finished, load_query
from .visual_v2_outcomes import decisions
from .visual_v2_rules import classify_visual, compare_primary


def snapshot_evidence(db,key,photo_scope,plan,task_id):
    queries=read_queries(db,key)
    if len(queries)<3 or queries[:3]!=plan['queries']:raise ValueError('Original query plan mismatch')
    blocks=[]
    for index,query in enumerate(queries):
        status=query_status(db,(*key,str(index)))
        if not status:break
        blocks.append({'query':query,'status':status,'observations':load_query(db,(*key,str(index)))})
    details=[json.loads(r[0]) for r in db.execute('''SELECT body FROM collector_details
        WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY detail_index''',key)]
    exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='visual_verdicts'").fetchone()
    photos=[json.loads(r[0]) for r in db.execute('SELECT receipt FROM visual_verdicts WHERE scope=? ORDER BY ordinal',(photo_scope,))] if exists else []
    return {'task_record_id':task_id,'platform':plan['platform'],'queries':blocks,'details':details,
            'decisions':decisions(db,key),'photo_receipts':photos}


def prepare_stage(db,key,photo_scope,plan,config,task_id,observed_at):
    build_result(db,key,photo_scope,plan,config,observed_at)
    evidence=snapshot_evidence(db,key,photo_scope,plan,task_id)
    if len(json.dumps(evidence,ensure_ascii=False,allow_nan=False).encode())>5*1024*1024:
        raise ValueError('Evidence exceeds bound')
    return evidence


def build_result(db, key, photo_scope, plan, config, observed_at):
    stamp=datetime.fromisoformat(observed_at.replace('Z','+00:00'))
    if stamp.utcoffset() is None: raise ValueError('Aware observation time required')
    queries=read_queries(db,key)
    if len(queries)<3 or queries[:3]!=plan['queries']: raise ValueError('Original query plan mismatch')
    complete=[]
    for index in range(len(queries)):
        if not query_status(db,(*key,str(index))): break
        complete.append(str(index))
    if len(complete)<3: raise ValueError('Initial queries incomplete')
    pool=candidate_pool(db,key,complete)
    rows=[json.loads(r[0]) for r in db.execute('''SELECT body FROM collector_details
        WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY detail_index''',key)]
    outcomes={v['identity']:v for v in decisions(db,key)}
    if len(rows)>len(pool):raise ValueError('Too many candidate decisions')
    candidates=[];pending=[]
    design=plan['design_profile']
    for ordinal,detail in enumerate(rows):
        item=pool[ordinal];pid=item['product_id'];identity=plan['platform']+':'+pid
        if not isinstance(detail,dict) or set(detail) not in (_DETAIL,_DETAIL|{'observed_at'}) or detail['identity']!=identity:
            raise ValueError('Invalid candidate detail order')
        current,_=_identity(detail['detail_url'],plan['platform'])
        if current!=pid or detail['product_id']!=pid or not isinstance(detail['title'],str) or not detail['title'].strip():
            raise ValueError('Candidate identity mismatch')
        sold=_number(detail['sold_display'])
        if detail['status']=='rejected' and detail['reason'] in ('threshold_failure','metric_missing_or_ambiguous'):
            if not ((detail['reason']=='threshold_failure' and sold and sold[0]<config['min_sold']) or
                    (detail['reason']=='metric_missing_or_ambiguous' and sold is None)):
                raise ValueError('Invalid sales rejection')
            continue
        decision=outcomes.get(pid)
        if not decision or decision['source']!=design['primary']:
            raise ValueError('Missing bound visual decision')
        labels=decision['labels']
        if any(labels[index] != (None if design[name] is None else sorted(set(design[name])))
               for index,name in ((0,'secondary'),(2,'styles'),(4,'occasions'))):
            raise ValueError('Source design labels changed')
        outcome=classify_visual(decision['source'],decision['candidate'],*labels,critical_conflict=decision['critical_conflict'])
        if decision['outcome']!=outcome or decision['primary']!=compare_primary(decision['source'],decision['candidate']):
            raise ValueError('Visual decision changed')
        row=db.execute('SELECT receipt FROM visual_verdicts WHERE scope=? AND ordinal=?',(photo_scope,ordinal)).fetchone()
        if not row:raise ValueError('Missing visual photo receipt')
        receipt=json.loads(row[0])
        if receipt.get('detail_digest')!=canonical_hash(detail) or not receipt.get('images'):
            raise ValueError('Visual photo receipt mismatch')
        if outcome in ('review','reject'):
            if detail['status']!='rejected' or detail['reason']!=('visual_review_required' if outcome=='review' else 'visual_mismatch'):
                raise ValueError('Visual rejection mismatch')
            if outcome=='review':pending.append({'item_id':pid,'comparison':decision['primary']})
            continue
        expected='高度相似' if outcome=='highly_similar' else '类似竞品'
        if (detail['status']!='qualified' or detail['reason'] is not None or detail['match_level']!=expected
                or detail['visual_category_code']!=plan['category'] or not sold or sold[0]<config['min_sold']):
            raise ValueError('Candidate does not meet bound criteria')
        card=item['card'];query=queries[int(item['queries'][0])]
        candidates.append({'platform':plan['platform'],'item_id':pid,'title':detail['title'],'url':detail['detail_url'],
            'raw_url':detail['detail_url'],'query':query,'position':card.get('rank'),'is_ad':card.get('is_ad'),
            'sold_display':detail['sold_display'],'sold_lower_bound':sold[0],'sold_upper_bound':None if sold[1] else sold[0],
            'review_count':None,'reviews_display':detail.get('reviews_display'),'rating':None,
            'image_url':card.get('thumbnail_url'),'visual_category':detail['visual_category_code'],
            'visual_features':'；'.join(detail['visual_features'] or []),'observed_at':observed_at,
            'observation_time_scope':'batch','selection_reason':expected,'evidence_ids':['collector'],
            'design_comparison':decision['primary'],'search_sources':[queries[int(i)] for i in item['queries']]})
    reached=len(candidates)>=config['result_limit']
    if not reached and (len(rows)!=len(pool) or len(complete)!=len(queries) or not (len(queries)==9 or plan_finished(db,key))):
        raise ValueError('Search plan or candidates remain incomplete')
    if len(candidates)>config['result_limit']:raise ValueError('Result target exceeded')
    candidates.sort(key=lambda item: {'同款':0,'高度相似':1,'类似竞品':2}.get(item['selection_reason'],3))
    evidence_data={'queries':queries,'details':rows}
    digest=hashlib.sha256(json.dumps(evidence_data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    result={'schema_version':'1','status':'complete' if reached else 'partial' if candidates else 'empty',
            'candidates':candidates,'evidence':[{'id':'collector','source':'collector:sha256:'+digest,'observed_at':observed_at}],
            'visual_pending':pending}
    if not reached:result['reason']='search_plan_exhausted'
    validate_result('research.bestseller',result)
    return result

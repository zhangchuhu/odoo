"""Rebuild ranked v3 research results from durable observations and photo receipts."""
import hashlib
import json
import io
from datetime import datetime
from PIL import Image
from .card_sales_v3_pipeline import selection
from .collector_import import _identity
from .domain import validate_result


def snapshot_evidence(db,key,photo_scope,plan,result,task_id):
    where=' WHERE request_id=? AND plan_digest=? AND lease_epoch=?'
    queries=[dict(index=i,query=q) for i,q in db.execute('SELECT query_index,query FROM card_v3_queries'+where+' ORDER BY query_index',key)]
    pages=[dict(query_index=i,page=p,status=s) for i,p,s in db.execute('SELECT query_index,page,status FROM card_v3_pages'+where+' ORDER BY query_index,page',key)]
    batches=[dict(query_index=i,page=p,batch_index=b,cards=json.loads(body)) for i,p,b,body in db.execute('SELECT query_index,page,batch_index,body FROM card_v3_batches'+where+' ORDER BY rowid',key)]
    batch_evidence=[dict(query_index=i,page=p,batch_index=b,screenshot_sha=sha,width=w,height=h) for i,p,b,sha,w,h in db.execute('SELECT query_index,page,batch_index,screenshot_sha,width,height FROM card_v3_batch_evidence'+where+' ORDER BY rowid',key)]
    details=[json.loads(body) for body, in db.execute('SELECT body FROM card_v3_details'+where+' ORDER BY ordinal',key)]
    originals={i:json.loads(body) for i,body in db.execute('SELECT ordinal,body FROM card_v3_detail_observations'+where,key)}
    visual=[json.loads(body) for body, in db.execute('SELECT body FROM card_v3_visual_evidence'+where+' ORDER BY ordinal',key)]
    photos=[json.loads(body) for body, in db.execute('SELECT receipt FROM visual_verdicts WHERE scope=? ORDER BY ordinal',(photo_scope,))]
    by_identity={row['identity']:originals[i] for i,row in enumerate(details) if i in originals}
    selected=result['candidates']+result['supplemental_candidates']
    selected_details=[]
    for item in selected:
        detail=dict(by_identity[item['item_id']],status='qualified',reason=None)
        selected_details.append(detail)
    return dict(task_record_id=task_id,platform=plan['platform'],details=selected_details,
                v3_observations=dict(queries=queries,pages=pages,batches=batches,batch_evidence=batch_evidence,details=details,originals={str(k):v for k,v in originals.items()},visual=visual,photos=photos))


def prepare_stage(db,key,photo_scope,plan,config,task_id,observed_at,verify_photo):
    result=build_result(db,key,photo_scope,plan,config,observed_at,verify_photo)
    evidence=snapshot_evidence(db,key,photo_scope,plan,result,task_id)
    if len(json.dumps(evidence,ensure_ascii=False,allow_nan=False).encode())>5*1024*1024:raise ValueError('Evidence exceeds bound')
    return evidence


def build_result(db,key,photo_scope,plan,config,observed_at,verify_photo):
    stamp=datetime.fromisoformat(observed_at.replace('Z','+00:00'))
    if stamp.utcoffset() is None or not callable(verify_photo):raise ValueError('Verified observation time and photos required')
    batches=db.execute('''SELECT b.body,e.screenshot_sha,e.screenshot_file,e.width,e.height FROM card_v3_batches b
        LEFT JOIN card_v3_batch_evidence e USING (request_id,plan_digest,lease_epoch,query_index,page,batch_index)
        WHERE b.request_id=? AND b.plan_digest=? AND b.lease_epoch=? ORDER BY b.rowid''',key).fetchall()
    for body,sha,filename,width,height in batches:
        if not sha or not filename:raise ValueError('Search card screenshot missing')
        raw=verify_photo(filename,sha)
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format!='JPEG' or image.size!=(width,height):raise ValueError('Search screenshot mismatch')
                image.verify()
        except (OSError,TypeError) as exc:raise ValueError('Invalid search screenshot') from exc
        for card in json.loads(body):
            box=card.get('bbox')
            if (not isinstance(box,list) or len(box)!=4 or any(type(v) is not int for v in box) or box[0]<0 or box[1]<0
                    or box[2]<=0 or box[3]<=0 or box[0]+box[2]>width or box[1]+box[3]>height):raise ValueError('Search card screenshot bounds invalid')
    selected=selection(db,key,config['result_limit'])
    details=[json.loads(row[0]) for row in db.execute('SELECT body FROM card_v3_details WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY ordinal',key)]
    eligible=[row for row in details if row['status']!='exclude']
    source=db.execute('SELECT body FROM card_v3_visual_source WHERE request_id=? AND plan_digest=? AND lease_epoch=?',key).fetchone()
    design=plan['design_profile']
    if eligible:
        expected={'source':design['primary'],'secondary':design['secondary'],
                  'style':list(dict.fromkeys((design['styles'] or [])+(design['occasions'] or [])))}
        if not source or json.loads(source[0])!=expected:raise ValueError('Source design evidence changed')
    queue={row[0]:json.loads(row[1]) for row in db.execute('SELECT product_id,body FROM card_v3_queue WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY ordinal',key)}
    observations={row[0]:json.loads(row[1]) for row in db.execute('SELECT ordinal,body FROM card_v3_detail_observations WHERE request_id=? AND plan_digest=? AND lease_epoch=?',key)}
    qualified={}
    for ordinal,detail in enumerate(eligible):
        pid=detail['identity'];identity=plan['platform']+':'+pid
        index=next(i for i,row in enumerate(details) if row['identity']==pid)
        observed=observations.get(index)
        if not observed or observed.get('identity')!=identity or observed.get('product_id')!=pid or observed.get('sold_display')!=detail['sales_text']:
            raise ValueError('Detail evidence mismatch')
        if _identity(observed['detail_url'],plan['platform'])[0]!=pid:raise ValueError('Detail identity mismatch')
        verdict=db.execute('SELECT receipt FROM visual_verdicts WHERE scope=? AND ordinal=?',(photo_scope,ordinal)).fetchone()
        visual=db.execute('SELECT body FROM card_v3_visual WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND ordinal=?',(*key,ordinal)).fetchone()
        if not verdict or not visual:raise ValueError('Visual photo receipt missing')
        receipt=json.loads(verdict[0]);decision=json.loads(visual[0])
        if receipt.get('identity')!=identity or receipt.get('classification')!=decision['level'] or not receipt.get('images'):
            raise ValueError('Visual photo receipt mismatch')
        for image in receipt['images']:
            row=db.execute('SELECT metadata,sha,file FROM visual_photos WHERE id=? AND scope=? AND ordinal=?',(image['receipt'],photo_scope,ordinal)).fetchone()
            if not row or row[1]!=image['sha256']:raise ValueError('Visual image evidence mismatch')
            metadata=json.loads(row[0])
            if metadata.get('detail')!=observed or metadata.get('source')!=image['source']:raise ValueError('Visual image evidence mismatch')
            verify_photo(row[2],row[1])
        qualified[pid]=(observed,queue[pid],detail)
    def candidate(row):
        observed,card,detail=qualified[row['identity']];pid=row['identity'];sold=detail['sales_lower_bound']
        canonical=_identity(observed['detail_url'],plan['platform'])[1]
        return {'platform':plan['platform'],'item_id':pid,'title':observed['title'],'url':canonical,
          'raw_url':observed['detail_url'],'query':card['query'],'position':card['rank'],'is_ad':card.get('is_ad'),
          'sold_display':detail['sales_text'],'sold_lower_bound':sold,'sold_upper_bound':None,'review_count':None,
          'reviews_display':observed.get('reviews_display'),'rating':None,'image_url':card.get('thumbnail_url'),
          'visual_category':plan['category'],'visual_features':'；'.join(row['visual_comparison']['matches']),
          'observed_at':observed_at,'observation_time_scope':'batch','selection_reason':row['level'],
          'evidence_ids':['collector'],'design_comparison':row['visual_comparison'],'search_sources':card.get('search_sources',[]),
          'sales_tier':detail['status']}
    candidates=[candidate(row) for row in selected['threshold_met']]
    supplemental=[candidate(row) for row in selected['supplemental']]
    digest=hashlib.sha256(json.dumps({'details':details,'selected':selected},sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    reached=len(candidates)>=config['result_limit']
    result={'schema_version':'1','status':'complete' if reached else 'partial' if candidates else 'empty',
            'candidates':candidates,'supplemental_candidates':supplemental,
            'visual_pending':[{'item_id':row['identity'],'comparison':row['visual_comparison']} for row in selected['review']],
            'evidence':[{'id':'collector','source':'collector:sha256:'+digest,'observed_at':observed_at}]}
    if not reached:result['reason']='visual_candidate_limit_reached' if len(eligible)>=30 and len(details)<len(queue) else 'search_plan_exhausted'
    validate_result('research.bestseller',result)
    return result

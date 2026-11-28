"""Read and extend business checkpoints for one authenticated collector binding."""
from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3
import time

from .collector_cli import collector_id
from .collector_import import _identity, _number, ranked_pool, validate_evidence_prefix, validate_staged_evidence_prefix


class CollectorProgressError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def initialize_details(db):
    db.execute("""CREATE TABLE IF NOT EXISTS collector_details (
        request_id TEXT, plan_digest TEXT, lease_epoch INTEGER, detail_index INTEGER,
        body TEXT NOT NULL, PRIMARY KEY(request_id, plan_digest, lease_epoch, detail_index))""")


def staged_in_flight(db,key,identity,elapsed):
    table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='staged_detail_reservations'").fetchone()
    reserved=db.execute('SELECT identity,started FROM staged_detail_reservations WHERE request_id=? AND plan_digest=? AND lease_epoch=?',key).fetchone() if table else None
    now=time.time()
    return bool(reserved and reserved[0]==identity and 0<=reserved[1]<=now and reserved[1]<=now-elapsed+3600)


def reserve_staged_detail(db,key,identity,started):
    from .collector_staged_policy import elapsed_seconds
    now=time.time();elapsed=elapsed_seconds(db,key)
    if type(started) not in (int,float) or not 0<=started<=now or started>now-elapsed+3600:
        raise ValueError('Detail started after the time budget')
    db.execute('CREATE TABLE IF NOT EXISTS staged_detail_reservations (request_id TEXT,plan_digest TEXT,lease_epoch INTEGER,identity TEXT,started REAL,PRIMARY KEY(request_id,plan_digest,lease_epoch))')
    db.execute('INSERT OR REPLACE INTO staged_detail_reservations VALUES (?,?,?,?,?)',(*key,identity,started))


def guarded_query_index_allowed(version, index):
    if type(index) is not int:
        return False
    return index == 2 or (version in (2, 3, 6) and 2 <= index <= (8 if version in (3, 6) else 5))


def handle_progress(broker, event):
    op = event['operation']
    sales_only = op == 'checkpoint_detail' and 'sales_only' in event
    expected = {'operation', 'request_id', 'window_id', 'session_id', 'tab_id', 'native_origin'}
    if op == 'collector_progress':
        expected.add('query_index')
        if 'expected_binding' in event:
            expected.add('expected_binding')
    elif op == 'checkpoint_detail':
        expected.update(('detail_index', 'detail'))
        if sales_only:
            expected.add('sales_only')
    elif op == 'gallery_capture':
        expected.update(('identity', 'detail_url', 'manifest', 'screenshot'))
        if 'variant' in event:
            expected.add('variant')
    elif op == 'seal_expansion':
        expected.add('queries')
    elif op != 'next_candidate':
        raise CollectorProgressError('checkpoint_binding_invalid', '未知采集操作')
    key = tuple(broker.challenge[k] for k in ('request_id', 'plan_digest', 'lease_epoch'))
    platform = broker.challenge['platform']
    origin = broker.anchor_origin if platform == 'mercado-libre-mx' else broker.peer_origin
    shein_home=platform=='shein-us' and broker.progress_plan and broker.progress_plan[0].get('strategy')=='shein-home-v1'
    if (set(event) != expected or (sales_only and (event['sales_only'] is not True or (platform != 'mercado-libre-mx' and not shein_home)))
            or not origin or event['native_origin'] != origin or event['request_id'] != key[0]
            or type(event['tab_id']) is not int or event['tab_id'] != broker.tab
            or type(event['window_id']) is not int or event['window_id'] != broker.window
            or not broker.session or event['session_id'] != broker.session
            or (broker.progress_plan is None and not (op=='gallery_capture' and getattr(broker,'gallery_binding',None)))):
        raise CollectorProgressError('checkpoint_binding_invalid', '当前任务、租约或浏览器绑定已失效')
    binding = dict(request_id=key[0], plan_digest=key[1], lease_epoch=key[2],
                   window_id=broker.window, tab_id=broker.tab, session_id=broker.session)
    guarded_read = op == 'collector_progress' and 'expected_binding' in event
    if guarded_read:
        proof = event['expected_binding']
        if (not guarded_query_index_allowed(broker.challenge.get('version'), event['query_index']) or not isinstance(proof, dict)
                or set(proof) != set(binding) or any(type(proof[k]) is not type(v) or proof[k] != v for k, v in binding.items())
                or broker.conflict or broker.rebind_requested or not broker.fence()):
            raise CollectorProgressError('checkpoint_identity_changed', '原请求、计划、租约或标签身份已经改变')
        if not broker.anchor_guard_alive():
            raise CollectorProgressError('checkpoint_anchor_pending', '第三组已确认，正在等待专用窗口连接')
    if op == 'gallery_capture':
        def ensure_current():
            if (not broker.watch_only or broker.conflict or broker.rebind_requested or not broker.fence() or not broker.anchor_guard_alive()
                    or not broker.guard_alive(event['session_id'])):
                raise CollectorProgressError('checkpoint_guard_pending', '请重新确认当前浏览器和任务标签')
            gallery = getattr(broker,'gallery_binding',None)
            if gallery and (not gallery.confirmed or gallery.confirmed[0] != event['identity']):
                raise CollectorProgressError('gallery_candidate_invalid','当前标签核验的商品与图库不一致')
        ensure_current()
        if not callable(broker.gallery_capture):
            raise CollectorProgressError('gallery_capture_unavailable', '宿主未接通图库回传')
        return broker.gallery_capture(event, ensure_current)
    if not guarded_read and (not broker.fence() or not broker.anchor_guard_alive()):
        raise CollectorProgressError('checkpoint_binding_invalid', '当前任务、租约或浏览器绑定已失效')
    plan, config = broker.progress_plan
    task = collector_id(key[0])
    params = dict(platform=platform, queries=plan['queries'], category=plan['category'],
                  **{k: config[k] for k in ('result_limit','min_sold','min_reviews','min_rating')})
    with closing(sqlite3.connect(broker.store.path)) as db, db:
        db.execute('BEGIN IMMEDIATE' if op in ('checkpoint_detail','seal_expansion','next_candidate') else 'BEGIN')
        where = ' WHERE request_id=? AND plan_digest=? AND lease_epoch=?'
        if plan.get('strategy') in ('card-home-v4','shein-home-v1') and op=='collector_progress':
            from .card_sales_v3_checkpoints import initialize
            from .home_collection_policy import next_home_query
            initialize(db)
            declared=[row[0] for row in db.execute('SELECT query FROM card_v3_queries'+where+' ORDER BY query_index',key)]
            index=event['query_index']
            if type(index) is not int or not 0<=index<len(declared):raise CollectorProgressError('checkpoint_index_invalid','无效的搜索组编号')
            step=next_home_query(db,key)
            accessible=bool(not broker.watch_only and broker.query_verified and broker.challenge['query_index']==index and step==(index,1))
            batches=[]
            if accessible:
                batches=[json.loads(row[0]) for row in db.execute('SELECT body FROM card_v3_batches'+where+' AND query_index=? AND page=1 ORDER BY batch_index',(*key,index))]
            return dict(ok=True,task=task,platform=platform,strategy=plan['strategy'],queries=declared,result_limit=config['result_limit'],
                        query_index=index,page=1 if step==(index,1) else None,next_page=step,page_action='collect_page' if step else 'idle',
                        acknowledged=accessible,cards=[card for batch in batches for card in batch] if accessible else None,
                        next_batch_index=len(batches),binding=binding)
        if plan.get('strategy')=='card-visual-v3' and op=='collector_progress':
            from .card_sales_v3_checkpoints import initialize,next_page
            initialize(db)
            declared=[row[0] for row in db.execute('SELECT query FROM card_v3_queries'+where+' ORDER BY query_index',key)]
            index=event['query_index']
            if type(index) is not int or not 0<=index<len(declared):raise CollectorProgressError('checkpoint_index_invalid','无效的搜索组编号')
            verified=broker.store.resume_index(*key[:2],declared,key[2],card_v3=True)
            desired=getattr(broker,'requested_page',None)
            step=(broker.challenge['query_index'],desired) if not broker.watch_only and desired is not None else next_page(db,key)
            page=abs(step[1]) if step and step[0]==index and step[1]!=0 else None
            action='verify_query' if step and step[1]==0 else 'verify_page' if step and step[1]<0 else 'collect_page' if step else 'idle'
            accessible=index<verified
            batches=[]
            if accessible and page is not None:
                batches=[json.loads(row[0]) for row in db.execute('SELECT body FROM card_v3_batches'+where+' AND query_index=? AND page=? ORDER BY batch_index',(*key,index,page))]
            cards=[card for batch in batches for card in batch]
            return dict(ok=True,task=task,platform=platform,strategy='card-visual-v3',queries=declared,result_limit=config['result_limit'],
                        query_index=index,page=page,next_page=step,page_action=action,acknowledged=accessible,cards=cards if accessible else None,
                        next_batch_index=len(batches),binding=binding)
        if plan.get('strategy') == 'visual-v2' and op == 'collector_progress':
            from .visual_v2_checkpoints import initialize, read_queries, load_query, query_status
            initialize(db)
            declared = read_queries(db, key)
            index = event['query_index']
            if type(index) is not int or not 0 <= index < len(declared):
                raise CollectorProgressError('checkpoint_index_invalid','无效的搜索组编号')
            query_key = (*key, str(index))
            status = query_status(db, query_key)
            verified = broker.store.resume_index(*key[:2], declared, key[2], visual_v2=True)
            accessible = index < verified
            cards = load_query(db, query_key) if accessible else None
            next_batch = db.execute('''SELECT count(*) FROM visual_v2_batches WHERE request_id=?
                AND plan_digest=? AND lease_epoch=? AND query_id=?''',query_key).fetchone()[0]
            details = [json.loads(r[0]) for r in db.execute(
                'SELECT body FROM collector_details' + where + ' ORDER BY detail_index', key)] if broker.watch_only and broker.guard_alive(broker.session) else []
            return dict(ok=True, task=task, platform=platform, strategy='visual-v2', queries=declared,
                        result_limit=config['result_limit'], query_index=index, acknowledged=bool(status) and accessible,
                        cards=cards, details=details, binding=binding, exhausted=accessible and status=='home_complete', status=status if accessible else None,
                        next_batch_index=next_batch)
        staged=plan.get('strategy') == 'staged-v1'
        declared=_declared_queries(db,key,plan) if staged else plan['queries']
        through = (db.execute('SELECT count(*) FROM query_cards'+where,key).fetchone()[0]
                   if staged and broker.watch_only else 3 if broker.watch_only else broker.challenge['query_index'])
        if op == 'collector_progress':
            index = event['query_index']
            if type(index) is not int or not 0 <= index < len(declared):
                raise CollectorProgressError('checkpoint_index_invalid', '无效的搜索组编号')
            cards = None
            row = None
            acknowledged = index < through
            if acknowledged or (index == through and broker.query_verified):
                row = db.execute('SELECT body FROM query_cards' + where + ' AND query_index=?', (*key, index)).fetchone()
                cards = json.loads(row[0]) if row else None
                if cards is None and acknowledged:
                    raise CollectorProgressError('checkpoint_cards_missing', '已确认搜索组的卡片缺失')
                if cards is None:
                    cards = broker.store.read_inherited_cards(*key, index)
            details = []
            detail_index = through-1 if staged else 2
            if broker.watch_only and index == detail_index and broker.guard_alive(broker.session):
                details = [json.loads(r[0]) for r in db.execute('SELECT body FROM collector_details' + where + ' ORDER BY detail_index', key)]
            elif broker.watch_only and index == detail_index:
                raise CollectorProgressError('checkpoint_guard_pending', '请等待当前浏览器连接确认再恢复详情')
            response=dict(ok=True, task=task, platform=platform, queries=declared, result_limit=config['result_limit'],
                          query_index=index, acknowledged=acknowledged, cards=cards, details=details, binding=binding)
            if staged:
                response['strategy']='staged-v1'
                meta=db.execute('SELECT exhausted FROM query_card_meta'+where+' AND query_index=?',(*key,index)).fetchone()
                if not meta and row is None and cards is not None:
                    meta=db.execute('SELECT exhausted FROM inherited_query_meta'+where+' AND query_index=?',(*key,index)).fetchone()
                response['exhausted']=bool(meta and meta[0]==1)
            return response
        if not broker.watch_only or not broker.guard_alive(broker.session):
            raise CollectorProgressError('checkpoint_guard_pending', '三组卡片及当前标签须先全部确认')
        if plan.get('strategy') in ('card-visual-v3','card-home-v4','shein-home-v1') and op in ('next_candidate','checkpoint_detail'):
            from .card_sales_v3_pipeline import record_detail,record_shein_detail
            from .collector_import import _DETAIL, valid_detail_fields
            queue=[(row[0],json.loads(row[1])) for row in db.execute('SELECT product_id,body FROM card_v3_queue'+where+' ORDER BY ordinal',key)]
            frozen=db.execute('SELECT 1 FROM card_v3_meta'+where,key).fetchone()
            details=[json.loads(row[0]) for row in db.execute('SELECT body FROM card_v3_details'+where+' ORDER BY ordinal',key)]
            eligible=[row for row in details if row['status']!='exclude']
            visual_done=db.execute('SELECT count(*) FROM card_v3_visual_evidence'+where,key).fetchone()[0]
            if not frozen and op=='next_candidate':
                if plan.get('strategy') in ('card-home-v4','shein-home-v1'):
                    from .home_collection_policy import next_home_query as next_page
                else:
                    from .card_sales_v3_checkpoints import next_page
                return dict(ok=True,task=task,candidate=None,stage='search',action='search',requires_expansion=next_page(db,key) is None,
                            **{k:event[k] for k in ('request_id','window_id','session_id','tab_id')})
            if not frozen or len(details)>len(queue) or visual_done>len(eligible):raise CollectorProgressError('checkpoint_cards_missing','候选队列尚未冻结')
            if op=='next_candidate':
                if visual_done<len(eligible):
                    pid=eligible[visual_done]['identity'];item=next((row for identity,row in queue if identity==pid),None);stage='visual'
                elif len(eligible)<30 and len(details)<len(queue):
                    pid,item=queue[len(details)];stage='detail'
                else:pid,item,stage=None,None,'finished'
                candidate={'identity':platform+':'+pid,'url':item['url']} if pid and item else None
                index=next((i for i,row in enumerate(details) if row['identity']==pid),None) if stage=='visual' else len(details) if stage=='detail' else None
                observation=db.execute('SELECT body FROM card_v3_detail_observations'+where+' AND ordinal=?',(*key,index)).fetchone() if stage=='visual' else None
                if stage=='visual' and not observation:raise CollectorProgressError('detail_checkpoint_missing','已保存的详情证据缺失')
                return dict(ok=True,task=task,candidate=candidate,stage=stage,action='review' if candidate else 'finish',detail_index=index,
                            detail=json.loads(observation[0]) if observation else None,**{k:event[k] for k in ('request_id','window_id','session_id','tab_id')})
            index=event['detail_index'];detail=event['detail']
            try:
                pid,_=_identity(detail['detail_url'],platform)
                valid=(type(index) is int and 0<=index<len(queue) and pid==queue[index][0]
                       and valid_detail_fields(detail,allow_observed_at=True,shein_home=bool(shein_home))
                       and detail.get('product_id')==pid and detail.get('identity')==platform+':'+pid
                       and isinstance(detail.get('title'),str) and bool(detail['title'].strip())
                       and (detail.get('sold_display') is None or isinstance(detail['sold_display'],str)))
            except (KeyError,TypeError,ValueError):valid=False
            if not valid:raise CollectorProgressError('detail_validation_failed','商品身份或销量证据不匹配')
            try:
                body=json.dumps(detail,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
                old=db.execute('SELECT body FROM card_v3_detail_observations'+where+' AND ordinal=?',(*key,index)).fetchone()
                if old and old[0]!=body:raise ValueError('Confirmed detail evidence changed')
                status=(record_shein_detail(db,key,index,detail,config['min_sold'],config['min_reviews'],fence=broker.fence)
                        if shein_home else record_detail(db,key,index,pid,detail['sold_display'],config['min_sold'],fence=broker.fence,homepage_only=plan.get('strategy')=='card-home-v4'))
                if not old:db.execute('INSERT INTO card_v3_detail_observations VALUES (?,?,?,?,?)',(*key,index,body))
                if not broker.anchor_guard_alive() or broker.fence() is not True:raise ValueError('Current browser or lease lost')
            except (ValueError,TypeError) as error:raise CollectorProgressError('detail_validation_failed',str(error)) from error
            return {'ok':True,'sales_screen':status}
        if plan.get('strategy') == 'visual-v2':
            from .visual_v2_checkpoints import read_queries
            declared=read_queries(db,key)
            inherited=db.execute('SELECT 1 FROM inherited_query_cards'+where+' LIMIT 1',key).fetchone()
            verified=broker.store.resume_index(*key[:2],declared,key[2],visual_v2=True) if inherited else len(declared)
            if verified<len(declared):
                if op=='next_candidate':
                    return dict(ok=True,task=task,candidate=None,stage='search',action='search',
                                requires_expansion=False,**{k:event[k] for k in ('request_id','window_id','session_id','tab_id')})
                raise CollectorProgressError('checkpoint_guard_pending','请先重新核验已恢复的搜索组')
        if op == 'seal_expansion':
            if plan.get('strategy') in ('card-home-v4','shein-home-v1'):
                from .card_sales_v3_checkpoints import known_count,seal_query
                from .card_sales_v3_pipeline import freeze_queue
                from .home_collection_policy import next_home_query,record_end_reason,shein_known_count
                queries=[row[0] for row in db.execute('SELECT query FROM card_v3_queries'+where+' ORDER BY query_index',key)]
                additions=event['queries'];lookup=getattr(broker,'home_expansion_candidates',None)
                known=shein_known_count(db,key) if shein_home else known_count(db,key,include_plus_hundred=True)
                if (next_home_query(db,key) is not None or known>=100 or len(queries)>=9
                        or not isinstance(additions,list) or len(additions)>3 or len(queries)+len(additions)>9
                        or not callable(lookup)):
                    raise CollectorProgressError('expansion_plan_invalid','当前不能扩展搜索词')
                try:
                    available=lookup(queries)
                    if not isinstance(available,list) or additions!=available[:min(3,9-len(queries))]:
                        raise ValueError('扩展搜索词缺少平台观察依据或顺序不一致')
                    if additions:
                        for offset,q in enumerate(additions):seal_query(db,key,len(queries)+offset,q,fence=broker.fence)
                    else:
                        freeze_queue(db,key,plan_exhausted=True,fence=broker.fence,homepage_only=True,platform=platform,threshold=config['min_sold'] if shein_home else None)
                        record_end_reason(db,key,'terms_exhausted',fence=broker.fence)
                    if not broker.anchor_guard_alive() or broker.fence() is not True:raise ValueError('Browser or lease lost')
                except ValueError as error:raise CollectorProgressError('expansion_plan_invalid',str(error)) from error
                return {'ok':True,'queries':queries+additions,'finished':not additions}
            if plan.get('strategy')=='card-visual-v3':
                from .card_sales_v3_checkpoints import next_page,known_count,seal_query
                from .card_sales_v3_pipeline import freeze_queue
                from .collector_import import disallowed_term
                queries=[row[0] for row in db.execute('SELECT query FROM card_v3_queries'+where+' ORDER BY query_index',key)]
                additions=event['queries']
                if (next_page(db,key) is not None or known_count(db,key)>=100 or len(queries)>=9
                        or not isinstance(additions,list) or len(additions)>3 or len(queries)+len(additions)>9
                        or any(not isinstance(q,str) or not q.strip() or len(q)>200 or disallowed_term(q) for q in additions)):
                    raise CollectorProgressError('expansion_plan_invalid','当前不能扩展搜索词')
                try:
                    if additions:
                        for offset,q in enumerate(additions):seal_query(db,key,len(queries)+offset,q,fence=broker.fence)
                    else:freeze_queue(db,key,plan_exhausted=True,fence=broker.fence)
                    if not broker.anchor_guard_alive() or broker.fence() is not True:raise ValueError('Browser or lease lost')
                except ValueError as error:raise CollectorProgressError('expansion_plan_invalid',str(error)) from error
                return {'ok':True,'queries':queries+additions,'finished':not additions}
            if plan.get('strategy') == 'visual-v2':
                from .visual_v2_checkpoints import read_queries,query_status,seal_queries,finish_plan,plan_finished
                queries=read_queries(db,key)
                _,order,reviewed,finished=_candidate_order(db,key,plan,config)
                if finished or reviewed<len(order) or any(not query_status(db,(*key,str(i))) for i in range(len(queries))):
                    raise CollectorProgressError('expansion_not_ready','请先处理已采集的候选和搜索组')
                additions=event['queries']
                if not isinstance(additions,list) or len(additions)>3 or len(queries)+len(additions)>9:
                    raise CollectorProgressError('expansion_plan_invalid','扩展搜索词无效')
                try:
                    if additions:
                        if plan_finished(db,key):raise ValueError('Search plan is complete')
                        seal_queries(db,key,queries+additions,fence=broker.fence)
                    else:
                        finish_plan(db,key,fence=broker.fence)
                except ValueError as error:
                    raise CollectorProgressError('expansion_plan_invalid',str(error)) from error
                return {'ok':True,'queries':queries+additions,'finished':not additions}
            if plan.get('strategy') != 'staged-v1':
                raise CollectorProgressError('expansion_unavailable', '当前任务不支持扩展搜索')
            from .collector_staged_policy import initialize_expansions, save_expansion, staged_order, elapsed_seconds
            if elapsed_seconds(db,key)>=3600:
                raise CollectorProgressError('expansion_budget_exhausted','已达到采集时间上限')
            pool, order, reviewed, finished = _candidate_order(db,key,plan,config)
            repeated, _ = staged_order(pool)
            rows = [json.loads(r[0]) for r in db.execute('SELECT body FROM collector_details' + where + ' ORDER BY detail_index',key)]
            history = [('repeated' if identity in repeated else 'single',identity) for identity in order[:reviewed]]
            try:
                initialize_expansions(db)
                digest = save_expansion(db,key,pool,history,sum(d.get('status') == 'qualified' for d in rows),
                                        config['result_limit'],event['queries'],plan['queries'],fence=broker.fence)
            except ValueError as error:
                raise CollectorProgressError('expansion_plan_invalid',str(error)) from error
            return {'ok':True,'digest':digest}
        if op == 'next_candidate':
            pool, recurrent, reviewed, finished = _candidate_order(db, key, plan, config)
            candidate = None
            if not finished and reviewed < len(recurrent):
                pid = recurrent[reviewed]
                candidate = {'identity': f'{platform}:{pid}', 'url': pool[pid]['url']}
            response = dict(ok=True, task=task, candidate=candidate, **{k:event[k] for k in ('request_id','window_id','session_id','tab_id')})
            if plan.get('strategy') == 'visual-v2':
                from .visual_v2_checkpoints import read_queries, query_status, plan_finished
                declared=read_queries(db,key)
                pending_search=any(not query_status(db,(*key,str(i))) for i in range(len(declared)))
                done_plan=plan_finished(db,key) or len(declared)>=9
                ended=finished or (not candidate and not pending_search and done_plan)
                response['stage']='finished' if ended else 'visual' if candidate else 'search'
                response['action']='finish' if ended else 'review' if candidate else 'search'
                if ended: response['reason']='target_reached' if finished else 'search_plan_exhausted'
                elif not candidate: response['requires_expansion']=not pending_search
            elif plan.get('strategy') == 'staged-v1':
                from .collector_staged_policy import staged_order, elapsed_seconds
                initial_count = _initial_candidate_count(db, key, plan)
                repeated, _ = staged_order(pool)
                group_count=db.execute('SELECT count(*) FROM query_cards'+where,key).fetchone()[0]
                elapsed=elapsed_seconds(db,key)
                in_flight=bool(candidate and staged_in_flight(db,key,candidate['identity'],elapsed))
                timeout=not finished and elapsed>=3600 and not in_flight
                if timeout: candidate=None; response['candidate']=None
                ended=finished or timeout or (candidate is None and group_count>3)
                response['stage'] = ('finished' if ended else 'expanded' if candidate is None or reviewed >= initial_count
                                     else 'repeated' if candidate['identity'].split(':',1)[1] in repeated else 'single')
                response['action'] = 'finish' if ended else 'review' if candidate else 'search'
                if ended:
                    response['reason'] = ('target_reached' if finished else 'time_budget_exhausted' if timeout else 'budget_exhausted'
                                          if reviewed >= 60 else 'candidates_exhausted')
            return response
        index = event['detail_index']
        prior = [json.loads(r[0]) for r in db.execute('SELECT body FROM collector_details' + where + ' ORDER BY detail_index', key)]
        if type(index) is not int or not 0 <= index <= len(prior):
            raise CollectorProgressError('detail_index_invalid', '候选详情进度不连续')
        detail = event['detail']
        if index < len(prior):
            if prior[index] != detail:
                raise CollectorProgressError('detail_checkpoint_conflict', '该候选已保存不同的核验结果')
            if sales_only and not _sales_screen_candidate(detail):
                raise CollectorProgressError('detail_validation_failed', '原记录并非销量淘汰')
            return {'ok': True, 'sales_screen': 'rejected'} if sales_only else {'ok': True}
        if plan.get('strategy')=='staged-v1':
            from .collector_staged_policy import elapsed_seconds
            elapsed=elapsed_seconds(db,key)
            if elapsed>=3600:
                if not (isinstance(detail,dict) and staged_in_flight(db,key,detail.get('identity'),elapsed)):
                    raise CollectorProgressError('detail_budget_exhausted','已达到采集时间上限')
        if sales_only:
            _, recurrent, reviewed, finished = _candidate_order(db, key, plan, config)
            try:
                pid, _ = _identity(detail['detail_url'], platform)
                matches = (not finished and reviewed < len(recurrent) and pid == recurrent[reviewed]
                           and detail.get('product_id') == pid and detail.get('identity') == f'{platform}:{pid}'
                           and isinstance(detail.get('title'), str) and detail['title'].strip())
            except (KeyError, TypeError, ValueError):
                matches = False
            if not matches or not _sales_screen_candidate(detail):
                raise CollectorProgressError('detail_validation_failed', '销量筛选须核对当前候选身份和标题')
            sold = _number(detail.get('sold_display'))
            if sold is None:
                return {'ok': True, 'sales_screen': 'metric_missing'}
            if sold[0] >= config['min_sold']:
                return {'ok': True, 'sales_screen': 'visual_required'}
        if plan.get('strategy') == 'visual-v2':
            from .collector_import import _DETAIL
            _,order,reviewed,finished=_candidate_order(db,key,plan,config)
            try:
                pid,_=_identity(detail['detail_url'],platform)
                valid=(isinstance(detail,dict) and set(detail) in (_DETAIL,_DETAIL|{'observed_at'})
                       and not finished and reviewed<len(order) and pid==order[reviewed]
                       and detail.get('identity')==platform+':'+pid and detail.get('product_id')==pid
                       and isinstance(detail.get('title'),str) and bool(detail['title'].strip())
                       and detail.get('status')=='rejected'
                       and detail.get('reason') in ('threshold_failure','metric_missing_or_ambiguous')
                       and all(detail.get(k) is None for k in ('visual_category_code','match_level','visual_features')))
                sold=_number(detail.get('sold_display'))
                valid=valid and ((detail['reason']=='threshold_failure' and sold is not None and sold[0]<config['min_sold'])
                                 or (detail['reason']=='metric_missing_or_ambiguous' and sold is None))
            except (KeyError,TypeError,ValueError): valid=False
            if not valid:raise CollectorProgressError('detail_validation_failed','销量淘汰证据不符合本任务门槛')
            if broker.fence() is not True:raise CollectorProgressError('checkpoint_binding_invalid','当前租约已失效')
            db.execute('INSERT INTO collector_details VALUES (?,?,?,?,?)',(*key,index,json.dumps(detail,ensure_ascii=False,allow_nan=False)))
            return {'ok':True,'sales_screen':'rejected'} if sales_only else {'ok':True}
        cards = [json.loads(r[0]) for r in db.execute('SELECT body FROM query_cards' + where + ' ORDER BY query_index', key)]
        extra_queries = _expanded_queries(db, key, cards) if plan.get('strategy') == 'staged-v1' else []
        evidence = dict(platform=platform, task_record_id=task, details=prior+[detail],
                        queries=[dict(query=q, observations=c) for q,c in zip(plan['queries']+extra_queries, cards)])
        try:
            if plan.get('strategy') == 'staged-v1':
                validate_staged_evidence_prefix(evidence, task, observed_at=datetime.now(timezone.utc).isoformat(), extra_queries=extra_queries, **params)
            else:
                validate_evidence_prefix(evidence, task, observed_at=datetime.now(timezone.utc).isoformat(), **params)
        except ValueError as error:
            raise CollectorProgressError('detail_validation_failed', str(error)) from error
        body = json.dumps(detail, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        if len(json.dumps(evidence, ensure_ascii=False).encode()) > 900000:
            raise CollectorProgressError('checkpoint_size_exceeded', '证据超出回传大小上限')
        db.execute('INSERT INTO collector_details VALUES (?, ?, ?, ?, ?)', (*key, index, body))
        if plan.get('strategy')=='staged-v1':
            db.execute('CREATE TABLE IF NOT EXISTS staged_detail_reservations (request_id TEXT,plan_digest TEXT,lease_epoch INTEGER,identity TEXT,started REAL,PRIMARY KEY(request_id,plan_digest,lease_epoch))')
            db.execute('DELETE FROM staged_detail_reservations'+where,key)
        return {'ok': True, 'sales_screen': 'rejected'} if sales_only else {'ok': True}


def _sales_screen_candidate(detail):
    return (isinstance(detail, dict) and detail.get('status') == 'rejected'
            and detail.get('reason') == 'threshold_failure'
            and all(detail.get(key) is None for key in ('visual_category_code', 'match_level', 'visual_features')))


def _candidate_order(db, key, plan, config):
    where = ' WHERE request_id=? AND plan_digest=? AND lease_epoch=?'
    if plan.get('strategy') == 'visual-v2':
        from .visual_v2_checkpoints import read_queries, query_status, candidate_pool
        queries=read_queries(db,key)
        if len(queries)<3 or queries[:3] != plan['queries']:
            raise CollectorProgressError('checkpoint_cards_missing','须先保存原始搜索计划')
        done=[]
        for index in range(len(queries)):
            if not query_status(db,(*key,str(index))): break
            done.append(str(index))
        if len(done)<3:
            raise CollectorProgressError('checkpoint_cards_missing','须先完成三组搜索卡片')
        items=candidate_pool(db,key,done)
        order=[item['product_id'] for item in items]
        pool={item['product_id']:{'url':item['card']['url'],'card':item['card'],
              'hits':set(item['queries'])} for item in items}
        details=[json.loads(r[0]) for r in db.execute('SELECT body FROM collector_details'+where+' ORDER BY detail_index',key)]
        if len(details)>len(order) or any(d.get('identity')!=plan['platform']+':'+order[i] for i,d in enumerate(details)):
            raise CollectorProgressError('detail_checkpoint_conflict','候选检查点顺序不一致')
        return pool,order,len(details),sum(d.get('status')=='qualified' for d in details)>=config['result_limit']
    groups = db.execute('SELECT query_index,body FROM query_cards' + where + ' ORDER BY query_index', key).fetchall()
    if [i for i, _ in groups] != list(range(len(groups))) or len(groups)<3 or (len(groups)>3 and plan.get('strategy')!='staged-v1'):
        raise CollectorProgressError('checkpoint_cards_missing', '须先完成三组搜索卡片')
    cards = [json.loads(raw) for _,raw in groups]
    extra_queries=[]
    if len(groups)>3:
        from .collector_staged_policy import elapsed_seconds
        incomplete=len(groups)<len(_declared_queries(db,key,plan))
        if incomplete and elapsed_seconds(db,key)>=3600:
            _expanded_queries(db,key,cards,allow_prefix=True)
        else:
            extra_queries=_expanded_queries(db,key,cards)
    blocks = [dict(query=plan['queries'][i], observations=cards[i]) for i in range(3)]
    pool, recurrent = ranked_pool(blocks, plan['queries'], plan['platform'])
    if plan.get('strategy') == 'staged-v1':
        from .collector_staged_policy import staged_order, STAGE_SIZE
        repeated, single = staged_order(pool)
        repeated = repeated[:STAGE_SIZE]
        recurrent = repeated + single[:2*STAGE_SIZE-len(repeated)]
        if extra_queries:
            from .collector_staged_policy import TOTAL_DETAILS
            extra_blocks=[dict(query=q,observations=c) for q,c in zip(extra_queries,cards[3:])]
            extra_pool,_=ranked_pool(extra_blocks,extra_queries,plan['platform'],allow_variable=True,allow_short=True)
            extra_repeated,extra_single=staged_order(extra_pool)
            seen=set(recurrent)
            additions=[identity for identity in extra_repeated+extra_single if identity not in seen][:TOTAL_DETAILS-len(recurrent)]
            for identity in additions:
                pool[identity]=extra_pool[identity]
            recurrent += additions
    details = db.execute('SELECT detail_index,body FROM collector_details' + where + ' ORDER BY detail_index', key).fetchall()
    qualified = 0
    for expected, (index, raw) in enumerate(details):
        detail = json.loads(raw)
        if (index != expected or index >= len(recurrent) or detail.get('status') not in ('qualified','rejected')
                or detail.get('identity') != f"{plan['platform']}:{recurrent[index]}"):
            raise CollectorProgressError('detail_index_invalid', '已保存的详情与候选顺序不一致')
        qualified += detail['status'] == 'qualified'
    return pool, recurrent, len(details), qualified >= config['result_limit']


def _expanded_queries(db, key, cards, *, allow_prefix=False):
    if len(cards)<=3:
        return []
    from .collector_staged_policy import load_expansion
    try:
        expansion=load_expansion(db,key,fence=lambda: True)
        queries=expansion['queries'] if expansion else []
        count=len(cards)-3
        if count>len(queries) or (not allow_prefix and count!=len(queries)):
            raise ValueError('Expansion search incomplete')
        queries=queries[:count]
        for index,(query,block) in enumerate(zip(queries,cards[3:]),3):
            if any(card.get('query') != query for card in block): raise ValueError('Expansion query mismatch')
            if len(block)<30:
                meta=db.execute('SELECT query,exhausted FROM query_card_meta WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=?',(*key,index)).fetchone()
                if meta!=(query,1): raise ValueError('Short search is not confirmed exhausted')
        return queries
    except (ValueError, sqlite3.OperationalError) as error:
        raise CollectorProgressError('expansion_checkpoint_invalid',str(error)) from error


def _declared_queries(db,key,plan):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='staged_expansions'").fetchone():
        return list(plan['queries'])
    from .collector_staged_policy import load_expansion
    expansion=load_expansion(db,key,fence=lambda:True)
    return list(plan['queries'])+(expansion['queries'] if expansion else [])


def _initial_candidate_count(db,key,plan):
    rows=db.execute('SELECT body FROM query_cards WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index<3 ORDER BY query_index',key).fetchall()
    blocks=[dict(query=q,observations=json.loads(row[0])) for q,row in zip(plan['queries'],rows)]
    pool,_=ranked_pool(blocks,plan['queries'],plan['platform'])
    from .collector_staged_policy import staged_order, STAGE_SIZE
    repeated,single=staged_order(pool)
    first=min(len(repeated),STAGE_SIZE)
    return first+min(len(single),2*STAGE_SIZE-first)


def checked_detail_identity(broker, url, *, activation_started=None):
    """Only activate a previously reviewed candidate or the next candidate."""
    if not broker.watch_only or not broker.guard_alive(broker.session) or broker.progress_plan is None:
        return None
    plan, config = broker.progress_plan
    key = tuple(broker.challenge[k] for k in ('request_id','plan_digest','lease_epoch'))
    try:
        pid, _ = _identity(url, plan['platform'])
        with closing(sqlite3.connect(broker.store.path)) as db:
            db.execute('BEGIN')
            if plan.get('strategy') in ('card-visual-v3','card-home-v4','shein-home-v1'):
                if not db.execute('SELECT 1 FROM card_v3_meta WHERE request_id=? AND plan_digest=? AND lease_epoch=?',key).fetchone():return None
                queue=[row[0] for row in db.execute('SELECT product_id FROM card_v3_queue WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY ordinal',key)]
                details=[json.loads(row[0]) for row in db.execute('SELECT body FROM card_v3_details WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY ordinal',key)]
                eligible=sum(row.get('status') in ('threshold_met','supplemental') for row in details)
                end=len(details)+(0 if eligible>=30 else 1)
                return f"{plan['platform']}:{pid}" if pid in queue[:end] else None
            _, recurrent, reviewed, finished = _candidate_order(db, key, plan, config)
            if plan.get('strategy')=='staged-v1':
                from .collector_staged_policy import elapsed_seconds
                elapsed=elapsed_seconds(db,key)
                if elapsed>=3600 and not finished:
                    now=time.time();expected=f"{plan['platform']}:{pid}"
                    in_flight=staged_in_flight(db,key,expected,elapsed) or (type(activation_started) in (int,float) and 0<=activation_started<=now and activation_started<=now-elapsed+3600)
                    finished=not in_flight
        end = reviewed + (0 if finished else 1)
        return f"{plan['platform']}:{pid}" if pid in recurrent[:end] else None
    except (KeyError, TypeError, ValueError, sqlite3.Error):
        return None


def verify_checkpoint_evidence(db, challenge, evidence, *, allow_previous_stage=False, staged=False):
    """Staging may only submit the acknowledged observations and detail prefix."""
    key = tuple(challenge[k] for k in ('request_id', 'plan_digest', 'lease_epoch'))
    where = ' WHERE request_id=? AND plan_digest=? AND lease_epoch=?'
    blocks = [json.loads(r[0]) for r in db.execute('SELECT body FROM query_cards' + where + ' ORDER BY query_index', key)]
    details = [json.loads(r[0]) for r in db.execute('SELECT body FROM collector_details' + where + ' ORDER BY detail_index', key)]
    if not isinstance(evidence, dict) or (not staged and len(blocks) != 3) or (staged and not 3<=len(blocks)<=6):
        return False
    if staged and len(blocks)>3:
        try:
            extra=_expanded_queries(db,key,blocks,allow_prefix=True)
        except CollectorProgressError:
            return False
    supplied=evidence.get('queries')
    if not isinstance(supplied,list) or len(supplied)!=len(blocks) or any(not isinstance(block,dict) or set(block)!={'query','observations'} or block['observations']!=cards or (cards and block['query']!=cards[0].get('query')) for block,cards in zip(supplied,blocks)):
        return False
    if staged and len(blocks)>3 and [b['query'] for b in supplied[3:]]!=extra:return False
    if evidence.get('details') == details:
        return True
    # Older extensions staged the entire review atomically without per-detail rows.
    if allow_previous_stage and not details:
        staged = db.execute('SELECT body FROM staged_evidence WHERE request_id=? AND lease_epoch=?',
                            (key[0], key[2])).fetchone()
        return bool(staged and json.loads(staged[0]) == evidence)
    return False

"""Create a broker for one current task and its immutable collector plan."""
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path
from .domain import canonical_hash
from .collector_cli import task_config
from .host_session import worker_records, fence
from .tab_receipt_broker import Broker
from .tab_receipt_store import TabReceiptStore


def can_rebind(record, run):
    if (not isinstance(record,dict) or not isinstance(run,dict) or record.get('retired')
            or record.get('task_type')!='research.bestseller' or run.get('phase')!='host_pending'
            or run.get('pending') or run.get('recovery_pending')): return False
    if record.get('state')=='prepared':
        return not any(record.get(k) for k in ('collector_pending','collector_evidence','collector_artifact_id','candidate_uploads','gallery_manifests','uploads','result'))
    if record.get('state')!='candidate_images_pending' or not str(record.get('collector_artifact_id','')).isdigit() or not isinstance(record.get('collector_evidence'),dict): return False
    pending=record.get('collector_pending')
    if not isinstance(pending,dict) or not isinstance(pending.get('result'),dict): return False
    uploads=pending.get('candidate_uploads',{})
    return isinstance(uploads,dict) and all(isinstance(v,dict) and v.get('state')=='confirmed' and str(v.get('artifact_id','')).isdigit() for v in uploads.values())


def build_native_broker(path, ledger, request_id, query_index, store, client, journal, *, rebind=False, watch_only=False, dedicated_required=False):
    from .native_collector_host import private_config, validate_origin
    try: config=private_config(Path(path).parent/'collector-native.json')
    except (OSError,ValueError): config={}
    anchor_id=config.get('anchor_extension_id')
    configured=config.get('extension_ids',config.get('extension_id'))
    anchor_origin='chrome-extension://'+anchor_id+'/' if isinstance(anchor_id,str) else None
    if not anchor_origin or not validate_origin(anchor_origin,configured):
        anchor_origin=None
    if dedicated_required and not anchor_origin:
        raise ValueError('Dedicated anchor configuration required')
    peer_id=config.get('peer_extension_id')
    peer_origin='chrome-extension://'+peer_id+'/' if isinstance(peer_id,str) else None
    if not peer_origin or not validate_origin(peer_origin,configured) or peer_origin==anchor_origin:peer_origin=None
    def current(task_id=None):
        rows=[r for r in worker_records(journal, task_id=task_id) if r.get('request_id')==request_id and not r.get('done')]
        if len(rows)!=1 or rows[0]['claim'].get('task_type')!='research.bestseller': raise ValueError('Active original task required')
        return rows[0]['claim']
    def renew():
        fence(client,journal,request_id,task_id=claim['id'])
        now=current(claim['id'])
        return now['lease_epoch']==claim['lease_epoch'] and now['input_hash']==claim['input_hash']
    claim=current(); fence(client,journal,request_id,task_id=claim['id'])
    record=store.inspect(request_id); binding=record.get('collector_plan') or {}; signed=dict(binding); digest=signed.pop('digest',None)
    plan=binding.get('plan') or {}; queries=plan.get('queries')
    if dedicated_required and plan.get('platform') == 'shein-us' and not peer_origin:
        raise ValueError('Dedicated SHEIN peer configuration required')
    if (record.get('retired') or record.get('task_type')!='research.bestseller' or not (record.get('state')=='prepared' or (record.get('state')=='candidate_images_pending' and isinstance(record.get('collector_pending'),dict) and isinstance(record['collector_pending'].get('result'),dict)))
            or digest != canonical_hash(signed) or binding.get('request_id')!=request_id
            or binding.get('snapshot_id')!=claim['snapshot_id'] or binding.get('company_id')!=claim['company_id']
            or binding.get('input_hash')!=claim['input_hash'] or plan.get('platform') not in ('mercado-libre-mx','shein-us')
            or not isinstance(queries,list) or len(queries)!=3 or type(query_index) is not int or not 0<=query_index<(9 if plan.get('strategy') in ('visual-v2','card-visual-v3','card-home-v4') else 6 if plan.get('strategy')=='staged-v1' else 3)):
        raise ValueError('Valid signed current plan required')
    if plan.get('strategy') in ('card-visual-v3','card-home-v4'):
        from .card_sales_v3_checkpoints import initialize, seal_query
        with closing(sqlite3.connect(str(ledger))) as db, db:
            db.execute('BEGIN IMMEDIATE')
            initialize(db)
            scope=(request_id,digest,claim['lease_epoch'])
            for index, item in enumerate(queries):
                seal_query(db,scope,index,item,fence=renew)
            rows=db.execute('SELECT query_index,query FROM card_v3_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index',scope).fetchall()
            if [index for index,_ in rows] != list(range(len(rows))) or query_index >= len(rows) or renew() is not True:
                raise ValueError('Current sealed card plan required')
            query=rows[query_index][1]
    elif plan.get('strategy') == 'visual-v2':
        from .visual_v2_checkpoints import initialize, read_queries, seal_queries
        with closing(sqlite3.connect(str(ledger))) as db, db:
            db.execute('BEGIN IMMEDIATE')
            initialize(db)
            scope=(request_id,digest,claim['lease_epoch'])
            declared=read_queries(db,scope)
            if not declared:
                seal_queries(db,scope,queries,fence=renew)
                declared=queries
            if declared[:3] != queries or query_index >= len(declared) or renew() is not True:
                raise ValueError('Current sealed visual plan required')
            query=declared[query_index]
    elif query_index >= 3:
        from .collector_staged_policy import initialize_expansions, load_expansion
        with closing(sqlite3.connect(str(ledger))) as db, db:
            initialize_expansions(db)
            expansion=load_expansion(db,(request_id,digest,claim['lease_epoch']),fence=renew)
        if not expansion or query_index-3 >= len(expansion['queries']):
            raise ValueError('Current sealed expansion required')
        query=expansion['queries'][query_index-3]
    else:
        query=queries[query_index]
    challenge={'version':5 if plan.get('strategy')=='card-home-v4' else 4 if plan.get('strategy')=='card-visual-v3' else 3 if plan.get('strategy')=='visual-v2' else 2 if plan.get('strategy')=='staged-v1' else 1,'request_id':request_id,'nonce':secrets.token_hex(16),'lease_epoch':claim['lease_epoch'],
               'plan_digest':digest,'platform':plan['platform'],'query_index':query_index,'query':query}
    rows=[r for r in worker_records(journal, task_id=claim['id']) if r.get('request_id')==request_id and not r.get('done')]
    if record.get('state')=='candidate_images_pending' and not watch_only and (len(rows)!=1 or not can_rebind(record,rows[0])):
        raise ValueError('Rebind requires reconciled candidate uploads')
    receipts = TabReceiptStore(str(ledger))
    current_binding=receipts.current_binding(request_id)
    if current_binding and (current_binding.get('plan_digest')!=digest or current_binding.get('platform')!=plan['platform']):
        raise ValueError('Existing tab belongs to another plan')
    gallery = None
    downloads = None
    from .research_result_candidates import all_candidates
    if (dedicated_required and record['state']=='candidate_images_pending' and getattr(client,'research_ledger_enabled',False) is True
            and all_candidates(record.get('collector_pending',{}).get('result') or {'candidates':[]})):
        from .gallery_binding import GalleryBinding, gallery_target_reader
        def outstanding():
            runs = [r for r in worker_records(journal, task_id=claim['id']) if r.get('request_id')==request_id and not r.get('done')]
            return runs[0] if len(runs)==1 else None
        gallery = GalleryBinding(gallery_target_reader(store,client,claim,request_id,record,outstanding))
        from .gallery_download import GalleryDownloads
        preview_reader = None
        if plan['platform']=='mercado-libre-mx':
            from .visual_candidate import VisualCandidate
            preview = VisualCandidate(ledger, (request_id,digest,claim['lease_epoch']), plan,
                                      None, claim)
            preview_reader = lambda identity,source,variant: preview.reuse_first_photo(store,identity,source,variant)
        downloads = GalleryDownloads(store,client,claim,Path.home()/'Downloads',preview_reader)
    if query_index and not gallery and not current_binding and plan.get('strategy')!='card-home-v4': raise ValueError('First query tab binding required')
    if watch_only and not gallery:
        if plan.get('strategy')=='card-home-v4':
            from .home_collection_policy import next_home_query
            with closing(sqlite3.connect(str(ledger))) as db:
                scope=(request_id,digest,claim['lease_epoch'])
                all_queries=[row[0] for row in db.execute('SELECT query FROM card_v3_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index',scope)]
                pages_complete=next_home_query(db,scope) is None
                evidence_count=db.execute('SELECT count(*) FROM card_v3_page_evidence WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND page=1',scope).fetchone()[0]
            complete=len(all_queries) if pages_complete and evidence_count==len(all_queries) else 0
        elif plan.get('strategy')=='card-visual-v3':
            from .card_sales_v3_checkpoints import next_page
            with closing(sqlite3.connect(str(ledger))) as db:
                scope=(request_id,digest,claim['lease_epoch'])
                all_queries=[row[0] for row in db.execute('SELECT query FROM card_v3_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=? ORDER BY query_index',scope)]
                pages_complete=next_page(db,scope) is None
            verified=receipts.resume_index(request_id,digest,all_queries,claim['lease_epoch'],card_v3=True)
            complete=len(all_queries) if pages_complete and verified==len(all_queries) else 0
        elif plan.get('strategy')=='visual-v2':
            with closing(sqlite3.connect(str(ledger))) as db:
                from .visual_v2_checkpoints import read_queries
                all_queries=read_queries(db,(request_id,digest,claim['lease_epoch']))
            complete=receipts.next_query_index(request_id,digest,all_queries,claim['lease_epoch'],visual_v2=True)
        elif plan.get('strategy')=='staged-v1':
            all_queries=queries+receipts.sealed_queries(request_id,digest,claim['lease_epoch'],renew)
            complete=receipts.next_query_index(request_id,digest,all_queries,claim['lease_epoch'],staged=True)
        else:
            complete=receipts.next_query_index(request_id,digest,queries,claim['lease_epoch'])
        if not current_binding or rebind or complete < 3: raise ValueError('Complete existing binding required for live session check')
    def reconcile():
        rows=[r for r in worker_records(journal, task_id=claim['id']) if r.get('request_id')==request_id and not r.get('done')]
        return len(rows)==1 and renew() and can_rebind(store.inspect(request_id),rows[0])
    if rebind and not gallery and ((query_index!=0 and plan.get('strategy')!='card-home-v4') or not current_binding or not reconcile()): raise ValueError('Rebind requires a reconciled original request')
    def capture_gallery(event, ensure_current):
        from .candidate_image_upload import capture_native_gallery
        return capture_native_gallery(store, request_id, current(claim['id']), event, ensure_current)

    existing=None if rebind or gallery else current_binding
    broker = Broker(path,ledger,challenge,existing['tab_id'] if existing else None,renew,
                  expected_window_id=existing['window_id'] if existing else None,
                  expected_session=existing['session_id'] if existing else None,
                  reconcile=reconcile if rebind else None, rebind_check=reconcile if existing or gallery else None,watch_only=bool(watch_only or gallery),anchor_origin=anchor_origin,peer_origin=peer_origin,
                  gallery_binding=gallery,gallery_downloads=downloads,gallery_capture=capture_gallery, progress_plan=(plan, task_config(record.get('context', {}).get('snapshot', {}), plan)) if 'result_limit' in plan else None)
    broker.task_id = claim['id']
    if plan.get('strategy') in ('card-visual-v3','card-home-v4'):
        broker.card_v3_store_screenshot=lambda raw:Path(store._file(raw)).name
    if plan.get('strategy')=='card-home-v4':
        def home_expansion_candidates(used):
            from .home_suggestion_bootstrap import available_expansion
            with closing(sqlite3.connect(str(ledger))) as db:
                return [row['text'] for row in available_expansion(store,request_id,current(claim['id']),db,used,fence=renew)]
        broker.home_expansion_candidates=home_expansion_candidates
    if dedicated_required and watch_only and not gallery and plan['platform']=='mercado-libre-mx':
        from .visual_candidate import VisualCandidate
        preview = VisualCandidate(ledger, (request_id,digest,claim['lease_epoch']), plan,
                                  broker.progress_plan[1], claim)
        if plan.get('strategy') == 'visual-v2':
            broker.visual_v2_scope=preview.scope
        elif plan.get('strategy') in ('card-visual-v3','card-home-v4'):
            broker.visual_v3_scope=preview.scope
            broker.visual_v3_verify_photo=lambda filename,sha:preview.photo(store.files,filename,sha)
        broker.visual_photo = lambda b,e: preview.native(b,e,store,Path.home()/'Downloads')
    return broker

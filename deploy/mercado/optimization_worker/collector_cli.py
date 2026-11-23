"""Manual, fenced Chrome collector import; never controls browser or reads Feishu."""
import argparse, base64, copy, hashlib, json, math, os, re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from .client import Client
from .collector_import import convert_evidence, disallowed_term, _CATEGORIES
from .domain import canonical_hash
from .host_session import SessionStore, fence, worker_records


def collector_id(request_id):
    """Map a canonical Odoo UUID injectively to the collector's record-shaped ID."""
    if not isinstance(request_id, str) or not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', request_id):
        raise ValueError('Canonical lowercase UUID request ID required')
    return 'rec' + request_id.replace('-', '')


def read_file(path, cap):
    fd=os.open(str(path), os.O_RDONLY | getattr(os,'O_NOFOLLOW',0))
    try:
        with os.fdopen(fd,'rb') as f:
            data=f.read(cap+1)
        if len(data)>cap: raise ValueError('File exceeds limit')
        return json.loads(data)
    except BaseException:
        raise


def validate_source(record, claim, plan, historical=False):
    snap=record['context']['snapshot']; source_id=plan.get('source_artifact_id')
    source_refs=snap.get('payload',{}).get('source_image_artifact_ids',[])
    if not isinstance(source_refs,list) or not source_refs or not isinstance(source_id,str) or (source_id not in [str(x) for x in source_refs] if historical else source_id != str(source_refs[0])):
        raise ValueError('First source image is required')
    if source_id not in record['images'] or source_id not in record['context']['artifacts']:
        raise ValueError('Original source image artifact is required')
    source=record['context']['artifacts'][source_id]
    if not snap.get('case_id') or not claim.get('company_id') or source.get('case_id') != snap['case_id'] or source.get('company_id') != claim['company_id']:
        raise ValueError('Source image ownership does not match request')
    path=Path(record['images'][source_id])
    if not path.is_file() or path.stat().st_size>8*1024*1024: raise ValueError('Source image unavailable')
    actual=hashlib.sha256(path.read_bytes()).hexdigest()
    if actual!=plan.get('viewed_source_sha256') or actual!=source.get('digest'): raise ValueError('Viewed source image digest mismatch')


def task_config(snapshot, plan):
    """Read immutable task thresholds; old tasks retain their historical defaults."""
    config = snapshot.get('payload', {}).get('collection_config')
    if config is None:
        return {'platform': plan['platform'], 'result_limit': plan['result_limit'],
                'min_sold': 500, 'min_reviews': 50, 'min_rating': 4.1}
    fields = {'platform', 'result_limit', 'min_sold', 'min_reviews', 'min_rating'}
    if not isinstance(config, dict) or set(config) != fields or config['platform'] not in ('shein-us', 'mercado-libre-mx'):
        raise ValueError('Invalid task collection config')
    if config['platform'] != plan['platform'] or config['result_limit'] != plan['result_limit']:
        raise ValueError('Collector plan conflicts with task config')
    if type(config['result_limit']) is not int or config['result_limit'] < 1:
        raise ValueError('Invalid task result limit')
    if any(type(config[k]) is not int or config[k] < 0 for k in ('min_sold', 'min_reviews')):
        raise ValueError('Invalid count threshold')
    rating = config['min_rating']
    if type(rating) not in (int, float) or not math.isfinite(rating) or not 0 <= rating <= 5:
        raise ValueError('Invalid rating threshold')
    return copy.deepcopy(config)


def validate_platform_site(snapshot, platform, site):
    if 'collection_config' in snapshot.get('payload', {}):
        expected = {'shein-us': 'SHEIN_US', 'mercado-libre-mx': 'MLM'}.get(platform)
        if expected is None or site != expected:
            raise ValueError('Platform and site mismatch')
    elif site != 'MLM':
        raise ValueError('Legacy request is not a Mexico research task')


def validate_bound_profile(snapshot, plan, historical=False):
    if 'collection_config' not in snapshot.get('payload', {}):
        if 'visual_profile' in plan:
            raise ValueError('Legacy plan must use legacy contract')
        return
    if historical and 'visual_profile' not in plan:
        return
    from .visual_profile import validate_profile
    try:
        profile = validate_profile(plan.get('visual_profile'), plan['platform'])
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError('Invalid source visual profile') from exc
    if profile != plan['visual_profile'] or profile['category_code'] != plan['category'] or profile['query_seeds'] != plan['queries']:
        raise ValueError('Plan must use exact visual profile category and queries')


def bind_plan(store, request_id, claim, plan):
    required={'platform','queries','category','result_limit','source_artifact_id','viewed_source_sha256'}
    if not isinstance(plan,dict) or set(plan) not in (required, required | {'visual_profile'}) or plan['platform'] not in ('shein-us','mercado-libre-mx') or not isinstance(plan['queries'],list) or len(plan['queries'])!=3 or any(not isinstance(x,str) or not x.strip() or len(x)>200 or disallowed_term(x) for x in plan['queries']) or len(set(plan['queries']))!=3 or plan['category'] not in _CATEGORIES or type(plan['result_limit'])!=int or plan['result_limit']<1:
        raise ValueError('Invalid collector plan')
    if claim['task_type']!='research.bestseller':raise ValueError('Research task required')
    with store._db() as db:
        record=store._get(db,request_id)
        snap=record['context']['snapshot']
        if record.get('retired') or record['state']!='prepared' or record['task_type']!='research.bestseller' or snap['snapshot_id']!=claim['snapshot_id'] or record['payload']!=claim['payload']:
            raise ValueError('Active original research request required')
        existing = record.get('collector_plan')
        if existing:
            signed = dict(existing); digest = signed.pop('digest', None)
            if (canonical_hash(signed) != digest or existing.get('protocol_version', 1) not in (1, 2)
                    or existing.get('request_id') != request_id or existing.get('company_id') != claim['company_id']
                    or existing.get('case_id') != snap['case_id'] or existing.get('snapshot_id') != claim['snapshot_id']
                    or existing.get('input_hash') != claim['input_hash'] or existing.get('collector_id') != collector_id(request_id)
                    or existing.get('plan') != plan):
                raise ValueError('Collector plan is immutable')
            historical = existing.get('protocol_version', 1) == 1
            validate_source(record, claim, plan, historical=historical)
            validate_bound_profile(snap, plan, historical=historical)
            config = task_config(snap, plan)
            if ('collection_config' not in existing and 'collection_config' in snap.get('payload', {})) or existing.get('collection_config', config) != config:
                raise ValueError('Task collection config changed')
            validate_platform_site(snap, config['platform'], claim['payload'].get('site_id'))
            return digest
        validate_source(record,claim,plan)
        validate_bound_profile(snap, plan)
        config=task_config(snap,plan)
        validate_platform_site(snap, config['platform'], claim['payload'].get('site_id'))
        binding={'protocol_version':2,'collection_config':config,'company_id':claim['company_id'],'case_id':snap['case_id'],'snapshot_id':claim['snapshot_id'],'request_id':request_id,'collector_id':collector_id(request_id),'input_hash':claim['input_hash'],'plan':copy.deepcopy(plan)}
        if record.get('collector_plan') and 'collection_config' not in record['collector_plan'] and 'collection_config' not in snap.get('payload', {}):
            binding.pop('collection_config')
        binding['digest']=canonical_hash(binding)
        if record.get('collector_plan') and record['collector_plan']!=binding:raise ValueError('Collector plan is immutable')
        record['collector_plan']=binding;store._put(db,record)
        return binding['digest']


def import_evidence(store, request_id, claim, data, observed_at, client=None, fence_request=None, *, tab_receipts=None):
    if client is None or fence_request is None:raise ValueError('Artifact upload client and lease fence required')
    fence_request()
    with store._db() as db:
        record=store._get(db,request_id); binding=record.get('collector_plan')
        if record.get('retired') or record['state']!='prepared' or not binding or binding['company_id']!=claim['company_id'] or binding['snapshot_id']!=claim['snapshot_id'] or binding['request_id']!=request_id or binding['input_hash']!=claim['input_hash'] or record['payload']!=claim['payload'] or binding['case_id']!=record['context']['snapshot']['case_id']:
            raise ValueError('Original collector plan and active request required')
        original=dict(binding); digest=original.pop('digest')
        if canonical_hash(original)!=digest:raise ValueError('Collector plan changed')
        if binding.get('collector_id') != collector_id(request_id): raise ValueError('Collector ID mapping mismatch')
        plan=binding['plan']
        historical = binding.get('protocol_version', 1) == 1
        if binding.get('protocol_version', 1) not in (1, 2): raise ValueError('Unsupported plan version')
        validate_source(record,claim,plan,historical=historical)
        validate_bound_profile(record['context']['snapshot'], plan, historical=historical)
        config=task_config(record['context']['snapshot'],plan)
        if ('collection_config' not in binding and 'collection_config' in record['context']['snapshot'].get('payload', {})) or config != binding.get('collection_config', config): raise ValueError('Task collection config changed')
        if tab_receipts is None or tab_receipts.verified_queries(request_id, digest, plan['queries'], claim.get('lease_epoch')) is not True:
            raise ValueError('Verified query receipts required')
        result=convert_evidence(data,binding['collector_id'],plan['platform'],plan['queries'],plan['category'],observed_at,result_limit=config['result_limit'],min_sold=config['min_sold'],min_reviews=config['min_reviews'],min_rating=config['min_rating'])
        raw=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        if len(raw)>5*1024*1024:raise ValueError('Evidence exceeds bound')
        record.update(state='collector_upload_unknown',collector_pending={'data':copy.deepcopy(data),'result':result,'digest':hashlib.sha256(raw).hexdigest(),'ledger_v1':getattr(client,'research_ledger_enabled',False) is True})
        store._put(db,record)
    from .research_upload import upload
    receipt=upload(client,claim,request_id,'evidence','',binding['case_id'],raw,'application/json')
    if receipt.get('digest')!=hashlib.sha256(raw).hexdigest():raise ValueError('Artifact receipt digest mismatch')
    with store._db() as db:
        record=store._get(db,request_id)
        if record['state']!='collector_upload_unknown' or record['collector_pending']['digest']!=receipt['digest']:raise ValueError('Upload intent changed')
        record['collector_pending']['artifact_id']=str(receipt['artifact_id']);store._put(db,record)
    fence_request()
    return reconcile_evidence(store,request_id,claim,str(receipt['artifact_id']),client)


def reconcile_evidence(store,request_id,claim,artifact_id,client):
    if not artifact_id:
        from .research_upload import lookup_receipt
        with store._db() as db:
            pending=store._get(db,request_id).get('collector_pending') or {}
            if pending.get('ledger_v1') is not True: raise ValueError('Legacy upload outcome remains unknown')
            digest=pending.get('digest')
        artifact_id=lookup_receipt(client,claim,request_id,'evidence','',digest)
    artifact=client.artifact(artifact_id)
    with store._db() as db:
        record=store._get(db,request_id);binding=record.get('collector_plan') or {}; pending=record.get('collector_pending') or {}
        if record.get('retired') or record['state']!='collector_upload_unknown' or binding.get('request_id')!=request_id or binding.get('company_id')!=claim['company_id'] or binding.get('snapshot_id')!=claim['snapshot_id'] or binding.get('input_hash')!=claim['input_hash'] or artifact.get('case_id')!=binding.get('case_id') or artifact.get('company_id')!=binding.get('company_id') or artifact.get('mime_type')!='application/json' or artifact.get('digest')!=pending.get('digest'):
            raise ValueError('Artifact receipt is not bound to the original request')
        original=dict(binding); digest=original.pop('digest',None)
        if canonical_hash(original)!=digest or binding.get('case_id')!=record['context']['snapshot'].get('case_id') or record.get('payload')!=claim.get('payload'):raise ValueError('Collector plan changed')
        historical = binding.get('protocol_version', 1) == 1
        if binding.get('protocol_version', 1) not in (1, 2): raise ValueError('Unsupported plan version')
        validate_source(record,claim,binding['plan'],historical=historical)
        validate_bound_profile(record['context']['snapshot'], binding['plan'], historical=historical)
        config=task_config(record['context']['snapshot'],binding['plan'])
        if ('collection_config' not in binding and 'collection_config' in record['context']['snapshot'].get('payload', {})) or config != binding.get('collection_config',config): raise ValueError('Task collection config changed')
        raw=base64.b64decode(artifact['data'],validate=True)
        if hashlib.sha256(raw).hexdigest()!=pending['digest'] or json.loads(raw)!=pending['data']:raise ValueError('Artifact content mismatch')
        result=pending['result']; result['evidence'][0]['source']='odoo-artifact:'+str(artifact_id)+':sha256:'+pending['digest']
        record.update(collector_artifact_id=str(artifact_id),collector_evidence=pending['data'],capabilities={'runtime':'codex','browser':'selected-chrome-collector','vision':'view_image','provenance':'manual visible-page collector import'})
        if pending.get('ledger_v1') is True:
            pending['artifact_id']=str(artifact_id)
        if pending.get('ledger_v1') is True or ('collection_config' in record['context']['snapshot'].get('payload', {}) and result['candidates']):
            record['state']='candidate_images_pending'
            pending['result']=result
        else:
            record.update(state='resolved',result=result)
            del record['collector_pending']
        store._put(db,record)
        return len(result['candidates'])


def require_cli_operation_allowed(command, scoped, record=None):
    persisted=isinstance(record,dict) and (record.get('collector_pending') or {}).get('ledger_v1') is True
    if (scoped or persisted) and command not in ('reconcile','reconcile-image'):
        raise ValueError('Scoped operations require the guarded research bridge')


def main():
    p=argparse.ArgumentParser(description='Import manually collected Chrome evidence into the original fenced host request')
    p.add_argument('command',choices=('plan','submit','reconcile','image','reconcile-image','finish-images','gallery-manifest'));p.add_argument('--request-id',required=True);p.add_argument('--file');p.add_argument('--artifact-id');p.add_argument('--identity');p.add_argument('--screenshot');p.add_argument('--image-source');p.add_argument('--order',type=int);p.add_argument('--observed-at',help='UTC time of actual visual observation; required for submit')
    args=p.parse_args();client=Client(os.environ.get('ODOO_OPTIMIZATION_URL','http://127.0.0.1:8069'),os.environ.get('ODOO_API_KEY',''),research_ledger_enabled=os.environ.get('OPTIMIZATION_RESEARCH_LEDGER_V1') == '1')
    require_cli_operation_allowed(args.command,client.research_ledger_enabled)
    store=SessionStore(os.environ.get('OPTIMIZATION_HOST_SESSION','~/.local/state/optimization-worker/host.sqlite'),client.identity)
    require_cli_operation_allowed(args.command,client.research_ledger_enabled,store.inspect(args.request_id))
    journal=os.environ.get('OPTIMIZATION_JOURNAL','~/.local/state/optimization-worker/journal.sqlite')
    records=[r for r in worker_records(journal) if r.get('request_id')==args.request_id and not r.get('done')]
    if len(records)!=1: raise ValueError('Exactly one active worker request required')
    claim=records[0]['claim'];fence(client,journal,args.request_id)
    if args.command in ('image','reconcile-image','finish-images','gallery-manifest'):
        from .candidate_image_upload import send, reconcile, finish, bind_gallery
        renew=lambda:fence(client,journal,args.request_id)
        if args.command=='gallery-manifest':
            result=bind_gallery(store,args.request_id,claim,args.identity,read_file(args.file,65536),args.screenshot,renew)
        elif args.command=='image':
            result=send(store,args.request_id,claim,args.identity,args.file,args.screenshot,client,renew,source=args.image_source,order=args.order)
        elif args.command=='reconcile-image':
            result=reconcile(store,args.request_id,claim,args.identity,args.artifact_id,client,renew,order=args.order)
        else:
            result=finish(store,args.request_id,claim,renew,client=client)
        print(json.dumps({'accepted':True,'result':result},ensure_ascii=False));return
    if args.command=='reconcile':
        if not args.artifact_id:raise ValueError('Existing artifact ID required')
        print(json.dumps({'accepted':True,'candidates':reconcile_evidence(store,args.request_id,claim,args.artifact_id,client)}));return
    if not args.file:raise ValueError('Input file required')
    value=read_file(args.file, 65536 if args.command=='plan' else 5*1024*1024)
    if args.command=='plan': print(json.dumps({'plan_digest':bind_plan(store,args.request_id,claim,value),'collector_id':collector_id(args.request_id)}))
    else:
        if not args.observed_at: raise ValueError('Actual observation time required')
        observed=datetime.fromisoformat(args.observed_at.replace('Z','+00:00'))
        now=datetime.now(timezone.utc)
        if observed.utcoffset()!=timedelta(0) or not now-timedelta(hours=24)<=observed<=now: raise ValueError('Observation time must be recent UTC')
        fence(client,journal,args.request_id)
        count=import_evidence(store,args.request_id,claim,value,observed.isoformat(),client,lambda:fence(client,journal,args.request_id))
        print(json.dumps({'accepted':True,'candidates':count}))

if __name__=='__main__':main()

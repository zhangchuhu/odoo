"""Fenced, per-candidate native-image upload with conservative lost-receipt recovery."""
import base64
import copy
import hashlib
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from PIL import Image
from .host_session import _raster
from .domain import canonical_hash
from .research_result_candidates import all_candidates


def _active(record, claim, identity):
    binding = record.get('collector_plan') or {}
    if (record.get('retired') or record.get('state') != 'candidate_images_pending' or
        any(binding.get(k) != claim.get(k) for k in ('company_id', 'snapshot_id', 'input_hash'))):
        raise ValueError('Active original research request required')
    original=dict(binding); digest=original.pop('digest',None)
    if digest != canonical_hash(original) or binding.get('request_id') != record.get('request_id'):
        raise ValueError('Collector plan identity changed')
    result = record['collector_pending']['result']
    matches = [c for c in all_candidates(result) if c['platform'] + ':' + c['item_id'] == identity]
    if len(matches) != 1: raise ValueError('Qualified candidate identity required')
    return binding, matches[0]


def _native_manifest(candidate, platform, page_url, manifest):
    from .collector_import import _identity
    from .collector_progress import CollectorProgressError
    if (not isinstance(manifest, dict) or set(manifest) != {'detail_url', 'images'}
            or platform not in ('mercado-libre-mx', 'shein-us')):
        raise CollectorProgressError('gallery_capture_invalid', '需要当前商品详情网址和完整的有序图库')
    allowed_detail = {'us.shein.com'} if platform == 'shein-us' else {
        'www.mercadolibre.com.mx', 'articulo.mercadolibre.com.mx'}
    for actual in (page_url, manifest['detail_url'], candidate['url']):
        try:
            pid, _ = _identity(actual, platform)
            if pid != candidate['item_id'] or urlsplit(actual).hostname not in allowed_detail:
                raise ValueError()
        except (TypeError, ValueError):
            raise CollectorProgressError('gallery_candidate_invalid', '详情页与已核验的候选商品不一致') from None
    urls = manifest['images']
    if not isinstance(urls, list) or not 1 <= len(urls) <= 100:
        raise CollectorProgressError('gallery_image_invalid', '图库须包含 1 到 100 张有序图片')
    cdns = ('mlstatic.com',) if platform == 'mercado-libre-mx' else ('ltwebstatic.com', 'sheincdn.com')
    for url in urls:
        try:
            if not isinstance(url, str) or not url or len(url) > 2048 or re.search(r'[\x00-\x20\x7f\\]', url):
                raise ValueError()
            parsed = urlsplit(url)
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                    or parsed.password is not None or parsed.port not in (None, 443) or parsed.fragment
                    or not parsed.path.strip('/') or not any(parsed.hostname == host or parsed.hostname.endswith('.' + host) for host in cdns)):
                raise ValueError()
        except ValueError:
            raise CollectorProgressError('gallery_image_invalid', '图片网址不属于当前平台的图片服务') from None
    if len(set(urls)) != len(urls):
        raise CollectorProgressError('gallery_image_invalid', '图库含有重复的图片网址')
    return {'detail_url': candidate['url'], 'images': urls}


def capture_native_gallery(store, request_id, claim, event, ensure_current):
    from .collector_progress import CollectorProgressError
    if not isinstance(event.get('detail_url'), str) or not event['detail_url']:
        raise CollectorProgressError('gallery_candidate_invalid', '需提供已打开的商品详情网址')
    variant = event.get('variant')
    if 'variant' in event and (not isinstance(variant,str) or not 1 <= len(variant) <= 3500):
        raise CollectorProgressError('gallery_candidate_invalid','规格凭证无效')
    encoded = event.get('screenshot')
    try:
        if not isinstance(encoded, str) or not 0 < len(encoded) < 900000:
            raise ValueError()
        raw = base64.b64decode(encoded, validate=True)
        if not raw.startswith(b'\xff\xd8\xff'):
            raise ValueError()
    except ValueError:
        raise CollectorProgressError('gallery_screenshot_invalid', '需提供该详情页的 JPEG 截图') from None
    with tempfile.TemporaryDirectory(prefix='collector-gallery-') as directory:
        path = Path(directory) / 'page.jpg'
        path.write_bytes(raw)
        result = bind_gallery(store, request_id, claim, event['identity'], event['manifest'], path,
                              ensure_current, observed_detail_url=event['detail_url'], variant=variant)
    return {'ok': True, 'identity': event['identity'], **result}


def bind_gallery(store, request_id, claim, identity, manifest, screenshot, fence_request, *, observed_detail_url=None, variant=None):
    from .collector_progress import CollectorProgressError
    from .research_return_bridge import next_gallery_step
    native = observed_detail_url is not None
    try:
        raw, mime, _, _ = _raster(screenshot)
        if native and mime != 'image/jpeg':
            raise ValueError()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        if native:
            raise CollectorProgressError('gallery_screenshot_invalid', '详情页截图不是可读取的 JPEG 图片') from exc
        raise
    fence_request()
    with store._db() as db:
        record = store._get(db, request_id)
        try:
            binding, candidate = _active(record, claim, identity)
        except ValueError as exc:
            if native:
                raise CollectorProgressError('gallery_candidate_invalid', '该候选或请求状态与当前任务不匹配') from exc
            raise
        if native:
            platform = binding.get('plan', {}).get('platform')
            evidence_id = record.get('collector_artifact_id')
            if (not str(evidence_id).isdigit() or record['collector_pending'].get('artifact_id') != str(evidence_id)
                    or not isinstance(record.get('collector_evidence'), dict) or platform != candidate['platform']):
                raise CollectorProgressError('gallery_phase_invalid', '须先确认本次候选证据的入库回执')
            manifest = _native_manifest(candidate, platform, observed_detail_url, manifest)
            existing = record['collector_pending'].get('gallery_manifests', {}).get(identity)
            if existing:
                if existing.get('detail_url') != manifest['detail_url'] or existing.get('images') != manifest['images']:
                    raise CollectorProgressError('gallery_manifest_conflict', '已保存的图库顺序或图片网址不同')
                saved = Path(existing['screenshot_path'])
                digest = existing['screenshot_sha256']
                if (not saved.is_file() or saved.parent != store.files or saved.stem != digest
                        or hashlib.sha256(saved.read_bytes()).hexdigest() != digest):
                    raise CollectorProgressError('gallery_screenshot_invalid', '已保存的原截图与回执不一致')
                return {'image_count': len(manifest['images']), 'screenshot_sha256': digest}
            step = next_gallery_step(record)
            if not step or step.get('stage') != 'manifest' or step.get('identity') != identity:
                raise CollectorProgressError('gallery_phase_invalid', '当前步骤尚未要求采集该候选的图库')

        if not isinstance(manifest,dict) or set(manifest)!={'detail_url','images'} or manifest['detail_url'] != candidate['url']:
            raise ValueError('Manifest must identify the verified candidate detail')
        urls=manifest['images']
        if not isinstance(urls,list) or not urls or len(set(urls)) != len(urls):
            raise ValueError('Ordered unique gallery image URLs required')
        for url in urls:
            if not isinstance(url,str) or len(url)>2048: raise ValueError('Invalid gallery image URL')
            parsed=urlsplit(url)
            if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('HTTPS gallery image URLs required')
        manifests=record['collector_pending'].setdefault('gallery_manifests',{})
        value={'detail_url':candidate['url'],'images':copy.deepcopy(urls),'screenshot_path':store._file(raw),'screenshot_sha256':hashlib.sha256(raw).hexdigest()}
        existing = manifests.get(identity)
        if variant is not None and (existing is None or 'variant' in existing):
            value['variant'] = variant
        if existing is not None and existing != value:
            raise ValueError('Gallery manifest is immutable')
        if identity in record['collector_pending'].get('candidate_uploads',{}): raise ValueError('Legacy upload already started')
        manifests[identity]=value; store._put(db,record)
    if native:
        return {'image_count': len(urls), 'screenshot_sha256': value['screenshot_sha256']}
    return len(urls)


def send(store, request_id, claim, identity, path, screenshot, client, fence_request, *, source=None, order=None):
    fence_request()
    raw, mime, dimensions, _ = _raster(path)
    screen_raw, _, _, _ = _raster(screenshot)
    digest = hashlib.sha256(raw).hexdigest()
    with store._db() as db:
        r = store._get(db, request_id); binding, candidate = _active(r, claim, identity)
        manifest=r['collector_pending'].get('gallery_manifests',{}).get(identity)
        if manifest:
            if type(order) is not int or not 0 <= order < len(manifest['images']) or source != manifest['images'][order]: raise ValueError('Image must match ordered gallery manifest')
            key=identity+':'+str(order)
        else:
            if order not in (None,0): raise ValueError('Legacy image order must be zero')
            key=identity
        parsed = urlsplit(source or '')
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or (not manifest and source != candidate.get('image_url')):
            raise ValueError('Candidate observed thumbnail URL required')
        uploads = r['collector_pending'].setdefault('candidate_uploads', {})
        if key in uploads: raise ValueError('Image upload already claimed; reconcile original')
        uploads[key] = {'order':order if manifest else 0, 'state':'unknown', 'digest':digest, 'mime_type':mime,
            'width':dimensions[0], 'height':dimensions[1], 'source':source,
            'image_path':store._file(raw), 'screenshot_path':store._file(screen_raw),
            'screenshot_sha256':hashlib.sha256(screen_raw).hexdigest(), 'detail_url':candidate['url'], 'ledger_v1':getattr(client,'research_ledger_enabled',False) is True}
        store._put(db,r)
    from .research_upload import upload
    receipt = upload(client, claim, request_id, 'image', key, binding['case_id'], raw, mime)
    if receipt.get('digest') != digest or not str(receipt.get('artifact_id','')).isdigit():
        raise ValueError('Unverifiable image receipt; reconcile original')
    with store._db() as db:
        current = store._get(db, request_id)
        pending = current.get('collector_pending', {}).get('candidate_uploads', {}).get(key)
        if not pending or pending['state'] != 'unknown' or pending['digest'] != digest:
            raise ValueError('Image upload intent changed')
        pending['artifact_id'] = str(receipt['artifact_id'])
        store._put(db,current)
    return reconcile(store, request_id, claim, identity, str(receipt['artifact_id']), client, fence_request, order=order)


def reconcile(store, request_id, claim, identity, artifact_id, client, fence_request, *, order=None):
    fence_request()
    if not artifact_id:
        from .research_upload import lookup_receipt
        with store._db() as db:
            record=store._get(db,request_id); _active(record,claim,identity)
            manifest=record['collector_pending'].get('gallery_manifests',{}).get(identity)
            if manifest and (type(order) is not int or not 0 <= order < len(manifest['images'])): raise ValueError('Gallery order required')
            if not manifest and order not in (None,0): raise ValueError('Invalid legacy order')
            key=identity+':'+str(order) if manifest else identity
            pending=record['collector_pending'].get('candidate_uploads',{}).get(key) or {}
            if pending.get('ledger_v1') is not True: raise ValueError('Legacy upload outcome remains unknown')
            digest=pending.get('digest')
        artifact_id=lookup_receipt(client,claim,request_id,'image',key,digest)
    artifact = client.artifact(artifact_id)
    with store._db() as db:
        r = store._get(db,request_id); binding,candidate = _active(r,claim,identity)
        manifest=r['collector_pending'].get('gallery_manifests',{}).get(identity)
        if manifest and (type(order) is not int or not 0 <= order < len(manifest['images'])): raise ValueError('Gallery order required')
        if not manifest and order not in (None,0): raise ValueError('Invalid legacy order')
        key=identity+':'+str(order) if manifest else identity
        pending = r['collector_pending'].get('candidate_uploads',{}).get(key)
        if manifest and pending and pending['source'] != manifest['images'][order]: raise ValueError('Manifest source changed')
        if not pending or pending['state'] not in ('unknown','confirmed'): raise ValueError('No original upload intent')
        if pending.get('artifact_id') and pending['artifact_id'] != str(artifact_id): raise ValueError('Artifact receipt differs from original')
        raw = base64.b64decode(artifact.get('data',''),validate=True)
        if (artifact.get('case_id') != binding['case_id'] or artifact.get('company_id') != binding['company_id'] or
            artifact.get('digest') != pending['digest'] or artifact.get('mime_type') != pending['mime_type'] or
            hashlib.sha256(raw).hexdigest() != pending['digest']):
            raise ValueError('Image artifact does not match original intent')
        image = {'artifact_id':str(artifact_id),'sha256':pending['digest'],'mime_type':pending['mime_type'],
            'width':pending['width'],'height':pending['height'],'source':pending['source'],
            'image_id':f"{candidate['item_id']}:{order}" if manifest else candidate['item_id'],'order':order if manifest else 0,'role':'main' if not manifest or order==0 else 'gallery','evidence_ids':['collector']}
        if manifest:
            images=candidate.setdefault('images',[])
            if pending['state']=='confirmed' and not any(x==image for x in images): raise ValueError('Confirmed image is immutable')
            images[:]=sorted([x for x in images if x['order'] != order]+[copy.deepcopy(image)],key=lambda x:x['order'])
            candidate['gallery_image_urls']=copy.deepcopy(manifest['images'])
        else:
            if pending['state'] == 'confirmed' and candidate.get('image') != image: raise ValueError('Confirmed image is immutable')
            candidate['image']=copy.deepcopy(image)
        pending['state']='confirmed'; pending['artifact_id']=str(artifact_id)
        store._put(db,r)
    return image


def finish(store, request_id, claim, fence_request, *, require_manifest=False, client=None):
    fence_request()
    with store._db() as db:
        r=store._get(db,request_id)
        binding=r.get('collector_plan') or {}
        if r.get('retired') or r.get('state') != 'candidate_images_pending' or any(binding.get(k) != claim.get(k) for k in ('company_id','snapshot_id','input_hash')):
            raise ValueError('Active original research request required')
        original=dict(binding); digest=original.pop('digest',None)
        if digest != canonical_hash(original) or binding.get('request_id') != request_id:
            raise ValueError('Collector plan identity changed')
        result=r['collector_pending']['result']; uploads=r['collector_pending'].get('candidate_uploads',{})
        manifests=r['collector_pending'].get('gallery_manifests',{})
        for c in all_candidates(result):
            identity=c['platform']+':'+c['item_id']; manifest=manifests.get(identity)
            if require_manifest:
                if not manifest or manifest.get('detail_url') != c.get('url') or not manifest.get('images'):
                    raise ValueError('Complete candidate gallery manifest required')
                screenshot_raw, _, _, _ = _raster(manifest.get('screenshot_path'))
                if hashlib.sha256(screenshot_raw).hexdigest() != manifest.get('screenshot_sha256'):
                    raise ValueError('Gallery screenshot digest mismatch')
                for i, image in enumerate(c.get('images', [])):
                    receipt = uploads.get(identity+':'+str(i), {})
                    if (receipt.get('state') != 'confirmed' or not receipt.get('artifact_id') or
                        str(image.get('artifact_id')) != str(receipt.get('artifact_id')) or
                        image.get('sha256') != receipt.get('digest') or image.get('source') != receipt.get('source') or
                        image.get('order') != receipt.get('order')):
                        raise ValueError('Gallery image receipt mismatch')
            if manifest:
                images=c.get('images',[])
                if len(images)!=len(manifest['images']) or [x.get('source') for x in images]!=manifest['images'] or [x.get('order') for x in images]!=list(range(len(images))) or any(uploads.get(identity+':'+str(i),{}).get('state')!='confirmed' for i in range(len(images))): raise ValueError('Every gallery image must be confirmed in order')
            elif uploads.get(identity,{}).get('state')!='confirmed' or not c.get('image'): raise ValueError('Every candidate image must be confirmed')
        if r['collector_pending'].get('ledger_v1') is True and (client is None or getattr(client,'research_ledger_enabled',False) is not True):
            raise ValueError('Scoped research client required to seal original intents')
        if client is not None and getattr(client,'research_ledger_enabled',False) is True:
            from .research_upload import seal_manifest
            fence_request()
            seal_manifest(client,claim,request_id,r['collector_pending'],uploads)
        r.update(state='resolved',result=result,candidate_image_receipts=copy.deepcopy(uploads))
        del r['collector_pending']
        store._put(db,r)
        return len(all_candidates(result))

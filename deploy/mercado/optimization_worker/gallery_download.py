"""Read only Chrome's preassigned local file for the next verified gallery original."""
import hashlib
import os
from contextlib import ExitStack, closing
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import tempfile
from urllib.parse import urlsplit

from .candidate_image_upload import send
from .client import APIError
from .collector_progress import CollectorProgressError
from .domain import canonical_hash
from .research_return_bridge import next_gallery_step


def fail(code, message):
    raise CollectorProgressError(code,message)


def read_slot(root, parts, expected_size=None):
    """Open each component relative to its held parent, including the final regular file."""
    maximum = 8 * 1024 * 1024
    with ExitStack() as stack:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
        parent = os.open(root,flags)
        stack.callback(os.close,parent)
        if os.fstat(parent).st_uid != os.getuid():
            raise ValueError('Foreign file owner')
        for directory in parts[:-1]:
            parent = os.open(directory,flags,dir_fd=parent)
            stack.callback(os.close,parent)
            info = os.fstat(parent)
            if info.st_uid!=os.getuid() or info.st_mode & 0o022:
                raise ValueError('Unsafe download directory')
        fd = os.open(parts[-1],os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,dir_fd=parent)
        with os.fdopen(fd,'rb') as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid!=os.getuid() or before.st_nlink!=1
                    or not 0<before.st_size<=maximum or (expected_size is not None and expected_size!=before.st_size)):
                raise ValueError('Invalid completed file')
            raw = stream.read(maximum+1)
            after = os.fstat(stream.fileno())
            if (len(raw)!=before.st_size or before.st_size!=after.st_size or before.st_mtime_ns!=after.st_mtime_ns
                    or before.st_ctime_ns!=after.st_ctime_ns or after.st_nlink!=1):
                raise ValueError('File changed during read')
            return raw


def supported_url(url,platform):
    hosts = ('mlstatic.com',) if platform=='mercado-libre-mx' else ('ltwebstatic.com','sheincdn.com')
    try:
        if not isinstance(url,str) or len(url)>2048 or re.search(r'[\x00-\x20\x7f\\]',url):
            return False
        p=urlsplit(url)
        return (p.scheme=='https' and p.username is None and p.password is None and p.port in (None,443)
                and not p.fragment and p.path.strip('/') and p.hostname and
                any(p.hostname==h or p.hostname.endswith('.'+h) for h in hosts))
    except ValueError:
        return False


class GalleryDownloads:
    def __init__(self,store,client,claim,root,preview_reader=None):
        self.store,self.client,self.claim,self.root = store,client,claim,Path(root)
        self.preview_reader = preview_reader

    def slot(self,broker,identity,order,source):
        c = broker.challenge
        key = canonical_hash(dict(request=c['request_id'],plan=c['plan_digest'],epoch=c['lease_epoch'],
                                  platform=c['platform'],tab_proof=list(broker.gallery_binding.proven_tab),
                                  session=broker.session,identity=identity,order=order,source=source))
        with closing(sqlite3.connect(broker.store.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS gallery_download_slots (slot TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE)')
            db.execute('INSERT OR IGNORE INTO gallery_download_slots VALUES (?,?)',(key,secrets.token_hex(16)))
            token = db.execute('SELECT token FROM gallery_download_slots WHERE slot=?',(key,)).fetchone()[0]
        suffix = Path(urlsplit(source).path).suffix.lower()
        suffix = suffix if suffix in ('.png','.jpg','.jpeg','.webp') else '.jpg'
        return token,('Odoo-Research',c['request_id'],str(c['lease_epoch']),token+suffix)

    def handle(self,broker,event):
        saved = event['operation']=='gallery_download_saved'
        fields = {'operation','request_id','window_id','tab_id','session_id','native_origin','identity'}
        reuse_requested = not saved and event.get('reuse_preview') is True
        if reuse_requested:
            fields.add('reuse_preview')
        if saved:
            fields.update(('order','source','filename','token','download_id','final_url','size'))
        gallery = broker.gallery_binding
        c = broker.challenge
        origin = broker.anchor_origin if c['platform']=='mercado-libre-mx' else broker.peer_origin
        if (set(event)!=fields or not origin or event['native_origin']!=origin
                or not re.fullmatch(r'[0-9a-fA-F-]{36}',c['request_id']) or event['request_id']!=c['request_id']
                or type(event['window_id']) is not int or type(event['tab_id']) is not int
                or event['window_id']!=broker.window or event['tab_id']!=broker.tab or event['session_id']!=broker.session
                or not gallery or not gallery.confirmed or gallery.confirmed[0]!=event['identity']):
            fail('gallery_binding_invalid','当前任务标签尚未核验此候选')
        def live():
            if not gallery.tab_matches(broker) or not broker._tab_guard_connected(broker.session) or not broker.fence():
                fail('checkpoint_guard_pending','原任务标签守护已失效')
        live()
        record = self.store.inspect(c['request_id'])
        pending = record.get('collector_pending') or {}
        manifest = pending.get('gallery_manifests',{}).get(event['identity'])
        if (record.get('state')!='candidate_images_pending' or record.get('retired') or not manifest or not manifest.get('images')
                or pending.get('ledger_v1') is not True or pending.get('artifact_id')!=record.get('collector_artifact_id')):
            fail('gallery_phase_invalid','本候选尚无已确认的图库清单')
        uploads = pending.get('candidate_uploads',{})
        if (not isinstance(uploads,dict) or any(not isinstance(u,dict) or u.get('state')!='confirmed'
                or u.get('ledger_v1') is not True or not str(u.get('artifact_id','')).isdigit() for u in uploads.values())):
            fail('gallery_download_reconcile','有图片上传结果待核对')
        identity = event['identity']
        images = manifest['images']
        if not saved and all(uploads.get(f'{identity}:{i}',{}).get('source')==source for i,source in enumerate(images)):
            return dict(ok=True,identity=identity,stage='complete')
        step = next_gallery_step(record)
        if not step or step.get('stage')!='image' or step.get('identity')!=identity or not gallery.ready(broker):
            fail('gallery_phase_invalid','当前候选或图库顺序已变化')
        order,source = step['order'],step['source']
        if not supported_url(source,c['platform']):
            fail('gallery_image_invalid','图库原图来源未通过平台核验')
        try:
            if reuse_requested and order == 0 and manifest.get('variant') and self.preview_reader:
                raw = self.preview_reader(identity,source,manifest['variant'])
            else:
                raw = None
            reused = raw is not None
            if not reused:
                token,parts = self.slot(broker,identity,order,source)
                filename = '/'.join(parts)
                if not saved:
                    return dict(ok=True,stage='image',identity=identity,order=order,source=source,filename=filename,token=token)
                if (type(event['order']) is not int or event['order']!=order or event['source']!=source or
                        event['filename']!=filename or event['token']!=token or type(event['download_id']) is not int
                        or event['download_id']<0 or type(event['size']) is not int or not 0<event['size']<=8*1024*1024
                        or not supported_url(event['final_url'],c['platform'])):
                    fail('gallery_download_mismatch','下载回执与该任务签发的顺序、来源或文件名不符')
                raw = read_slot(self.root,parts,event['size'])
            screenshot = Path(manifest['screenshot_path'])
            if screenshot.parent!=self.store.files or screenshot.stem!=manifest['screenshot_sha256']:
                raise ValueError('Screenshot receipt changed')
            screen = read_slot(self.store.files,(screenshot.name,))
            if hashlib.sha256(screen).hexdigest()!=manifest['screenshot_sha256']:
                raise ValueError('Screenshot digest changed')
        except CollectorProgressError:
            raise
        except (OSError,ValueError):
            fail('gallery_download_file_invalid','未能读取指定下载目录中的完整原图或已核验详情截图')
        with tempfile.TemporaryDirectory(prefix='research-gallery-upload-') as folder:
            original = Path(folder)/'original'
            original.write_bytes(raw)
            screenshot = Path(folder)/'detail'
            screenshot.write_bytes(screen)
            if not gallery.ready(broker):
                fail('gallery_phase_invalid','当前候选的图库授权已失效')
            # After send records its own unknown intent, keep fencing the same live
            # tab/lease so it can reconcile that specific upload.
            try:
                result = send(self.store,c['request_id'],self.claim,identity,original,screenshot,self.client,live,
                              source=source,order=order)
            except (ValueError,OSError,APIError) as error:
                latest = self.store.inspect(c['request_id']).get('collector_pending',{}).get('candidate_uploads',{})
                if latest.get(f'{identity}:{order}',{}).get('state')=='unknown':
                    raise CollectorProgressError('gallery_download_reconcile','须核对该原图的原始入库意图') from error
                raise CollectorProgressError('gallery_download_file_invalid','原图格式或入库回执未通过校验') from error
        receipt = dict(ok=True,identity=identity,order=order,artifact_id=result['artifact_id'],digest=result['sha256'])
        return dict(receipt,stage='reused',source=source) if reused else receipt

"""Bind a fresh, proven task tab directly to the next committed gallery candidate."""
import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .collector_import import _identity
from .collector_progress import CollectorProgressError
from .research_upload import lookup_receipt


def detail_matches(platform, identity, url):
    allowed = ('us.shein.com',) if platform == 'shein-us' else ('www.mercadolibre.com.mx','articulo.mercadolibre.com.mx')
    try:
        product, _ = _identity(url, platform)
        return urlsplit(url).hostname in allowed and identity == platform + ':' + product
    except (TypeError, ValueError):
        return False


def gallery_target_reader(store, client, claim, request, record, outstanding):
    """Check Odoo's request-scoped intent once, fence each subsequent target against its immutable content."""
    from .research_return_bridge import next_gallery_step
    from .tab_receipt_factory import can_rebind
    pending = record.get('collector_pending') or {}
    digest = pending.get('digest')
    artifact = record.get('collector_artifact_id')
    candidates = (pending.get('result') or {}).get('candidates')
    evidence = record.get('collector_evidence')
    raw = json.dumps(evidence,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    if (not can_rebind(record,outstanding()) or pending.get('ledger_v1') is not True or not candidates
            or record.get('request_id') != request or pending.get('artifact_id') != artifact
            or hashlib.sha256(raw).hexdigest()!=digest or evidence != pending.get('data')
            or lookup_receipt(client,claim,request,'evidence','',digest)!=artifact):
        raise ValueError('Confirmed original gallery evidence required')
    platform = record['collector_plan']['plan']['platform']
    targets = {c['platform'] + ':' + c['item_id']: c['url'] for c in candidates}
    qualified = [(d['identity'],_identity(d['detail_url'],platform)[1]) for d in evidence.get('details',[])
                 if d.get('status')=='qualified']
    if (evidence.get('platform')!=platform or list(targets.items())!=qualified or len(targets)!=len(candidates)
            or not all(detail_matches(platform, name, url) for name,url in targets.items())):
        raise ValueError('Confirmed candidate identities required')
    def read():
        current = store.inspect(request)
        saved = current.get('collector_pending') or {}
        if (not can_rebind(current,outstanding()) or current.get('collector_plan')!=record['collector_plan']
                or current.get('collector_artifact_id')!=artifact or saved.get('artifact_id')!=artifact
                or saved.get('ledger_v1') is not True or saved.get('digest')!=digest
                or not all(u.get('ledger_v1') is True for u in saved.get('candidate_uploads',{}).values())
                or [(c['platform']+':'+c['item_id'],c['url']) for c in saved.get('result',{}).get('candidates',[])]!=list(targets.items())):
            return None
        step = next_gallery_step(current)
        if step and step['stage']=='finish':
            # The last qualified candidate still owns the final gallery guard.
            name = next(reversed(targets))
            return name,targets[name]
        if step and step['stage'] in ('manifest','image') and targets.get(step['identity'])==step['detail_url']:
            return step['identity'],step['detail_url']
        return None
    return read


class GalleryBinding:
    def __init__(self, target):
        self.target = target
        self.proven_tab = None
        self.pending = None
        self.confirmed = None

    def tab_matches(self, broker):
        if not self.proven_tab or broker.rebind_requested or broker.conflict or not broker._anchor_connected():
            return False
        anchor = broker.anchor_registry.current()
        if not anchor:
            return False
        window,anchor_session,marker = anchor
        if self.proven_tab != (window,anchor_session,marker,broker.tab) or window != broker.window:
            return False
        if broker.challenge['platform']=='shein-us':
            return broker._peer_verified == (broker.session,window,broker.tab)
        return broker.session == anchor_session

    def ready(self,broker):
        return bool(self.confirmed and self.tab_matches(broker) and self.confirmed == self.target())

    def handle(self,broker,event):
        fields = {'operation','request_id','session_id','window_id','tab_id','native_origin'}
        confirm = event['operation']=='gallery_binding_confirm'
        if confirm:
            fields.update(('nonce','epoch','identity','detail_url','observed_at'))
        platform = broker.challenge['platform']
        origin = broker.peer_origin if platform=='shein-us' else broker.anchor_origin
        if (set(event)!=fields or not origin or event['native_origin']!=origin or not self.tab_matches(broker)
                or not broker.fence() or event['request_id']!=broker.challenge['request_id']
                or type(event['tab_id']) is not int or event['tab_id']!=broker.tab
                or type(event['window_id']) is not int or event['window_id']!=broker.window
                or event['session_id']!=broker.session):
            raise CollectorProgressError('gallery_binding_invalid','窗口、会话、任务标签或租约与本次相册不一致')
        target = self.target()
        if not target:
            raise CollectorProgressError('gallery_phase_invalid','当前图库请求或入库回执待核对')
        identity,url = target
        epoch = broker.challenge['lease_epoch']
        if not confirm:
            nonce = secrets.token_urlsafe(32)
            self.pending = (nonce,time.monotonic(),target,self.proven_tab)
            return dict(ok=True,request_id=event['request_id'],session_id=broker.session,window_id=broker.window,
                        tab_id=broker.tab,platform=platform,identity=identity,detail_url=url,nonce=nonce,epoch=epoch)
        try:
            observed = datetime.fromisoformat(event['observed_at'].replace('Z','+00:00'))
            age = (datetime.now(timezone.utc)-observed).total_seconds()
        except (TypeError,ValueError,AttributeError):
            age = -1
        if (not self.pending or self.pending[0]!=event['nonce'] or not 0<=time.monotonic()-self.pending[1]<=90
                or self.pending[2:]!=(target,self.proven_tab) or not 0<=age<=15 or type(event['epoch']) is not int
                or event['epoch']!=epoch or event['identity']!=identity or not detail_matches(platform,identity,event['detail_url'])):
            raise CollectorProgressError('gallery_candidate_invalid','候选商品身份、页面、挑战或观察时间不匹配')
        self.pending = None
        self.confirmed = target
        return {'ok':True}

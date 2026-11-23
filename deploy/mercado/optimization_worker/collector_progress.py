"""Read and extend business checkpoints for one authenticated collector binding."""
from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3

from .collector_cli import collector_id
from .collector_import import _identity, _number, ranked_pool, validate_evidence_prefix


class CollectorProgressError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def initialize_details(db):
    db.execute("""CREATE TABLE IF NOT EXISTS collector_details (
        request_id TEXT, plan_digest TEXT, lease_epoch INTEGER, detail_index INTEGER,
        body TEXT NOT NULL, PRIMARY KEY(request_id, plan_digest, lease_epoch, detail_index))""")


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
    elif op != 'next_candidate':
        raise CollectorProgressError('checkpoint_binding_invalid', '未知采集操作')
    key = tuple(broker.challenge[k] for k in ('request_id', 'plan_digest', 'lease_epoch'))
    platform = broker.challenge['platform']
    origin = broker.anchor_origin if platform == 'mercado-libre-mx' else broker.peer_origin
    if (set(event) != expected or (sales_only and (event['sales_only'] is not True or platform != 'mercado-libre-mx'))
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
        if (type(event['query_index']) is not int or event['query_index'] != 2 or not isinstance(proof, dict)
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
        db.execute('BEGIN IMMEDIATE' if op == 'checkpoint_detail' else 'BEGIN')
        where = ' WHERE request_id=? AND plan_digest=? AND lease_epoch=?'
        through = 3 if broker.watch_only else broker.challenge['query_index']
        if op == 'collector_progress':
            index = event['query_index']
            if type(index) is not int or not 0 <= index < 3:
                raise CollectorProgressError('checkpoint_index_invalid', '无效的搜索组编号')
            cards = None
            acknowledged = index < through
            if acknowledged or (index == through and broker.query_verified):
                row = db.execute('SELECT body FROM query_cards' + where + ' AND query_index=?', (*key, index)).fetchone()
                cards = json.loads(row[0]) if row else None
                if cards is None and acknowledged:
                    raise CollectorProgressError('checkpoint_cards_missing', '已确认搜索组的卡片缺失')
                if cards is None:
                    cards = broker.store.read_inherited_cards(*key, index)
            details = []
            if broker.watch_only and index == 2 and broker.guard_alive(broker.session):
                details = [json.loads(r[0]) for r in db.execute('SELECT body FROM collector_details' + where + ' ORDER BY detail_index', key)]
            elif broker.watch_only and index == 2:
                raise CollectorProgressError('checkpoint_guard_pending', '请等待当前浏览器连接确认再恢复详情')
            return dict(ok=True, task=task, platform=platform, queries=plan['queries'], result_limit=config['result_limit'],
                        query_index=index, acknowledged=acknowledged, cards=cards, details=details, binding=binding)
        if not broker.watch_only or not broker.guard_alive(broker.session):
            raise CollectorProgressError('checkpoint_guard_pending', '三组卡片及当前标签须先全部确认')
        if op == 'next_candidate':
            pool, recurrent, reviewed, finished = _candidate_order(db, key, plan, config)
            candidate = None
            if not finished and reviewed < len(recurrent):
                pid = recurrent[reviewed]
                candidate = {'identity': f'{platform}:{pid}', 'url': pool[pid]['url']}
            return dict(ok=True, task=task, candidate=candidate, **{k:event[k] for k in ('request_id','window_id','session_id','tab_id')})
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
        cards = [json.loads(r[0]) for r in db.execute('SELECT body FROM query_cards' + where + ' ORDER BY query_index', key)]
        evidence = dict(platform=platform, task_record_id=task, details=prior+[detail],
                        queries=[dict(query=q, observations=c) for q,c in zip(plan['queries'], cards)])
        try:
            validate_evidence_prefix(evidence, task, observed_at=datetime.now(timezone.utc).isoformat(), **params)
        except ValueError as error:
            raise CollectorProgressError('detail_validation_failed', str(error)) from error
        body = json.dumps(detail, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        if len(json.dumps(evidence, ensure_ascii=False).encode()) > 900000:
            raise CollectorProgressError('checkpoint_size_exceeded', '证据超出回传大小上限')
        db.execute('INSERT INTO collector_details VALUES (?, ?, ?, ?, ?)', (*key, index, body))
        return {'ok': True, 'sales_screen': 'rejected'} if sales_only else {'ok': True}


def _sales_screen_candidate(detail):
    return (isinstance(detail, dict) and detail.get('status') == 'rejected'
            and detail.get('reason') == 'threshold_failure'
            and all(detail.get(key) is None for key in ('visual_category_code', 'match_level', 'visual_features')))


def _candidate_order(db, key, plan, config):
    where = ' WHERE request_id=? AND plan_digest=? AND lease_epoch=?'
    groups = db.execute('SELECT query_index,body FROM query_cards' + where + ' ORDER BY query_index', key).fetchall()
    if [i for i, _ in groups] != [0, 1, 2]:
        raise CollectorProgressError('checkpoint_cards_missing', '须先完成三组搜索卡片')
    blocks = [dict(query=plan['queries'][i], observations=json.loads(raw)) for i, raw in groups]
    pool, recurrent = ranked_pool(blocks, plan['queries'], plan['platform'])
    details = db.execute('SELECT detail_index,body FROM collector_details' + where + ' ORDER BY detail_index', key).fetchall()
    qualified = 0
    for expected, (index, raw) in enumerate(details):
        detail = json.loads(raw)
        if (index != expected or index >= len(recurrent) or detail.get('status') not in ('qualified','rejected')
                or detail.get('identity') != f"{plan['platform']}:{recurrent[index]}"):
            raise CollectorProgressError('detail_index_invalid', '已保存的详情与候选顺序不一致')
        qualified += detail['status'] == 'qualified'
    return pool, recurrent, len(details), qualified >= config['result_limit']


def checked_detail_identity(broker, url):
    """Only activate a previously reviewed candidate or the next candidate."""
    if not broker.watch_only or not broker.guard_alive(broker.session) or broker.progress_plan is None:
        return None
    plan, config = broker.progress_plan
    key = tuple(broker.challenge[k] for k in ('request_id','plan_digest','lease_epoch'))
    try:
        pid, _ = _identity(url, plan['platform'])
        with closing(sqlite3.connect(broker.store.path)) as db:
            db.execute('BEGIN')
            _, recurrent, reviewed, finished = _candidate_order(db, key, plan, config)
        end = reviewed + (0 if finished else 1)
        return f"{plan['platform']}:{pid}" if pid in recurrent[:end] else None
    except (KeyError, TypeError, ValueError):
        return None


def verify_checkpoint_evidence(db, challenge, evidence, *, allow_previous_stage=False):
    """Staging may only submit the acknowledged observations and detail prefix."""
    key = tuple(challenge[k] for k in ('request_id', 'plan_digest', 'lease_epoch'))
    where = ' WHERE request_id=? AND plan_digest=? AND lease_epoch=?'
    blocks = [json.loads(r[0]) for r in db.execute('SELECT body FROM query_cards' + where + ' ORDER BY query_index', key)]
    details = [json.loads(r[0]) for r in db.execute('SELECT body FROM collector_details' + where + ' ORDER BY detail_index', key)]
    if not isinstance(evidence, dict) or len(blocks) != 3:
        return False
    if evidence.get('queries') != [dict(query=cards[0]['query'], observations=cards) for cards in blocks]:
        return False
    if evidence.get('details') == details:
        return True
    # Older extensions staged the entire review atomically without per-detail rows.
    if allow_previous_stage and not details:
        staged = db.execute('SELECT body FROM staged_evidence WHERE request_id=? AND lease_epoch=?',
                            (key[0], key[2])).fetchone()
        return bool(staged and json.loads(staged[0]) == evidence)
    return False

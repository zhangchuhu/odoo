"""Durable mailbox for actual Codex tools. Python never invokes session tools.

Private local state is bound to the same origin/credential identity as Worker.
Claims never expire into a second image edit. Unknown tool/upload results need
reconciliation, not another dispatch. No vendor APIs or Feishu dependencies.
"""
import argparse
import base64
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

from PIL import Image

from .domain import canonical_hash, validate_result

QC_CHECKS = ('garment_construction', 'color_material', 'garment_details',
             'identity_pose', 'accessories', 'scene_lighting_framing', 'text_layout')

CORRECTIONS = {
    'wrong_collar': 'Match the reference collar and neckline construction.',
    'open_front': 'Match the reference front closure and openings.',
    'missing_sleeve': 'Restore the reference sleeves and their construction.',
    'wrong_skirt_shape': 'Match the reference skirt silhouette.',
    'wrong_color': 'Match the reference garment color.',
    'original_clothing_remains': 'Remove every visible remnant of the original clothing.',
    'identity_changed': 'Preserve the target person identity and face.',
    'pose_changed': 'Preserve the target pose and body position.',
    'accessory_changed': 'Preserve the target accessories.',
    'background_changed': 'Preserve the target background.',
    'bad_occlusion': 'Correct garment occlusion to match the target pose.',
    'anatomy_distortion': 'Preserve natural target anatomy.',
    'secondary_garment_details_changed': 'Preserve the reference garment details.',
    'missing_infographic_instance': 'Preserve all required garment instances.',
    'text_changed': 'Preserve the target text exactly.',
    'layout_changed': 'Preserve the target layout exactly.',
}

def _review_round(record, attempt, best=False):
    if record.get('active_review_binding'):
        return record['active_review_binding']
    intent = record.get('resume_intent_id')
    seq = record.get('review_seq')
    attempts = record.get('quality_cycle', {}).get('attempts', [])
    if intent is not None or best or record.get('resume_ack_pending'):
        if not isinstance(intent, str) or not intent or type(seq) is not int or seq < 1 or len(attempts) > 1:
            raise ValueError('Historical review round cannot be verified')
        return {'attempt_id': attempt.get('attempt_id'), 'review_seq': seq, 'intent_id': intent}
    if seq or len(attempts) > 1:
        raise ValueError('Historical review round cannot be verified')
    return {'attempt_id': attempt.get('attempt_id'), 'review_seq': 0, 'intent_id': None}


class SessionPending(Exception):
    """Host work is durably prepared and can be polled without redispatch."""


class SessionBlocked(Exception):
    """A known interruption which the operator can resolve in the original task."""

    MESSAGES = {
        'window_registration_failed': '采集窗口登记失败，请检查扩展连接后恢复原任务',
        'captcha_required': '采集页面要求安全验证。请完成验证后恢复原任务。',
        'login_required': '采集页面要求登录。请在专用窗口登录后恢复原任务。',
        'page_unreadable': '采集页面无法读取。请检查当前任务标签后恢复原任务。',
    }

    def __init__(self, code='window_registration_failed'):
        self.code = code
        self.message = self.MESSAGES[code]
        super().__init__(self.message)


class SessionUnknown(Exception):
    """The original invocation requires reconciliation; do not redispatch."""

    def __init__(self, diagnostic=None):
        self.diagnostic = diagnostic or {}

    def user_message(self):
        messages = {
            'timeout': '交互式采集进程超时',
            'process_or_io_error': '执行进程或本机读写发生错误',
            'preparation_or_bridge_error': '研究计划准备或本机回传失败',
            'window_blocker_rejected': '后台未确认窗口登记失败的回执',
            'process_nonzero': '交互式采集进程异常退出',
            'collector_binding_unavailable': '扩展未连接到任务标签',
            'no_progress_tool_failure': '电脑操作工具调用失败，未保存新进度',
            'no_progress_turn_failure': 'Codex 会话中断，未保存新进度',
            'no_persisted_progress': '交互式进程已结束，未保存新进度',
            'invocation_interrupted': '上一次采集进程中断，缺少终止回执',
        }
        code = self.diagnostic.get('code')
        if code not in messages:
            return None
        exit_code = self.diagnostic.get('exit_code')
        suffix = f'，退出码 {exit_code}' if type(exit_code) is int else ''
        return f'{messages[code]}（{code}{suffix}）'


class SessionFailure(Exception):
    """A confirmed terminal host outcome; never retry the native invocation."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _read_json(path):
    path = Path(path)
    if path.stat().st_size > 256 * 1024:
        raise ValueError('Host submission exceeds bound')
    return json.loads(path.read_text())


def _capabilities(value, kind):
    if (value.get('runtime') != 'codex' or (kind not in ('research.keywords', 'title.generate') and value.get('vision') != 'view_image')
            or not isinstance(value.get('provenance'), str) or not value['provenance'].strip()):
        raise ValueError('Actual Codex visual capability evidence required')
    if kind == 'image.generate' and value.get('generation') != 'image_gen.imagegen':
        raise ValueError('Actual image editing capability required')
    if kind in ('gallery.collect', 'research.bestseller') and not value.get('browser'):
        raise ValueError('Actual browser capability required')


def _raster(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('Readable raster up to 8 MiB required')
    raw = path.read_bytes()
    with Image.open(io.BytesIO(raw)) as image:
        if image.format not in ('PNG', 'JPEG', 'WEBP') or image.width * image.height > 40_000_000:
            raise ValueError('Unsupported raster')
        image.load()
        return raw, 'image/' + {'JPEG': 'jpeg', 'PNG': 'png', 'WEBP': 'webp'}[image.format], image.size, 'A' in image.getbands() or 'transparency' in image.info


def _verify_invocation(record, invocation_id):
    """Omission is safe only for the first invocation of the entire request."""
    journal = record.get('generation_retry')
    if not journal:
        attempts = record.get('quality_cycle', {}).get('attempts', [])
        if invocation_id is not None or len(attempts) > 1:
            raise ValueError('Legacy invocation cannot be verified')
        return
    current = journal.get('invocation_id')
    if invocation_id is not None and invocation_id != current:
        raise ValueError('Image invocation identity mismatch')
    attempts = record.get('quality_cycle', {}).get('attempts', [])
    if (journal.get('receipts') or record.get('generation_retry_history') or len(attempts) > 1) and invocation_id != current:
        raise ValueError('Current image invocation identity required after first invocation')


class SessionStore:
    def __init__(self, path, identity):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.files = self.path.parent / (self.path.stem + '-files')
        self.files.mkdir(exist_ok=True, mode=0o700)
        os.chmod(self.files, 0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS identity (value TEXT NOT NULL)')
            row = db.execute('SELECT value FROM identity').fetchone()
            if row and row[0] != identity:
                raise ValueError('Host session origin/credential identity mismatch')
            if not row:
                db.execute('INSERT INTO identity VALUES (?)', (identity,))
            db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, record TEXT NOT NULL)')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            with db:
                yield db
        finally:
            db.close()

    def _get(self, db, request_id):
        row = db.execute('SELECT record FROM requests WHERE id=?', (request_id,)).fetchone()
        if not row:
            raise ValueError('Unknown host request')
        return json.loads(row[0])

    def _put(self, db, record):
        db.execute('INSERT OR REPLACE INTO requests VALUES (?,?)',
                   (record['request_id'], json.dumps(record, allow_nan=False)))

    def _file(self, raw):
        with Image.open(io.BytesIO(raw)) as image:
            suffix = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}.get(image.format)
        if suffix is None:
            raise ValueError('Unsupported image format')
        path = self.files / (hashlib.sha256(raw).hexdigest() + suffix)
        if path.exists() and path.read_bytes() != raw:
            raise ValueError('Host input file integrity mismatch')
        if not path.exists():
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        return str(path)

    def enqueue(self, request_id, kind, payload, context):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', request_id):
            raise ValueError('Invalid request ID')
        binding = canonical_hash({'kind': kind, 'payload': payload, 'context': context})
        with self._db() as db:
            existing = db.execute('SELECT record FROM requests WHERE id=?', (request_id,)).fetchone()
            if existing:
                if json.loads(existing[0])['binding'] != binding:
                    raise ValueError('Immutable host request changed')
                return
            images = {}
            qc_unverified = False
            qc_refs = None
            if kind == 'image.qc':
                target = context.get('qc_target_artifact_id')
                plan = context.get('artifacts', {}).get(payload.get('generation_plan_id'), {}).get('payload', {})
                sources = plan.get('source_artifact_ids', []) if isinstance(plan, dict) else []
                targets = plan.get('target_artifact_ids', []) if isinstance(plan, dict) else []
                if (not isinstance(target, str) or type(context.get('generation_task_id')) is not int or context['generation_task_id'] <= 0 or not isinstance(targets, list) or target not in targets or
                        not isinstance(sources, list) or not sources or any(not isinstance(x, str) for x in sources)):
                    qc_unverified = True
                    qc_refs = set()
                else:
                    qc_refs = set([target, payload.get('image_artifact_id')] + sources)
            for ref, artifact in context['artifacts'].items():
                if qc_refs is not None and ref not in qc_refs:
                    continue
                if artifact.get('data'):
                    raw = base64.b64decode(artifact['data'], validate=True)
                    if hashlib.sha256(raw).hexdigest() != artifact['digest']:
                        raise ValueError('Input artifact digest mismatch')
                    path = self._file(raw)
                    _raster(path)
                    images[ref] = path
            if qc_refs is not None and set(images) != qc_refs:
                qc_unverified = True
                images = {}
            record = {'request_id': request_id, 'task_type': kind, 'payload': payload,
                      'context': {**context, 'artifacts': {k: {f: v for f, v in a.items() if f != 'data'} for k, a in context['artifacts'].items()}},
                      'binding': binding, 'images': images, 'state': 'waiting' if qc_unverified else 'planning' if kind == 'image.generate' else 'prepared'}
            if qc_unverified:
                record.update(reason='qc_target_unverified', resume_state='prepared')
            self._put(db, record)

    def _digests(self, paths):
        return {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}

    def _verify_views(self, record, supplied, paths=None):
        paths = list(record['images'].values()) if paths is None else paths
        expected = self._digests(paths)
        originals = {p: record['context']['artifacts'][k]['digest'] for k, p in record['images'].items()}
        originals.update({u['path']: u['sha256'] for u in record.get('uploads', {}).values()})
        if any(expected[p] != originals[p] for p in paths if p in originals) or supplied != expected:
            raise ValueError('Original views/digests required; input changed')
        return expected

    def synchronize(self, worker_records, observed_ids=None):
        """Retire departed leases without altering their unknown/paid history."""
        active = {r['request_id'] for r in worker_records if not r.get('done')}
        with self._db() as db:
            records = [json.loads(row[0]) for row in db.execute('SELECT record FROM requests')]
            for r in records:
                if (observed_ids is None or r['request_id'] in observed_ids) and r['request_id'] not in active and not r.get('retired'):
                    r.update(retired=True, retirement_reason='original_worker_lease_inactive')
                    self._put(db, r)

    def request_ids(self):
        with self._db() as db:
            return {row[0] for row in db.execute('SELECT id FROM requests')}

    def reactivate_research(self, request_id, claim, *, validate_only=False, verified_gallery=False):
        """Re-enable the original, verified gallery checkpoint under a recovered lease."""
        with self._db() as db:
            r = self._get(db, request_id)
            plan = r.get('collector_plan') or {}
            signed = dict(plan)
            digest = signed.pop('digest', None)
            pending = r.get('collector_pending') or {}
            uploads = pending.get('candidate_uploads', {})
            snap = r.get('context', {}).get('snapshot', {})
            isolated_gallery = (verified_gallery and r.get('retired') and r.get('state') == 'unknown'
                                and r.get('reason') == 'unknown' and r.get('resume_state') == 'candidate_images_pending')
            if (r.get('task_type') != 'research.bestseller' or not (r.get('state') == 'candidate_images_pending' or isolated_gallery)
                    or r.get('payload') != claim.get('payload') or plan.get('request_id') != request_id
                    or digest != canonical_hash(signed) or plan.get('case_id') != snap.get('case_id')
                    or any(plan.get(k) != claim.get(k) for k in ('company_id', 'snapshot_id', 'input_hash'))
                    or not str(pending.get('artifact_id', '')).isdigit()
                    or not isinstance(uploads, dict)
                    or any(not isinstance(v, dict) or v.get('state') not in ('confirmed', 'unknown') for v in uploads.values())
                    or (verified_gallery and (pending.get('ledger_v1') is not True or pending.get('artifact_id') != r.get('collector_artifact_id')
                                              or any(v.get('state') != 'confirmed' for v in uploads.values())))
                    or (r.get('retired') and r.get('retirement_reason') != 'original_worker_lease_inactive')):
                raise ValueError('Original verified gallery checkpoint required')
            if not validate_only:
                if isolated_gallery:
                    r['state'] = 'candidate_images_pending'
                    r.pop('resume_state',None)
                    r.pop('reason',None)
                r.pop('retired', None)
                r.pop('retirement_reason', None)
                self._put(db, r)

    def inspect(self, request_id):
        with self._db() as db:
            return self._get(db, request_id)

    def mark_interrupted_text(self, kinds):
        """Fence ambiguous invocations owned by a stopped, exclusive host."""
        with self._db() as db:
            records = [json.loads(row[0]) for row in db.execute('SELECT record FROM requests')]
            for record in records:
                if record['task_type'] in kinds and record['state'] == 'dispatched' and not record.get('retired'):
                    record['state'] = 'unknown'
                    record['reason'] = 'host_interrupted'
                    self._put(db, record)

    def claim_text(self, request_id):
        """Durably reserve one text invocation before starting an external process."""
        with self._db() as db:
            r = self._get(db, request_id)
            if r.get('retired') or r['state'] != 'prepared' or r['task_type'] not in ('product.facts', 'research.keywords', 'title.generate'):
                return False
            r['state'] = 'dispatched'
            self._put(db, r)
            return True

    def save_text_result(self, request_id, result):
        with self._db() as db:
            r = self._get(db, request_id)
            if r.get('retired') or r['state'] != 'dispatched' or r['task_type'] not in ('product.facts', 'research.keywords', 'title.generate'):
                raise ValueError('Active text invocation required')
            validate_result(r['task_type'], result)
            r.update(state='result_ready', pending_result=result)
            self._put(db, r)

    def save_image_result(self, request_id, path, digest, attempt_id=None, invocation_id=None):
        path = Path(path)
        if path.parent != self.files or path.is_symlink():
            raise ValueError('Invalid image result path')
        raw, _, _, _ = _raster(path)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('Image result digest mismatch')
        with self._db() as db:
            r = self._get(db, request_id)
            if r.get('retired') or r['task_type'] != 'image.generate' or r['state'] not in ('dispatched', 'unknown'):
                raise ValueError('Image request must be dispatched')
            attempt = r.get('quality_cycle', {}).get('attempts', [{}])[-1]
            if (attempt.get('number', 1) > 1 or attempt_id is not None) and attempt.get('attempt_id') != attempt_id:
                raise ValueError('Image result attempt mismatch')
            _verify_invocation(r, invocation_id)
            _capabilities(r.get('capabilities', {}), 'image.generate')
            if r.get('generation_retry'):
                from .outfit_retry import settle
                r = settle(r)
            r.update(state='image_result_ready', image_result_path=str(path), image_result_digest=digest,
                     image_result_invocation_id=r.get('generation_retry', {}).get('invocation_id'))
            self._put(db, r)

    def archive_image(self, request_id, path, attempt_id=None):
        """Preserve a late native output locally, even after lease retirement."""
        raw, _, _, _ = _raster(path)
        digest = hashlib.sha256(raw).hexdigest()
        with self._db() as db:
            r = self._get(db, request_id)
            attempts = r.get('quality_cycle', {}).get('attempts', [])
            if r['task_type'] != 'image.generate' or (not attempts and r['state'] not in ('dispatched', 'unknown')):
                raise ValueError('Only an original unresolved edit can receive a late output')
            if attempts:
                if attempt_id is None and len(attempts) == 1:
                    attempt_id = attempts[0].get('attempt_id')
                if not attempt_id or not any(a.get('attempt_id') == attempt_id for a in attempts):
                    raise ValueError('Unknown original attempt')
            else:
                if attempt_id is not None:
                    raise ValueError('Legacy request has no attempt identity')
                attempt_id = 'legacy'
            results = r.setdefault('late_results', {})
            if attempt_id not in results and len(attempts) <= 1 and r.get('late_result_sha256'):
                results[attempt_id] = {'path': r['late_result_path'], 'sha256': r['late_result_sha256']}
            if attempt_id in results and results[attempt_id]['sha256'] != digest:
                raise ValueError('Late result already immutable')
            if attempt_id not in results:
                results[attempt_id] = {'path': self._file(raw), 'sha256': digest}
            if len(attempts) <= 1:
                r.update(late_result_path=results[attempt_id]['path'], late_result_sha256=digest)
            self._put(db, r)

    def next(self, kinds=None, states=None, request_id=None):
        with self._db() as db:
            records = [json.loads(r[0]) for r in db.execute('SELECT record FROM requests ORDER BY rowid')]
        for r in records:
            if (request_id is not None and r['request_id'] != request_id) or r['state'] in ('resolved', 'terminal') or r.get('retired') or (kinds is not None and r['task_type'] not in kinds) or (states is not None and r['state'] not in states):
                continue
            if r['state'] == 'waiting' and r.get('reason') == 'manual_review_binding_required':
                continue
            paths = list(r['images'].values())
            action = {'request_id': r['request_id'], 'task_type': r['task_type'], 'payload': r['payload'],
                      'context': r['context'], 'image_paths': r['images'], 'input_digests': self._digests(paths),
                      'state': r['state']}
            if r['task_type'] == 'research.bestseller' and isinstance(r.get('collector_plan'), dict):
                binding = r['collector_plan']
                action['collector_plan'] = copy.deepcopy(binding.get('plan'))
                action['collector_id'] = binding.get('collector_id')
                action['collector_plan_digest'] = binding.get('digest')
            if r['state'] == 'image_result_ready':
                action['action'] = 'replay_saved_image'
            elif r['state'] in ('dispatched', 'unknown'):
                action['action'] = 'awaiting_result'
                if r.get('generation_retry'):
                    action['invocation_id'] = r['generation_retry'].get('invocation_id')
            elif r['state'] == 'service_retryable':
                failure = r['generation_retry']['reports'][-1]
                action.update(action='retry_generation_service', expected_unknown_at=failure['observed_at'], not_before=failure['not_before'])
            elif r['state'] == 'candidate_images_pending':
                action['action'] = 'collect_candidate_images'
                action['candidates'] = r['collector_pending']['result']['candidates']
                action['candidate_uploads'] = r['collector_pending'].get('candidate_uploads', {})
                action['gallery_manifests'] = {
                    key: {field: copy.deepcopy(value.get(field)) for field in ('detail_url', 'images', 'screenshot_sha256')}
                    for key, value in r['collector_pending'].get('gallery_manifests', {}).items()
                }
            elif r['state'] == 'collector_upload_unknown':
                action['action'] = 'reconcile_upload'
                action['collector_artifact_id'] = r.get('collector_pending', {}).get('artifact_id')
                action['collector_digest'] = r.get('collector_pending', {}).get('digest')
            elif r['state'] == 'upload_unknown':
                action['action'] = 'reconcile_upload'
                action['uploads'] = r.get('uploads', {})
            elif r['state'] in ('qc_pending', 'best_qc_pending'):
                best = r['state'] == 'best_qc_pending'
                action['action'] = 'review_best_candidate' if best else 'review_candidate'
                action['candidate_path'] = r['candidate_path']
                action['input_digests'] = self._digests(r['ordered_image_paths'] + [r['candidate_path']])
                action['required_checks'] = list(QC_CHECKS)
                if r.get('confirmed_inventory') is not None:
                    action['inventory'] = r['confirmed_inventory']
                attempts = r.get('quality_cycle', {}).get('attempts', [{}])
                attempt = attempts[r['best_of_budget']['attempt_number'] - 1] if best else attempts[-1]
                number = attempt.get('number', 1)
                key = 'candidate' if number == 1 else f'candidate_{number}'
                action['quality_attempt'] = number
                action['attempt_id'] = attempt.get('attempt_id')
                try:
                    action['review_binding'] = _review_round(r, attempt, best)
                except ValueError:
                    with self._db() as db:
                        current = self._get(db, r['request_id'])
                        if current == r:
                            current.update(state='waiting', reason='manual_review_binding_required', resume_state=r['state'])
                            self._put(db, current)
                    continue
                action['quality_binding'] = {'request_id': r['request_id'], 'input_digest': canonical_hash(r['generation_digests']),
                    'plan_digest': canonical_hash(r['plan']), 'candidate_sha256': r['uploads'][key]['sha256']}
            elif r['state'] == 'comparison_pending':
                action['action'] = 'compare_candidates'
                action['classification'] = r['plan']['classification']
                if r.get('confirmed_inventory') is not None:
                    action['inventory'] = copy.deepcopy(r['confirmed_inventory'])
                action['candidate_paths'] = {f'candidate_{n}': r['uploads']['candidate' if n == 1 else f'candidate_{n}']['path'] for n in range(1, 4)}
                action['input_digests'] = self._digests(r['ordered_image_paths'] + list(action['candidate_paths'].values()))
            elif r['state'] in ('waiting', 'unknown'):
                action['action'] = 'waiting'
                action['reason'] = r.get('reason', 'Host capability or result unavailable')
            elif r['state'] == 'inventory':
                round_no = len(r['inventory_readings']) + 1
                action.update(action='adjudicate_inventory' if round_no == 3 else 'read_inventory',
                              reading_round=round_no, target_digest=r['target_digest'],
                              target_path=r['images'][r['payload']['target_artifact_id']])
                if round_no == 3:
                    action['readings'] = r['inventory_readings']
                    first, second = (item['inventory'] for item in r['inventory_readings'])
                    action['differences'] = [key for key in ('visible_text', 'panels', 'garment_instances') if first[key] != second[key]]
            elif r['state'] == 'planning':
                action['action'] = 'select_references'
                if r.get('confirmed_inventory') is not None:
                    action['inventory'] = r['confirmed_inventory']
                    action['missing_instances'] = r.get('missing_instances', [])
            elif r['task_type'] == 'image.generate':
                action.update(action='generate', ordered_image_paths=r['ordered_image_paths'],
                              input_digests=self._digests(r['ordered_image_paths']))
            else:
                action['action'] = {'gallery.collect': 'browse_gallery', 'research.bestseller': 'browse_research',
                                    'image.qc': 'review_image'}.get(r['task_type'], 'analyze')
                if r['task_type'] == 'image.qc':
                    target = r['context']['qc_target_artifact_id']
                    plan = r['context']['artifacts'][r['payload']['generation_plan_id']]['payload']
                    candidate = r['payload']['image_artifact_id']
                    action['review_image_ids'] = [target] + plan['source_artifact_ids'] + [candidate]
                    action['required_checks'] = list(QC_CHECKS)
                    action['quality_attempt'] = 1
                    action['quality_binding'] = {'request_id': r['request_id'], 'input_digest': canonical_hash(action['input_digests']),
                        'plan_digest': canonical_hash(plan), 'candidate_sha256': r['context']['artifacts'][candidate]['digest']}
            return action
        return {'action': 'idle'}

    def plan(self, request_id, plan):
        try:
            return self._plan(request_id, plan)
        except ValueError:
            with self._db() as db:
                r = self._get(db, request_id)
                if r['state'] == 'planning' and r.get('confirmed_inventory') is not None:
                    r['selection_corrections'] = r.get('selection_corrections', 0) + 1
                    if r['selection_corrections'] >= 3:
                        r.update(state='waiting', reason='selection_submission_exhausted', resume_state='planning')
                    self._put(db, r)
            raise

    def _plan(self, request_id, plan):
        with self._db() as db:
            r = self._get(db, request_id)
            if r.get('plan') == plan and r['state'] != 'planning':
                return
            if r['state'] != 'planning':
                raise ValueError('Plan already immutable')
            self._verify_views(r, plan.get('viewed_digests'))
            from .outfit_plan import validate_and_render
            source_ids = r['payload']['source_artifact_ids']
            source_digests = {ref: r['context']['artifacts'][ref]['digest'] for ref in source_ids}
            inventory = r.get('confirmed_inventory')
            infographic = plan.get('classification') == 'infographic'
            if inventory is not None and not infographic:
                raise ValueError('Confirmed infographic cannot change classification')
            validated, prompt = validate_and_render(plan, source_ids, source_digests, inventory=inventory, tentative=infographic and inventory is None)
            if infographic and inventory is None:
                r.update(plan=plan, state='inventory', inventory_readings=[], target_digest=r['context']['artifacts'][r['payload']['target_artifact_id']]['digest'])
                self._put(db, r)
                return
            if inventory is not None:
                selected = set(validated['selected_reference_ids'])
                roles = {role for item in validated['source_evidence'] if item['source_id'] in selected for role in item['roles']}
                missing = [x for x in inventory['garment_instances'] if 'instance:' + x not in roles]
                if missing:
                    r['selection_corrections'] = r.get('selection_corrections', 0) + 1
                    if r['selection_corrections'] > 2:
                        r.update(state='waiting', reason='capacity_insufficient', resume_state='planning')
                    r['missing_instances'] = missing
                    self._put(db, r)
                    return
            refs = validated['selected_reference_ids']
            paths = [r['images'][r['payload']['target_artifact_id']]] + [r['images'][x] for x in refs]
            _, _, _, transparent = _raster(paths[0])
            r.update(plan=plan, state='prepared', ordered_image_paths=paths,
                     invocation={'prompt': prompt,
                         'referenced_image_paths': paths, 'transparent_background': transparent})
            r['generation_digests'] = self._digests(paths)
            from .outfit_attempts import bind
            r = bind(r, request_id, canonical_hash(r['generation_digests']), canonical_hash(r['plan']))
            self._put(db, r)

    def inventory(self, request_id, submission):
        try:
            return self._inventory(request_id, submission)
        except ValueError:
            with self._db() as db:
                r = self._get(db, request_id)
                if r['state'] == 'inventory':
                    counts = r.setdefault('inventory_invalid_counts', {})
                    key = str(len(r['inventory_readings']) + 1)
                    counts[key] = counts.get(key, 0) + 1
                    if counts[key] >= 3:
                        r.update(state='waiting', reason='inventory_submission_exhausted', resume_state='inventory')
                    self._put(db, r)
            raise

    def _inventory(self, request_id, submission):
        completed_plan = None
        with self._db() as db:
            r = self._get(db, request_id)
            if r.get('retired') or r['state'] != 'inventory':
                raise ValueError('Inventory is not pending')
            reading_round = len(r['inventory_readings']) + 1
            target = r['images'][r['payload']['target_artifact_id']]
            if self._digests([target])[target] != r['target_digest']:
                raise ValueError('Target bytes changed')
            keys = {'schema_version','request_id','target_digest','reading_round','inventory'}
            if reading_round == 3:
                keys.add('confirmed')
            if (not isinstance(submission, dict) or set(submission) != keys or type(submission['schema_version']) is not int
                    or submission['schema_version'] != 1 or submission['request_id'] != request_id
                    or submission['target_digest'] != r['target_digest'] or type(submission['reading_round']) is not int
                    or submission['reading_round'] != reading_round
                    or (reading_round == 3 and type(submission['confirmed']) is not bool)):
                raise ValueError('Invalid inventory binding')
            value = submission['inventory']
            limits = {'visible_text':128, 'panels':64, 'garment_instances':12}
            if not isinstance(value, dict) or set(value) != set(limits):
                raise ValueError('Invalid inventory')
            for key, limit in limits.items():
                items = value[key]
                if (not isinstance(items, list) or len(items) > limit or (key != 'visible_text' and not items)
                        or any(not isinstance(x, str) or not x.strip() or len(x) > 4096 for x in items)):
                    raise ValueError('Invalid inventory items')
            r['inventory_readings'].append(copy.deepcopy(submission))
            if reading_round == 2 and r['inventory_readings'][0]['inventory'] == value:
                r.update(confirmed_inventory=value, state='planning')
            elif reading_round == 3:
                if submission['confirmed']:
                    r.update(confirmed_inventory=value, state='planning')
                else:
                    r.update(state='waiting', reason='inventory_unsettled', resume_state='inventory')
            if r.get('confirmed_inventory') is not None:
                selected = set(r['plan']['selected_reference_ids'])
                roles = {role for item in r['plan']['source_evidence'] if item['source_id'] in selected for role in item['roles']}
                if all('instance:' + x in roles for x in r['confirmed_inventory']['garment_instances']):
                    completed_plan = r['plan']
            self._put(db, r)
        if completed_plan is not None:
            self.plan(request_id, completed_plan)

    def dispatch(self, request_id, capabilities, viewed_digests):
        with self._db() as db:
            r = self._get(db, request_id)
            if r.get('retired') or r.get('resume_ack_pending') or r['state'] != 'prepared' or r['task_type'] != 'image.generate':
                return {'should_invoke': False, 'request_id': request_id, 'state': r['state']}
            _capabilities(capabilities, 'image.generate')
            self._verify_views(r, viewed_digests, r['ordered_image_paths'])
            if viewed_digests != r['generation_digests']:
                raise ValueError('Generation bytes changed')
            cap = capabilities.get('max_input_images')
            if cap is not None and (type(cap) is not int or cap < 1):
                raise ValueError('Invalid host capacity')
            if cap is not None and cap < len(r['ordered_image_paths']):
                r.update(state='waiting', reason='capacity_insufficient', resume_state='prepared')
                self._put(db, r)
                return {'should_invoke': False, 'request_id': request_id, 'state': 'waiting', 'reason': 'capacity_insufficient'}
            from .outfit_attempts import bind, start
            r = bind(r, request_id, canonical_hash(r['generation_digests']), canonical_hash(r['plan']))
            r, number = start(r)
            attempt_id = uuid.uuid4().hex
            invocation = copy.deepcopy(r['invocation'])
            if number == 1 and r.get('manual_retry_correction'):
                invocation['prompt'] += '\nCorrection: ' + r['manual_retry_correction']
            if number > 1:
                defect = r['quality_cycle']['attempts'][-2]['report'].get('primary_defect')
                if defect not in CORRECTIONS:
                    raise ValueError('A known primary defect is required for a retry')
                invocation['prompt'] += '\nCorrection: ' + CORRECTIONS[defect]
            r['quality_cycle']['attempts'][-1].update(attempt_id=attempt_id, invocation=invocation)
            if r.get('generation_retry'):
                r.setdefault('generation_retry_history', []).append(r.pop('generation_retry'))
            from .outfit_retry import bind as bind_retry
            r = bind_retry(r, number, time.time())
            r['generation_retry']['invocation_id'] = attempt_id
            r['active_review_binding'] = {'attempt_id': attempt_id, 'review_seq': 0, 'intent_id': None}
            r.pop('image_result_path', None)
            r.pop('image_result_digest', None)
            r.update(state='dispatched', capabilities=capabilities)
            self._put(db, r)
            return {'should_invoke': True, 'request_id': request_id, 'attempt_number': number,
                    'attempt_id': attempt_id, 'invocation_id': attempt_id, 'invocation': invocation}

    def report_service_error(self, request_id, invocation_id, diagnostic, retry_after=0, *, confirmed_finished=False):
        """Report a confirmed finished failure, never an ambiguous timeout."""
        if confirmed_finished is not True:
            raise ValueError('Explicit confirmation of finished service failure required')
        with self._db() as db:
            r = self._get(db, request_id)
            journal = r.get('generation_retry', {})
            if r.get('retired') or r['state'] != 'dispatched' or journal.get('invocation_id') != invocation_id:
                raise ValueError('Active invocation identity required')
            from .outfit_retry import report
            r = report(r, 'retryable', diagnostic, time.time(), retry_after)
            r.update(state='service_retryable', reason='retryable')
            self._put(db, r)

    def claim_service_retry(self, request_id, expected_unknown_at, capabilities, viewed_digests):
        """Persist one retry claim in the same SQLite transaction as the state."""
        with self._db() as db:
            r = self._get(db, request_id)
            if r.get('retired') or r['task_type'] != 'image.generate':
                raise ValueError('Active original request required')
            j = r.get('generation_retry', {})
            if j.get('receipts') and j['receipts'][-1]['observed_at'] == expected_unknown_at:
                return {'should_invoke': False, 'request_id': request_id, 'state': r['state']}
            if r['state'] != 'service_retryable':
                raise ValueError('No confirmed retryable error')
            _capabilities(capabilities, 'image.generate')
            if capabilities != r['capabilities']:
                raise ValueError('Original capability binding required')
            self._verify_views(r, viewed_digests, r['ordered_image_paths'])
            if viewed_digests != r['generation_digests']:
                raise ValueError('Generation bytes changed')
            from .outfit_retry import claim
            r, invoke = claim(r, expected_unknown_at, time.time())
            if not invoke:
                return {'should_invoke': False, 'request_id': request_id, 'state': r['state']}
            invocation_id = uuid.uuid4().hex
            r['generation_retry']['invocation_id'] = invocation_id
            r['state'] = 'dispatched'
            attempt = r['quality_cycle']['attempts'][-1]
            self._put(db, r)
            return {'should_invoke': True, 'request_id': request_id, 'attempt_number': attempt['number'],
                    'attempt_id': attempt['attempt_id'], 'invocation_id': invocation_id,
                    'invocation': copy.deepcopy(attempt['invocation'])}

    def error(self, request_id, outcome, category=None, invocation_id=None):
        if outcome not in ('unknown', 'unavailable', 'moderation_blocked', 'failed'):
            raise ValueError('Unsupported host outcome')
        if category is not None and category not in ('codex_exit_nonzero', 'missing_output', 'output_too_large', 'invalid_json', 'invalid_contract', 'local_io_error'):
            raise ValueError('Unsupported failure category')
        with self._db() as db:
            r = self._get(db, request_id)
            if r['state'] in ('resolved', 'terminal'):
                raise ValueError('Settled outcome immutable')
            if r['task_type'] == 'image.generate':
                _verify_invocation(r, invocation_id)
            if outcome == 'unknown' and r['state'] == 'dispatched' and r.get('quality_cycle'):
                if r.get('generation_retry') and r['generation_retry']['status'] == 'running':
                    from .outfit_retry import report
                    r = report(r, 'unknown', 'Unresolved host invocation', time.time())
                from .outfit_attempts import mark_unknown
                r = mark_unknown(r, r['quality_cycle']['attempts'][-1]['number'])
            if r['state'] != 'waiting':
                r['resume_state'] = r['state']
            r.update(state='terminal' if outcome in ('failed', 'moderation_blocked') else 'unknown' if outcome == 'unknown' else 'waiting', reason=outcome)
            if category:
                r['failure_category'] = category
            self._put(db, r)

    def resume(self, request_id):
        """Restore a missing capability/QC checkpoint without clearing any receipt."""
        with self._db() as db:
            r = self._get(db, request_id)
            if r.get('retired') or r['state'] != 'waiting' or r.get('reason') != 'unavailable':
                raise ValueError('Only capability or QC waiting can resume')
            r['state'] = r.get('resume_state', 'qc_pending')
            self._put(db, r)

    def consume_review_resume(self, request_id, intent_id, review_seq):
        if not isinstance(intent_id, str) or not intent_id or len(intent_id) > 100:
            raise ValueError('Valid review intent required')
        with self._db() as db:
            r = self._get(db, request_id)
            if r.get('retired') or r['task_type'] != 'image.generate':
                raise ValueError('Original request is not active')
            if type(review_seq) is not int or review_seq <= 0 or r.get('review_seq') != review_seq:
                raise ValueError('Review sequence mismatch')
            if r.get('resume_intent_id') == intent_id:
                return bool(r.get('resume_ack_pending'))
            if r.get('resume_ack_pending') or r['state'] != 'waiting' or r.get('reason') != 'candidate_qc_unsettled':
                raise ValueError('Review is not waiting for a new intent')
            attempts = r.get('quality_cycle', {}).get('attempts', [])
            if attempts and attempts[-1].get('status') == 'review':
                attempts[-1]['status'] = 'review_pending'
            r['active_review_binding'] = {'attempt_id': attempts[-1].get('attempt_id') if attempts else None,
                                          'review_seq': review_seq, 'intent_id': intent_id}
            r.update(state='qc_pending', resume_intent_id=intent_id, resume_ack_pending=True)
            self._put(db, r)
            return True

    def ensure_review_sequence(self, request_id):
        """Migrate an old unresolved candidate wait without resuming it."""
        with self._db() as db:
            r = self._get(db, request_id)
            if r.get('retired') or r['state'] != 'waiting' or r.get('reason') != 'candidate_qc_unsettled':
                raise ValueError('Candidate is not waiting for review')
            if 'review_seq' not in r:
                r['review_seq'] = 1
                self._put(db, r)
            return r['review_seq']

    def review_evidence(self, request_id):
        """Return only a candidate paired with its own complete QC report."""
        with self._db() as db:
            r = self._get(db, request_id)
            if r.get('retired') or r.get('state') != 'waiting' or r.get('reason') not in ('candidate_qc_unsettled', 'best_of_budget_review_required'):
                raise ValueError('Candidate is not awaiting a decision')
            attempts = r.get('quality_cycle', {}).get('attempts', [])
            selected = r.get('best_of_budget') if r.get('reason') == 'best_of_budget_review_required' else None
            number = selected.get('attempt_number') if selected else len(attempts)
            if type(number) is not int or not 1 <= number <= len(attempts):
                raise ValueError('Candidate attempt unavailable')
            attempt = attempts[number - 1]
            upload = r.get('uploads', {}).get('candidate' if number == 1 else f'candidate_{number}') or {}
            digest = upload.get('sha256')
            report = attempt.get('full_qc_report') or r.get('qc')
            if (not isinstance(report, dict) or (report.get('quality_binding') or {}).get('candidate_sha256') != digest
                    or attempt.get('digest') != digest or not str(upload.get('artifact_id', '')).isdigit()
                    or int(upload['artifact_id']) <= 0 or not attempt.get('attempt_id')
                    or (selected and (str(selected.get('artifact_id')) != str(upload['artifact_id'])
                                      or selected.get('sha256') != digest or selected.get('attempt_id') != attempt['attempt_id']))):
                raise ValueError('Candidate and QC report are not bound')
            return {'request_id': request_id, 'review_seq': r.get('review_seq'),
                    'candidate_artifact_id': int(upload['artifact_id']), 'candidate_sha256': digest,
                    'candidate_attempt_id': attempt['attempt_id'], 'candidate_attempt_number': number,
                    'report': copy.deepcopy(report)}

    def consume_image_decision(self, request_id, decision, event_id):
        """Consume a server-recorded decision once, without changing its QC report."""
        if not isinstance(decision, dict) or decision.get('action') not in ('manual_accept','regenerate') or type(event_id) is not int or event_id <= 0:
            raise ValueError('Invalid recorded image decision')
        intent = decision.get('intent_id')
        if not isinstance(intent, str) or not re.fullmatch(r'[0-9a-f]{32}', intent):
            raise ValueError('Invalid image decision intent')
        current = self.inspect(request_id)
        if current.get('decision_intent_id') == intent:
            if current.get('decision_event_id') != event_id or current.get('decision_digest') != canonical_hash(decision):
                raise ValueError('Decision replay differs')
            return False
        evidence = self.review_evidence(request_id)
        for key in ('request_id','review_seq','candidate_artifact_id','candidate_sha256','candidate_attempt_id','candidate_attempt_number'):
            if decision.get(key) != evidence.get(key):
                raise ValueError('Decision candidate binding mismatch')
        if decision.get('report_hash') != canonical_hash(evidence['report']):
            raise ValueError('Decision report binding mismatch')
        with self._db() as db:
            r = self._get(db, request_id)
            if r.get('retired') or r.get('state') != 'waiting' or r.get('review_seq') != evidence['review_seq']:
                raise ValueError('Decision became stale')
            number = evidence['candidate_attempt_number']
            upload = r['uploads']['candidate' if number == 1 else f'candidate_{number}']
            if upload.get('sha256') != evidence['candidate_sha256'] or int(upload.get('artifact_id') or 0) != evidence['candidate_artifact_id']:
                raise ValueError('Candidate changed')
            r.update(decision_intent_id=intent, decision_event_id=event_id, decision_digest=canonical_hash(decision),
                     resume_intent_id=intent, resume_ack_pending=True)
            if decision['action'] == 'manual_accept':
                image = {k:v for k,v in upload.items() if k != 'path'}
                image.update(image_id='candidate',order=0,role='generated',source='codex-session:'+request_id,evidence_ids=['host'])
                r.update(state='resolved', result={'schema_version':'1','request_id':request_id,'image':image,
                    'evidence':[{'id':'host','source':'codex-session:'+request_id,'observed_at':datetime.now(timezone.utc).isoformat()}],
                    'visual_qc':{'status':'manual_accepted','candidate_sha256':evidence['candidate_sha256'],
                                 'report':evidence['report'],'decision_event_id':event_id}})
            else:
                r.setdefault('quality_round_history', []).append({'quality_cycle':copy.deepcopy(r.get('quality_cycle')),
                    'uploads':copy.deepcopy(r.get('uploads')), 'qc':copy.deepcopy(r.get('qc')), 'decision':copy.deepcopy(decision)})
                defect = (evidence['report'].get('skill_report') or {}).get('primary_defect')
                r['manual_retry_correction'] = CORRECTIONS.get(defect, 'Match all visible garment details and color to the reference images.')
                for key in ('quality_cycle','best_of_budget','active_review_binding','qc'):
                    r.pop(key, None)
                r['uploads'] = {}
                r.update(state='prepared',reason='user_regeneration',resume_intent_id=intent,resume_ack_pending=True)
            self._put(db, r)
            return True

    def consume_best_resume(self, request_id, intent_id, review_seq, selected):
        if not isinstance(intent_id, str) or not intent_id or len(intent_id) > 100:
            raise ValueError('Valid review intent required')
        with self._db() as db:
            r = self._get(db, request_id)
            if r.get('retired') or r['task_type'] != 'image.generate' or r.get('review_seq') != review_seq or r.get('best_of_budget') != selected:
                raise ValueError('Selected candidate binding mismatch')
            if r.get('resume_intent_id') == intent_id:
                return bool(r.get('resume_ack_pending'))
            if r.get('resume_ack_pending') or r['state'] != 'waiting' or r.get('reason') != 'best_of_budget_review_required':
                raise ValueError('Selected candidate is not awaiting review')
            attempt = r['quality_cycle']['attempts'][selected['attempt_number'] - 1]
            key = 'candidate' if selected['attempt_number'] == 1 else selected['alias']
            upload = r['uploads'][key]
            if (attempt.get('attempt_id') != selected['attempt_id'] or attempt.get('digest') != selected['sha256'] or
                    upload.get('sha256') != selected['sha256'] or upload.get('artifact_id') != selected['artifact_id']):
                raise ValueError('Selected candidate receipt mismatch')
            r.update(state='best_qc_pending', candidate_path=upload['path'], resume_intent_id=intent_id, resume_ack_pending=True,
                     active_review_binding={'attempt_id': selected['attempt_id'], 'review_seq': review_seq, 'intent_id': intent_id})
            self._put(db, r)
            return True

    def acknowledge_review_resume(self, request_id, intent_id):
        with self._db() as db:
            r = self._get(db, request_id)
            if r.get('retired') or r.get('resume_intent_id') != intent_id:
                raise ValueError('Review intent mismatch')
            r['resume_ack_pending'] = False
            self._put(db, r)

    def result(self, request_id):
        with self._db() as db:
            r = self._get(db, request_id)
            return copy.deepcopy(r.get('result')) if r['state'] == 'resolved' and not r.get('resume_ack_pending') else None

    def submit(self, request_id, result, capabilities):
        with self._db() as db:
            r = self._get(db, request_id)
            if r['task_type'] == 'research.bestseller':
                if r['state'] == 'resolved' and r.get('result') == result:
                    return
                raise ValueError('Research requires a bound collector evidence import')
            capability_kind = r['task_type']
            if capability_kind == 'product.facts' and not r['images'] and all(f.get('category') == 'known' for f in result.get('facts', [])):
                capability_kind = 'research.keywords'
            if capability_kind == 'research.keywords' and r['task_type'] == 'product.facts' and capabilities.get('runtime') == 'local_snapshot':
                if not capabilities.get('provenance'):
                    raise ValueError('Snapshot provenance required')
            else:
                _capabilities(capabilities, capability_kind)
            if r['task_type'] == 'image.generate':
                raise ValueError('Use actual local image import and QC')
            validate_result(r['task_type'], result)
            if r['task_type'] == 'image.qc':
                if r.get('retired') or r['state'] not in ('prepared', 'resolved') or not r['context'].get('qc_target_artifact_id'):
                    raise ValueError('QC requires verified target and active request')
                self._verify_views(r, result.get('viewed_digests'))
                checks = result.get('checks', [])
                if len(checks) != len(QC_CHECKS) or {c.get('name') for c in checks} != set(QC_CHECKS):
                    raise ValueError('Complete visible QC checks required')
                from .outfit_quality import validate_binding, evaluate
                candidate = r['payload']['image_artifact_id']
                digest = r['context']['artifacts'][candidate]['digest']
                plan = r['context']['artifacts'][r['payload']['generation_plan_id']]['payload']
                binding = {'request_id': request_id, 'input_digest': canonical_hash(result['viewed_digests']),
                           'plan_digest': canonical_hash(plan), 'candidate_sha256': digest}
                validate_binding(binding, result.get('quality_binding'))
                quality = evaluate(result.get('skill_report'), 'candidate_1', digest,
                                   hashlib.sha256(Path(r['images'][candidate]).read_bytes()).hexdigest(), 1)
                expected = ('failed' if any(c.get('status') == 'failed' for c in checks) or quality == 'reject'
                            else 'review' if quality != 'early_qc_pass' or any(c.get('status') == 'unknown' for c in checks)
                            else 'passed')
                if result.get('status') != expected:
                    raise ValueError('QC status conflicts with visual gates')
            if r['task_type'] == 'gallery.collect':
                manifest=r.get('gallery_source_manifest')
                if (r['payload'].get('selected_variant') or {}).get('library_id') and (not manifest or (result.get('browser_observation') or {}).get('observed_variant') != r.get('gallery_observed_variant') or [x.get('source') for x in result.get('images',[])] != manifest['images']):
                    raise ValueError('Bound gallery manifest required')
                if any(result[k] != r['payload'][k] for k in ('reference_item_id', 'selected_variant')):
                    raise ValueError('Observed SKU differs from requested SKU')
                observation = result.get('browser_observation', {})
                if (not observation.get('selected_variant_verified') or not observation.get('gallery_only_verified')
                        or not observation.get('page_url') or not observation.get('screenshot_path')
                        or not Path(observation['screenshot_path']).is_file()
                        or observation.get('blocked') is not False
                        or (result['completeness_status'] == 'complete' and observation.get('observed_gallery_count') != len(result['images']))):
                    raise ValueError('Visible selected SKU and gallery evidence required')
                raw, _, _, _ = _raster(observation['screenshot_path'])
                observation['screenshot_path'] = self._file(raw)
                observation['screenshot_sha256'] = hashlib.sha256(raw).hexdigest()
            if r['task_type'] == 'image.qc' and result['image_artifact_id'] != r['payload']['image_artifact_id']:
                raise ValueError('QC identity mismatch')
            if r['state'] == 'resolved' and r['result'] != result:
                raise ValueError('Result immutable')
            r.update(state='resolved', result=result, capabilities=capabilities)
            self._put(db, r)

    def _upload(self, request_id, path, client, key, attempt_id=None):
        raw, mime, (width, height), _ = _raster(path)
        digest = hashlib.sha256(raw).hexdigest()
        with self._db() as db:
            r = self._get(db, request_id)
            if r.get('retired'):
                raise ValueError('Retired lease cannot upload')
            if key == 'candidate' or re.fullmatch(r'candidate_[23]', key):
                attempt = r.get('quality_cycle', {}).get('attempts', [{}])[-1]
                number = attempt.get('number', 1)
                expected_key = 'candidate' if number == 1 else f'candidate_{number}'
                if key != expected_key or ((number > 1 or attempt_id is not None) and attempt.get('attempt_id') != attempt_id):
                    raise ValueError('Image upload attempt mismatch')
            if (key == 'candidate' or re.fullmatch(r'candidate_[23]', key)) and r.get('image_result_digest') and r['image_result_digest'] != digest:
                raise ValueError('Candidate differs from saved image result')
            uploads = r.setdefault('uploads', {})
            existing = uploads.get(key)
            if existing:
                if existing['sha256'] != digest:
                    raise ValueError('Candidate bytes immutable')
                if not existing.get('artifact_id'):
                    raise ValueError('Upload outcome unknown; reconcile original upload')
                return existing
            uploads[key] = {'sha256': digest, 'path': self._file(raw), 'mime_type': mime,
                            'width': width, 'height': height}
            previous = r['state']
            r['upload_resume_state'] = previous
            r['state'] = 'upload_unknown'
            self._put(db, r)
            case_id = r['context']['snapshot']['case_id']
        receipt = client.create_artifact(case_id, data=raw, mime_type=mime)
        if receipt['digest'] != digest:
            raise ValueError('Upload receipt digest mismatch')
        with self._db() as db:
            r = self._get(db, request_id)
            current = r['uploads'][key]
            if current['sha256'] != digest:
                raise ValueError('Upload receipt candidate changed')
            if current.get('artifact_id'):
                if current['artifact_id'] != str(receipt['artifact_id']):
                    raise ValueError('Conflicting upload receipt')
                return current
            if r['state'] != 'upload_unknown' or r.get('upload_resume_state') != previous:
                raise ValueError('Upload receipt no longer pending')
            current['artifact_id'] = str(receipt['artifact_id'])
            if not r.get('retired'):
                r['state'] = previous
            self._put(db, r)
            return current

    def image(self, request_id, path, client, attempt_id=None, invocation_id=None):
        with self._db() as db:
            r = self._get(db, request_id)
            if r['task_type'] != 'image.generate' or r['state'] not in ('dispatched', 'unknown', 'image_result_ready', 'upload_unknown', 'qc_pending', 'resolved'):
                raise ValueError('No dispatched generation to import')
            _verify_invocation(r, invocation_id)
            attempt = r.get('quality_cycle', {}).get('attempts', [{}])[-1]
            number = attempt.get('number', 1)
            captured_attempt_id = attempt.get('attempt_id')
            if (number > 1 or attempt_id is not None) and attempt.get('attempt_id') != attempt_id:
                raise ValueError('Image result attempt mismatch')
            key = 'candidate' if number == 1 else f'candidate_{number}'
            persist = r['state'] in ('dispatched', 'unknown') and not r.get('retired')
        if persist:
            raw, _, _, _ = _raster(path)
            digest = hashlib.sha256(raw).hexdigest()
            saved = self._file(raw)
            self.save_image_result(request_id, saved, digest, attempt_id, invocation_id)
            path = saved
        upload = self._upload(request_id, path, client, key, attempt_id)
        with self._db() as db:
            r = self._get(db, request_id)
            _verify_invocation(r, invocation_id)
            active = r.get('quality_cycle', {}).get('attempts', [{}])[-1]
            if active.get('number', 1) != number or active.get('attempt_id') != captured_attempt_id:
                raise ValueError('Image completion attempt mismatch')
            if r['state'] == 'resolved':
                return
            if r['state'] == 'qc_pending':
                if r.get('candidate_path') != upload['path']:
                    raise ValueError('Image completion differs from original candidate')
                return
            if r['state'] not in ('dispatched', 'unknown', 'image_result_ready'):
                raise ValueError('Image completion state changed')
            if r.get('quality_cycle'):
                from .outfit_attempts import record_candidate
                attempt = r['quality_cycle']['attempts'][-1]
                if attempt['status'] in ('dispatched', 'unknown'):
                    r = record_candidate(r, attempt['number'], upload['sha256'])
                elif attempt.get('digest') != upload['sha256']:
                    raise ValueError('Attempt candidate immutable')
            r.update(candidate_path=upload['path'], state='qc_pending')
            self._put(db, r)

    def gallery(self, request_id, result, files, client, capabilities, *, manifest=None, fence_request=lambda:None):
        """Import browser-exported original files; Python performs no remote downloads."""
        with self._db() as db:
            r = self._get(db, request_id)
            if r['task_type'] != 'gallery.collect' or r.get('retired') or r['state'] not in ('prepared','upload_unknown'):
                raise ValueError('Active gallery request required')
            _capabilities(capabilities, 'gallery.collect')
            variant=r['payload'].get('selected_variant') or {}
            if variant.get('library_id'):
                from urllib.parse import urlsplit
                if not isinstance(manifest,dict) or set(manifest)!={'detail_url','images'} or manifest['detail_url'] != variant.get('product_url'):
                    raise ValueError('Manifest must match requested product')
                from .collector_import import _identity
                candidate=variant.get('candidate')
                expected_id=r['payload']['reference_item_id']
                platform='mercado-libre-mx' if str(candidate).upper().startswith('MLM') else 'shein-us'
                identity,_=_identity(manifest['detail_url'],platform)
                if identity != expected_id or candidate != expected_id:
                    raise ValueError('Product URL identity differs from requested item')
                urls=manifest['images']; images=result.get('images') or []
                obs=result.get('browser_observation') or {}
                actual=obs.get('observed_variant')
                if (not isinstance(actual,dict) or not actual or len(actual)>20
                    or any(not isinstance(k,str) or not k.strip() or k in ('candidate','library_id','product_url') or not isinstance(v,str) or not v.strip() or len(v)>200 for k,v in actual.items())):
                    raise ValueError('Visible selected variant attributes required')
                if (not isinstance(urls,list) or not urls or len(set(urls))!=len(urls)
                    or len(images)!=len(urls) or [x.get('source') for x in images]!=urls
                    or [x.get('order') for x in images]!=list(range(len(urls)))
                    or len({x.get('image_id') for x in images})!=len(images)
                    or set(files)!=set(x.get('image_id') for x in images)
                    or result.get('selected_variant') != variant or result.get('reference_item_id') != r['payload']['reference_item_id']
                    or not obs.get('selected_variant_verified') or not obs.get('gallery_only_verified') or obs.get('blocked') is not False
                    or obs.get('observed_gallery_count')!=len(urls) or obs.get('page_url','').split('#')[0].split('?')[0] != manifest['detail_url']):
                    raise ValueError('Complete ordered selected-variant observation required')
                for url in urls:
                    if not isinstance(url,str) or len(url)>2048: raise ValueError('Invalid image URL')
                    u=urlsplit(url)
                    if u.scheme!='https' or not u.hostname or u.username or u.password: raise ValueError('HTTPS image URL required')
                _raster(obs.get('screenshot_path'))
                previous_variant=r.get('gallery_observed_variant')
                if previous_variant is not None and previous_variant != actual: raise ValueError('Observed variant immutable')
                r['gallery_observed_variant']=copy.deepcopy(actual)
                existing=r.get('gallery_source_manifest')
                if existing is not None and existing!=manifest: raise ValueError('Gallery manifest immutable')
                r['gallery_source_manifest']=copy.deepcopy(manifest);self._put(db,r)
        result = copy.deepcopy(result)
        for image in result['images']:
            fence_request()
            upload = self._upload(request_id, files[image['image_id']], client, image['image_id'])
            image.update({k: upload[k] for k in ('artifact_id', 'sha256', 'mime_type', 'width', 'height')})
        result['gallery_manifest_hash'] = canonical_hash({'selected_variant': result['selected_variant'], 'images': result['images']})
        fence_request()
        self.submit(request_id, result, capabilities)

    def reconcile(self, request_id, key, artifact_id, client):
        """Bind a known existing artifact after a lost upload receipt; never upload."""
        artifact = client.artifact(artifact_id)
        with self._db() as db:
            r = self._get(db, request_id)
            upload = r['uploads'][key]
            snapshot = r['context']['snapshot']
            if (type(snapshot.get('case_id')) is not int or snapshot['case_id'] <= 0 or
                    type(snapshot.get('company_id')) is not int or snapshot['company_id'] <= 0 or
                    artifact.get('case_id') != snapshot['case_id'] or artifact.get('company_id') != snapshot['company_id']):
                raise ValueError('Artifact ownership mismatch')
            raw = base64.b64decode(artifact['data'], validate=True)
            if hashlib.sha256(raw).hexdigest() != upload['sha256'] or artifact['mime_type'] != upload['mime_type']:
                raise ValueError('Artifact does not match original upload')
            if upload.get('artifact_id'):
                if upload['artifact_id'] != str(artifact_id):
                    raise ValueError('Upload receipt is immutable')
                return
            if r['state'] != 'upload_unknown':
                raise ValueError('No pending unknown upload')
            upload['artifact_id'] = str(artifact_id)
            if not r.get('retired'):
                r['state'] = r.get('upload_resume_state') or ('dispatched' if r['task_type'] == 'image.generate' else 'prepared')
            self._put(db, r)

    def qc(self, request_id, report):
        with self._db() as db:
            r = self._get(db, request_id)
            if r['state'] == 'resolved' and r.get('qc') == report:
                return
            best = r['state'] == 'best_qc_pending'
            if r['state'] not in ('qc_pending', 'best_qc_pending'):
                raise ValueError('Candidate not pending QC')
            if r.get('resume_ack_pending'):
                raise ValueError('Review resume acknowledgement pending')
            attempts = r.get('quality_cycle', {}).get('attempts', [{}])
            bound_attempt = attempts[r['best_of_budget']['attempt_number'] - 1] if best else attempts[-1]
            expected_review = _review_round(r, bound_attempt, best)
            if report.get('review_binding') != expected_review:
                raise ValueError('Review round binding mismatch')
            self._verify_views(r, report.get('viewed_digests'), r['ordered_image_paths'] + [r['candidate_path']])
            checks = report.get('checks', [])
            if (len(checks) != len(QC_CHECKS) or {c.get('name') for c in checks} != set(QC_CHECKS)
                    or any(c.get('status') not in ('passed', 'failed', 'unknown') or not c.get('evidence') for c in checks)):
                raise ValueError('Complete visible QC checks required')
            from .outfit_quality import validate_binding, evaluate
            attempts = r.get('quality_cycle', {}).get('attempts', [{}])
            attempt = attempts[r['best_of_budget']['attempt_number'] - 1] if best else attempts[-1]
            number = attempt.get('number', 1)
            key = 'candidate' if number == 1 else f'candidate_{number}'
            digest = r['uploads'][key]['sha256']
            binding = {'request_id': request_id, 'input_digest': canonical_hash(r['generation_digests']),
                       'plan_digest': canonical_hash(r['plan']), 'candidate_sha256': digest}
            validate_binding(binding, report.get('quality_binding'))
            actual_digest = hashlib.sha256(Path(r['candidate_path']).read_bytes()).hexdigest()
            quality = evaluate(report.get('skill_report'), f'candidate_{number}', digest, actual_digest, number, infographic=r.get('confirmed_inventory') is not None)
            # Keep the complete report with its own attempt. The latest report
            # may belong to a different image than the selected best candidate.
            attempt['full_qc_report'] = copy.deepcopy(report)
            if best:
                from .outfit_quality import parse_report, early_accept
                parsed = parse_report(json.dumps(report['skill_report'], allow_nan=False), infographic=r.get('confirmed_inventory') is not None)
                quality = 'early_qc_pass' if parsed.decision == 'accept' and early_accept(parsed, infographic=r.get('confirmed_inventory') is not None) else 'review'
                r.setdefault('best_review_history', []).append(copy.deepcopy(report))
            r['qc'] = report
            if (quality == 'reject' and not any(c['status'] == 'unknown' for c in checks)
                    and r.get('quality_cycle') and (number == 3 or report['skill_report'].get('primary_defect') in CORRECTIONS)):
                from .outfit_attempts import review
                r, decision = review(r, number, report['skill_report'], digest, infographic=r.get('confirmed_inventory') is not None)
                if decision == 'reject':
                    r.update(state='prepared', reason='candidate_rejected')
                elif decision == 'comparison_pending':
                    r.update(state='comparison_pending', reason='best_of_budget_comparison_required')
                else:
                    r.update(state='waiting', reason='candidate_qc_unsettled', resume_state='qc_pending', review_seq=r.get('review_seq', 0)+1)
            elif quality != 'early_qc_pass' or any(c['status'] != 'passed' for c in checks):
                r.update(state='waiting', reason='best_of_budget_review_required' if best else 'candidate_qc_unsettled',
                         resume_state='best_qc_pending' if best else 'qc_pending', review_seq=r.get('review_seq', 0)+1)
            else:
                if r.get('quality_cycle') and not best:
                    from .outfit_attempts import review
                    r, decision = review(r, number, report['skill_report'], digest, infographic=r.get('confirmed_inventory') is not None)
                    if decision != 'early_qc_pass':
                        raise ValueError('Quality cycle decision mismatch')
                image = {k: v for k, v in r['uploads'][key].items() if k != 'path'}
                image.update(image_id='candidate', order=0, role='generated', source='codex-session:' + request_id, evidence_ids=['host'])
                r.update(state='resolved', result={'schema_version': '1', 'request_id': request_id,
                    'image': image, 'evidence': [{'id': 'host', 'source': 'codex-session:' + request_id,
                        'observed_at': datetime.now(timezone.utc).isoformat()}], 'visual_qc': {
                            'status': 'passed', 'candidate_sha256': digest, 'report': report}})
            self._put(db, r)

    def compare(self, request_id, evidence):
        """Record a fresh native visual comparison; selection is not QC pass."""
        with self._db() as db:
            r = self._get(db, request_id)
            if r.get('retired') or r['state'] != 'comparison_pending':
                raise ValueError('Fresh comparison unavailable')
            uploads = [r['uploads']['candidate' if n == 1 else f'candidate_{n}'] for n in range(1, 4)]
            paths = r['ordered_image_paths'] + [u['path'] for u in uploads]
            self._verify_views(r, evidence.get('viewed_digests'), paths)
            digests = {f'candidate_{n}': u['sha256'] for n, u in enumerate(uploads, 1)}
            from .outfit_attempts import compare
            r = compare(r, evidence.get('reports'), digests, infographic=r.get('confirmed_inventory') is not None)
            selection = r['quality_cycle']['selection']
            number = int(selection['alias'].removeprefix('candidate_'))
            selected_upload = uploads[number - 1]
            if not str(selected_upload.get('artifact_id', '')).isdigit() or int(selected_upload['artifact_id']) <= 0:
                raise ValueError('Selected candidate needs a verified artifact receipt')
            selected_attempt = r['quality_cycle']['attempts'][number - 1]
            r.update(state='waiting', reason='best_of_budget_review_required', review_seq=r.get('review_seq', 0)+1,
                     best_of_budget={'alias': selection['alias'], 'artifact_id': selected_upload['artifact_id'],
                                     'sha256': selected_upload['sha256'], 'attempt_number': number,
                                     'attempt_id': selected_attempt['attempt_id']})
            self._put(db, r)


class SessionProvider:
    durable_session = True

    def __init__(self, store):
        self.store = store

    def synchronize(self, worker_records):
        self.store.synchronize(worker_records)

    def run(self, task_type, payload, context, request_id):
        self.store.enqueue(request_id, task_type, payload, context)
        record = self.store.inspect(request_id)
        if record.get('retired'):
            raise SessionPending()
        if record['state'] == 'waiting' and record.get('reason') in SessionBlocked.MESSAGES:
            raise SessionBlocked(record['reason'])
        if record['state'] == 'terminal':
            raise SessionFailure(record['reason'])
        if record['state'] == 'unknown' and task_type in ('research.bestseller', 'research.keywords', 'product.facts'):
            raise SessionUnknown(record.get('dispatch_diagnostic'))
        result = self.store.result(request_id)
        if result is None:
            raise SessionPending()
        return result


def worker_records(journal_path, *, task_id=None):
    """Read authoritative Worker history without taking its writer lock."""
    uri = Path(journal_path).expanduser().resolve().as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        if task_id is None:
            rows = db.execute('SELECT record FROM runs')
        else:
            if type(task_id) is not int or task_id <= 0:
                raise ValueError('Positive task ID required')
            rows = db.execute('SELECT record FROM runs WHERE id GLOB ?', (f'{task_id}:*',))
        records = [json.loads(row[0]) for row in rows]
    finally:
        db.close()
    return records


def synchronize_live(client, store, journal_path):
    from .client import APIError
    observed_ids = store.request_ids()
    active = []
    progress = []
    for record in worker_records(journal_path):
        if record.get('done'):
            continue
        try:
            response = client.heartbeat(record['claim'])
        except APIError as exc:
            if exc.status not in (403, 404, 409):
                raise
        else:
            active.append(record)
            progress.append((record, response if isinstance(response, dict) else {}))
    store.synchronize(active, observed_ids)
    for record, response in progress:
        request_id = record.get('request_id')
        if request_id not in observed_ids:
            continue
        local = store.inspect(request_id)
        if local.get('retired') or local.get('task_type') != 'image.generate':
            continue
        remote = response.get('host_progress') or {}
        review_seq = local.get('review_seq')
        if (remote.get('code') == 'image_decision_requested' and remote.get('request_id') == request_id
                and remote.get('review_seq') == review_seq):
            decision, event_id = remote.get('decision'), remote.get('decision_event_id')
            store.consume_image_decision(request_id, decision, event_id)
            client.host_progress(record['claim'], request_id, 'resumed', decision['intent_id'], review_seq)
            store.acknowledge_review_resume(request_id, decision['intent_id'])
            continue
        if (remote.get('code') == 'host_review_resumed' and local.get('resume_ack_pending')
                and local.get('decision_intent_id') == remote.get('intent_id')
                and remote.get('request_id') == request_id and remote.get('review_seq') == review_seq):
            store.acknowledge_review_resume(request_id, remote['intent_id'])
            continue
        if (local.get('state') == 'waiting' and remote.get('code') == 'host_review_required'
                and remote.get('request_id') == request_id and remote.get('review_seq') == review_seq
                and callable(getattr(client, 'host_review_evidence', None))):
            try:
                evidence = store.review_evidence(request_id)
            except ValueError:
                # Historical candidates lacking a bound full report remain waiting.
                evidence = None
            if evidence and remote.get('review_evidence') != evidence:
                client.host_review_evidence(record['claim'], evidence)
        if local.get('state') == 'waiting' and local.get('reason') == 'target_color_unconfirmed':
            if remote.get('code') != 'host_color_required' or remote.get('request_id') != request_id:
                client.host_progress(record['claim'], request_id, 'color_required', review_seq=1)
            continue
        if local.get('state') == 'waiting' and local.get('reason') == 'best_of_budget_review_required':
            selected = local['best_of_budget']
            matched = (remote.get('request_id') == request_id and remote.get('review_seq') == review_seq and
                       remote.get('review_kind') == 'best_of_budget' and
                       remote.get('candidate_artifact_id') == int(selected['artifact_id']) and
                       remote.get('candidate_sha256') == selected['sha256'] and
                       remote.get('candidate_attempt_id') == selected['attempt_id'] and
                       remote.get('candidate_attempt_number') == selected['attempt_number'])
            if remote.get('code') == 'host_resume_requested' and matched:
                intent = remote.get('intent_id')
                if store.consume_best_resume(request_id, intent, review_seq, selected):
                    client.host_progress(record['claim'], request_id, 'resumed', intent, review_seq)
                    store.acknowledge_review_resume(request_id, intent)
            elif remote.get('code') in ('host_review_required', 'host_resume_requested') and remote.get('review_seq') == review_seq and not matched:
                raise ValueError('Remote selected candidate binding mismatch')
            elif remote.get('code') != 'host_review_required' or not matched:
                client.host_progress(record['claim'], request_id, 'review_required', review_seq=review_seq,
                    review_kind='best_of_budget', candidate_artifact_id=int(selected['artifact_id']),
                    candidate_sha256=selected['sha256'], candidate_attempt_id=selected['attempt_id'],
                    candidate_attempt_number=selected['attempt_number'])
            continue
        if local.get('state') == 'waiting' and local.get('reason') == 'candidate_qc_unsettled' and review_seq is None:
            review_seq = store.ensure_review_sequence(request_id)
        requested = (remote.get('code') == 'host_resume_requested' and remote.get('request_id') == request_id and
                     type(review_seq) is int and review_seq > 0 and remote.get('review_seq') == review_seq)
        intent_id = remote.get('intent_id') if requested else None
        if local.get('state') == 'waiting' and local.get('reason') == 'candidate_qc_unsettled':
            if requested:
                if store.consume_review_resume(request_id, intent_id, review_seq):
                    client.host_progress(record['claim'], request_id, 'resumed', intent_id, review_seq)
                    store.acknowledge_review_resume(request_id, intent_id)
            elif (remote.get('code') != 'host_review_required' or remote.get('request_id') != request_id or
                  remote.get('review_seq') != review_seq):
                client.host_progress(record['claim'], request_id, 'review_required', review_seq=review_seq)
        elif local.get('state') in ('qc_pending', 'best_qc_pending') and requested and local.get('resume_ack_pending'):
            if local.get('resume_intent_id') != intent_id:
                raise ValueError('Review acknowledgement intent mismatch')
            if local.get('state') == 'best_qc_pending':
                selected = local['best_of_budget']
                if (remote.get('review_kind') != 'best_of_budget' or remote.get('candidate_artifact_id') != int(selected['artifact_id']) or
                    remote.get('candidate_sha256') != selected['sha256'] or remote.get('candidate_attempt_id') != selected['attempt_id'] or
                    remote.get('candidate_attempt_number') != selected['attempt_number']):
                    raise ValueError('Review acknowledgement candidate mismatch')
            client.host_progress(record['claim'], request_id, 'resumed', intent_id, review_seq)
            store.acknowledge_review_resume(request_id, intent_id)
        elif (local.get('state') in ('qc_pending', 'best_qc_pending') and local.get('resume_ack_pending') and
              remote.get('code') == 'host_review_resumed' and remote.get('request_id') == request_id and
              remote.get('intent_id') == local.get('resume_intent_id') and remote.get('review_seq') == review_seq):
            if local.get('state') == 'best_qc_pending':
                selected = local['best_of_budget']
                if (remote.get('review_kind') != 'best_of_budget' or remote.get('candidate_artifact_id') != int(selected['artifact_id']) or
                    remote.get('candidate_sha256') != selected['sha256'] or remote.get('candidate_attempt_id') != selected['attempt_id'] or
                    remote.get('candidate_attempt_number') != selected['attempt_number']):
                    raise ValueError('Review acknowledgement candidate mismatch')
            store.acknowledge_review_resume(request_id, remote['intent_id'])


def fence(client, journal_path, request_id, *, task_id=None):
    """Renew the exact original lease before any new external effect."""
    records = worker_records(journal_path, task_id=task_id)
    matches = [r for r in records if r.get('request_id') == request_id and not r.get('done') and not r.get('recovery_pending')]
    if len(matches) != 1:
        raise ValueError('Original active worker lease required')
    client.heartbeat(matches[0]['claim'])


def replay_saved_image(store, client, journal, request_id):
    """Upload an already persisted native result; never invoke generation."""
    r = store.inspect(request_id)
    if r.get('retired') or r.get('state') != 'image_result_ready':
        return 'not_ready'
    path = Path(r['image_result_path'])
    if path.parent != store.files or path.is_symlink():
        raise ValueError('Invalid stored image')
    raw, _, _, _ = _raster(path)
    if hashlib.sha256(raw).hexdigest() != r['image_result_digest']:
        raise ValueError('Stored image changed')
    fence(client, journal, request_id)
    attempts = r.get('quality_cycle', {}).get('attempts', [])
    store.image(request_id, str(path), client, attempts[-1].get('attempt_id') if attempts else None, r.get('image_result_invocation_id'))
    return 'qc_pending'


def main():
    from .client import Client
    parser = argparse.ArgumentParser(description='Codex host-session bridge; no tool calls from Python')
    parser.add_argument('command', choices=('next', 'plan', 'inventory', 'dispatch', 'submit', 'image', 'replay-image', 'gallery', 'qc', 'compare', 'error', 'reconcile', 'resume', 'inspect', 'archive-image', 'service-error', 'retry-service'))
    parser.add_argument('--request-id')
    parser.add_argument('--file', type=Path)
    parser.add_argument('--capabilities', type=Path)
    parser.add_argument('--views', type=Path)
    parser.add_argument('--files', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--outcome')
    parser.add_argument('--artifact-id')
    parser.add_argument('--attempt-id')
    parser.add_argument('--invocation-id')
    parser.add_argument('--expected-unknown-at', type=float)
    parser.add_argument('--upload-key', default='candidate')
    args = parser.parse_args()
    try:
        client = Client(os.environ.get('ODOO_OPTIMIZATION_URL', 'http://127.0.0.1:8069'), os.environ.get('ODOO_API_KEY', ''))
        store = SessionStore(os.environ.get('OPTIMIZATION_HOST_SESSION', '~/.local/state/optimization-worker/host.sqlite'), client.identity)
        command, request = args.command, args.request_id
        if command != 'next' and not request:
            raise ValueError('Request ID required')
        output = {'accepted': True}
        if command == 'next':
            synchronize_live(client, store, os.environ.get('OPTIMIZATION_JOURNAL', '~/.local/state/optimization-worker/journal.sqlite'))
        if command in ('plan', 'inventory', 'dispatch', 'image', 'gallery', 'submit', 'qc', 'compare', 'resume', 'service-error', 'retry-service', 'error'):
            fence(client, os.environ.get('OPTIMIZATION_JOURNAL', '~/.local/state/optimization-worker/journal.sqlite'), request)
        if command == 'next':
            if request:
                fence(client, os.environ.get('OPTIMIZATION_JOURNAL', '~/.local/state/optimization-worker/journal.sqlite'), request)
            output = store.next(request_id=request)
        elif command == 'inspect': output = store.inspect(request)
        elif command == 'archive-image': store.archive_image(request, args.file, args.attempt_id)
        elif command == 'plan': store.plan(request, _read_json(args.file))
        elif command == 'inventory': store.inventory(request, _read_json(args.file))
        elif command == 'dispatch': output = store.dispatch(request, _read_json(args.capabilities), _read_json(args.views))
        elif command == 'submit': store.submit(request, _read_json(args.file), _read_json(args.capabilities))
        elif command == 'image': store.image(request, args.file, client, args.attempt_id, args.invocation_id)
        elif command == 'replay-image':
            output = {'state': replay_saved_image(store, client, os.environ.get('OPTIMIZATION_JOURNAL', '~/.local/state/optimization-worker/journal.sqlite'), request)}
        elif command == 'gallery': store.gallery(request, _read_json(args.file), _read_json(args.files), client, _read_json(args.capabilities), manifest=_read_json(args.manifest) if args.manifest else None, fence_request=lambda:fence(client, os.environ.get('OPTIMIZATION_JOURNAL', '~/.local/state/optimization-worker/journal.sqlite'), request))
        elif command == 'qc':
            store.qc(request, _read_json(args.file))
            synchronize_live(client, store, os.environ.get('OPTIMIZATION_JOURNAL', '~/.local/state/optimization-worker/journal.sqlite'))
        elif command == 'compare':
            store.compare(request, _read_json(args.file))
            synchronize_live(client, store, os.environ.get('OPTIMIZATION_JOURNAL', '~/.local/state/optimization-worker/journal.sqlite'))
        elif command == 'resume': store.resume(request)
        elif command == 'error': store.error(request, args.outcome, invocation_id=args.invocation_id)
        elif command == 'service-error':
            error = _read_json(args.file)
            if set(error) - {'error', 'retry_after', 'confirmed_finished'} or not {'error', 'confirmed_finished'} <= set(error):
                raise ValueError('Invalid service error')
            store.report_service_error(request, args.invocation_id, error['error'], error.get('retry_after', 0), confirmed_finished=error['confirmed_finished'])
        elif command == 'retry-service':
            output = store.claim_service_retry(request, args.expected_unknown_at, _read_json(args.capabilities), _read_json(args.views))
        elif command == 'reconcile': store.reconcile(request, args.upload_key, args.artifact_id, client)
        print(json.dumps(output, ensure_ascii=False))
        return 0
    except Exception:
        print(json.dumps({'accepted': False, 'reason': 'Host action rejected; preserve request and inspect private state.'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

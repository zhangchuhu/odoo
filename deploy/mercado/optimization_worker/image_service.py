"""Supervise image task claims and a capability-checked Codex host."""
import os
import json
import re
import plistlib
import signal
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .keyword_worker_service import ServiceLock, codex_ready
from .host_session import SessionProvider, SessionStore
from .runtime import Worker
from .client import Client
from .research_service import UnknownRetryPoll
from .research_launchd import _private_source

def image_error_event(stage, exc, *, task_id=None, epoch=None):
    from .client import APIError
    return {'at': datetime.now(timezone.utc).isoformat(), 'stage': stage,
            'code': 'odoo_request_failed' if isinstance(exc, APIError) else 'worker_error',
            'http_status': exc.status if isinstance(exc, APIError) else None,
            'task_id': task_id, 'lease_epoch': epoch, 'exception_type': type(exc).__name__}

def image_diagnostic(stage, code, claim, exc):
    messages = {'claim_succeeded': '任务领取成功', 'claim_error': '任务领取请求失败',
                'lease_renewed': '任务租约续期成功', 'lease_rejected': '任务租约已失效或被拒绝',
                'lease_renewal_error': '任务租约续期请求失败'}
    print(json.dumps({'at': datetime.now(timezone.utc).isoformat(), 'stage': stage, 'code': code,
        'task_id': claim.get('id') if claim else None, 'lease_epoch': claim.get('lease_epoch') if claim else None,
        'http_status': getattr(exc, 'status', None), 'exception_type': type(exc).__name__ if exc else None,
        'message': messages.get(code, '图片任务执行异常')},
        ensure_ascii=False), flush=True)


class HostBackoff:
    def __init__(self, *, clock=time.monotonic, delay=60):
        self.clock = clock
        self.delay = delay
        self.until = 0

    def ready(self):
        return self.clock() >= self.until

    def record(self, outcome):
        if outcome == 'no_progress':
            self.until = self.clock() + self.delay
            print('image_host_no_progress: retrying after 60 seconds', flush=True)
        else:
            self.until = 0


def review_parked(store, record):
    if record.get('phase') != 'host_pending' or record.get('pending') or record.get('recovery_pending'):
        return False
    try:
        local = store.inspect(record['request_id'])
        return (not local.get('retired') and local.get('state') == 'waiting' and
                local.get('reason') in ('candidate_qc_unsettled', 'best_of_budget_review_required', 'target_color_unconfirmed'))
    except (KeyError, ValueError):
        return False


def build_worker(client, journal, host):
    store = SessionStore(host, client.identity)
    return Worker(client, journal, providers={'image.generate': SessionProvider(store)},
                  capabilities=('image.generate',), park=lambda record: review_parked(store, record),
                  maintain_active_leases=True, diagnostic=image_diagnostic)


def claim_loop(worker, stop, retry_poll=None, *, ready):
    while not stop.is_set():
        try:
            if retry_poll is not None:
                retry_poll.tick()
            worker.once(allow_claim=ready())
        except Exception as exc:
            print(json.dumps(image_error_event('claim_or_heartbeat', exc), ensure_ascii=False), flush=True)
        stop.wait(5)

def start_claimant(worker, stop, retry_poll, probe):
    ready = threading.Event()
    thread = threading.Thread(target=claim_loop, args=(worker, stop, retry_poll),
                              kwargs={'ready': ready.is_set}, daemon=True)
    thread.start()
    if probe():
        ready.set()
    return ready, thread

def verify_image_tool(home, *, runner=subprocess.run):
    """Exercise native image generation and verify a fresh decodable PNG."""
    from PIL import Image
    from .text_host import proxy_environment
    home = Path(home)
    generated = home / 'generated_images'
    before = {p for p in generated.glob('*/*.png') if p.is_file()}
    try:
        with tempfile.TemporaryDirectory(prefix='optimization-image-probe-') as cwd:
            env = {k: os.environ[k] for k in ('PATH', 'TMPDIR', 'LANG', 'LC_ALL') if k in os.environ}
            env.update(HOME=cwd, CODEX_HOME=str(home))
            proxy = os.environ.get('OPTIMIZATION_CODEX_PROXY', '')
            env.update(proxy_environment(proxy))
            if proxy:
                env['ALL_PROXY'] = proxy
            result = runner(['codex', 'exec', '--ignore-user-config', '-c', 'cli_auth_credentials_store="file"',
                '--sandbox', 'read-only', '--skip-git-repo-check', '--json', '-C', cwd,
                'Use the available image generation tool to create one small solid blue test image.'],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=240, check=False)
        if result.returncode != 0:
            from .image_host import codex_error, diagnostic
            code, message = codex_error((getattr(result, 'stderr', '') or '') + '\n' + (getattr(result, 'stdout', '') or ''))
            diagnostic('image_capability_probe', code, None, 'probe', exit_code=result.returncode, message=message)
            return False
        events = [json.loads(line) for line in result.stdout.splitlines()]
        ids = [e.get('thread_id') for e in events if e.get('type') == 'thread.started']
        if len(ids) != 1 or not isinstance(ids[0], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', ids[0]):
            from .image_host import diagnostic
            diagnostic('image_capability_probe', 'probe_thread_missing', None, 'probe', message='能力检查未返回有效线程标识')
            return False
        for path in set((generated / ids[0]).glob('*.png')) - before:
            if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 25_000_000:
                continue
            with Image.open(path) as image:
                if image.format != 'PNG' or image.width * image.height > 40_000_000:
                    continue
                image.verify()
            return True
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        from .image_host import diagnostic
        diagnostic('image_capability_probe', 'probe_exception', None, 'probe', message=type(exc).__name__)
        return False
    from .image_host import diagnostic
    diagnostic('image_capability_probe', 'probe_image_missing', None, 'probe', message='能力检查未生成可验证图片')
    return False


def production_probe():
    # No native image_gen executor/callback exists in the independent process.
    return False


def plist_bytes(root, private):
    root, private = Path(root), Path(private)
    if not root.is_absolute() or not private.is_absolute():
        raise ValueError('Absolute paths required')
    return plistlib.dumps({'Label': 'local.odoo.optimization-image-worker',
        'ProgramArguments': [str(root / '.venv/bin/python'), '-m', 'optimization_worker.image_service'],
        'WorkingDirectory': str(root), 'EnvironmentVariables': {'PATH': '/opt/homebrew/bin:/usr/bin:/bin',
        'PYTHONPATH': str(root / 'deploy/mercado')}, 'RunAtLoad': True, 'KeepAlive': True,
        'ThrottleInterval': 60, 'StandardOutPath': str(private / 'image-worker.stdout.log'),
        'StandardErrorPath': str(private / 'image-worker.stderr.log')})


def claim_if_ready(worker, capability_probe):
    try:
        ready = callable(capability_probe) and capability_probe() is True
    except Exception:
        ready = False
    if not ready:
        return 'image_generation_unavailable'
    return worker.once()


def main():
    root = Path(__file__).resolve().parents[3]
    private = root / '.local/optimization-codex-worker'
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    # No journal or claim is created while no real image executor is available.
    with ServiceLock(private / 'image-service.lock'):
        config = _private_source(private / 'environment.json')
        if config.get('ODOO_OPTIMIZATION_URL') != 'http://127.0.0.1:8069':
            raise ValueError('Unexpected service')
        client = Client(config['ODOO_OPTIMIZATION_URL'], config['ODOO_API_KEY'])
        retry_poll = UnknownRetryPoll(lambda: client.retry_unknown_tasks(('image.generate',)))
        os.environ.setdefault('OPTIMIZATION_CODEX_HOME', str(Path.home() / '.local/state/optimization-codex-host/auth'))
        os.environ.setdefault('OPTIMIZATION_CODEX_PROXY', 'http://127.0.0.1:7897')
        journal = str(private / 'image-worker.sqlite')
        host = str(private / 'image-host.sqlite')
        from .image_host import safe_run_one, recover_interrupted
        store = SessionStore(host, client.identity)
        recover_interrupted(store)
        worker = build_worker(client, journal, host)
        # Resume existing leases immediately; the image capability probe can take
        # several minutes and must only gate *new* claims, not renewals.
        ready, thread = start_claimant(worker, stop, retry_poll,
            lambda: codex_ready() and verify_image_tool(Path(os.environ['OPTIMIZATION_CODEX_HOME'])))
        print('image_host_ready' if ready.is_set() else 'waiting_for_image_host', flush=True)
        backoff = HostBackoff()
        while not stop.wait(5):
            if not thread.is_alive():
                raise RuntimeError('Image queue claimant stopped')
            if ready.is_set() and backoff.ready():
                backoff.record(safe_run_one(store, client, journal))
        thread.join(timeout=35)



if __name__ == '__main__':
    os.umask(0o077)
    main()

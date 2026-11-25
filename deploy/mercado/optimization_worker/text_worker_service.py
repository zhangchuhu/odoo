"""Persistent queue worker for product facts and Codex-backed keyword research."""
import os
import plistlib
import signal
import threading
from pathlib import Path
from .client import Client
from .host_session import SessionProvider, SessionStore
from .runtime import Worker
from .research_launchd import _private_source
from .keyword_worker_service import ServiceLock, claim_loop
from .text_host import safe_run_one
from .research_service import UnknownRetryPoll


def build_worker(client, journal, host):
    store = SessionStore(host, client.identity)
    return Worker(client, journal, providers={'product.facts': SessionProvider(store)}, capabilities=('product.facts',))


def execute_pending(store, client, journal):
    return safe_run_one(store, client, journal, kinds={'product.facts'})


def recover_interrupted(store):
    store.mark_interrupted_text({'product.facts'})


def plist_bytes(root, private):
    root, private = Path(root), Path(private)
    if not root.is_absolute() or not private.is_absolute():
        raise ValueError('Absolute paths required')
    return plistlib.dumps({'Label': 'local.odoo.optimization-facts-worker',
        'ProgramArguments': [str(root / '.venv/bin/python'), '-m', 'optimization_worker.text_worker_service'],
        'WorkingDirectory': str(root), 'EnvironmentVariables': {'PATH': '/opt/homebrew/bin:/usr/bin:/bin',
        'PYTHONPATH': str(root / 'deploy/mercado')}, 'RunAtLoad': True, 'KeepAlive': True,
        'ThrottleInterval': 60, 'StandardOutPath': str(private / 'facts-worker.stdout.log'),
        'StandardErrorPath': str(private / 'facts-worker.stderr.log')})


def main():
    root = Path(__file__).resolve().parents[3]
    private = root / '.local/optimization-codex-worker'
    config = _private_source(private / 'environment.json')
    if config.get('ODOO_OPTIMIZATION_URL') != 'http://127.0.0.1:8069':
        raise ValueError('Unexpected service')
    os.umask(0o077)
    client = Client(config['ODOO_OPTIMIZATION_URL'], config['ODOO_API_KEY'])
    journal, host = str(private / 'facts-worker.sqlite'), str(private / 'facts-host.sqlite')
    os.environ.setdefault('OPTIMIZATION_CODEX_HOME', str(Path.home() / '.local/state/optimization-codex-host/auth'))
    os.environ.setdefault('OPTIMIZATION_CODEX_PROXY', 'http://127.0.0.1:7897')
    store = SessionStore(host, client.identity)
    recover_interrupted(store)
    worker = build_worker(client, journal, host)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    retry_poll = UnknownRetryPoll(lambda: client.retry_unknown_tasks(('product.facts',)))
    thread = threading.Thread(target=claim_loop, args=(worker, stop, retry_poll), daemon=True)
    thread.start()
    while not stop.wait(5):
        if not thread.is_alive():
            raise RuntimeError('Facts queue claimant stopped')
        execute_pending(store, client, journal)
    thread.join(timeout=35)


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[3]
    with ServiceLock(root / '.local/optimization-codex-worker/facts-service.lock'):
        main()

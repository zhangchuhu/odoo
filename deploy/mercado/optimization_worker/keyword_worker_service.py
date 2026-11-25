"""Keyword queue claimant and Codex executor in one durable service."""
import os
import fcntl
import plistlib
import signal
import shutil
import socket
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlsplit

from .client import Client, APIError
from .host_session import SessionProvider, SessionStore
from .runtime import Worker
from .research_launchd import _private_source
from .text_host import safe_run_one
from .research_service import UnknownRetryPoll


class ServiceLock:
    def __init__(self, path):
        self.path = path
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            raise RuntimeError('Keyword service is already running') from None
        return self

    def __exit__(self, *args):
        os.close(self.fd)
        self.fd = None


def build_worker(client, journal, host):
    return Worker(client, journal, providers={'research.keywords': SessionProvider(SessionStore(host, client.identity))},
                  capabilities=('research.keywords',))


def execute_pending(store, client, journal):
    return safe_run_one(store, client, journal, kinds={'research.keywords'})


def recover_interrupted(store):
    store.mark_interrupted_text({'research.keywords'})


def codex_ready():
    executable = shutil.which('codex')
    home = os.environ.get('OPTIMIZATION_CODEX_HOME', '')
    if not executable or not os.path.isabs(home) or not os.path.isdir(home):
        return False
    try:
        from .text_host import proxy_environment
        proxy = os.environ.get('OPTIMIZATION_CODEX_PROXY', '')
        environment = {**os.environ, 'CODEX_HOME': home, **proxy_environment(proxy)}
        if proxy:
            parsed = urlsplit(proxy)
            with socket.create_connection((parsed.hostname, parsed.port), timeout=2):
                pass
        result = subprocess.run([executable, 'login', 'status'], env=environment,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        return result.returncode == 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def claim_once(worker, *, ready):
    return worker.once(allow_claim=ready)


def claim_loop(worker, stop, retry_poll=None):
    while not stop.is_set():
        try:
            if retry_poll is not None:
                retry_poll.tick()
            claim_once(worker, ready=codex_ready())
        except APIError:
            pass
        stop.wait(5)


def plist_bytes(root, private):
    root, private = Path(root), Path(private)
    if not root.is_absolute() or not private.is_absolute():
        raise ValueError("Absolute paths required")
    return plistlib.dumps({"Label": "local.odoo.optimization-keyword-worker",
        "ProgramArguments": [str(root / ".venv/bin/python"), "-m", "optimization_worker.keyword_worker_service"],
        "WorkingDirectory": str(root), "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/bin:/bin",
        "PYTHONPATH": str(root / "deploy/mercado")}, "RunAtLoad": True, "KeepAlive": True,
        "ThrottleInterval": 60, "StandardOutPath": str(private / "keyword-worker.stdout.log"),
        "StandardErrorPath": str(private / "keyword-worker.stderr.log")})


def main():
    root = Path(__file__).resolve().parents[3]
    private = root / '.local/optimization-codex-worker'
    config = _private_source(private / 'environment.json')
    if config.get('ODOO_OPTIMIZATION_URL') != 'http://127.0.0.1:8069':
        raise ValueError('Unexpected service')
    os.umask(0o077)
    client = Client(config['ODOO_OPTIMIZATION_URL'], config['ODOO_API_KEY'])
    journal = str(private / 'keyword-worker.sqlite')
    host = str(private / 'keyword-host.sqlite')
    os.environ.setdefault('OPTIMIZATION_CODEX_HOME', str(Path.home() / '.local/state/optimization-codex-host/auth'))
    os.environ.setdefault('OPTIMIZATION_CODEX_PROXY', 'http://127.0.0.1:7897')
    store = SessionStore(host, client.identity)
    recover_interrupted(store)
    worker = build_worker(client, journal, host)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    retry_poll = UnknownRetryPoll(lambda: client.retry_unknown_tasks(('research.keywords',)))
    thread = threading.Thread(target=claim_loop, args=(worker, stop, retry_poll), daemon=True)
    thread.start()
    while not stop.wait(5):
        if not thread.is_alive():
            raise RuntimeError("Keyword queue claimant stopped")
        execute_pending(store, client, journal)
    thread.join(timeout=35)


if __name__ == '__main__':
    _root = Path(__file__).resolve().parents[3]
    with ServiceLock(_root / '.local/optimization-codex-worker/keyword-service.lock'):
        main()

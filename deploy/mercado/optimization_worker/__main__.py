"""CLI secrets are read only from the environment, never arguments or logs."""
import argparse
import json
import os
from pathlib import Path
import signal
import threading

from .client import APIError, Client
from .handlers import TASK_TYPES
from .providers import OfflineProvider
from .runtime import Worker


def session_providers(provider, interactive=False):
    # Only the unattended text host has been verified and deployed. Browser
    # and image requests must wait for a separately verified executor instead
    # of remaining indefinitely in the durable-session mailbox.
    kinds = (kind for kind in TASK_TYPES if kind != 'product.facts') if interactive else ('research.keywords', 'title.generate')
    return {kind: provider for kind in kinds}


def main():
    parser = argparse.ArgumentParser(description='Provider-injected durable optimization Worker')
    parser.add_argument('mode', choices=('once', 'loop'))
    parser.add_argument('--session', action='store_true', help='Use durable native Codex host-session bridge')
    parser.add_argument('--interactive-host', action='store_true', help='Allow other task types only when an interactive Codex host is present')
    parser.add_argument('--task-id', type=int, help='Claim only this task')
    parser.add_argument('--task-type', choices=('research.bestseller', 'gallery.collect', 'image.generate', 'image.qc'), default='research.bestseller', help='Type of targeted interactive task')
    parser.add_argument('--offline', action='store_true', help='Explicitly enable user-supplied fixture results')
    parser.add_argument('--fixture', type=Path, help='JSON mapping of task types to offline results')
    args = parser.parse_args()
    if args.session and args.offline:
        parser.error('--session and --offline are mutually exclusive')
    if args.interactive_host and not args.session:
        parser.error('--interactive-host requires --session')
    if args.task_id is not None and (args.task_id < 1 or not args.interactive_host):
        parser.error('--task-id requires --interactive-host and a positive ID')
    if args.task_type != 'research.bestseller' and args.task_id is None:
        parser.error('--task-type requires --task-id')
    if args.fixture and not args.offline:
        parser.error('--fixture requires --offline')
    try:
        providers = {}
        if args.offline:
            if not args.fixture:
                parser.error('--offline requires --fixture')
            if args.fixture.stat().st_size > 256 * 1024:
                raise ValueError('Fixture exceeds size limit')
            results = json.loads(args.fixture.read_text())
            if not isinstance(results, dict) or set(results) - set(TASK_TYPES):
                raise ValueError('Fixture must map supported task types to results')
            provider = OfflineProvider(offline=True, results=results)
            providers = {kind: provider for kind in results}
        client = Client(os.environ.get('ODOO_OPTIMIZATION_URL', 'http://127.0.0.1:8069'),
                        os.environ.get('ODOO_API_KEY', ''))
        if args.session:
            from .host_session import SessionProvider, SessionStore
            store = SessionStore(os.environ.get('OPTIMIZATION_HOST_SESSION', '~/.local/state/optimization-worker/host.sqlite'), client.identity)
            provider = SessionProvider(store)
            providers = session_providers(provider, interactive=args.interactive_host)
        path = Path(os.environ.get('OPTIMIZATION_JOURNAL', '~/.local/state/optimization-worker/journal.sqlite')).expanduser()
        worker = Worker(client, path, providers=providers, task_id=args.task_id,
                        **({'capabilities': (args.task_type,)} if args.task_id is not None else {}))
        if args.mode == 'once':
            print(worker.once())
        else:
            stop = threading.Event()
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: stop.set())
            worker.loop(stop)
        return 0
    except (APIError, ValueError, OSError, RuntimeError):
        # Never stringify exceptions originating in HTTP, provider or file input.
        print('Worker could not finish; check configuration, API health and private journal.', file=__import__('sys').stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

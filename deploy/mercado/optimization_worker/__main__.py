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


def main():
    parser = argparse.ArgumentParser(description='Provider-injected durable optimization Worker')
    parser.add_argument('mode', choices=('once', 'loop'))
    parser.add_argument('--offline', action='store_true', help='Explicitly enable user-supplied fixture results')
    parser.add_argument('--fixture', type=Path, help='JSON mapping of task types to offline results')
    args = parser.parse_args()
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
        path = Path(os.environ.get('OPTIMIZATION_JOURNAL', '~/.local/state/optimization-worker/journal.sqlite')).expanduser()
        worker = Worker(client, path, providers=providers)
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

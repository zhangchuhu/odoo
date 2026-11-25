"""Create a fresh disposable database and run BFF + connector tests.

Run with the Odoo virtualenv Python. No production configuration is loaded.
Artifacts and the test database are retained for inspection; cleanup is explicit.
"""
import argparse
import importlib
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import unittest
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--odoo-root', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--addons-root', type=Path, default=Path(__file__).resolve().parents[1] / 'addons')
    parser.add_argument('--tags', default='/meli_bff,/meli_accounts')
    parser.add_argument('--reuse', type=Path, help='Artifact directory created by an earlier run of this script')
    parser.add_argument('--unit-only', action='store_true')
    args = parser.parse_args()
    root, addons = args.odoo_root.resolve(), args.addons_root.resolve()
    sys.path.insert(0, str(addons))
    suite = unittest.defaultTestLoader.loadTestsFromNames([
        'meli_bff.tests.test_protocol', 'meli_bff.tests.test_client',
    ])
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        return 1
    if args.unit_only:
        return 0
    if not (root / 'odoo-bin').is_file():
        parser.error('--odoo-root must point to the Odoo checkout')
    # Fail on missing environment dependencies before claiming any integration result.
    importlib.import_module('psycopg2')
    if args.reuse:
        work = args.reuse.resolve()
        meta = json.loads((work / 'test-run.json').read_text())
        db = meta['database']
        if not re.fullmatch(r'meli_bff_test_[a-f0-9]{20}', db):
            parser.error('Refusing a database not created by this runner')
        if meta['odoo_root'] != str(root) or meta['addons_root'] != str(addons):
            parser.error('Reused run must have the same source directories')
        mode = '-u'
    else:
        work = Path(tempfile.mkdtemp(prefix='meli-bff-tests-'))
        db = 'meli_bff_test_' + uuid.uuid4().hex[:20]
        # createdb must fail rather than overwrite an existing database.
        subprocess.run(['createdb', db], check=True)
        (work / 'test-run.json').write_text(json.dumps({
            'database': db, 'odoo_root': str(root), 'addons_root': str(addons),
        }, indent=2))
        mode = '-i'
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    log = work / 'odoo-tests.log'
    print('Database:', db, '\nArtifacts:', work, '\nLog:', log, flush=True)
    command = [sys.executable, str(root / 'odoo-bin'), '--config=/dev/null',
               '--addons-path=' + ','.join([str(addons), str(root / 'addons')]),
               '-d', db, '--db-filter=^' + db + '$', '--no-database-list',
               '--data-dir=' + str(work / 'data'), '--http-interface=127.0.0.1',
               '--http-port=' + str(port), '--without-demo=all', '--max-cron-threads=0',
               '--workers=0', '--stop-after-init', '--test-enable', '--test-tags=' + args.tags,
               '--log-level=test', mode, 'meli_accounts,meli_bff']
    with log.open('w') as output:
        result = subprocess.run(command, cwd=root, stdout=output, stderr=subprocess.STDOUT)
    content = log.read_text(errors='replace')
    summaries = re.findall(r'(\d+) failed, (\d+) error\(s\) of (\d+) tests', content)
    print('\n'.join(content.splitlines()[-25:]))
    print('Exit:', result.returncode, 'Summary:', summaries[-1:] or 'MISSING', '\nLog:', log)
    return 0 if result.returncode == 0 and summaries and all(int(n) == 0 for n in summaries[-1][:2]) and int(summaries[-1][2]) > 0 else 1


if __name__ == '__main__':
    sys.exit(main())

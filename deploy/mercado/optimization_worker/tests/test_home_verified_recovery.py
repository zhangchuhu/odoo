import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from optimization_worker.home_verification_watch import initialize, arm, observe
from optimization_worker.home_verified_recovery import resume_verified_waiting


class VerifiedRecoveryTests(unittest.TestCase):
    def test_only_verified_original_wait_is_requeued_once(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / 'collector-bindings.sqlite'
            db = sqlite3.connect(ledger)
            initialize(db)
            scope = (2, 42, 3, 1)
            binding = {'tab_id': 7, 'window_id': 8, 'session_id': 'a' * 32}
            arm(db, 'request', scope, 'search', {'query': 'vestido'}, binding, fence=lambda: True)
            observe(db, 'request', scope, binding,
                {'url': 'https://listado.mercadolibre.com.mx/vestido', 'query': 'vestido', 'blocked': False}, authorize=lambda: True)
            db.commit(); db.close()
            journal = SimpleNamespace(db=sqlite3.connect(':memory:'))
            journal.db.execute('CREATE TABLE runs (record TEXT)')
            claim = {'company_id': 2, 'id': 42, 'snapshot_id': 3, 'lease_epoch': 1, 'task_type': 'research.bestseller'}
            journal.db.execute('INSERT INTO runs VALUES (?)', (json.dumps({'request_id': 'request', 'claim': claim, 'done': True, 'outcome': 'waiting'}),))
            store = SimpleNamespace(inspect=lambda _: {'request_id': 'request', 'task_type': 'research.bestseller', 'state': 'waiting', 'reason': 'captcha_required'})
            calls = []
            client = SimpleNamespace(resume_verified_captcha=lambda *args: calls.append(args) or {'task_id': 42, 'state': 'queued'})
            self.assertEqual(resume_verified_waiting(journal, store, client, ledger, task_id=43), 0)
            self.assertEqual(resume_verified_waiting(journal, store, client, ledger), 1)
            self.assertEqual(resume_verified_waiting(journal, store, client, ledger), 0)
            self.assertEqual(calls, [(42, 1, 'request')])

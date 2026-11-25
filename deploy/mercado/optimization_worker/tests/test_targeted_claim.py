import tempfile
import unittest
from pathlib import Path

from optimization_worker.client import Client
from optimization_worker.runtime import Worker


class TargetedClaimTest(unittest.TestCase):
    def test_client_sends_target_only_when_requested(self):
        client = Client('http://localhost', 'fixture')
        calls = []
        client.request = lambda *args: calls.append(args)
        client.claim(['research.bestseller'], task_id=51)
        self.assertEqual(calls[0][1], '/internal/meli-optimization/tasks/51/claim')
        self.assertEqual(calls[0][2], {'capabilities': ['research.bestseller']})

    def test_target_worker_never_claims_other_id(self):
        class Fake:
            identity = 'test'
            def claim(self, capabilities, task_id=None):
                self.args = capabilities, task_id
                return False
        client = Fake()
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(client, Path(directory) / 'journal.sqlite', capabilities=['research.bestseller'], task_id=51)
            self.assertEqual(worker.once(), 'idle')
        self.assertEqual(client.args[1], 51)

    def test_invalid_target_rejected(self):
        with self.assertRaises(ValueError):
            Worker(object(), '/tmp/unused', task_id=True)

    def test_cli_can_target_gallery_without_widening_claim(self):
        from unittest.mock import patch
        from optimization_worker import __main__ as cli
        import sys
        workers = []
        class FakeWorker:
            def __init__(self, *args, **kwargs): workers.append(kwargs)
            def once(self): return 'idle'
        class FakeClient:
            identity = 'test'
            def __init__(self, *args): pass
        with patch.object(sys, 'argv', ['worker','once','--session','--interactive-host','--task-id','61','--task-type','gallery.collect']), patch.object(cli, 'Worker', FakeWorker), patch.object(cli, 'Client', FakeClient):
            self.assertEqual(cli.main(), 0)
        self.assertEqual(workers[0]['task_id'], 61)
        self.assertEqual(workers[0]['capabilities'], ('gallery.collect',))

    def test_cli_targets_only_requested_image_capability(self):
        from unittest.mock import patch
        from optimization_worker import __main__ as cli
        import sys
        workers = []
        class FakeWorker:
            def __init__(self, *args, **kwargs): workers.append(kwargs)
            def once(self): return 'idle'
        class FakeClient:
            identity = 'test'
            def __init__(self, *args): pass
        for kind in ('image.generate', 'image.qc'):
            with self.subTest(kind=kind), patch.object(sys, 'argv', ['worker','once','--session','--interactive-host','--task-id','69','--task-type',kind]), patch.object(cli, 'Worker', FakeWorker), patch.object(cli, 'Client', FakeClient):
                self.assertEqual(cli.main(), 0)
            self.assertEqual(workers[-1]['task_id'], 69)
            self.assertEqual(workers[-1]['capabilities'], (kind,))

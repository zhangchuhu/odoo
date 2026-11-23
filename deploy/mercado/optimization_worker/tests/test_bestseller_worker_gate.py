import unittest
from optimization_worker.bestseller_worker_gate import GatedWorker

class Tests(unittest.TestCase):
    def test_missing_executor_prevents_new_claim(self):
        calls=[]
        worker=GatedWorker(lambda: calls.append('work'), lambda: False, lambda: False)
        self.assertEqual(worker.once(), 'waiting_for_host')
        self.assertEqual(calls, [])

    def test_existing_lease_continues_even_if_host_unavailable(self):
        calls=[]
        worker=GatedWorker(lambda **kwargs: calls.append('work') or 'host_pending', lambda: False, lambda: True)
        self.assertEqual(worker.once(), 'host_pending')
        self.assertEqual(calls, ['work'])

    def test_probe_exceptions_fail_closed(self):
        def broken(): raise RuntimeError('not connected')
        worker=GatedWorker(lambda: self.fail('must not claim'), broken, lambda: False)
        self.assertEqual(worker.once(), 'waiting_for_host')

    def test_live_probe_enables_worker(self):
        worker=GatedWorker(lambda: 'idle', lambda: True, lambda: False)
        self.assertEqual(worker.once(), 'idle')

class RaceTests(unittest.TestCase):
    def test_offline_active_path_forbids_claim_even_if_lease_disappears(self):
        seen=[]
        def run(*, allow_claim=True):
            seen.append(allow_claim)
            return 'idle'
        worker=GatedWorker(run, lambda: False, lambda: True)
        self.assertEqual(worker.once(), 'idle')
        self.assertEqual(seen, [False])

class WorkerNoClaimTests(unittest.TestCase):
    def test_empty_journal_does_not_claim(self):
        import tempfile
        from pathlib import Path
        from optimization_worker.runtime import Worker
        class Client:
            identity = 'test-identity'
            def claim(self, *args, **kwargs): raise AssertionError('must not claim')
        with tempfile.TemporaryDirectory() as directory:
            worker=Worker(Client(), Path(directory)/'journal.sqlite', capabilities=('research.bestseller',))
            self.assertEqual(worker.once(allow_claim=False), 'idle')

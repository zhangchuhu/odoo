import tempfile
import unittest
import os
from pathlib import Path
from optimization_worker.interactive_supervisor import Supervisor, SupervisorBusy


class Process:
    def __init__(self): self.running = True; self.stops = 0
    def poll(self): return None if self.running else 0
    def terminate(self): self.stops += 1; self.running = False
    def wait(self, timeout=None): return 0


class SupervisorTest(unittest.TestCase):
    def test_lock_requires_private_regular_single_link_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'lock'
            path.write_text('')
            path.chmod(0o666)
            with self.assertRaises((ValueError, PermissionError)):
                with Supervisor(path, lambda: True, lambda: Process()): pass
            path.chmod(0o600)
            os.link(path, Path(d) / 'other')
            with self.assertRaises((ValueError, PermissionError)):
                with Supervisor(path, lambda: True, lambda: Process()): pass

    def test_fail_closed_recovery_and_no_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            ready = [False]; processes = []
            def start():
                p = Process(); processes.append(p); return p
            with Supervisor(Path(d) / 'lock', lambda: ready[0], start) as supervisor:
                self.assertEqual(supervisor.tick(), 'waiting')
                self.assertFalse(processes)
                ready[0] = True
                self.assertEqual(supervisor.tick(), 'running')
                self.assertEqual(supervisor.tick(), 'running')
                self.assertEqual(len(processes), 1)
                ready[0] = False
                self.assertEqual(supervisor.tick(), 'waiting')
                self.assertEqual(processes[0].stops, 1)
                ready[0] = True
                self.assertEqual(supervisor.tick(), 'running')
                self.assertEqual(len(processes), 2)
            self.assertEqual(processes[1].stops, 1)

    def test_singleton_and_probe_errors_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'lock'; launched = []
            def broken(): raise RuntimeError('offline')
            with Supervisor(path, broken, lambda: launched.append(True)) as first:
                self.assertEqual(first.tick(), 'waiting')
                with self.assertRaises(SupervisorBusy):
                    with Supervisor(path, lambda: True, lambda: None): pass
            self.assertFalse(launched)

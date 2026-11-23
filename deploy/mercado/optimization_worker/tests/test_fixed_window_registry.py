import tempfile
import unittest
from pathlib import Path

from optimization_worker.fixed_window_registry import FixedWindowRegistry


class FixedWindowRegistryTest(unittest.TestCase):
    def test_window_shared_across_tasks_and_platforms_but_other_window_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = FixedWindowRegistry(Path(tmp) / 'ledger')
            self.assertTrue(registry.select('mercado-libre-mx', 7, 'a' * 32, lambda: True))
            self.assertTrue(registry.select('mercado-libre-mx', 7, 'a' * 32, lambda: True))
            self.assertTrue(registry.select('shein-us', 7, 'b' * 32, lambda: True))
            self.assertFalse(registry.select('shein-us', 8, 'b' * 32, lambda: True))

    def test_stale_session_and_lease_fail_closed_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ledger'
            registry = FixedWindowRegistry(path)
            self.assertFalse(registry.select('mercado-libre-mx', 7, 'a' * 32, lambda: False))
            self.assertTrue(registry.select('mercado-libre-mx', 7, 'a' * 32, lambda: True))
            registry = FixedWindowRegistry(path)
            self.assertFalse(registry.select('mercado-libre-mx', 7, 'c' * 32, lambda: True))
            self.assertFalse(registry.select('mercado-libre-mx', 7, 'a' * 32, lambda: False))
            self.assertTrue(registry.select('mercado-libre-mx', 7, 'a' * 32, lambda: True))

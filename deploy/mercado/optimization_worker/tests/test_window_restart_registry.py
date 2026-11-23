import tempfile
import unittest
from pathlib import Path

from optimization_worker.window_restart_registry import WindowRestartRegistry


class WindowRestartRegistryTest(unittest.TestCase):
    def test_restart_requires_fresh_anchor_proof_and_rejects_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = WindowRestartRegistry(Path(tmp) / 'db', clock=lambda: 10)
            token = r.challenge('req', 1, 'a' * 32, lambda: True)
            proof = dict(request='req', epoch=1, session='a' * 32, window=4, marker='b' * 32, token=token)
            self.assertTrue(r.confirm(proof, lambda p: True, lambda: True))
            self.assertFalse(r.confirm(proof, lambda p: True, lambda: True))
            token2 = r.challenge('req2', 2, 'c' * 32, lambda: True)
            newer = dict(request='req2', epoch=2, session='c' * 32, window=8, marker='b' * 32, token=token2)
            self.assertFalse(r.confirm(newer, lambda p: False, lambda: True))
            self.assertFalse(r.confirm(dict(newer, marker='d' * 32), lambda p: True, lambda: True))
            self.assertTrue(r.confirm(newer, lambda p: True, lambda: True))
            self.assertEqual(r.current(), (8, 'c' * 32, 'b' * 32))

    def test_expiry_and_lost_lease_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tick = [0]
            r = WindowRestartRegistry(Path(tmp) / 'db', clock=lambda: tick[0], ttl=10)
            token = r.challenge('req', 1, 'a' * 32, lambda: True)
            proof = dict(request='req', epoch=1, session='a' * 32, window=1, marker='b' * 32, token=token)
            self.assertFalse(r.confirm(proof, lambda p: True, lambda: False))
            tick[0] = 11
            self.assertFalse(r.confirm(proof, lambda p: True, lambda: True))
            self.assertIsNone(r.current())

import unittest
import importlib.util
from pathlib import Path
path=Path(__file__).resolve().parents[2]/'addons/meli_optimization/models/callback_probe_contract.py'
spec=importlib.util.spec_from_file_location('probe_contract',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
nonce_digest=module.nonce_digest

class ContractTest(unittest.TestCase):
    def test_nonce_is_strict_and_returns_digest_only(self):
        import hashlib
        self.assertEqual(nonce_digest('A'*32),hashlib.sha256(('A'*32).encode()).hexdigest())
        for nonce in ('x', 'A'*31+'!', 'A'*33, 1, None):
            with self.assertRaises(ValueError): nonce_digest(nonce)

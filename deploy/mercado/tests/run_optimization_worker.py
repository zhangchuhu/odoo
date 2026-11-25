"""Offline real-HTTP fixture suite; no Odoo, database or external network."""
from pathlib import Path
import unittest

root = Path(__file__).resolve().parents[1] / 'optimization_worker' / 'tests'
result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(root)))
raise SystemExit(not result.wasSuccessful())

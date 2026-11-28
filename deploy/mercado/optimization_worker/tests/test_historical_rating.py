import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[2] / 'addons/meli_optimization/domain/historical_rating.py'
spec = importlib.util.spec_from_file_location('historical_rating', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class HistoricalRatingTest(unittest.TestCase):
    def test_verified_matching_evidence_only(self):
        raw = json.dumps({'platform':'mercado-libre-mx','details':[{'identity':'mercado-libre-mx:MLM1','product_id':'MLM1','rating_display':'4.7'}]}).encode()
        digest = hashlib.sha256(raw).hexdigest()
        self.assertEqual(module.historical_rating(raw, digest, 'mercado-libre-mx', 'MLM1'), 4.7)
        self.assertIsNone(module.historical_rating(raw, digest, 'mercado-libre-mx', 'MLM2'))
        self.assertIsNone(module.historical_rating(raw, '0'*64, 'mercado-libre-mx', 'MLM1'))
        other = json.dumps({'platform':'mercado-libre-mx','details':[{'identity':'mercado-libre-mx:MLM1','product_id':'MLM1','rating_display':'4.7'},{'identity':'mercado-libre-mx:MLM1','product_id':'MLM1','rating_display':'4.8'}]}).encode()
        self.assertIsNone(module.historical_rating(other, hashlib.sha256(other).hexdigest(), 'mercado-libre-mx', 'MLM1'))

import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).parents[1] / 'models' / 'generation_sources.py'
spec = importlib.util.spec_from_file_location('generation_sources', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class SnapshotSourcesTest(unittest.TestCase):
    def test_preserves_all_ordered_source_images(self):
        self.assertEqual(module.snapshot_sources({'source_image_artifact_ids': [12, '8']}), ['12', '8'])
    def test_missing_invalid_and_duplicate_are_rejected(self):
        for payload in ({}, {'source_image_artifact_ids': []}, {'source_image_artifact_ids': ['1', 1]}, {'source_image_artifact_ids': ['x']}):
            with self.assertRaises(ValueError): module.snapshot_sources(payload)

import unittest
from optimization_worker.handlers import prepare
from optimization_worker.domain import DomainError

class Inputs:
    def __init__(self):
        self.items = {'1': {'mime_type': 'image/jpeg', 'data': 'eA=='},
                      '2': {'mime_type': 'image/png', 'data': 'eA=='},
                      '3': {'mime_type': 'application/json', 'payload': {}}}
    def artifact(self, ref): return self.items[ref]
    def snapshot(self, ref): return {'payload': {}}

class ImageInputTests(unittest.TestCase):
    def setUp(self):
        self.client = Inputs()
        self.claim = {'task_type': 'image.generate', 'snapshot_id': 5, 'company_id': 1,
                      'payload': {'schema_version': '1', 'source_artifact_ids': ['1'],
                                  'target_artifact_id': '2', 'generation_plan_id': '3'}}
    def test_valid_image_and_plan_are_accepted(self):
        self.assertEqual(len(prepare(self.client, self.claim)['artifacts']), 3)
    def test_json_source_is_rejected_before_dispatch(self):
        self.client.items['1'] = {'mime_type': 'application/json', 'payload': {}}
        with self.assertRaises(DomainError): prepare(self.client, self.claim)
    def test_json_target_is_rejected_before_dispatch(self):
        self.client.items['2'] = {'mime_type': 'application/json', 'payload': {}}
        with self.assertRaises(DomainError): prepare(self.client, self.claim)
    def test_image_plan_is_rejected_before_dispatch(self):
        self.client.items['3'] = {'mime_type': 'image/jpeg', 'data': 'eA=='}
        with self.assertRaises(DomainError): prepare(self.client, self.claim)
    def test_legacy_structured_plan_without_mime_is_accepted(self):
        self.client.items['3'] = {'mime_type': False, 'payload': {'schema_version': '1'}}
        self.assertEqual(len(prepare(self.client, self.claim)['artifacts']), 3)
    def test_missing_mime_and_non_object_plan_is_rejected(self):
        self.client.items['3'] = {'mime_type': False, 'payload': 'not an object'}
        with self.assertRaises(DomainError): prepare(self.client, self.claim)
    def test_image_mime_cannot_masquerade_as_structured_plan(self):
        self.client.items['3'] = {'mime_type': 'image/png', 'payload': {'schema_version': '1'}, 'data': 'eA=='}
        with self.assertRaises(DomainError): prepare(self.client, self.claim)
    def test_missing_mime_plan_with_binary_data_is_rejected(self):
        self.client.items['3'] = {'mime_type': False, 'payload': {'schema_version': '1'}, 'data': 'eA=='}
        with self.assertRaises(DomainError): prepare(self.client, self.claim)

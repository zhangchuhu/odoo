import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from optimization_worker.handlers import CapabilityMissing

class BrowserProviderTest(unittest.TestCase):
    def test_missing_browser_does_not_fabricate_a_result(self):
        from optimization_worker.browser_provider import BrowserResearchProvider
        provider = BrowserResearchProvider(None)
        with self.assertRaises(CapabilityMissing):
            provider.run('research.bestseller', {}, {}, 'r1')

    def test_adapter_result_is_validated_and_copied(self):
        from optimization_worker.browser_provider import BrowserResearchProvider
        value = {'schema_version':'1','status':'empty','reason':'No eligible listings', 'candidates':[],
                 'evidence':[{'id':'e','source':'observed search page','observed_at':'2026-11-15T00:00:00Z'}]}
        calls=[]
        def adapter(payload, context, request_id):
            calls.append((payload, context, request_id)); return value
        result=BrowserResearchProvider(adapter).run('research.bestseller', {'site_id':'MLM'}, {'snapshot':{}}, 'r2')
        self.assertEqual(result,value)
        result['evidence'].clear()
        self.assertEqual(len(value['evidence']),1)
        self.assertEqual(calls[0][2], 'r2')
        value['status']='complete'
        with self.assertRaises(ValueError):
            BrowserResearchProvider(adapter).run('research.bestseller',{}, {}, 'r3')

class BrowserProviderWorkerTest(unittest.TestCase):
    def test_real_worker_processes_valid_observation_from_injected_adapter(self):
        import tempfile
        from pathlib import Path
        from test_runtime import Fixture
        from optimization_worker.client import Client
        from optimization_worker.runtime import Worker
        from optimization_worker.browser_provider import BrowserResearchProvider
        fixture=Fixture()
        self.addCleanup(fixture.close)
        fixture.claim['task_type']='research.bestseller'
        fixture.claim['payload']={'schema_version':'1','facts_artifact_id':'23','keywords_artifact_id':'24','site_id':'MLM'}
        value={'schema_version':'1','status':'empty','reason':'No eligible listings','candidates':[],
               'evidence':[{'id':'e','source':'fixture search page','observed_at':'2026-11-15T00:00:00Z'}]}
        with tempfile.TemporaryDirectory() as directory:
            worker=Worker(Client(fixture.url,'fixture-key'),Path(directory)/'worker.sqlite',
                          providers={'research.bestseller':BrowserResearchProvider(lambda *args:value)})
            self.assertEqual(worker.once(),'succeeded')
        self.assertEqual(fixture.state,'succeeded')
        self.assertTrue(any(event[0]=='complete' for event in fixture.events))

import unittest
from unittest.mock import patch

class TextWorkerServiceTest(unittest.TestCase):
    def test_worker_claims_only_facts_and_keywords(self):
        from optimization_worker.text_worker_service import build_worker
        client = type("Client", (), {"identity": "test"})()
        with patch('optimization_worker.text_worker_service.SessionStore') as store, patch('optimization_worker.text_worker_service.SessionProvider') as provider, patch('optimization_worker.text_worker_service.Worker') as worker:
            build_worker(client, '/tmp/test-journal', '/tmp/test-host')
            self.assertEqual(worker.call_args.kwargs['capabilities'], ('product.facts',))
            self.assertEqual(set(worker.call_args.kwargs['providers']), {'product.facts'})

    def test_facts_executor_is_scoped_and_startup_recovers_only_facts(self):
        from optimization_worker.text_worker_service import execute_pending, recover_interrupted
        with patch('optimization_worker.text_worker_service.safe_run_one') as run:
            execute_pending('store', 'client', 'journal')
            self.assertEqual(run.call_args.kwargs['kinds'], {'product.facts'})
        from unittest.mock import Mock
        store = Mock()
        recover_interrupted(store)
        store.mark_interrupted_text.assert_called_once_with({'product.facts'})

    def test_launchd_is_keepalive(self):
        import plistlib
        from optimization_worker.text_worker_service import plist_bytes
        data = plistlib.loads(plist_bytes('/tmp/project', '/tmp/private'))
        self.assertTrue(data['KeepAlive'])
        self.assertTrue(data['RunAtLoad'])

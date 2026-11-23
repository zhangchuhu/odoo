import unittest
from unittest.mock import patch

class KeywordWorkerServiceTest(unittest.TestCase):
    def test_claim_loop_disables_new_claim_when_capability_missing(self):
        from optimization_worker.keyword_worker_service import claim_once
        from unittest.mock import Mock
        worker = Mock()
        claim_once(worker, ready=False)
        worker.once.assert_called_once_with(allow_claim=False)
        claim_once(worker, ready=True)
        worker.once.assert_called_with(allow_claim=True)

    def test_missing_auth_prevents_new_claim(self):
        from optimization_worker.keyword_worker_service import codex_ready
        with patch('optimization_worker.keyword_worker_service.shutil.which', return_value=None):
            self.assertFalse(codex_ready())
        from unittest.mock import Mock
        with patch.dict('os.environ', {'OPTIMIZATION_CODEX_HOME':'/tmp', 'OPTIMIZATION_CODEX_PROXY':''}), \
             patch('optimization_worker.keyword_worker_service.shutil.which', return_value='/tmp/codex'), \
             patch('optimization_worker.keyword_worker_service.subprocess.run', return_value=Mock(returncode=1)):
            self.assertFalse(codex_ready())
    def test_only_claims_keywords_and_executes_embedded_host(self):
        from optimization_worker.keyword_worker_service import build_worker, execute_pending
        client = type('Client', (), {'identity': 'test'})()
        with patch('optimization_worker.keyword_worker_service.Worker') as worker:
            build_worker(client, '/tmp/journal', '/tmp/host')
            self.assertEqual(worker.call_args.kwargs['capabilities'], ('research.keywords',))
        with patch('optimization_worker.keyword_worker_service.safe_run_one', return_value='submitted') as run:
            self.assertEqual(execute_pending('store', client, '/tmp/journal'), 'submitted')
            self.assertEqual(run.call_args.kwargs['kinds'], {'research.keywords'})

    def test_launch_agent_runs_dedicated_process_without_credentials_in_plist(self):
        import plistlib
        from optimization_worker.keyword_worker_service import plist_bytes
        data = plistlib.loads(plist_bytes('/tmp/workspace', '/tmp/private'))
        self.assertEqual(data['ProgramArguments'][-1], 'optimization_worker.keyword_worker_service')
        self.assertTrue(data['RunAtLoad'])
        self.assertTrue(data['KeepAlive'])
        self.assertNotIn('ODOO_API_KEY', data['EnvironmentVariables'])

    def test_service_lock_prevents_concurrent_daemons(self):
        import tempfile
        from pathlib import Path
        from optimization_worker.keyword_worker_service import ServiceLock
        with tempfile.TemporaryDirectory() as d:
            with ServiceLock(Path(d) / 'service.lock'):
                with self.assertRaises(RuntimeError):
                    with ServiceLock(Path(d) / 'service.lock'):
                        pass
            with ServiceLock(Path(d) / 'service.lock'):
                pass

    def test_restart_fences_interrupted_keyword_before_claiming(self):
        from optimization_worker.keyword_worker_service import recover_interrupted
        from unittest.mock import Mock
        store = Mock()
        recover_interrupted(store)
        store.mark_interrupted_text.assert_called_once_with({'research.keywords'})

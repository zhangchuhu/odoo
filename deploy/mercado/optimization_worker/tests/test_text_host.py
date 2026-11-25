import tempfile
import unittest
from pathlib import Path
from optimization_worker.host_session import SessionStore

class TextHostTest(unittest.TestCase):
    def test_claim_is_atomic_and_crash_does_not_redispatch(self):
        with tempfile.TemporaryDirectory() as d:
            s = SessionStore(Path(d)/'host.sqlite', 'test')
            s.enqueue('r1', 'research.keywords', {}, {'artifacts': {}})
            self.assertTrue(s.claim_text('r1'))
            self.assertFalse(s.claim_text('r1'))
            self.assertEqual(s.inspect('r1')['state'], 'dispatched')

    def test_text_submission_and_secret_exclusion(self):
        import json
        from unittest.mock import patch
        from optimization_worker.text_host import run_one
        with tempfile.TemporaryDirectory() as d:
            s = SessionStore(Path(d)/'host.sqlite', 'test')
            s.enqueue('r1', 'research.keywords', {}, {'artifacts': {}})
            s.enqueue('r2', 'gallery.collect', {}, {'artifacts': {}})
            class Done: returncode = 0
            def runner(args, **kw):
                self.assertNotIn('ODOO_API_KEY', kw['env'])
                self.assertIn('cli_auth_credentials_store="file"', args)
                schema = json.loads(Path(args[args.index('--output-schema')+1]).read_text())
                self.assertEqual(schema['properties']['schema_version']['enum'], ['1'])
                self.assertEqual(kw['env']['HTTPS_PROXY'], 'http://127.0.0.1:7897')
                self.assertEqual(kw['env']['HTTP_PROXY'], 'http://127.0.0.1:7897')
                Path(args[args.index('-o')+1]).write_text(json.dumps({'schema_version':'1', 'status':'complete', 'keywords':[{'text':'falda', 'evidence_ids':['e1']}], 'evidence':[{'id':'e1','source':'snapshot','observed_at':'2026-11-15T00:00:00Z'}]}))
                return Done()
            with patch.dict('os.environ', {'OPTIMIZATION_CODEX_HOME': d, 'OPTIMIZATION_CODEX_PROXY': 'http://127.0.0.1:7897'}), patch('optimization_worker.text_host.synchronize_live'), patch('optimization_worker.text_host.fence'):
                self.assertEqual(run_one(s, None, 'test', runner), 'submitted')
            self.assertEqual(s.inspect('r1')['state'], 'resolved')
            self.assertEqual(s.inspect('r1')['result']['status'], 'partial')

    def test_prepared_text_is_not_blocked_by_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            s = SessionStore(Path(d)/'host.sqlite', 'test')
            s.enqueue('r1', 'research.keywords', {}, {'artifacts': {}})
            s.claim_text('r1')
            s.enqueue('r2', 'title.generate', {}, {'artifacts': {}})
            self.assertEqual(s.next(kinds={'research.keywords', 'title.generate'}, states={'prepared'})['request_id'], 'r2')

    def test_result_persisted_if_fence_fails_then_replayed(self):
        import json
        from unittest.mock import patch
        from optimization_worker.client import APIError
        from optimization_worker.text_host import run_one
        with tempfile.TemporaryDirectory() as d:
            s = SessionStore(Path(d)/'host.sqlite', 'test')
            s.enqueue('r1', 'research.keywords', {}, {'artifacts': {}})
            class Done: returncode = 0
            def runner(args, **kw):
                Path(args[args.index('-o')+1]).write_text(json.dumps({'schema_version':'1','status':'complete','keywords':[{'text':'falda','evidence_ids':['e']}], 'evidence':[{'id':'e','source':'facts','observed_at':'2026-11-15T00:00:00Z'}]}))
                return Done()
            with patch.dict('os.environ', {'OPTIMIZATION_CODEX_HOME': d}), patch('optimization_worker.text_host.synchronize_live'), patch('optimization_worker.text_host.fence', side_effect=[None, APIError(503)]):
                with self.assertRaises(APIError):
                    run_one(s, None, 'test', runner)
            self.assertEqual(s.inspect('r1')['state'], 'result_ready')
            with patch.dict('os.environ', {'OPTIMIZATION_CODEX_HOME': d}), patch('optimization_worker.text_host.synchronize_live'), patch('optimization_worker.text_host.fence'):
                self.assertEqual(run_one(s, None, 'test', lambda *a, **k: self.fail('must not rerun')), 'submitted')

    def test_timeout_unknown_but_missing_binary_waits(self):
        import subprocess
        from unittest.mock import patch
        from optimization_worker.text_host import run_one
        for exception, expected in [(subprocess.TimeoutExpired('codex', 1), 'unknown'), (FileNotFoundError(), 'waiting')]:
            with tempfile.TemporaryDirectory() as d:
                s = SessionStore(Path(d)/'host.sqlite', 'test')
                s.enqueue('r1', 'research.keywords', {}, {'artifacts': {}})
                def runner(*a, **k): raise exception
                with patch.dict('os.environ', {'OPTIMIZATION_CODEX_HOME': d}), patch('optimization_worker.text_host.synchronize_live'), patch('optimization_worker.text_host.fence'):
                    run_one(s, None, 'test', runner)
                self.assertEqual(s.inspect('r1')['state'], expected)

    def test_api_error_retries_and_stale_is_classified(self):
        from unittest.mock import patch
        from optimization_worker.client import APIError
        from optimization_worker.text_host import safe_run_one
        with patch('optimization_worker.text_host.run_one', side_effect=[APIError(503), APIError(409), 'submitted']):
            self.assertEqual(safe_run_one(None, None, None), 'api_unavailable')
            self.assertEqual(safe_run_one(None, None, None), 'stale_lease')
            self.assertEqual(safe_run_one(None, None, None), 'submitted')

    def test_proxy_is_loopback_and_credential_free(self):
        from optimization_worker.text_host import proxy_environment
        self.assertEqual(proxy_environment('http://127.0.0.1:7897'), {'HTTP_PROXY':'http://127.0.0.1:7897','HTTPS_PROXY':'http://127.0.0.1:7897'})
        self.assertEqual(proxy_environment(''), {})
        for bad in ['http://u:p@127.0.0.1:7897', 'http://example.com:7897', 'http://127.0.0.1:7897/path', 'socks5://127.0.0.1:7897', 'http://localhost']:
            with self.assertRaises(ValueError):
                proxy_environment(bad)

    def test_known_failure_category_is_stored_without_private_output(self):
        from unittest.mock import patch
        from optimization_worker.text_host import run_one
        with tempfile.TemporaryDirectory() as d:
            s = SessionStore(Path(d)/'host.sqlite', 'test')
            s.enqueue('r1', 'research.keywords', {}, {'artifacts': {}})
            class Failed: returncode = 7
            with patch.dict('os.environ', {'OPTIMIZATION_CODEX_HOME': d}), patch('optimization_worker.text_host.synchronize_live'), patch('optimization_worker.text_host.fence'):
                self.assertEqual(run_one(s, None, 'test', lambda *a, **k: Failed()), 'failed')
            self.assertEqual(s.inspect('r1')['failure_category'], 'codex_exit_nonzero')

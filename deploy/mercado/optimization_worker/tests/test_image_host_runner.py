import unittest
from unittest.mock import patch
from optimization_worker import image_host

class ImageHostRunnerTest(unittest.TestCase):
    def test_waiting_action_never_runs_codex(self):
        class Store:
            def next(self, **kw): return {'action':'waiting'}
        with patch.object(image_host, 'synchronize_live'), patch.object(image_host.subprocess, 'run') as run:
            self.assertEqual(image_host.safe_run_one(Store(), object(), 'j'), 'idle')
            run.assert_not_called()
    def test_recovery_marks_dispatched_unknown(self):
        class Store:
            def mark_interrupted_text(self, kinds): self.kinds=kinds
        s=Store(); image_host.recover_interrupted(s)
        self.assertEqual(s.kinds, {'image.generate'})

    def test_success_exit_without_state_change_is_no_progress(self):
        import tempfile
        from types import SimpleNamespace
        class Store:
            path='/tmp/image-host-test.sqlite'
            def next(self, **kw): return {'action':'select_references','request_id':'r-1'}
            def inspect(self, request): return {'state':'planning','review_seq':0}
        with tempfile.TemporaryDirectory() as home, patch.dict('os.environ', {'OPTIMIZATION_CODEX_HOME':home}), patch.object(image_host,'synchronize_live'), patch.object(image_host,'fence'):
            client=SimpleNamespace(base_url='http://localhost',_api_key='test')
            self.assertEqual(image_host.run_one(Store(), client, 'j', runner=lambda *a,**k:SimpleNamespace(returncode=0)), 'no_progress')

    def test_codex_can_access_local_callback(self):
        import tempfile
        from types import SimpleNamespace
        class Store:
            path='/tmp/image-host-test.sqlite'
            def next(self, **kw): return {'action':'select_references','request_id':'r-1'}
            def inspect(self, request): return {'state':'planning'}
        captured=[]
        def runner(args, **kwargs): captured.extend(args); return SimpleNamespace(returncode=0)
        with tempfile.TemporaryDirectory() as home, patch.dict('os.environ', {'OPTIMIZATION_CODEX_HOME':home}), patch.object(image_host,'synchronize_live'), patch.object(image_host,'fence'):
            image_host.run_one(Store(), SimpleNamespace(base_url='http://localhost',_api_key='test'), 'j', runner=runner)
        self.assertIn('sandbox_workspace_write.network_access=true', captured)

    def test_color_pause_reports_remote_status(self):
        from optimization_worker import host_session as hs
        class Store:
            def request_ids(self): return ['r']
            def synchronize(self,*a): pass
            def inspect(self,r): return {'task_type':'image.generate','state':'waiting','reason':'target_color_unconfirmed'}
        class Client:
            def heartbeat(self,c): return {}
            def host_progress(self,*a,**kw): self.result=(a,kw)
        c=Client()
        with patch.object(hs,'worker_records',return_value=[{'claim':{'id':1},'request_id':'r'}]):
            hs.synchronize_live(c, Store(), 'journal')
        self.assertEqual(c.result[0][2], 'color_required')

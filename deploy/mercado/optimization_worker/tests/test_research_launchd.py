import unittest
import tempfile
from pathlib import Path
from optimization_worker.research_launchd import service_environment, plist_bytes, install_launch_agent, scoped_identity_preflight

class ResearchLaunchdTest(unittest.TestCase):
    def test_queued_retry_requires_explicit_proof_for_target_task(self):
        from unittest.mock import Mock
        client = Mock()
        proof = dict(scoped_worker=True, non_admin=True, legacy_artifact_denied=True,
                     task_id=240, fresh_task=False, retry_allowed=True)
        client.request.return_value = proof
        self.assertTrue(scoped_identity_preflight(client, 240))
        self.assertFalse(scoped_identity_preflight(client, 241))
        for invalid in (False, None, 'true'):
            client.request.return_value = dict(proof, retry_allowed=invalid)
            self.assertFalse(scoped_identity_preflight(client, 240))

    def test_scoped_resume_requires_server_confirmation_of_same_request(self):
        class C:
            def request(self,method,path,body):
                if path.endswith('scoped-preflight'):
                    return {'scoped_worker':True,'non_admin':True,'legacy_artifact_denied':True,'fresh_task':False,'task_id':42}
                return {'task_id':42,'request_id':'r','resume_allowed':True,'coverage':'unknown'}
        self.assertTrue(scoped_identity_preflight(C(),42,resume={'request_id':'r','lease_token':'x','lease_epoch':1}))
        self.assertFalse(scoped_identity_preflight(C(),42))
    def test_scoped_preflight_requires_positive_server_proof_and_target(self):
        class C:
            def request(self, method, path, body):
                return {'scoped_worker':True,'non_admin':True,'legacy_artifact_denied':True,'fresh_task':True,'task_id':42}
        self.assertTrue(scoped_identity_preflight(C(),42))
        self.assertFalse(scoped_identity_preflight(C(),None))
        class Missing:
            def request(self,*a): return {'scoped_worker':True,'non_admin':True,'fresh_task':True,'task_id':42}
        self.assertFalse(scoped_identity_preflight(Missing(),42))
    def test_scoped_queue_preflight_requires_independent_identity_and_atomic_queue_proof(self):
        class C:
            def request(self,method,path,body):
                self.path=path
                return {'scoped_worker':True,'non_admin':True,'legacy_artifact_denied':True,'new_tasks_only':True,'case_id':229}
        c=C()
        self.assertTrue(scoped_identity_preflight(c,None,case_id=229))
        self.assertEqual(c.path,'/internal/meli-optimization/scoped-identity')
        self.assertFalse(scoped_identity_preflight(c,None,case_id=None))
    def test_scoped_identity_has_separate_state_and_opt_in(self):
        env=service_environment(self.values,'/repo','/private','/home/user',scoped=True)
        self.assertEqual(env['OPTIMIZATION_RESEARCH_LEDGER_V1'],'1')
        self.assertEqual(env['OPTIMIZATION_JOURNAL'],'/private/research-scoped-worker.sqlite')
        self.assertEqual(env['OPTIMIZATION_HOST_SESSION'],'/private/research-scoped-host.sqlite')
        self.assertEqual(env['OPTIMIZATION_DISPATCH_JOURNAL'],'/private/research-scoped-dispatch.sqlite')
        self.assertEqual(env['OPTIMIZATION_RESEARCH_LOCK'],'/private/research-service.lock')
        self.assertNotIn('OPTIMIZATION_RESEARCH_LEDGER_V1',service_environment(self.values,'/repo','/private','/home/user'))
        self.assertIn(b'OPTIMIZATION_RESEARCH_SCOPED',plist_bytes('/repo','/private',scoped=True))
        self.assertNotIn(b'OPTIMIZATION_RESEARCH_SCOPED',plist_bytes('/repo','/private'))
    def setUp(self):
        self.values={'ODOO_API_KEY':'SYNTHETIC_SECRET','ODOO_OPTIMIZATION_URL':'http://127.0.0.1:8069',
                     'OPTIMIZATION_JOURNAL':'/unused/worker.sqlite','OPTIMIZATION_HOST_SESSION':'/unused/host.sqlite'}
    def test_private_credential_is_not_in_launchd_plist(self):
        content=plist_bytes('/repo','/private')
        self.assertNotIn(b'SYNTHETIC_SECRET',content)
        self.assertNotIn(b'ODOO_API_KEY',content)
        self.assertIn(b'optimization_worker.research_launchd',content)
    def test_isolated_paths_and_probe_case(self):
        env=service_environment(self.values,'/repo','/private','/home/user')
        self.assertEqual(env['ODOO_API_KEY'],'SYNTHETIC_SECRET')
        self.assertEqual(env['OPTIMIZATION_PROBE_CASE_ID'],'229')
        self.assertEqual(env['OPTIMIZATION_JOURNAL'],'/private/research-worker.sqlite')
        self.assertEqual(env['OPTIMIZATION_HOST_SESSION'],'/private/research-host.sqlite')
        self.assertEqual(env['OPTIMIZATION_CODEX_PROXY'],'http://127.0.0.1:7897')
        self.assertNotIn('HTTP_PROXY',env)
    def test_invalid_source_config_fails_closed(self):
        for value in (dict(self.values, ODOO_OPTIMIZATION_URL='https://elsewhere.test'),dict(self.values,ODOO_API_KEY=''),dict(self.values, OTHER='x')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                service_environment(value,'/repo','/private','/home/user')

    def test_restart_uses_same_private_journals_without_leaking_token_to_plist(self):
        first=service_environment(self.values,'/repo','/private','/home/user')
        second=service_environment(self.values,'/repo','/private','/home/user')
        for key in ('OPTIMIZATION_JOURNAL','OPTIMIZATION_HOST_SESSION','OPTIMIZATION_DISPATCH_JOURNAL','OPTIMIZATION_RESEARCH_LOCK'):
            self.assertEqual(first[key],second[key])
        self.assertNotIn(first['ODOO_API_KEY'].encode(),plist_bytes('/repo','/private'))

    def test_installs_login_agent_idempotently_and_never_overwrites_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp); (home/'Library').mkdir(); (home/'Library/LaunchAgents').mkdir()
            data=plist_bytes('/repo','/private')
            path=install_launch_agent(home,data)
            self.assertEqual(path.parent,home/'Library/LaunchAgents')
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(path.read_bytes(),data)
            self.assertEqual(install_launch_agent(home,data),path)
            path.write_text('user configuration')
            with self.assertRaises((ValueError,PermissionError)): install_launch_agent(home,data)
            self.assertEqual(path.read_text(),'user configuration')

    def test_rejects_symlink_launchagents_and_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp); (home/'Library').mkdir(); real=home/'real'; real.mkdir()
            (home/'Library/LaunchAgents').symlink_to(real)
            with self.assertRaises((ValueError,PermissionError,OSError)): install_launch_agent(home,b'x')
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp); (home/'Library').mkdir(); d=home/'Library/LaunchAgents'; d.mkdir()
            (d/'local.odoo.optimization-research-host.plist').symlink_to(home/'victim')
            with self.assertRaises((ValueError,PermissionError,OSError)): install_launch_agent(home,b'x')

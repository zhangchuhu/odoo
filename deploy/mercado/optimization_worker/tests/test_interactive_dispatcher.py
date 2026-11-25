import subprocess
import tempfile
from pathlib import Path
import unittest

from optimization_worker.interactive_dispatcher import Dispatcher


class Store:
    def __init__(self):
        self.action = {'request_id': 'r1', 'task_type': 'research.bestseller', 'state': 'prepared', 'action': 'browse_research'}
    def next(self, **kw):
        return dict(self.action)
    def inspect(self, request):
        return dict(self.action)

class ProcessTelemetryTest(unittest.TestCase):
    def test_records_structural_events_and_stderr_presence_only(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'journal'
            def runner(*args,**kwargs):
                kwargs['stdout'].write(b'{"type":"item.completed","item":{"type":"mcp_tool_call","secret":"private-prompt"}}\n')
                kwargs['stderr'].write(b'secret-token')
                return subprocess.CompletedProcess(args[0],1)
            self.assertEqual(Dispatcher(path,Store(),runner=runner,host_probe=lambda:True).tick(),'unknown')
            with sqlite3.connect(path) as db:
                rows=db.execute('select stage,code,elapsed_ms,stderr_present from invocation_telemetry order by id').fetchall()
            self.assertEqual(rows[0][0:2],('process','started'))
            self.assertIn(('process','nonzero'),[(r[0],r[1]) for r in rows])
            self.assertEqual(rows[-1][3],1)
            self.assertNotIn('secret-token',str(rows)); self.assertNotIn('private-prompt',str(rows))


def test_no_duplicate_after_ambiguous_timeout(tmp_path):
    store = Store()
    calls = []
    def runner(*args, **kwargs):
        calls.append(1)
        raise subprocess.TimeoutExpired(args[0], 1)
    d = Dispatcher(tmp_path / 'journal.sqlite', store, runner=runner, host_probe=lambda:True)
    assert d.tick() == 'unknown'
    assert d.tick() == 'unknown'
    assert len(calls) == 1


def test_checkpoint_change_allows_next_invocation(tmp_path):
    store = Store()
    def runner(*args, **kwargs):
        store.action['state'] = 'candidate_images_pending'
        store.action['action'] = 'collect_candidate_images'
        return subprocess.CompletedProcess(args[0], 0)
    d = Dispatcher(tmp_path / 'journal.sqlite', store, runner=runner, host_probe=lambda:True)
    assert d.tick() == 'progress'
    assert d.tick() == 'unknown'


def test_waiting_request_does_not_launch(tmp_path):
    store = Store()
    store.action['action'] = 'waiting'
    assert Dispatcher(tmp_path / 'journal.sqlite', store).tick() == 'idle'


class DispatcherTests(unittest.TestCase):
    def test_child_failure_resumes_only_new_confirmed_gallery_operations(self):
        from copy import deepcopy
        from optimization_worker.domain import canonical_hash

        store = Store()
        plan = {'request_id': 'r1', 'plan': {'platform': 'mercado-libre-mx'}}
        store.action['collector_plan'] = dict(plan, digest=canonical_hash(plan))
        evidence = {'cards': ['one']}
        digest = canonical_hash(evidence)
        url = 'https://www.mercadolibre.com.mx/up/MLMU100'
        source = 'https://images.mlstatic.com/one.jpg'
        identity = 'mercado-libre-mx:MLMU100'
        candidate = {'url': url, 'platform': 'mercado-libre-mx', 'item_id': 'MLMU100'}
        pending = {'artifact_id': '203', 'data': evidence, 'digest': digest, 'result': {
            'candidates': [candidate], 'evidence': [{'source': 'odoo-artifact:203:sha256:' + digest}]}}
        history = []
        def runner(*args, **kwargs):
            history.append(store.action['request_id'])
            if len(history) == 1:
                store.action.update(action='collect_candidate_images', state='candidate_images_pending',
                                    collector_artifact_id='203', collector_evidence=evidence, collector_pending=deepcopy(pending))
                raise subprocess.TimeoutExpired(args[0], 5)
            new = deepcopy(store.action['collector_pending'])
            if len(history) == 2:
                new['gallery_manifests'] = {identity: {'detail_url': url, 'images': [source],
                                                       'screenshot_path': '/private/screen', 'screenshot_sha256': 'e'*64}}
            if len(history) == 3:
                new['candidate_uploads'] = {identity+':0': {'state': 'confirmed', 'artifact_id': '204',
                                                           'digest': 'f'*64, 'order': 0, 'source': source}}
                new['result']['candidates'][0]['images'] = [{'order':0,'source':source,'artifact_id':'204','sha256':'f'*64}]
            store.action['collector_pending'] = new
            return subprocess.CompletedProcess(args[0], 1)
        with tempfile.TemporaryDirectory() as tmp:
            dispatcher = Dispatcher(Path(tmp)/'journal', store, runner=runner, host_probe=lambda:True)
            self.assertEqual([dispatcher.tick() for _ in range(5)],
                             ['progress','progress','progress','unknown','unknown'])
            self.assertEqual(history, ['r1']*4)

    def test_child_failure_keeps_uncertain_or_retired_gallery_for_reconciliation(self):
        from optimization_worker.domain import canonical_hash
        from unittest.mock import Mock
        for blocker in ('unknown_upload', 'retired', 'lost_lease', 'unverified_evidence'):
            with self.subTest(blocker=blocker), tempfile.TemporaryDirectory() as tmp:
                store = Store()
                evidence = {'cards': ['one']}
                plan = {'request_id': 'r1'}
                store.action['collector_plan'] = dict(plan, digest=canonical_hash(plan))
                def runner(*args, **kwargs):
                    digest = canonical_hash(evidence)
                    pending = {'artifact_id':'9', 'digest':digest, 'data':evidence,
                               'result':{'candidates':[], 'evidence':[{'source':'odoo-artifact:9:sha256:'+digest}]}}
                    if blocker == 'unknown_upload':
                        pending['candidate_uploads'] = {'unrelated:0': {'state':'unknown'}}
                    store.action.update(state='candidate_images_pending', collector_evidence=evidence,
                                        collector_artifact_id='8' if blocker=='unverified_evidence' else '9',
                                        collector_pending=pending, retired=blocker=='retired')
                    return subprocess.CompletedProcess(args[0], 1)
                check_lease = Mock(side_effect=[None,ValueError('lease gone')] if blocker=='lost_lease' else None)
                dispatcher = Dispatcher(Path(tmp)/'journal',store,runner=runner,before=check_lease,host_probe=lambda:True)
                self.assertEqual(dispatcher.tick(), 'unknown')

    def test_persisted_page_blocker_survives_child_failure(self):
        import sqlite3
        for reason in ('timeout', 'nonzero'):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as tmp:
                store = Store()
                def runner(*args, **kwargs):
                    store.action.update(state='waiting', reason='captcha_required', retired=True)
                    if reason == 'timeout':
                        raise subprocess.TimeoutExpired(args[0], 1)
                    return subprocess.CompletedProcess(args[0], 1)
                journal = Path(tmp) / 'journal'
                self.assertEqual(Dispatcher(journal, store, runner=runner, host_probe=lambda: True).tick(), 'progress')
                with sqlite3.connect(journal) as db:
                    self.assertEqual(db.execute('SELECT status FROM invocations').fetchone(), ('finished',))

    def test_receipt_progress_is_a_new_checkpoint(self):
        progress = [0]
        calls = []
        def runner(*args, **kwargs):
            calls.append(1)
            progress[0] += 1
            return subprocess.CompletedProcess(args[0], 0)
        with tempfile.TemporaryDirectory() as tmp:
            d = Dispatcher(Path(tmp)/'journal', Store(), runner=runner, host_probe=lambda:True,
                           progress_probe=lambda request: progress[0])
            self.assertEqual(d.tick(), 'progress')
            self.assertEqual(d.tick(), 'progress')
            self.assertEqual(len(calls), 2)

    def test_each_saved_detail_advances_the_same_three_group_checkpoint(self):
        progress = iter(((3, 0), (3, 1), (3, 1), (3, 2)))
        with tempfile.TemporaryDirectory() as tmp:
            dispatcher = Dispatcher(Path(tmp)/'journal', Store(), host_probe=lambda: True,
                                    runner=lambda *a, **k: subprocess.CompletedProcess(a[0], 0),
                                    progress_probe=lambda request: next(progress))
            self.assertEqual(dispatcher.tick(), 'progress')
            self.assertEqual(dispatcher.tick(), 'progress')
    def test_receipts_are_not_mistaken_for_collected_cards(self):
        from optimization_worker.interactive_dispatcher import research_prompt
        text = research_prompt({'request_id': 'r', 'action': 'browse_research'})
        self.assertIn('回执数量不代表卡片采集进度', text)
        self.assertIn('同任务同查询重新核验并采集', text)
        self.assertNotIn('按 completed 数量继续', text)

    def test_research_prompt_identifies_bound_collector(self):
        from optimization_worker.interactive_dispatcher import research_prompt
        prompt = research_prompt({'action':'browse_research','collector_plan':{'plan':{}}})
        self.assertIn('运营中心绑定版', prompt)
        self.assertIn('前两组 guard_active=false 是正常状态', prompt)
        self.assertIn('第三组完成后必须确认 guard_active=true', prompt)
        self.assertIn('连接当前任务后首先点击“恢复本任务采集进度”', prompt)
        self.assertIn('首次任务会按已绑定的计划初始化', prompt)
        self.assertIn('不得覆盖已有进度', prompt)
        self.assertIn('保留旧采集器弹窗及其进度', prompt)
    def test_both_bound_extension_titles_are_distinct_from_legacy(self):
        import json
        root = Path(__file__).resolve().parents[1]
        for folder in ('chrome_extension', 'shein_extension'):
            manifest = json.loads((root / folder / 'manifest.json').read_text())
            self.assertIn('运营中心绑定版', manifest['name'])
            self.assertIn('运营中心绑定版', manifest['action']['default_title'])
    def test_binding_failure_is_classified_without_storing_message(self):
        import json, sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'journal'
            def runner(*a, **k):
                event = {'type':'item.completed','item':{'type':'agent_message','text':'缺少连接当前任务入口 PRIVATE-SECRET'}}
                k['stdout'].write((json.dumps(event, ensure_ascii=False)+'\n').encode())
                return subprocess.CompletedProcess(a[0], 0)
            self.assertEqual(Dispatcher(path,Store(),runner=runner,host_probe=lambda:True).tick(),'unknown')
            with sqlite3.connect(path) as db:
                rows = db.execute('SELECT category FROM invocation_diagnostics').fetchall()
                self.assertEqual(rows, [('collector_binding_unavailable',)])
                self.assertNotIn('PRIVATE-SECRET', str(rows))
    def test_no_progress_records_structural_tool_failure_without_details(self):
        import json,sqlite3
        from contextlib import closing
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'journal'
            def runner(*a,**k):
                k['stdout'].write((json.dumps({'type':'item.completed','item':{'type':'mcp_tool_call','status':'failed','error':{'message':'PRIVATE-SECRET'}}})+'\n').encode())
                return subprocess.CompletedProcess(a[0],0)
            self.assertEqual(Dispatcher(path,Store(),runner=runner,host_probe=lambda:True).tick(),'unknown')
            with closing(sqlite3.connect(path)) as db:
                rows=db.execute('SELECT category FROM invocation_diagnostics').fetchall()
                self.assertEqual(rows,[('no_progress_tool_failure',)])
                self.assertNotIn('PRIVATE-SECRET',str(rows))
    def test_unknown_diagnostic_categories(self):
        import sqlite3
        from contextlib import closing
        def timed(*a, **k): raise subprocess.TimeoutExpired('private',1)
        def ioerr(*a, **k): raise OSError('secret')
        def invalid(*a, **k): raise ValueError('secret')
        for runner, expected in ((timed,'timeout'),(ioerr,'process_or_io_error'),(invalid,'preparation_or_bridge_error'),(lambda *a, **k: subprocess.CompletedProcess(a[0],0),'no_persisted_progress')):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'journal'; d=Dispatcher(path,Store(),runner=runner,host_probe=lambda:True)
                self.assertEqual(d.tick(),'unknown')
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(db.execute('SELECT category FROM invocation_diagnostics').fetchone()[0],expected)
    def test_unknown_diagnostics_are_bounded_and_do_not_store_output(self):
        import sqlite3
        from contextlib import closing
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'journal'
            d=Dispatcher(path,Store(),runner=lambda *a,**k:subprocess.CompletedProcess(a[0],7,stdout='secret-token',stderr='private-prompt'),host_probe=lambda:True)
            self.assertEqual(d.tick(),'unknown')
            with closing(sqlite3.connect(path)) as db:
                row=db.execute('SELECT category,exit_code FROM invocation_diagnostics').fetchone()
                self.assertEqual(row,('process_nonzero',7))
                self.assertNotIn('secret-token',str(db.execute('SELECT * FROM invocation_diagnostics').fetchall()))
                self.assertNotIn('private-prompt',str(db.execute('SELECT * FROM invocation_diagnostics').fetchall()))
    def test_empty_candidate_recovery_prompts_guarded_finish(self):
        from optimization_worker.interactive_dispatcher import research_prompt
        prompt=research_prompt({'action':'collect_candidate_images','candidates':[]})
        self.assertIn('candidates 为空',prompt)
        self.assertIn('确认当前标签守护可用',prompt)
    def test_cases(self):
        for case in (test_no_duplicate_after_ambiguous_timeout, test_checkpoint_change_allows_next_invocation, test_waiting_request_does_not_launch, test_child_does_not_inherit_odoo_secret, test_unrelated_request_is_not_progress, test_configured_codex_home_is_forwarded_without_secrets, test_timeout_survives_dispatcher_restart):
            with self.subTest(case=case.__name__), tempfile.TemporaryDirectory() as directory:
                case(Path(directory))


def test_child_does_not_inherit_odoo_secret(tmp_path):
    import os
    from unittest.mock import patch
    store = Store()
    def runner(*args, **kwargs):
        assert 'ODOO_API_KEY' not in kwargs['env']
        return subprocess.CompletedProcess(args[0], 0)
    with patch.dict(os.environ, {'ODOO_API_KEY': 'secret'}):
        Dispatcher(tmp_path / 'j.sqlite', store, runner=runner, host_probe=lambda:True).tick()


def test_unrelated_request_is_not_progress(tmp_path):
    store = Store()
    original = dict(store.action)
    store.inspect = lambda request: original
    def runner(*args, **kwargs):
        store.action['request_id'] = 'other'
        return subprocess.CompletedProcess(args[0], 0)
    assert Dispatcher(tmp_path / 'j.sqlite', store, runner=runner, host_probe=lambda:True).tick() == 'unknown'


def test_configured_codex_home_is_forwarded_without_secrets(tmp_path):
    import os
    from unittest.mock import patch
    seen = {}
    def runner(*args, **kwargs):
        seen.update(kwargs['env'])
        return subprocess.CompletedProcess(args[0], 1)
    with patch.dict(os.environ, {'ODOO_API_KEY': 'private', 'OPTIMIZATION_INTERACTIVE_CODEX_HOME': '/tmp/codex-home'}):
        Dispatcher(tmp_path / 'j.sqlite', Store(), runner=runner, host_probe=lambda:True).tick()
    assert seen['CODEX_HOME'] == '/tmp/codex-home'
    assert 'ODOO_API_KEY' not in seen


def test_timeout_survives_dispatcher_restart(tmp_path):
    calls = []
    def runner(*args, **kwargs):
        calls.append(1)
        raise subprocess.TimeoutExpired(args[0], 1)
    path = tmp_path / 'j.sqlite'
    assert Dispatcher(path, Store(), runner=runner, host_probe=lambda:True).tick() == 'unknown'
    assert Dispatcher(path, Store(), runner=runner, host_probe=lambda:True).tick() == 'unknown'
    assert len(calls) == 1

class SocketIntegrationTests(unittest.TestCase):
    def test_subprocess_receives_request_bound_socket_and_can_return(self):
        import os
        from optimization_worker.research_bridge_socket import send
        store=Store()
        class Bridge:
            request_id='r1'
            def call(self, request, operation, data):
                self_request=request
                assert self_request == 'r1'
                store.action['state']='candidate_images_pending'
                store.action['action']='collect_candidate_images'
                return {'accepted':True}
        def runner(*args, **kwargs):
            assert 'ODOO_API_KEY' not in kwargs['env']
            result=send(kwargs['env']['OPTIMIZATION_RETURN_SOCKET'],{'operation':'finish','data':{}})
            assert result == {'accepted':True}
            return subprocess.CompletedProcess(args[0],0)
        with tempfile.TemporaryDirectory() as tmp:
            config={'home':tmp,'cwd':tmp,'computer_use_cwd':tmp}
            dispatcher=Dispatcher(Path(tmp)/'j.sqlite', store, runner=runner, host_probe=lambda:True, bridge_factory=lambda request: Bridge(),config=config)
            self.assertEqual(dispatcher.tick(),'progress')

class InvocationConfigTests(unittest.TestCase):
    def test_configuration_requires_real_absolute_paths_and_loopback_proxy(self):
        from optimization_worker.interactive_dispatcher import codex_settings
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            config={'home':str(root),'cwd':str(root),'computer_use_cwd':str(root),'proxy':'http://127.0.0.1:7897'}
            result=codex_settings(config)
            self.assertEqual(result['cwd'],str(root))
            self.assertEqual(result['env']['CODEX_HOME'],str(root))
            self.assertEqual(result['env']['HTTPS_PROXY'],'http://127.0.0.1:7897')
            for bad in ('http://example.com:7897','http://user:pass@127.0.0.1:7897','http://127.0.0.1:7897/path'):
                with self.assertRaises(ValueError): codex_settings({**config,'proxy':bad})
            with self.assertRaises(ValueError): codex_settings({**config,'cwd':'relative'})

    def test_prompt_contains_socket_protocol_and_no_base_write(self):
        from optimization_worker.interactive_dispatcher import research_prompt
        prompt=research_prompt({'request_id':'r','action':'browse_research'})
        for term in ('OPTIMIZATION_RETURN_SOCKET','observed_at','base64','collector_cli','飞书','验证码','实际观察时刻','完整的商品详情图库','严禁冒充成功'):
            self.assertIn(term,prompt)

    def test_runner_uses_configured_directory_proxy_and_no_business_secret(self):
        from unittest.mock import patch
        store=Store()
        with tempfile.TemporaryDirectory() as tmp:
            config={'home':tmp,'cwd':tmp,'computer_use_cwd':tmp,'proxy':'http://127.0.0.1:7897'}
            def runner(*args,**kwargs):
                self.assertEqual(kwargs['cwd'],tmp)
                self.assertIn('mcp_servers.computer-use.cwd='+__import__('json').dumps(tmp),args[0])
                self.assertEqual(kwargs['env']['CODEX_HOME'],tmp)
                self.assertEqual(kwargs['env']['HTTPS_PROXY'],config['proxy'])
                self.assertNotIn('ODOO_API_KEY',kwargs['env'])
                return subprocess.CompletedProcess(args[0],0)
            with patch.dict('os.environ',{'ODOO_API_KEY':'secret'}):
                dispatcher=Dispatcher(Path(tmp)/'j.sqlite',store,runner=runner, host_probe=lambda:True,config=config)
                self.assertEqual(dispatcher.tick(),'unknown')

class SafetyDefaultsTests(unittest.TestCase):
    def test_missing_or_failed_probe_prevents_launch(self):
        calls=[]
        with tempfile.TemporaryDirectory() as tmp:
            runner=lambda *a,**k: calls.append(1)
            self.assertEqual(Dispatcher(Path(tmp)/'one',Store(),runner=runner).tick(),'waiting_for_host')
            self.assertEqual(Dispatcher(Path(tmp)/'two',Store(),runner=runner,host_probe=lambda:False).tick(),'waiting_for_host')
            self.assertEqual(calls,[])

    def test_mcp_cwd_is_separate_and_safely_quoted(self):
        from optimization_worker.interactive_dispatcher import codex_settings
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'computer "use"';p.mkdir()
            settings=codex_settings({'home':tmp,'cwd':tmp,'computer_use_cwd':str(p)})
            self.assertEqual(settings['mcp_override'],'mcp_servers.computer-use.cwd='+__import__('json').dumps(str(p)))

class DurableCheckpointTests(unittest.TestCase):
    def test_authorized_retry_consumes_reconciled_same_checkpoint_only_once(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            runner=Mock(return_value=subprocess.CompletedProcess([],1))
            dispatcher=Dispatcher(Path(tmp)/'journal',Store(),runner=runner,host_probe=lambda:True)
            self.assertEqual(dispatcher.tick(),'unknown')
            dispatcher.acknowledge_reconciliation('r1')
            self.assertEqual(dispatcher.tick(),'unknown')
            self.assertEqual(dispatcher.tick(),'unknown')
            self.assertEqual(runner.call_count,2)

    def test_plan_change_with_unchanged_action_can_continue(self):
        store=Store()
        store.record={'state':'prepared'}
        store.inspect=lambda request: dict(store.record)
        calls=[]
        def runner(*args, **kwargs):
            calls.append(1)
            if len(calls)==1: store.record['collector_plan']={'digest':'verified-plan'}
            return subprocess.CompletedProcess(args[0],0)
        with tempfile.TemporaryDirectory() as tmp:
            dispatcher=Dispatcher(Path(tmp)/'j.sqlite',store,runner=runner,host_probe=lambda:True)
            self.assertEqual(dispatcher.tick(),'progress')
            self.assertEqual(dispatcher.tick(),'unknown')
            self.assertEqual(len(calls),2)

class ResearchResumePromptTests(unittest.TestCase):
    def test_search_requires_platform_field_and_post_action_verification(self):
        from optimization_worker.interactive_dispatcher import research_prompt
        prompt=research_prompt({'request_id':'r','action':'browse_research'})
        for phrase in ('不能将搜索词追加到地址栏已有网址','搜索框获得焦点','搜索结果页面和查询词','不要额外按 Return','打开任务专属标签'):
            self.assertIn(phrase,prompt)
    def test_stale_collector_does_not_confuse_request_and_collector_ids(self):
        from optimization_worker.interactive_dispatcher import research_prompt
        prompt=research_prompt({'request_id':'r','action':'browse_research'})
        for phrase in ('旧任务已非活动','无需确认旧采集进度','保留扩展 storage','不得把 request_id 输入采集记录 ID'):
            self.assertIn(phrase,prompt)
    def test_prompt_explicitly_reuses_binding_and_does_not_repeat_unknown_upload(self):
        from optimization_worker.interactive_dispatcher import research_prompt
        prompt=research_prompt({'request_id':'r','action':'collect_candidate_images','collector_plan':{'queries':['a','b','c']},'gallery_manifests':{},'candidate_uploads':{}})
        for phrase in ('collector_plan','gallery_step','gallery_manifests','unknown','不得重复上传'):
            self.assertIn(phrase,prompt)

class SandboxProfileTests(unittest.TestCase):
    def test_profile_allowlists_only_request_socket_and_denies_known_secrets(self):
        from optimization_worker.interactive_dispatcher import socket_sandbox_overrides
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);work=root/'work';work.mkdir();home=root/'home';home.mkdir();ipc=root/'ipc';ipc.mkdir()
            values=socket_sandbox_overrides(ipc/'socket',work,home)
            joined=' '.join(values)
            self.assertIn('default_permissions="research-',joined)
            self.assertIn('extends=":read-only"',joined)
            self.assertIn(str(ipc/'socket'),joined)
            self.assertIn(str(work/'.local'),joined)
            self.assertIn(str(home/'.codex'/'auth.json'),joined)
            self.assertNotIn('danger-full-access',joined)

    def test_permission_profile_is_unique_per_invocation(self):
        from optimization_worker.interactive_dispatcher import socket_sandbox_overrides
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);work=root/'work';work.mkdir();home=root/'home';home.mkdir();ipc=root/'ipc';ipc.mkdir()
            first=socket_sandbox_overrides(ipc/'socket',work,home)
            second=socket_sandbox_overrides(ipc/'socket',work,home)
            names=lambda flags: [s for s in flags if s.startswith('default_permissions=')]
            self.assertNotEqual(names(first),names(second))

class BridgeShutdownTests(unittest.TestCase):
    def test_dispatcher_waits_for_inflight_callback_before_return(self):
        import threading
        from optimization_worker.research_bridge_socket import send
        store=Store();entered=threading.Event();release=threading.Event();finished=threading.Event()
        class Bridge:
            request_id='r1'
            def call(self, request, operation, data):
                entered.set();release.wait(20);store.action['state']='candidate_images_pending';store.action['action']='collect_candidate_images';return {'accepted':True}
        senders=[]
        def runner(*args,**kwargs):
            t=threading.Thread(target=lambda:send(kwargs['env']['OPTIMIZATION_RETURN_SOCKET'],{'operation':'finish','data':{}}));t.start();senders.append(t)
            assert entered.wait(1)
            return subprocess.CompletedProcess(args[0],0)
        with tempfile.TemporaryDirectory() as tmp:
            config={'home':tmp,'cwd':tmp,'computer_use_cwd':tmp}
            dispatcher=Dispatcher(Path(tmp)/'j.sqlite',store,runner=runner,host_probe=lambda:True,bridge_factory=lambda request:Bridge(),config=config)
            worker=threading.Thread(target=lambda:(dispatcher.tick(),finished.set()));worker.start()
            self.assertTrue(entered.wait(1));self.assertFalse(finished.wait(12.5))
            release.set();worker.join(3);senders[0].join(3);self.assertTrue(finished.is_set())

class UnknownIsolationProviderTests(unittest.TestCase):
    def test_original_unknown_request_never_returns_a_result(self):
        from optimization_worker.host_session import SessionProvider, SessionUnknown
        class Store:
            def enqueue(self,*args): pass
            def inspect(self,request): return {'state':'unknown','reason':'unknown'}
            def result(self,request): raise AssertionError('unknown request cannot be treated as result')
        with self.assertRaises(SessionUnknown):
            SessionProvider(Store()).run('research.bestseller',{}, {}, 'original')

class PrivateImageStagingTests(unittest.TestCase):
    def test_verified_image_is_staged_without_private_path(self):
        import hashlib
        from optimization_worker.interactive_dispatcher import stage_research_action
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'original.webp';source.write_bytes(b'image fixture'); out=root/'ipc';out.mkdir();digest=hashlib.sha256(source.read_bytes()).hexdigest()
            action={'request_id':'r','image_paths':{'1':str(source)},'input_digests':{str(source):digest},'context':{'artifacts':{'1':{'digest':digest,'mime_type':'image/webp'}}}}
            result=stage_research_action(action,out)
            staged=Path(result['image_paths']['1']);self.assertEqual(staged.read_bytes(),b'image fixture');self.assertEqual(result['input_digests'][str(staged)],digest);self.assertNotIn(str(source),str(result))
    def test_changed_digest_and_symlink_are_rejected(self):
        import hashlib
        from optimization_worker.interactive_dispatcher import stage_research_action
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'original.webp';source.write_bytes(b'image'); link=root/'link.webp';link.symlink_to(source);out=root/'ipc';out.mkdir();digest=hashlib.sha256(b'image').hexdigest()
            for path, observed, artifact in ((source,'0'*64,digest),(source,digest,'0'*64),(link,digest,digest)):
                action={'image_paths':{'1':str(path)},'input_digests':{str(path):observed},'context':{'artifacts':{'1':{'digest':artifact,'mime_type':'image/webp'}}}}
                with self.assertRaises((ValueError,OSError)):stage_research_action(action,out)

class PrivateImageInvocationTests(unittest.TestCase):
    def test_runner_reads_staged_image_and_original_path_is_absent(self):
        import hashlib,json
        class StagedStore(Store):
            def __init__(self, source):
                super().__init__();digest=hashlib.sha256(source.read_bytes()).hexdigest()
                self.action.update(image_paths={'1':str(source)},input_digests={str(source):digest},context={'artifacts':{'1':{'digest':digest,'mime_type':'image/webp'}}})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'secret.webp';source.write_bytes(b'source image');store=StagedStore(source);seen=[]
            class Bridge: request_id='r1'
            def runner(*args,**kwargs):
                prompt=kwargs['input'];self.assertNotIn(str(source),prompt)
                action=json.loads(prompt.split('任务数据：\n',1)[1]);staged=Path(action['image_paths']['1'])
                self.assertEqual(staged.read_bytes(),b'source image');seen.append(staged)
                store.action['state']='candidate_images_pending'
                return subprocess.CompletedProcess(args[0],0)
            config={'home':tmp,'cwd':tmp,'computer_use_cwd':tmp}
            d=Dispatcher(root/'journal',store,runner=runner,host_probe=lambda:True,bridge_factory=lambda request:Bridge(),config=config)
            self.assertEqual(d.tick(),'progress');self.assertFalse(seen[0].exists())

class ReturnInterpreterTests(unittest.TestCase):
    def test_bridge_prompt_uses_current_python_executable(self):
        import sys
        from optimization_worker.interactive_dispatcher import research_prompt
        self.assertIn(sys.executable+' -m optimization_worker.research_bridge_socket',research_prompt({'request_id':'r'}))

class StagedImageTypeTests(unittest.TestCase):
    def test_staged_suffix_follows_verified_artifact_mime(self):
        import hashlib
        from optimization_worker.interactive_dispatcher import stage_research_action
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=root/'original';s.write_bytes(b'abc');o=root/'out';o.mkdir();d=hashlib.sha256(b'abc').hexdigest()
            a={'image_paths':{'1':str(s)},'input_digests':{str(s):d},'context':{'artifacts':{'1':{'digest':d,'mime_type':'image/webp'}}}}
            self.assertEqual(Path(stage_research_action(a,o)['image_paths']['1']).suffix,'.webp')

class SourceImageAttachmentTests(unittest.TestCase):
    def test_staged_first_source_image_is_attached_to_codex(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'a.jpg';source.write_bytes(b'img');digest=hashlib.sha256(b'img').hexdigest();store=Store()
            store.action.update(image_paths={'1':str(source)},input_digests={str(source):digest},context={'artifacts':{'1':{'digest':digest,'mime_type':'image/jpeg'}},'snapshot':{'payload':{'source_image_artifact_ids':[1]}}})
            class Bridge:request_id='r1'
            def runner(*args,**kwargs):
                cmd=args[0];self.assertIn('--image',cmd);p=Path(cmd[cmd.index('--image')+1]);self.assertEqual(p.read_bytes(),b'img');self.assertNotEqual(p,source)
                store.action['state']='candidate_images_pending';return subprocess.CompletedProcess(cmd,0)
            config={'home':tmp,'cwd':tmp,'computer_use_cwd':tmp}
            self.assertEqual(Dispatcher(root/'journal',store,runner=runner,host_probe=lambda:True,bridge_factory=lambda _:Bridge(),config=config).tick(),'progress')

class PlanPromptContractTests(unittest.TestCase):
    def test_prompt_explains_plan_contract(self):
        from optimization_worker.interactive_dispatcher import research_prompt
        text=research_prompt({'request_id':'r'})
        for term in ('category_code','womens_pants_set','query_seeds','source_artifact_id','viewed_source_sha256','颜色','尺码','具体子类'):
            self.assertIn(term,text)

class ResearchSearchSessionPromptTests(unittest.TestCase):
    def test_prompt_requires_fresh_session_and_exact_search_on_same_native_tab(self):
        from optimization_worker.interactive_dispatcher import research_prompt
        prompt=research_prompt({'request_id':'r'})
        for phrase in ('每次新任务','打开任务专属标签','不关闭或改写既有标签','搜索框','逐字','首条查询','完全可见','复用当前任务的采集器弹窗','三组','旧任务','网址 slug','检查点'):
            self.assertIn(phrase,prompt)

class ResearchStagePromptTests(unittest.TestCase):
    def test_bound_plan_is_not_resubmitted(self):
        from optimization_worker.interactive_dispatcher import research_prompt
        prompt = research_prompt({'request_id':'r','action':'browse_research','collector_plan':{'queries':['a','b','c']}})
        self.assertNotIn('每次新任务先提交计划',prompt)
        self.assertIn('已绑定计划，禁止再次提交计划',prompt)

    def test_first_stage_requires_plan(self):
        from optimization_worker.interactive_dispatcher import research_prompt
        prompt=research_prompt({'request_id':'r','action':'browse_research'})
        self.assertIn('先提交研究计划',prompt)

    def test_image_phase_rebinds_only_to_restore_the_existing_gallery(self):
        from optimization_worker.interactive_dispatcher import research_prompt
        prompt=research_prompt({'request_id':'r','action':'collect_candidate_images','collector_plan':{'queries':['a','b','c']}})
        for step in ('打开当前合格商品图库', '确认当前任务与窗口', '打开任务专属标签', 'guard_active=true',
                     '保留原请求', 'gallery_manifests', '回传有序图库清单与详情截图', 'image_saved'):
            self.assertIn(step, prompt)
        for search_action in ('第 1、2、3 组', 'Collect current search query', '切换到已绑定任务标签'):
            self.assertNotIn(search_action, prompt)
        self.assertNotIn('点击采集器“回传当前任务证据”', prompt)
        self.assertNotIn('调用 submit_staged', prompt)

    def test_bound_plan_checks_binding_before_any_new_tab(self):
        from optimization_worker.interactive_dispatcher import research_prompt
        prompt=research_prompt({'request_id':'r','action':'browse_research','collector_plan':{'queries':['a','b','c']}})
        self.assertIn('先调用回传桥 binding_status',prompt)
        self.assertIn('已有绑定时复用原 tab',prompt)
        self.assertIn('仅无绑定时',prompt)
        self.assertIn('回执数量不代表卡片采集进度',prompt)
        self.assertLess(prompt.index('先调用回传桥 binding_status'),prompt.index('仅无绑定时'))
        self.assertIn('会话变化时暂停',prompt)

class HomeRegistrationPromptTests(unittest.TestCase):
    def test_restart_recovery_uses_owned_window_controls(self):
        from optimization_worker.interactive_dispatcher import research_prompt
        prompt = research_prompt({'request_id':'r', 'action':'browse_research'})
        self.assertIn('准备专用采集窗口', prompt)
        self.assertIn('打开任务专属标签', prompt)
        self.assertNotIn('窗口登记后在 15 秒', prompt)

    def test_new_task_does_not_treat_unregistered_window_as_failure(self):
        from optimization_worker.interactive_dispatcher import research_prompt
        prompt = research_prompt({'request_id': 'r', 'action': 'browse_research'})
        self.assertIn('初始未登记是正常状态', prompt)
        self.assertIn('先提交研究计划', prompt)
        self.assertIn('登记尝试失败后才调用 window_blocked', prompt)
        self.assertNotIn('未登记时调用 window_blocked 并停止', prompt)
        self.assertNotIn('倒计时为 0 时调用 window_blocked', prompt)

    def test_control_page_precedes_platform_collector(self):
        from optimization_worker.interactive_dispatcher import research_prompt
        prompt = research_prompt({'request_id':'r','action':'browse_research'})
        self.assertIn('先打开专用窗口控制页', prompt)
        self.assertIn('控制页不依赖目标标签参数', prompt)
        self.assertIn('打开采集控制页', prompt)
        self.assertLess(prompt.index('先打开专用窗口控制页'), prompt.index('打开任务专属标签'))
        self.assertIn('不得在标记页打开平台采集器', prompt)
        self.assertNotIn('允许从目标平台首页登记窗口', prompt)

    def test_home_registration_precedes_search_and_receipts_are_not_progress(self):
        from optimization_worker.interactive_dispatcher import research_prompt
        prompt = research_prompt({'request_id':'r','action':'browse_research'})
        self.assertIn('先打开专用窗口控制页', prompt)
        self.assertIn('window_registration_failed', prompt)
        self.assertNotIn('completed 对应的下一条查询', prompt)

class ResearchBridgeStdinPromptTests(unittest.TestCase):
    def test_binding_example_uses_pipe_without_shell_heredoc(self):
        from optimization_worker.interactive_dispatcher import research_prompt
        prompt = research_prompt({'request_id':'r','action':'browse_research'})
        self.assertIn("printf '%s'", prompt)
        self.assertIn('禁止使用 heredoc', prompt)
        self.assertIn('{"operation":"binding_status","data":{}}', prompt)


class WaitingHandoffTests(unittest.TestCase):
    def test_waiting_handoff_survives_worker_journal_completion(self):
        store = Store()
        def runner(*args, **kwargs):
            store.action.update(state='waiting', reason='window_registration_failed', retired=True)
            return subprocess.CompletedProcess(args[0], 0)
        def probe(request):
            if store.action['state'] == 'waiting':
                raise ValueError('Active lease required')
            return 0
        with tempfile.TemporaryDirectory() as tmp:
            dispatcher = Dispatcher(Path(tmp) / 'journal.sqlite', store, runner=runner,
                                    host_probe=lambda: True, progress_probe=probe)
            self.assertEqual(dispatcher.tick(), 'progress')

    def test_unexplained_lost_lease_is_not_marked_finished(self):
        store = Store()
        def probe(request):
            if store.action.get('retired'):
                raise ValueError('Active lease required')
            return 0
        def runner(*args, **kwargs):
            store.action.update(retired=True)
            return subprocess.CompletedProcess(args[0], 0)
        with tempfile.TemporaryDirectory() as tmp:
            dispatcher = Dispatcher(Path(tmp) / 'journal.sqlite', store, runner=runner,
                                    host_probe=lambda: True, progress_probe=probe)
            with self.assertRaises(ValueError):
                dispatcher.tick()

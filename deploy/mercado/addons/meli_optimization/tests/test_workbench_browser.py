"""Actual native action smoke test against a disposable database, no platform traffic."""
from unittest.mock import patch
from pathlib import Path
from odoo.tests import HttpCase, tagged
from odoo.tests.common import ChromeBrowser


@tagged('post_install', '-at_install', 'optimization_browser')
class WorkbenchBrowserCase(HttpCase):
    browser_size = '1440x1000'

    def test_workbench_browser_create_select_confirm(self):
        self.env.ref('base.user_admin').write({'group_ids': [(4, self.env.ref('meli_optimization.group_editor').id)]})
        ui = self.env['meli.optimization.case']
        detail = ui.workbench_create({'shop': '演示店铺', 'item_id': 'MLM-DEMO', 'title': 'Vestido elegante', 'seller_sku': 'SKU-A'})
        task = self.env['meli.optimization.task'].create({'case_id': detail['id'], 'snapshot_id': detail['snapshot']['id'],
            'task_type': 'title.generate', 'payload': {'schema_version': '1', 'facts_artifact_id': 'fixture', 'keywords_artifact_id': 'fixture', 'language': 'es-MX'}})
        claim = task.claim(['title.generate'])
        task.complete(claim['lease_token'], claim['lease_epoch'], 'browser-fixture', {'schema_version': '1',
            'evidence': [{'id': 'fixture', 'source': 'test fixture', 'observed_at': '2026-11-15T00:00:00Z'}],
            'candidates': [{'text': 'Vestido elegante opción %s' % i, 'evidence_ids': ['fixture']} for i in range(10)]})
        action_id = self.env.ref('meli_optimization.workbench_action').id
        spawn = ChromeBrowser._spawn_chrome
        def fixed_port_spawn(browser, cmd):
            # Chrome writes DevToolsActivePort only for port=0. Keep debugger
            # confined to the disposable runner's explicit loopback allowlist.
            Path(browser.user_data_dir, 'DevToolsActivePort').write_text('55441\n/devtools/browser\n')
            return spawn(browser, cmd)
        handle_console = ChromeBrowser._handle_console
        def screenshot_console(browser, type, args=None, **kwargs):
            values = [arg.get('value') for arg in (args or [])]
            handle_console(browser, type, args=args, **kwargs)
            for value in values:
                if isinstance(value, str) and value.startswith('workbench-shot-'):
                    browser.take_screenshot(prefix=value.replace('-', '_') + '_')
        original = ChromeBrowser._wait_code_ok
        def capture(browser, *args, **kwargs):
            result = original(browser, *args, **kwargs)
            browser.take_screenshot(prefix='workbench_').result(timeout=15)
            return result
        with patch.object(ChromeBrowser, 'remote_debugging_port', 55441), patch.object(ChromeBrowser, '_wait_code_ok', capture), patch.object(ChromeBrowser, '_spawn_chrome', fixed_port_spawn), patch.object(ChromeBrowser, '_handle_console', screenshot_console):
            self.browser_js('/odoo/action-%s' % action_id, r"""
                (async () => {
                    const wait = async (fn) => { for(let i=0;i<150;i++){const v=fn();if(v)return v;await new Promise(r=>setTimeout(r,100));}throw Error('UI timeout'); };
                    const button = (text) => [...document.querySelectorAll('.meli-workbench button')].find(b=>b.textContent.includes(text));
                    await wait(()=>document.querySelector('.mw-table .mw-link'));
                    document.querySelector('.mw-table .mw-link').click();
                    await wait(()=>button('标题候选')); button('标题候选').click();
                    await wait(()=>document.querySelector('.mw-choice input'));
                    console.log('workbench-shot-titles');
                    await new Promise(r=>setTimeout(r,300));
                    document.querySelector('.mw-choice input').click();
                    await wait(()=>button('生成确切方案预览')); button('生成确切方案预览').click();
                    await wait(()=>button('确认以上确切方案'));
                    if(!document.querySelector('.mw-preview').textContent.includes('Vestido elegante opción 0')) throw Error('Exact candidate absent');
                    console.log('workbench-shot-preview');
                    await new Promise(r=>setTimeout(r,300));
                    button('确认以上确切方案').click();
                    await wait(()=>document.querySelector('.mw-task')?.textContent.includes('已确认'));
                    button('返回列表').click(); button('新建优化').click();
                    await wait(()=>document.querySelector('.mw-form'));
                    if(document.querySelectorAll('.mw-form input').length < 7) throw Error('Product fields missing');
                    console.log('workbench-shot-form');
                    await new Promise(r=>setTimeout(r,300));
                    document.querySelector('[aria-label="关闭对话框"]').click();
                    console.log('test successful');
                })().catch(e=>console.error(e));
            """, login='admin', timeout=100)

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from optimization_worker.chrome_live_probe import probe_chrome


def event(code='await cua.getApp("Google Chrome");', status='completed', error=None):
    return {'type': 'item.completed', 'item': {'type': 'mcp_tool_call', 'server': 'cua_repl', 'tool': 'js', 'status': status, 'error': error, 'arguments': {'code': code}, 'result': {'content': [{'type': 'text', 'text': 'Window: Google Chrome'}]}}}


def screenshot_event():
    return {'type': 'item.completed', 'item': {'type':'mcp_tool_call','server':'cua_repl','tool':'js','status':'completed','error':None,'arguments':{'code':'await app.getScreenshot();'},'result':{'content':[{'type':'image','mimeType':'image/jpeg','data':'/9j/2Q=='}]}}}

class ChromeProbeTests(unittest.TestCase):
    def test_screenshot_wrapped_as_json_text_is_verified(self):
        with tempfile.TemporaryDirectory() as root:
            capture=screenshot_event()
            image=capture['item']['result']['content'][0]
            capture['item']['result']['content']=[{'type':'text','text':json.dumps({'content':[image]})}]
            runner=lambda command, **kwargs: subprocess.CompletedProcess(command,0,json.dumps(event())+'\n'+json.dumps(capture))
            self.assertTrue(probe_chrome(root,root,root,'',runner=runner))
    def test_actual_tool_event_required_and_credentials_excluded(self):
        with tempfile.TemporaryDirectory() as root:
            seen = {}
            def runner(command, **kwargs):
                seen.update(kwargs)
                return subprocess.CompletedProcess(command, 0, json.dumps(event()) + '\n' + json.dumps(screenshot_event()) + '\n')
            self.assertTrue(probe_chrome(root, root, root, '', runner=runner))
            self.assertNotIn('ODOO_API_KEY', seen['env'])

    def test_model_claim_or_extra_ui_action_is_not_proof(self):
        with tempfile.TemporaryDirectory() as root:
            for stream in (json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'success'}}),
                           '\n'.join([json.dumps(event()), json.dumps(event('await app.click(3);'))])):
                runner = lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stream)
                self.assertFalse(probe_chrome(root, root, root, '', runner=runner))

    def test_ax_alone_and_screenshot_failure_are_not_ready(self):
        with tempfile.TemporaryDirectory() as root:
            for stream in (json.dumps(event()), json.dumps(event())+'\n'+json.dumps({**screenshot_event(), 'item':{**screenshot_event()['item'], 'result':{'content':[{'type':'text','text':'Screenshot unavailable'}]}}})):
                runner=lambda command, **kwargs: subprocess.CompletedProcess(command,0,stream)
                self.assertFalse(probe_chrome(root,root,root,'',runner=runner))

    def test_screenshot_call_allows_optional_title(self):
        with tempfile.TemporaryDirectory() as root:
            capture=screenshot_event()
            capture['item']['arguments']['title']='检查截图'
            stream=json.dumps(event())+'\n'+json.dumps(capture)
            runner=lambda command, **kwargs: subprocess.CompletedProcess(command,0,stream)
            self.assertTrue(probe_chrome(root,root,root,'',runner=runner))

    def test_failure_and_timeout_are_unavailable(self):
        with tempfile.TemporaryDirectory() as root:
            def runner(command, **kwargs): raise subprocess.TimeoutExpired(command, 1)
            self.assertFalse(probe_chrome(root, root, root, '', runner=runner))

    def test_probe_distinguishes_missing_and_invalid_screenshots(self):
        with tempfile.TemporaryDirectory() as root:
            for content, code in [
                ([{'type':'text','text':'AX state unchanged'}], 'chrome_screenshot_missing'),
                ([{'type':'image','mimeType':'image/jpeg','data':'not a screenshot'}], 'chrome_screenshot_invalid'),
            ]:
                capture = screenshot_event()
                capture['item']['result']['content'] = content
                def runner(command, **kwargs):
                    return subprocess.CompletedProcess(command, 0, json.dumps(event())+'\n'+json.dumps(capture))
                with self.subTest(code=code), self.assertLogs('optimization_worker.chrome_live_probe', level='WARNING') as logs:
                    self.assertFalse(probe_chrome(root,root,root,'',runner=runner))
                self.assertIn(code, logs.output[0])

    def test_compact_probe_proof_requires_exact_tool_code(self):
        from optimization_worker.chrome_live_probe import _COMPACT_CAPTURE
        capture = screenshot_event()
        item = capture['item']
        item['arguments']['code'] = _COMPACT_CAPTURE
        item['result']['content'] = [{'type':'text','text':'Chrome screenshot proof: jpeg 523010'}]
        with tempfile.TemporaryDirectory() as root:
            def runner(command, **kwargs):
                return subprocess.CompletedProcess(command, 0, json.dumps(event())+'\n'+json.dumps(capture))
            self.assertTrue(probe_chrome(root,root,root,'',runner=runner))
            item['arguments']['code'] = 'nodeRepl.write("Chrome screenshot proof: jpeg 523010")'
            self.assertFalse(probe_chrome(root,root,root,'',runner=runner))

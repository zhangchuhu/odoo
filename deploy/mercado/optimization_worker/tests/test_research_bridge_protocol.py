import base64
import json
import unittest
from optimization_worker.research_bridge_protocol import handle_message

class Tests(unittest.TestCase):
    def test_rejected_window_blocker_is_visible_to_supervisor(self):
        class Bridge:
            request_id='bound'
            window_blocker_rejected=False
            def call(self,*args): raise ValueError('private details')
        bridge=Bridge()
        self.assertEqual(json.loads(handle_message(bridge,b'{"operation":"window_blocked","data":{"code":"window_registration_failed"}}'))['accepted'],False)
        self.assertTrue(bridge.window_blocker_rejected)
    def test_window_blocker_reaches_fenced_bridge(self):
        from optimization_worker.research_return_bridge import ReturnBridge
        rid='8b4f7606-7811-4070-925d-d21cc78f13f0'; calls=[]
        bridge=ReturnBridge(rid,lambda:{'task_type':'research.bestseller'},lambda:calls.append('fence'),{'window_blocked':lambda c,d:calls.append(d['code'])})
        payload=b'{"operation":"window_blocked","data":{"code":"window_registration_failed"}}'
        self.assertEqual(json.loads(handle_message(bridge,payload)),{'accepted':True})
        self.assertEqual(calls,['fence','window_registration_failed'])

    def test_bound_request_and_image_bytes(self):
        calls=[]
        class Bridge:
            request_id='bound'
            def call(self, request, action, data): calls.append((request, action, data)); return {'accepted':True}
        msg={'operation':'image','data':{'file':base64.b64encode(b'img').decode(),'screenshot':base64.b64encode(b'screen').decode(),'identity':'id','source':'https://example.test/a','order':0}}
        self.assertEqual(json.loads(handle_message(Bridge(),json.dumps(msg).encode())),{'accepted':True})
        self.assertEqual(calls[0][0], 'bound')
        self.assertEqual(calls[0][2]['file'],b'img')

    def test_cannot_select_request_or_expose_exception(self):
        class Bridge:
            request_id='bound'
            def call(self,*args): raise RuntimeError('secret')
        for msg in ({'operation':'finish','data':{},'request_id':'other'}, {'operation':'finish','data':{}}):
            result=handle_message(Bridge(),json.dumps(msg).encode())
            self.assertNotIn(b'secret',result)
            self.assertEqual(json.loads(result),{'accepted':False})

    def test_rejects_oversize_or_invalid_base64(self):
        class Bridge:
            request_id='bound'
            def call(self,*args): raise AssertionError('must not call')
        for msg in (b'x'*24000000, b'{"operation":"image","data":{"file":"!"}}'):
            self.assertEqual(json.loads(handle_message(Bridge(),msg)),{'accepted':False})

class PlanFeedbackTests(unittest.TestCase):
    def test_visual_v2_plan_accepts_bound_design_profile(self):
        import re
        from pathlib import Path
        from optimization_worker.research_bridge_protocol import _valid_plan_input
        doc=(Path(__file__).resolve().parents[1]/'RESEARCH_BRIDGE.md').read_text()
        plan=json.loads(re.findall(r'```json\n(.*?)\n```',doc,re.S)[-1])
        plan['strategy']='visual-v2'
        plan['design_profile']={'primary':{'category':plan['category'],'components':None,'length':'long','shoulders':'regular',
            'structure':'two_piece','silhouette':'a_line','fit':None,'sleeve_length':None},'secondary':['lace'],'styles':['boho'],'occasions':[]}
        self.assertTrue(_valid_plan_input(plan))
        self.assertTrue(_valid_plan_input(dict(plan,strategy='card-visual-v3')))
        plan['design_profile']['primary']['category']='wrong'
        self.assertFalse(_valid_plan_input(plan))

    def test_invalid_plan_returns_safe_static_guidance_only(self):
        from optimization_worker.research_bridge_protocol import handle_message
        class Bridge:
            request_id='r'
            def call(self,*args): raise ValueError('secret credential path')
        import json
        result=json.loads(handle_message(Bridge(),b'{"operation":"plan","data":{}}'))
        self.assertEqual(result['accepted'],False)
        self.assertEqual(result['code'],'invalid_plan')
        self.assertIn('category_code',result['guidance'])
        self.assertNotIn('secret',str(result))

class FenceFeedbackTests(unittest.TestCase):
    def test_valid_plan_with_fence_failure_returns_generic_reject(self):
        import re
        from pathlib import Path
        doc=(Path(__file__).resolve().parents[1]/'RESEARCH_BRIDGE.md').read_text()
        plan=json.loads(re.findall(r'```json\n(.*?)\n```',doc,re.S)[-1])
        class Bridge:
            request_id='r'
            def call(self,*args): raise ValueError('lease expired sensitive')
        result=json.loads(handle_message(Bridge(),json.dumps({'operation':'plan','data':plan}).encode()))
        self.assertEqual(result,{'accepted':False})

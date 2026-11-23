import unittest
from optimization_worker.research_tab_binding import bind_tab, verify_tab

class ResearchTabBindingTests(unittest.TestCase):
    def test_binding_roundtrip_and_exact_query(self):
        plan={'platform':'mercado-libre-mx','queries':['vestido corto','vestido fiesta','vestido encaje']}
        binding=bind_tab('req-1', plan, {'tab_id':123,'window_id':42,'platform':'mercado-libre-mx','browser_session':'a','collector_tab_id':123})
        self.assertTrue(verify_tab(binding, 'req-1', plan, {'tab_id':123,'window_id':42,'platform':'mercado-libre-mx','browser_session':'a','collector_tab_id':123,'query':'vestido fiesta'}, 1))
        self.assertFalse(verify_tab(binding, 'req-1', plan, {'tab_id':124,'window_id':42,'platform':'mercado-libre-mx','browser_session':'a','collector_tab_id':123,'query':'vestido fiesta'}, 1))
        self.assertFalse(verify_tab(binding, 'req-1', plan, {'tab_id':123,'window_id':42,'platform':'mercado-libre-mx','browser_session':'a','collector_tab_id':123,'query':'vestido  fiesta'}, 1))
        self.assertFalse(verify_tab(binding, 'req-1', plan, {'tab_id':123,'window_id':42,'platform':'shein-us','browser_session':'a','collector_tab_id':123,'query':'vestido fiesta'}, 1))

    def test_tampering_and_invalid_ids_rejected(self):
        plan={'platform':'shein-us','queries':['one','two','three']}
        with self.assertRaises(ValueError): bind_tab('r',plan,{'tab_id':True,'window_id':1,'platform':'shein-us','browser_session':'a','collector_tab_id':1})
        binding=bind_tab('r',plan,{'tab_id':1,'window_id':2,'platform':'shein-us','browser_session':'a','collector_tab_id':1})
        binding['tab_id']=3
        self.assertFalse(verify_tab(binding,'r',plan,{'tab_id':3,'window_id':2,'platform':'shein-us','browser_session':'a','collector_tab_id':123,'query':'one'},0))

    def test_collector_and_browser_session_are_bound(self):
        plan={'platform':'shein-us','queries':['one','two','three']}
        binding=bind_tab('r',plan,{'tab_id':1,'window_id':2,'platform':'shein-us','browser_session':'session-a','collector_tab_id':1})
        observation={'tab_id':1,'window_id':2,'platform':'shein-us','browser_session':'a','collector_tab_id':123,'query':'one','browser_session':'session-a','collector_tab_id':1}
        self.assertTrue(verify_tab(binding,'r',plan,observation,0))
        self.assertFalse(verify_tab(binding,'r',plan,{**observation,'browser_session':'session-b'},0))
        self.assertFalse(verify_tab(binding,'r',plan,{**observation,'collector_tab_id':9},0))

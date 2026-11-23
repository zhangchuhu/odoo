import unittest
from optimization_worker.color_guard import validate_colors

class ColorGuardTest(unittest.TestCase):
    def base(self):
        return {'title':'Conjunto Color Caqui'}, {'selected_reference_ids':['1'], 'source_evidence':[{'source_id':'1','observed_color':'khaki'}], 'target_color':'khaki'}
    def test_matching_color_is_accepted(self):
        snapshot,plan=self.base(); validate_colors(snapshot,plan)
    def test_mismatched_or_unknown_is_blocked(self):
        snapshot,plan=self.base()
        for color in ('black','unknown',None):
            plan['source_evidence'][0]['observed_color']=color
            with self.assertRaises(ValueError): validate_colors(snapshot,plan)
    def test_multiple_variants_without_selection_are_blocked(self):
        snapshot,plan=self.base(); snapshot['variations']=[{'attribute_combinations':[{'id':'COLOR','value_name':'Marrón'}]}, {'attribute_combinations':[{'id':'COLOR','value_name':'Negro'}]}]
        with self.assertRaises(ValueError): validate_colors(snapshot,plan)
    def test_missing_or_conflicting_title_is_blocked(self):
        snapshot,plan=self.base()
        for title in ('Conjunto','Conjunto negro y caqui'):
            snapshot['title']=title
            with self.assertRaises(ValueError): validate_colors(snapshot,plan)
    def test_unselected_other_color_does_not_block(self):
        snapshot,plan=self.base()
        plan['source_evidence'].append({'source_id':'2','observed_color':'black','roles':['model']})
        validate_colors(snapshot,plan)
    def test_chinese_color_in_compound_title(self):
        snapshot,plan=self.base(); snapshot['title']='女士黑色套装';plan['target_color']='black';plan['source_evidence'][0]['observed_color']='black'
        validate_colors(snapshot,plan)
    def test_unknown_variant_color_pauses(self):
        snapshot,plan=self.base(); snapshot['variations']=[{'attribute_combinations':[{'id':'COLOR','value_name':'Fantasy 42'}]}]
        with self.assertRaises(ValueError): validate_colors(snapshot,plan)
    def test_absent_snapshot_fails_closed(self):
        _,plan=self.base()
        with self.assertRaises(ValueError): validate_colors({},plan)

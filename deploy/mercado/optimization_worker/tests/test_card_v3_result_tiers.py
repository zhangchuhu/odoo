import unittest
from optimization_worker.domain import card_v3_tier_valid

class ResultTierTest(unittest.TestCase):
    def test_threshold_and_supplemental_use_task_threshold_and_strict_floor(self):
        self.assertTrue(card_v3_tier_valid({'sales_tier':'threshold_met','sold_lower_bound':500},500,'threshold_met'))
        self.assertTrue(card_v3_tier_valid({'sales_tier':'supplemental','sold_lower_bound':150},500,'supplemental'))
        self.assertFalse(card_v3_tier_valid({'sales_tier':'supplemental','sold_lower_bound':100},500,'supplemental'))
        self.assertFalse(card_v3_tier_valid({'sales_tier':'supplemental','sold_lower_bound':500},500,'supplemental'))
        self.assertFalse(card_v3_tier_valid({'sales_tier':'threshold_met','sold_lower_bound':100},0,'threshold_met'))
        plus={'sales_tier':'supplemental','sold_lower_bound':100,'sold_display':'+100 vendidos'}
        self.assertTrue(card_v3_tier_valid(plus,500,'supplemental',allow_plus_hundred=True))
        self.assertFalse(card_v3_tier_valid(plus,500,'supplemental'))
        self.assertFalse(card_v3_tier_valid(dict(plus,sold_display='100 vendidos'),500,'supplemental',allow_plus_hundred=True))
        self.assertFalse(card_v3_tier_valid(dict(plus,sold_display='+1000 vendidos'),500,'supplemental',allow_plus_hundred=True))
        self.assertTrue(card_v3_tier_valid(dict(plus,sales_tier='threshold_met'),101,'threshold_met',allow_plus_hundred=True))
        self.assertTrue(card_v3_tier_valid(plus,102,'supplemental',allow_plus_hundred=True))

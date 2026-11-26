import unittest
from optimization_worker.research_result_candidates import all_candidates

class ResultCandidateTests(unittest.TestCase):
    def test_combines_distinct_tiers_and_rejects_duplicate_identity(self):
        main={'platform':'mercado-libre-mx','item_id':'MLM1'}
        extra={'platform':'mercado-libre-mx','item_id':'MLM2'}
        self.assertEqual(all_candidates({'candidates':[main],'supplemental_candidates':[extra]}),[main,extra])
        self.assertEqual(all_candidates({'candidates':[main]}),[main])
        with self.assertRaises(ValueError):all_candidates({'candidates':[main],'supplemental_candidates':[main]})

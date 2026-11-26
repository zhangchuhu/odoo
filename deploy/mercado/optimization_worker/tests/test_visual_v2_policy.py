import unittest

from optimization_worker.visual_v2_policy import next_action, validate_queries


class VisualV2PolicyTest(unittest.TestCase):
    def test_target_counts_only_completed_galleries(self):
        self.assertEqual(next_action(2, 2, [], ['a'], True)['reason'], 'target_reached')

    def test_unfinished_candidate_before_expansion(self):
        self.assertEqual(next_action(3, 0, ['a'], [], False), {'action': 'review', 'identity': 'a'})

    def test_pending_search_before_expansion(self):
        self.assertEqual(next_action(3, 0, [], ['q1'], False), {'action': 'search', 'query': 'q1'})

    def test_generate_expansion_when_existing_work_done(self):
        self.assertEqual(next_action(3, 0, [], [], False), {'action': 'expand'})

    def test_finished_when_plan_and_candidates_exhausted(self):
        self.assertEqual(next_action(3, 0, [], [], True), {'action': 'finish', 'reason': 'search_plan_exhausted'})

    def test_long_running_or_many_candidates_is_not_a_stop_condition(self):
        self.assertEqual(next_action(100, 0, [str(n) for n in range(100)], [], True)['action'], 'review')

    def test_query_plan_allows_three_initial_and_six_extra(self):
        self.assertEqual(len(validate_queries(['vestido modelo ' + str(i) for i in range(9)])), 9)

    def test_query_plan_rejects_duplicates_and_more_than_nine(self):
        with self.assertRaises(ValueError):
            validate_queries(['vestido dama', 'Vestido   dama', 'vestido largo'])
        with self.assertRaises(ValueError):
            validate_queries(['vestido modelo ' + str(i) for i in range(10)])

    def test_query_plan_rejects_colour_and_size(self):
        with self.assertRaises(ValueError):
            validate_queries(['vestido largo', 'vestido negro', 'vestido fiesta'])

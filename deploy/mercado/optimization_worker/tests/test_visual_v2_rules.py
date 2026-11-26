import unittest

from optimization_worker.visual_v2_rules import compare_primary, classify_visual, validate_source_profile


class VisualV2RulesTest(unittest.TestCase):
    def setUp(self):
        self.source = {'category': 'skirt_set', 'components': 'top_skirt',
                       'length': 'long', 'shoulders': 'regular', 'structure': 'two_piece',
                       'silhouette': 'a_line', 'fit': 'fitted', 'sleeve_length': 'sleeveless'}

    def test_primary_allows_one_flexible_match(self):
        candidate = dict(self.source, fit='loose', sleeve_length='short')
        self.assertEqual(compare_primary(self.source, candidate)['outcome'], 'pass')

    def test_primary_rejects_conflicting_required_feature(self):
        self.assertEqual(compare_primary(self.source, dict(self.source, length='short'))['outcome'], 'reject')

    def test_unknown_required_candidate_is_pending(self):
        candidate = dict(self.source, shoulders=None)
        self.assertEqual(compare_primary(self.source, candidate)['outcome'], 'review')

    def test_unknown_source_requirement_is_pending_but_not_applicable_is_excluded(self):
        source = dict(self.source, components=None)
        self.assertEqual(compare_primary(source, self.source)['outcome'], 'review')
        source['components'] = {'status': 'not_applicable', 'value': None}
        self.assertEqual(compare_primary(source, self.source)['outcome'], 'pass')

    def test_unknown_flexible_features_do_not_count_as_match(self):
        candidate = dict(self.source, silhouette='straight', fit=None, sleeve_length=None)
        self.assertEqual(compare_primary(self.source, candidate)['outcome'], 'review')

    def test_no_source_flexible_feature_requires_review(self):
        source = dict(self.source, silhouette=None, fit=None, sleeve_length=None)
        self.assertEqual(compare_primary(source, self.source)['outcome'], 'review')

    def test_distinctive_secondary_match_is_highly_similar(self):
        result = classify_visual(self.source, self.source, {'lace'}, {'lace'}, set(), set(), set(), set())
        self.assertEqual(result, 'highly_similar')

    def test_explicit_style_match_can_yield_competitor(self):
        result = classify_visual(self.source, self.source, {'lace'}, {'buttons'}, {'boho'}, {'boho'}, set(), set())
        self.assertEqual(result, 'competitor')

    def test_generic_style_does_not_qualify(self):
        result = classify_visual(self.source, self.source, {'lace'}, {'buttons'}, {'modern'}, {'modern'}, set(), set())
        self.assertEqual(result, 'reject')

    def test_missing_style_or_occasion_evidence_is_review(self):
        result = classify_visual(self.source, self.source, {'lace'}, {'buttons'}, {'boho'}, None, set(), set())
        self.assertEqual(result, 'review')

    def test_obvious_conflict_outweighs_an_unknown_required_field(self):
        candidate = dict(self.source, length='short', shoulders=None)
        self.assertEqual(compare_primary(self.source, candidate)['outcome'], 'reject')

    def test_structured_observations_handle_unknown_and_not_applicable(self):
        source = {key: {'status': 'known', 'value': value} for key, value in self.source.items()}
        source['components'] = {'status': 'not_applicable', 'value': None}
        candidate = {key: {'status': 'known', 'value': value} for key, value in self.source.items()}
        candidate['shoulders'] = {'status': 'unknown', 'value': None}
        self.assertEqual(compare_primary(source, candidate)['outcome'], 'review')

    def test_unknown_secondary_evidence_is_review(self):
        result = classify_visual(self.source, self.source, None, set(), set(), set(), set(), set())
        self.assertEqual(result, 'review')

    def test_unknown_secondary_can_still_match_an_explicit_style(self):
        result = classify_visual(self.source, self.source, {'lace'}, None, {'boho'}, {'boho'}, set(), set())
        self.assertEqual(result, 'competitor')

    def test_no_secondary_conflict_needs_matching_feature_for_high_similarity(self):
        result = classify_visual(self.source, self.source, {'lace'}, {'lace'}, {'boho'}, {'boho'}, set(), set(), critical_conflict=True)
        self.assertEqual(result, 'competitor')

    def test_source_profile_requires_category_and_a_confirmed_flexible_feature(self):
        self.assertTrue(validate_source_profile(self.source, 'skirt_set'))
        with self.assertRaises(ValueError):
            validate_source_profile(dict(self.source, category=None), 'skirt_set')
        with self.assertRaises(ValueError):
            validate_source_profile(dict(self.source, silhouette=None, fit=None, sleeve_length=None), 'skirt_set')
        with self.assertRaises(ValueError):
            validate_source_profile(self.source, 'dress')

    def test_components_support_unordered_unique_garment_list(self):
        source = dict(self.source, components=['top', 'skirt'])
        candidate = dict(self.source, components=['skirt', 'top'])
        self.assertTrue(validate_source_profile(source, 'skirt_set'))
        self.assertEqual(compare_primary(source, candidate)['outcome'], 'pass')

    def test_invalid_candidate_values_are_not_treated_as_visual_evidence(self):
        with self.assertRaises(ValueError):compare_primary(self.source,dict(self.source,fit=True))

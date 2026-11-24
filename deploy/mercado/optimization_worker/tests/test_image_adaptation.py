import unittest

from optimization_worker.image_adaptation import assess_adaptation


class AdaptationTest(unittest.TestCase):
    def setUp(self):
        self.regions = ['button close-up', 'back view']
        self.sources = {'1': 'a' * 64, '2': 'b' * 64}
        self.report = {'regions': [
            {'index': 0, 'decision': 'replace', 'source_ids': ['1'], 'detail': 'Use the evidenced cuff'},
            {'index': 1, 'decision': 'unsupported', 'source_ids': [], 'detail': 'No back view'},
        ]}

    def test_unsupported_region_skips_with_precise_reason(self):
        result = assess_adaptation(self.regions, self.sources, self.report)
        self.assertEqual(result['decision'], 'skip')
        self.assertEqual(result['unsupported'][0]['index'], 1)

    def test_target_detail_replaced_from_evidenced_reference(self):
        self.report['regions'][1] = {'index': 1, 'decision': 'direct', 'source_ids': ['2'], 'detail': 'Back construction visible'}
        self.assertEqual(assess_adaptation(self.regions, self.sources, self.report)['decision'], 'generate')

    def test_missing_or_unbound_evidence_is_not_skip(self):
        for bad in ([], ['unknown']):
            report = {'regions': [dict(self.report['regions'][0], source_ids=bad), self.report['regions'][1]]}
            with self.assertRaises(ValueError):
                assess_adaptation(self.regions, self.sources, report)

    def test_region_coverage_must_be_complete_and_unique(self):
        for rows in (self.report['regions'][:1], [self.report['regions'][0]] * 2):
            with self.assertRaises(ValueError):
                assess_adaptation(self.regions, self.sources, {'regions': rows})

    def test_product_text_requires_evidence_and_qc_uses_replacement(self):
        from optimization_worker.image_adaptation import adapt_text
        original = ['SALE!', 'One shoulder top']
        rows = [
            {'index':0, 'decision':'preserve', 'source_ids':[], 'replacement':'SALE!'},
            {'index':1, 'decision':'replace', 'source_ids':['1'], 'replacement':'Long sleeve top'},
        ]
        self.assertEqual(adapt_text(original, self.sources, rows), ['SALE!', 'Long sleeve top'])
        rows[1]['source_ids'] = []
        with self.assertRaises(ValueError):
            adapt_text(original, self.sources, rows)

    def test_unverifiable_product_text_triggers_skip(self):
        from optimization_worker.image_adaptation import adapt_text
        with self.assertRaisesRegex(ValueError, 'unsupported product text'):
            adapt_text(['One shoulder top'], self.sources, [
                {'index':0, 'decision':'unsupported', 'source_ids':[], 'replacement':''}])

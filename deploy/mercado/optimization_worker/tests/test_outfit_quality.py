import json
import unittest

from optimization_worker.outfit_quality import evaluate, select_comparison, validate_binding, QualityError


def report(alias='candidate_1'):
    return {'schema_version': 1, 'candidate': alias, 'scores': {'garment_construction': 90, 'color_material': 88, 'garment_details': 88, 'target_preservation': 90, 'text_layout': None}, 'critical_defects': [], 'primary_defect': None, 'evidence': ['visible seam'], 'confidence': .85, 'decision': 'accept', 'exact_text': None, 'added_text': None, 'missing_text': None, 'instances_exact': None, 'panel_count_exact': None, 'panel_layout_exact': None}


class OutfitQualityTest(unittest.TestCase):
    def test_gates_and_binding(self):
        x = report()
        self.assertEqual(evaluate(x, 'candidate_1', 'a'*64, 'a'*64, 1), 'early_qc_pass')
        x['critical_defects'] = ['wrong_collar']
        self.assertEqual(evaluate(x, 'candidate_1', 'a'*64, 'a'*64, 1), 'reject')
        x['confidence'] = .5
        self.assertEqual(evaluate(x, 'candidate_1', 'a'*64, 'a'*64, 1), 'review')
        with self.assertRaises(QualityError): evaluate(x, 'candidate_1', 'a'*64, 'b'*64, 1)
        with self.assertRaises(QualityError): evaluate(report(), 'candidate_1', 'a'*64, 'a'*64, 4)

    def test_comparison_is_local_and_not_a_pass(self):
        a, b = report('candidate_1'), report('candidate_3')
        b['scores']['garment_construction'] = 95
        self.assertEqual(select_comparison([a, b], {'candidate_1': 1, 'candidate_3': 3}), ('candidate_3', 'best_of_budget'))
        with self.assertRaises(QualityError): select_comparison([a, a], {'candidate_1': 1})

    def test_all_input_hashes_are_immutable(self):
        binding = {'request_id':'r1', 'input_digest':'a'*64, 'plan_digest':'b'*64, 'candidate_sha256':'c'*64}
        validate_binding(binding, dict(binding))
        with self.assertRaises(QualityError): validate_binding(binding, dict(binding, plan_digest='d'*64))
        with self.assertRaises(QualityError): validate_binding(binding, dict(binding, input_digest='not-a-hash'))
        x = report(); x['evidence'] = []
        with self.assertRaises(QualityError): evaluate(x, 'candidate_1', 'a'*64, 'a'*64, 1)

    def test_score_boundaries_and_infographic_exactness(self):
        for key in ('garment_construction', 'color_material', 'garment_details', 'target_preservation'):
            x = report(); x['scores'][key] -= 1
            self.assertEqual(evaluate(x, 'candidate_1', 'a'*64, 'a'*64, 1), 'reject')
        x = report()
        x['scores']['text_layout'] = 95
        x.update(exact_text=True, added_text=[], missing_text=[], instances_exact=True, panel_count_exact=True, panel_layout_exact=True)
        self.assertEqual(evaluate(x, 'candidate_1', 'a'*64, 'a'*64, 1, infographic=True), 'early_qc_pass')
        x['added_text'] = ['invented text']
        self.assertEqual(evaluate(x, 'candidate_1', 'a'*64, 'a'*64, 1, infographic=True), 'reject')

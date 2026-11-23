import json
import unittest
from optimization_worker import outfit_attempts as a
from test_outfit_quality import report

class AttemptsTest(unittest.TestCase):
    def base(self):
        return a.bind({'request_id':'req'}, 'req', 'a'*64, 'b'*64)

    def test_crash_recovered_unknown_does_not_restart(self):
        record, n = a.start(self.base())
        record = a.mark_unknown(record, n)
        recovered = json.loads(json.dumps(record))
        with self.assertRaises(ValueError): a.start(recovered)
        recovered = a.record_candidate(recovered, 1, 'c'*64)
        with self.assertRaises(ValueError): a.start(recovered)

    def test_budget_and_selection(self):
        record = self.base()
        for n in range(1, 4):
            record, actual = a.start(record); self.assertEqual(actual, n)
            digest = str(n)*64; record = a.record_candidate(record, n, digest)
            qc = report(f'candidate_{n}'); qc.update(decision='reject', critical_defects=['wrong_collar'])
            record, decision = a.review(record, n, qc, digest)
            self.assertEqual(decision, 'comparison_pending' if n == 3 else 'reject')
        with self.assertRaises(ValueError): a.start(record)
        reports = [v['report'] for v in record['quality_cycle']['attempts']]
        digests = {v['alias']: v['digest'] for v in record['quality_cycle']['attempts']}
        with self.assertRaises(ValueError): a.compare(record, reports, dict(digests, candidate_1='a'*64))
        selected = a.compare(record, reports, digests)['quality_cycle']['selection']
        self.assertEqual(selected['reason'], 'best_of_budget'); self.assertFalse(selected['quality_passed'])

    def test_malicious_binding_and_digests_are_rejected(self):
        record, n = a.start(self.base())
        with self.assertRaises(ValueError): a.bind(record, 'other', 'a'*64, 'b'*64)
        with self.assertRaises(ValueError): a.bind(record, 'req', 'c'*64, 'b'*64)
        record = a.record_candidate(record, n, 'c'*64)
        with self.assertRaises(ValueError): a.review(record, n, report(), 'd'*64)
        with self.assertRaises(ValueError): a.start(record)

    def test_uncertain_report_stops_without_spending_next_attempt(self):
        record, n = a.start(self.base()); record = a.record_candidate(record, n, 'c'*64)
        qc = report(); qc['confidence'] = 0.2; qc['decision'] = 'retry'
        record, decision = a.review(record, n, qc, 'c'*64)
        self.assertEqual(decision, 'review')
        with self.assertRaises(ValueError): a.start(record)
        self.assertEqual(len(record['quality_cycle']['attempts']), 1)

    def test_infographic_gates_and_comparison(self):
        def infographic(n):
            x = report(f'candidate_{n}'); x['scores']['text_layout'] = 95
            x.update(exact_text=True, added_text=[], missing_text=[], instances_exact=True, panel_count_exact=True, panel_layout_exact=True)
            return x
        record, n = a.start(self.base()); record = a.record_candidate(record, n, '1'*64)
        x = infographic(1); x['missing_text'] = ['original']; x['decision'] = 'reject'
        record, decision = a.review(record, 1, x, '1'*64, infographic=True)
        self.assertEqual(decision, 'reject')
        record, n = a.start(record); record = a.record_candidate(record, n, '2'*64)
        x = infographic(2); x['instances_exact'] = False; x['decision'] = 'reject'
        record, decision = a.review(record, 2, x, '2'*64, infographic=True)
        self.assertEqual(decision, 'reject')
        record, n = a.start(record); record = a.record_candidate(record, n, '3'*64)
        x = infographic(3); x['panel_layout_exact'] = False; x['decision'] = 'reject'
        record, decision = a.review(record, 3, x, '3'*64, infographic=True)
        self.assertEqual(decision, 'comparison_pending')
        attempts = record['quality_cycle']['attempts']
        result = a.compare(record, [v['report'] for v in attempts], {v['alias']: v['digest'] for v in attempts}, infographic=True)
        self.assertFalse(result['quality_cycle']['selection']['quality_passed'])

import json
import unittest

from optimization_worker import outfit_attempts as attempts
from optimization_worker import outfit_retry as retry


class ServiceRetryTest(unittest.TestCase):
    def dispatched(self):
        r = attempts.bind({'request_id': 'req'}, 'req', 'a' * 64, 'b' * 64)
        r, number = attempts.start(r)
        return retry.bind(r, number, 100.0)

    def test_explicit_retry_is_atomic_replay_safe_and_keeps_quality_attempt(self):
        r = self.dispatched()
        r = retry.report(r, 'retryable', 'HTTP 503', 101.0)
        with self.assertRaises(ValueError):
            retry.claim(r, 101.0, 105.0)
        r, invoke = retry.claim(r, 101.0, 106.0)
        self.assertTrue(invoke)
        r = json.loads(json.dumps(r))
        r, invoke = retry.claim(r, 101.0, 107.0)
        self.assertFalse(invoke)
        self.assertEqual(len(r['quality_cycle']['attempts']), 1)
        self.assertEqual(r['generation_retry']['first_dispatched_at'], 100.0)
        self.assertEqual(r['generation_retry']['request_id'], 'req')
        self.assertEqual(r['quality_cycle']['attempts'][0]['number'], 1)

    def test_unknown_and_running_are_never_automatically_retried(self):
        r = self.dispatched()
        with self.assertRaises(ValueError): retry.claim(r, 100.0, 1000.0)
        r = retry.report(r, 'unknown', 'connection lost after dispatch', 101.0)
        with self.assertRaises(ValueError): retry.claim(r, 101.0, 1000.0)
        with self.assertRaises(ValueError): retry.report(r, 'retryable', 'guess', 102.0)

    def test_backoff_server_delay_and_stale_receipts(self):
        r = self.dispatched()
        for i, delay in enumerate((5, 10, 20, 60, 60)):
            now = 1000.0 + 100 * i
            r = retry.report(r, 'retryable', 'HTTP 503', now, retry_after=70 if i == 1 else 0)
            required = 70 if i == 1 else delay
            with self.assertRaises(ValueError): retry.claim(r, now, now + required - 0.1)
            with self.assertRaises(ValueError): retry.claim(r, now - 1, now + required)
            r, invoke = retry.claim(r, now, now + required)
            self.assertTrue(invoke)
        self.assertEqual(len(r['generation_retry']['receipts']), 5)

    def test_permanent_or_success_is_terminal_and_immutable_binding_is_checked(self):
        r = retry.report(self.dispatched(), 'moderation_blocked', 'blocked', 101.0)
        with self.assertRaises(ValueError): retry.claim(r, 101.0, 1000.0)
        with self.assertRaises(ValueError): retry.report(r, 'retryable', '503', 102.0)
        r = self.dispatched()
        r = retry.settle(r)
        with self.assertRaises(ValueError): retry.report(r, 'retryable', '503', 101.0)
        r = self.dispatched(); r['quality_cycle']['input_digest'] = 'c' * 64
        with self.assertRaises(ValueError): retry.report(r, 'retryable', '503', 101.0)

    def test_invalid_times_or_unknown_outcomes_do_not_open_retry(self):
        r = self.dispatched()
        for timestamp in (float('nan'), float('inf'), 99.0):
            with self.assertRaises(ValueError): retry.report(r, 'retryable', '503', timestamp)
        with self.assertRaises(ValueError): retry.report(r, 'retryable', '', 101.0)
        with self.assertRaises(ValueError): retry.report(r, 'retryable', '503', 101.0, retry_after=-1)
        with self.assertRaises(ValueError): retry.report(r, 'maybe', '503', 101.0)

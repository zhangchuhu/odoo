import unittest
from unittest.mock import patch
from optimization_worker.research_service import UnknownRetryPoll
from optimization_worker.client import Client
from optimization_worker.keyword_worker_service import claim_loop


class UnknownRetryPollTests(unittest.TestCase):
    def test_success_activates_and_limits_poll_frequency(self):
        now = [10]
        calls = []
        poll = UnknownRetryPoll(lambda: calls.append(1) or {'retried': []}, clock=lambda: now[0], interval=60)
        self.assertTrue(poll.tick())
        self.assertTrue(poll.ready)
        now[0] = 69
        poll.tick()
        self.assertEqual(len(calls), 1)
        now[0] = 70
        poll.tick()
        self.assertEqual(len(calls), 2)

    def test_failure_does_not_activate_retry_and_retries_after_backoff(self):
        now = [0]
        calls = []
        def request():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('unavailable')
            return {'retried': []}
        poll = UnknownRetryPoll(request, clock=lambda: now[0], interval=60)
        self.assertFalse(poll.tick())
        self.assertFalse(poll.ready)
        now[0] = 59
        poll.tick()
        self.assertEqual(len(calls), 1)
        now[0] = 60
        self.assertTrue(poll.tick())
        self.assertTrue(poll.ready)

    def test_bad_response_is_not_accepted(self):
        poll = UnknownRetryPoll(lambda: {'retried': 'invalid'})
        self.assertFalse(poll.tick())
        self.assertFalse(poll.ready)

    def test_client_calls_scoped_batch_route(self):
        client = Client('http://127.0.0.1:8069', 'test')
        calls = []
        client.request = lambda method, path, body: calls.append((method, path, body)) or {'retried': []}
        self.assertEqual(client.retry_unknown_research_batch(), {'retried': []})
        self.assertEqual(calls, [('POST', '/internal/meli-optimization/research-retry-unknown', {'limit': 50})])

    def test_client_calls_generic_batch_route_for_requested_capabilities(self):
        client = Client('http://127.0.0.1:8069', 'test')
        calls = []
        client.request = lambda method, path, body: calls.append((method, path, body)) or {'retried': []}
        client.retry_unknown_tasks(('product.facts',))
        self.assertEqual(calls, [('POST', '/internal/meli-optimization/retry-unknown-tasks',
                                  {'limit': 50, 'task_types': ['product.facts']})])
        with self.assertRaises(ValueError):
            client.retry_unknown_tasks(('research.bestseller',))

    def test_claim_loop_polls_retry_before_claim(self):
        calls = []
        class Stop:
            def __init__(self): self.stopped = False
            def is_set(self): return self.stopped
            def wait(self, seconds): self.stopped = True
        class Worker:
            def once(self, allow_claim): calls.append(('claim', allow_claim))
        class Retry:
            def tick(self): calls.append(('retry',))
        with patch('optimization_worker.keyword_worker_service.codex_ready', return_value=True):
            claim_loop(Worker(), Stop(), Retry())
        self.assertEqual(calls, [('retry',), ('claim', True)])

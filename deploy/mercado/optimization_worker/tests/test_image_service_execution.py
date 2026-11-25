import threading
import unittest
from unittest.mock import Mock
from optimization_worker.image_service import build_worker, claim_loop

class ImageServiceExecutionTests(unittest.TestCase):
    def test_claim_failure_reports_http_status_without_exception_text(self):
        from optimization_worker.client import APIError
        from optimization_worker.image_service import image_error_event
        event = image_error_event('claim', APIError(503), task_id=23, epoch=2)
        self.assertEqual(event['stage'], 'claim')
        self.assertEqual(event['code'], 'odoo_request_failed')
        self.assertEqual(event['http_status'], 503)
        self.assertEqual(event['task_id'], 23)
        self.assertNotIn('secret', str(image_error_event('claim', ValueError('secret'))))
    def test_worker_only_claims_image_generation(self):
        client = Mock(); client.identity = 'test'
        worker = build_worker(client, ':memory:', ':memory:')
        self.assertEqual(worker.capabilities, ('image.generate',))

    def test_claim_loop_checks_probe_before_claiming_and_polls_retries(self):
        stop = Mock(); stop.is_set.side_effect = [False, True]
        worker = Mock(); poll = Mock()
        claim_loop(worker, stop, poll, ready=lambda: False)
        poll.tick.assert_called_once()
        worker.once.assert_called_once_with(allow_claim=False)

    def test_existing_leases_are_processed_during_slow_capability_probe(self):
        from optimization_worker.image_service import start_claimant
        stop = threading.Event()
        processed = threading.Event()
        worker = Mock()
        worker.once.side_effect = lambda **kwargs: processed.set()
        def probe():
            self.assertTrue(processed.wait(2))
            self.assertEqual(worker.once.call_args.kwargs, {'allow_claim': False})
            return True
        try:
            ready, thread = start_claimant(worker, stop, None, probe)
            self.assertTrue(ready.is_set())
        finally:
            stop.set()
            thread.join(2)

class ImageProbeTests(unittest.TestCase):
    def test_probe_requires_new_decodable_image_and_successful_process(self):
        import tempfile
        from pathlib import Path
        from PIL import Image
        from optimization_worker.image_service import verify_image_tool
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            def successful(cmd, **kwargs):
                out = home / 'generated_images' / 'session' / 'result.png'
                out.parent.mkdir(parents=True)
                Image.new('RGB', (2, 2), 'blue').save(out)
                return Mock(returncode=0, stdout='{\"type\":\"thread.started\",\"thread_id\":\"session\"}\n')
            self.assertTrue(verify_image_tool(home, runner=successful))
            self.assertFalse(verify_image_tool(home, runner=lambda *a, **kw: Mock(returncode=0, stdout='{}')))

    def test_probe_rejects_images_created_by_other_sessions(self):
        import tempfile
        from pathlib import Path
        from PIL import Image
        from optimization_worker.image_service import verify_image_tool
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            def other_process(cmd, **kwargs):
                out = home / 'generated_images' / 'other' / 'result.png'
                out.parent.mkdir(parents=True)
                Image.new('RGB', (2, 2)).save(out)
                return Mock(returncode=0, stdout='{"type":"thread.started","thread_id":"own"}\n')
            self.assertFalse(verify_image_tool(home, runner=other_process))

class ImageHostBackoffTests(unittest.TestCase):
    def test_no_progress_defers_next_invocation_and_success_resets(self):
        from optimization_worker.image_service import HostBackoff
        now = [100]
        gate = HostBackoff(clock=lambda: now[0], delay=60)
        self.assertTrue(gate.ready())
        gate.record('no_progress')
        self.assertFalse(gate.ready())
        now[0] += 60
        self.assertTrue(gate.ready())
        gate.record('progress')
        self.assertTrue(gate.ready())

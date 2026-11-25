import unittest
from optimization_worker.client import APIError
from optimization_worker.runtime import _Heartbeat, LeaseLost


class HeartbeatDiagnosticsTest(unittest.TestCase):
    def test_transient_failure_preserves_retriable_error(self):
        class Client:
            def heartbeat(self, claim):
                raise APIError(503)
        pulse = _Heartbeat(Client(), {'heartbeat_seconds': .01})
        pulse.thread.start()
        pulse.thread.join(timeout=1)
        with self.assertRaises(APIError) as error:
            pulse.check()
        self.assertEqual(error.exception.status, 503)

    def test_rejected_heartbeat_includes_status(self):
        class Client:
            def heartbeat(self, claim):
                raise APIError(409)
        pulse = _Heartbeat(Client(), {'heartbeat_seconds': .01})
        pulse.thread.start()
        pulse.thread.join(timeout=1)
        with self.assertRaises(LeaseLost) as error:
            pulse.check()
        self.assertEqual(error.exception.status, 409)

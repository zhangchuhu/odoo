import unittest
from unittest.mock import Mock

from optimization_worker.image_service import claim_if_ready, plist_bytes, production_probe
import plistlib


class ImageServiceGateTests(unittest.TestCase):
    def test_production_never_claims_without_native_adapter(self):
        worker = Mock()
        self.assertEqual(claim_if_ready(worker, production_probe), 'image_generation_unavailable')
        worker.once.assert_not_called()

    def test_launch_agent_is_keepalive_and_autostart(self):
        config = plistlib.loads(plist_bytes('/repo', '/private'))
        self.assertTrue(config['RunAtLoad'])
        self.assertTrue(config['KeepAlive'])
        self.assertIn('optimization_worker.image_service', config['ProgramArguments'])
    def test_no_claim_without_real_image_adapter(self):
        worker = Mock()
        self.assertEqual(claim_if_ready(worker, None), 'image_generation_unavailable')
        worker.once.assert_not_called()

    def test_claim_only_after_adapter_proves_ready(self):
        worker = Mock()
        worker.once.return_value = 'idle'
        self.assertEqual(claim_if_ready(worker, lambda: True), 'idle')
        worker.once.assert_called_once()

    def test_adapter_error_fails_closed(self):
        worker = Mock()
        def unavailable():
            raise RuntimeError('unavailable')
        self.assertEqual(claim_if_ready(worker, unavailable), 'image_generation_unavailable')
        worker.once.assert_not_called()

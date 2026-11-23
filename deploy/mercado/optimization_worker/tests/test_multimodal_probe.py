import subprocess
import unittest
from pathlib import Path
from optimization_worker.multimodal_probe import probe_multimodal, multimodal_capability_adapter

class MultimodalProbeTest(unittest.TestCase):
    def test_reads_generated_image_and_accepts_exact_color_sequence(self):
        def runner(args, **kwargs):
            from PIL import Image
            palette = {(255,0,0):'RED',(0,255,0):'GREEN',(0,0,255):'BLUE',(255,255,0):'YELLOW'}
            with Image.open(args[args.index('--image')+1]) as im:
                text = ' '.join(palette[im.getpixel((40*i+20, 20))] for i in range(6))
            Path(args[args.index('--output-last-message')+1]).write_text(text)
            return subprocess.CompletedProcess(args, 0)
        self.assertTrue(probe_multimodal('/tmp', 'http://127.0.0.1:7897', runner=runner))

    def test_incorrect_output_failure_and_timeout_are_false(self):
        def wrong(args, **kwargs):
            Path(args[args.index('--output-last-message')+1]).write_text('not a color sequence')
            return subprocess.CompletedProcess(args, 0)
        def timeout(*args, **kwargs): raise subprocess.TimeoutExpired('codex', 1)
        for runner in (wrong, timeout, lambda args, **kwargs: subprocess.CompletedProcess(args, 1)):
            self.assertFalse(probe_multimodal('/tmp', 'http://127.0.0.1:7897', runner=runner))

    def test_untrusted_proxy_rejected(self):
        self.assertFalse(probe_multimodal('/tmp', 'http://example.com:8000'))

    def test_capability_adapter_binds_fresh_challenge_and_failure(self):
        probe=multimodal_capability_adapter('/tmp','http://127.0.0.1:7897',run_probe=lambda *a, **k: True, now=lambda:100.0)
        self.assertEqual(probe('fresh'),{'nonce':'fresh','observed_at':100.0,'ok':True})
        probe=multimodal_capability_adapter('/tmp','http://127.0.0.1:7897',run_probe=lambda *a, **k: False, now=lambda:100.0)
        self.assertFalse(probe('fresh')['ok'])

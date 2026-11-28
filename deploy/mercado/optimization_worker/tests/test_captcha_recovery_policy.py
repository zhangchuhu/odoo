import unittest

from optimization_worker.captcha_recovery_policy import next_action


class CaptchaRecoveryPolicyTests(unittest.TestCase):
    def test_wait_before_interactive_attempt(self):
        self.assertEqual(next_action('loading',0,False),{'action':'wait'})
        self.assertEqual(next_action('interactive',0,True),{'action':'attempt','attempt':1})

    def test_attempts_are_bounded_and_manual_code_pauses(self):
        self.assertEqual(next_action('interactive',2,True),{'action':'pause','reason':'captcha_attempts_exhausted'})
        self.assertEqual(next_action('manual_code',0,True),{'action':'pause','reason':'user_verification_required'})
        self.assertEqual(next_action('interactive',0,False),{'action':'pause','reason':'browser_capability_unavailable'})

    def test_success_requires_actual_readable_page(self):
        self.assertEqual(next_action('passed',1,True),{'action':'verify_page'})
        with self.assertRaises(ValueError):next_action('guessed_success',1,True)
        with self.assertRaises(ValueError):next_action('interactive',True,True)

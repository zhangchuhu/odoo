import unittest

from optimization_worker.home_retry_policy import next_retry


class HomeRetryTests(unittest.TestCase):
    def test_reconnect_backoff_is_bounded(self):
        self.assertEqual([next_retry('tab_closed',n) for n in range(4)],[
            {'action':'retry','delay_seconds':2}, {'action':'retry','delay_seconds':5},
            {'action':'retry','delay_seconds':10}, {'action':'pause','reason':'browser_unavailable'}])

    def test_platform_verification_never_retries_as_connection_failure(self):
        self.assertEqual(next_retry('captcha',0),{'action':'captcha'})
        self.assertEqual(next_retry('login',1),{'action':'pause','reason':'login_required'})
        with self.assertRaises(ValueError):next_retry('untrusted',0)
        with self.assertRaises(ValueError):next_retry('tab_closed',True)

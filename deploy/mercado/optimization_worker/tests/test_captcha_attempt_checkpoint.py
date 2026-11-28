import sqlite3
import unittest

from optimization_worker.captcha_attempt_checkpoint import initialize, reserve_attempt, attempts


class CaptchaAttemptCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');initialize(self.db);self.addCleanup(self.db.close)
        self.task=(4,7,9);self.challenge='a'*64

    def test_attempt_reservation_is_idempotent_and_survives_lease_change(self):
        self.assertEqual(reserve_attempt(self.db,self.task,self.challenge,'action-a',fence=lambda:True),1)
        self.assertEqual(reserve_attempt(self.db,self.task,self.challenge,'action-a',fence=lambda:True),1)
        self.assertEqual(reserve_attempt(self.db,self.task,self.challenge,'action-b',fence=lambda:True),2)
        with self.assertRaises(ValueError):reserve_attempt(self.db,self.task,self.challenge,'action-c',fence=lambda:True)
        self.assertEqual(attempts(self.db,self.task,self.challenge),2)

    def test_attempts_are_isolated_and_fenced(self):
        with self.assertRaises(ValueError):reserve_attempt(self.db,self.task,self.challenge,'action-a',fence=lambda:False)
        self.assertEqual(attempts(self.db,self.task,self.challenge),0)
        self.assertEqual(reserve_attempt(self.db,self.task,'b'*64,'action-a',fence=lambda:True),1)
        self.assertEqual(attempts(self.db,(4,8,9),'b'*64),0)
        with self.assertRaises(ValueError):reserve_attempt(self.db,self.task,'123456','action-a',fence=lambda:True)

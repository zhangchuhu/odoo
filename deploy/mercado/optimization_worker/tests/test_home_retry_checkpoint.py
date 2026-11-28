import sqlite3
import unittest


class HomeRetryCheckpointTests(unittest.TestCase):
    def test_retries_survive_reopen_and_reset_only_after_progress(self):
        from optimization_worker.home_retry_checkpoint import initialize,reserve_retry,clear_retry
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);initialize(db);scope=('request','digest',1)
        self.assertEqual(reserve_retry(db,scope,1,fence=lambda:True),2)
        self.assertEqual(reserve_retry(db,scope,1,fence=lambda:True),5)
        self.assertEqual(reserve_retry(db,scope,1,fence=lambda:True),10)
        with self.assertRaises(ValueError):reserve_retry(db,scope,1,fence=lambda:True)
        with self.assertRaises(ValueError):clear_retry(db,scope,1,fence=lambda:False)
        clear_retry(db,scope,1,fence=lambda:True)
        self.assertEqual(reserve_retry(db,scope,1,fence=lambda:True),2)

    def test_other_scope_cannot_clear_retry(self):
        from optimization_worker.home_retry_checkpoint import initialize,reserve_retry,clear_retry
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);initialize(db);scope=('request','digest',1)
        reserve_retry(db,scope,0,fence=lambda:True)
        clear_retry(db,('other','digest',1),0,fence=lambda:True)
        self.assertEqual(reserve_retry(db,scope,0,fence=lambda:True),5)

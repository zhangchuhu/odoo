import unittest
from optimization_worker.task_tab_challenge import TaskTabChallenge

class TaskTabChallengeTest(unittest.TestCase):
    def test_one_time_bound_identity_and_fencing(self):
        fence = [True]
        gate = TaskTabChallenge(lambda: fence[0], clock=lambda: 10)
        issued = gate.issue('request', 7, 'a'*32, 44)
        self.assertFalse(gate.confirm(issued, 'other', 7, 'a'*32, 44, 99))
        self.assertFalse(gate.confirm(issued, 'request', 7, 'b'*32, 44, 99))
        self.assertFalse(gate.confirm(issued, 'request', 8, 'a'*32, 44, 99))
        fence[0] = False
        self.assertFalse(gate.confirm(issued, 'request', 7, 'a'*32, 44, 99))
        fence[0] = True
        self.assertTrue(gate.confirm(issued, 'request', 7, 'a'*32, 44, 99))
        self.assertFalse(gate.confirm(issued, 'request', 7, 'a'*32, 44, 99))
    def test_expiry_and_reissue_revoke_previous(self):
        now = [0]
        gate = TaskTabChallenge(lambda: True, clock=lambda: now[0], ttl=120)
        old = gate.issue('r', 1, 'a'*32, 1)
        new = gate.issue('r', 1, 'a'*32, 1)
        self.assertFalse(gate.confirm(old, 'r', 1, 'a'*32, 1, 2))
        now[0]=121
        self.assertFalse(gate.confirm(new, 'r', 1, 'a'*32, 1, 2))

    def test_single_creation_offer_expires_without_revoking_inflight_confirmation(self):
        now = [0]
        gate = TaskTabChallenge(lambda:True, clock=lambda:now[0], ttl=5)
        args = ('r',1,'a'*32,44)
        old = gate.issue(*args,keep_pending=True)
        now[0] = 5
        self.assertIsNone(gate.issue(*args,keep_pending=True))
        now[0] = 6
        new = gate.issue(*args,keep_pending=True)
        self.assertFalse(gate.confirm(old,*args,99))
        self.assertTrue(gate.confirm(new,*args,99))

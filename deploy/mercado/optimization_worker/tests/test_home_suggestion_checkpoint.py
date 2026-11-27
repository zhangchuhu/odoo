import sqlite3
import unittest

from optimization_worker.home_suggestion_checkpoint import initialize, save_observation, observations, save_read, reads


class SuggestionCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');initialize(self.db)
        self.scope=('request-a','plan-digest',1)
        self.row={'prefix':'conjunto mujer','text':'conjunto mujer falda blusa','position':1,'observed_at':'2026-11-27T00:00:00Z','evidence_sha256':'a'*64}

    def tearDown(self):self.db.close()

    def test_fenced_idempotent_and_scoped(self):
        save_observation(self.db,self.scope,self.row,fence=lambda:True,verify_evidence=lambda _:True)
        save_observation(self.db,self.scope,self.row,fence=lambda:True,verify_evidence=lambda _:True)
        self.assertEqual(observations(self.db,self.scope),[self.row])
        self.assertEqual(observations(self.db,('request-b','plan-digest',1)),[])
        with self.assertRaises(ValueError):save_observation(self.db,self.scope,self.row,fence=lambda:False,verify_evidence=lambda _:True)
        with self.assertRaises(ValueError):save_observation(self.db,self.scope,self.row,fence=lambda:True,verify_evidence=lambda _:False)

    def test_invalid_or_conflicting_observation_is_rejected(self):
        save_observation(self.db,self.scope,self.row,fence=lambda:True,verify_evidence=lambda _:True)
        with self.assertRaises(ValueError):save_observation(self.db,self.scope,dict(self.row,text='other'),fence=lambda:True,verify_evidence=lambda _:True)
        with self.assertRaises(ValueError):save_observation(self.db,self.scope,dict(self.row,position=True),fence=lambda:True,verify_evidence=lambda _:True)

    def test_unavailable_read_is_not_a_verified_empty_suggestion_list(self):
        read={'prefix':'conjunto mujer','state':'unavailable','observed_at':'2026-11-27T00:00:00Z','evidence_sha256':'a'*64}
        save_read(self.db,self.scope,read,fence=lambda:True,verify_evidence=lambda _:True)
        self.assertEqual(reads(self.db,self.scope),[read])
        with self.assertRaises(ValueError):save_read(self.db,self.scope,dict(read,state='empty'),fence=lambda:True,verify_evidence=lambda _:True)

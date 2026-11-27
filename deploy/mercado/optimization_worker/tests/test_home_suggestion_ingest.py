import sqlite3
import unittest

from optimization_worker.home_suggestion_checkpoint import initialize,reads,observations
from optimization_worker.home_suggestion_ingest import ingest


class SuggestionIngestTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');initialize(self.db);self.addCleanup(self.db.close)
        self.scope=('request','digest',1)
        self.body={'page_url':'https://www.mercadolibre.com.mx/','prefix':'conjunto mujer',
                   'visible_input':'conjunto mujer','state':'observed',
                   'suggestions':[{'text':'Conjunto mujer falda blusa','position':1}]}

    def test_atomic_host_observation_is_recorded_with_evidence(self):
        ingest(self.db,self.scope,self.body,'a'*64,'2026-11-27T00:00:00Z',fence=lambda:True,verify=lambda _:True)
        self.assertEqual(reads(self.db,self.scope)[0]['state'],'observed')
        self.assertEqual(observations(self.db,self.scope)[0]['text'],'Conjunto mujer falda blusa')

    def test_mismatch_or_false_evidence_saves_nothing(self):
        for updates in ({'visible_input':'different'},{'page_url':'https://example.org/'},{'state':'unavailable'},{'state':'blocked'}):
            with self.subTest(updates=updates),self.assertRaises(ValueError):
                ingest(self.db,self.scope,dict(self.body,**updates),'a'*64,'2026-11-27T00:00:00Z',fence=lambda:True,verify=lambda _:True)
        with self.assertRaises(ValueError):ingest(self.db,self.scope,self.body,'a'*64,'2026-11-27T00:00:00Z',fence=lambda:True,verify=lambda _:False)
        self.assertEqual(reads(self.db,self.scope),[])

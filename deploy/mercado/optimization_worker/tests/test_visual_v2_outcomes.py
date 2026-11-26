import sqlite3
import unittest

from optimization_worker.visual_v2_checkpoints import initialize, append_batch
from optimization_worker.visual_v2_outcomes import save_decision, decisions


class VisualDecisionTest(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close);initialize(self.db)
        self.scope=('request','digest',1)
        append_batch(self.db,(*self.scope,'0'),0,[{'product_id':'MLM1'}],fence=lambda:True)
        self.source={'category':'dress','components':{'status':'not_applicable','value':None},'length':'long','shoulders':'regular','structure':'one_piece',
                     'silhouette':'a_line','fit':'fitted','sleeve_length':'short'}
        self.args=(self.db,self.scope,['0'],'MLM1',self.source,self.source,{'lace'},{'lace'},set(),set(),set(),set())

    def test_decision_is_durable_and_cannot_be_changed(self):
        self.assertEqual(save_decision(*self.args,fence=lambda:True),'highly_similar')
        self.assertEqual(save_decision(*self.args,fence=lambda:True),'highly_similar')
        self.assertEqual(decisions(self.db,self.scope)[0]['identity'],'MLM1')
        changed=list(self.args);changed[7]={'buttons'}
        with self.assertRaises(ValueError):save_decision(*changed,fence=lambda:True)

    def test_unknown_candidate_and_expired_lease_are_rejected(self):
        changed=list(self.args);changed[3]='MLM2'
        with self.assertRaises(ValueError):save_decision(*changed,fence=lambda:True)
        with self.assertRaises(ValueError):save_decision(*self.args,fence=lambda:False)

    def test_structured_source_values_can_be_recorded(self):
        source={k:v if isinstance(v,dict) else {'status':'known','value':v} if v is not None else {'status':'not_applicable','value':None}
                for k,v in self.source.items()}
        args=list(self.args);args[4]=source
        self.assertEqual(save_decision(*args,fence=lambda:True),'highly_similar')

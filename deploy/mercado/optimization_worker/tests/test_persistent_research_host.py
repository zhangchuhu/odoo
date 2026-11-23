import tempfile
import unittest
from pathlib import Path
from optimization_worker.persistent_research_host import ResearchHost

class Process:
    def __init__(self): self.alive=True
    def poll(self): return None if self.alive else 1
    def terminate(self): self.alive=False
    def wait(self, timeout=None): return 0

class HostTest(unittest.TestCase):
    def test_fail_closed_then_start_once_and_do_not_restart_one_dead_child(self):
        with tempfile.TemporaryDirectory() as d:
            created=[]; ready=[False]
            def start(kind):
                p=Process(); created.append((kind,p)); return p
            with ResearchHost(Path(d)/'lock',lambda:ready[0],start) as host:
                self.assertEqual(host.tick(),'waiting')
                self.assertEqual(created,[])
                ready[0]=True
                self.assertEqual(host.tick(),'running')
                self.assertEqual([x[0] for x in created],['dispatcher','worker'])
                self.assertEqual(host.tick(),'running')
                created[1][1].alive=False
                self.assertEqual(host.tick(),'reconciliation_required')
                self.assertEqual(len(created),2)

    def test_probe_loss_does_not_terminate_inflight_children(self):
        with tempfile.TemporaryDirectory() as d:
            children=[]; ready=[True]
            def start(kind):
                p=Process(); children.append(p); return p
            with ResearchHost(Path(d)/'lock',lambda:ready[0],start) as host:
                self.assertEqual(host.tick(),'running')
                ready[0]=False
                self.assertEqual(host.tick(),'reconciliation_required')
                self.assertTrue(all(p.alive for p in children))

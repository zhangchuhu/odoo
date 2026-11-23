import tempfile
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from optimization_worker.domain import canonical_hash
from optimization_worker.journal import Journal
from optimization_worker.research_return_bridge import build_bridge

class BridgeLiveTest(unittest.TestCase):
    def test_existing_signed_plan_starts_real_broker(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
            root=Path(tmp); private=root/'.local/optimization-codex-worker';private.mkdir(parents=True,mode=0o700)
            anchor='a'*32
            config=private/'collector-native.json'
            config.write_text(json.dumps({'extension_ids':[anchor], 'anchor_extension_id':anchor}))
            config.chmod(0o600)
            fake_file=root/'a/b/c/research_return_bridge.py'; fake_file.parent.mkdir(parents=True)
            request='11111111-1111-1111-1111-111111111111'
            claim={'id':1,'task_type':'research.bestseller','snapshot_id':2,'company_id':3,'input_hash':'c'*64,'lease_epoch':4}
            plan={'platform':'mercado-libre-mx','queries':['one','two','three']}
            signed={'request_id':request,'snapshot_id':2,'company_id':3,'input_hash':'c'*64,'plan':plan};signed['digest']=canonical_hash(signed)
            class Store:
                def inspect(self,r): return {'state':'prepared','task_type':'research.bestseller','collector_plan':signed}
            class Client:
                def heartbeat(self,c): pass
            journal=private/'journal';j=Journal(journal);j.save({'claim':claim,'request_id':request,'phase':'host_pending'});j.close()
            with patch('optimization_worker.research_return_bridge.__file__',str(fake_file)):
                bridge=build_bridge(request,Client(),Store(),str(journal))
                try:
                    deadline=time.monotonic()+2
                    while not (private/'collector-native.sock').exists() and time.monotonic()<deadline: time.sleep(.01)
                    self.assertTrue((private/'collector-native.sock').exists(),'Real broker did not start')
                finally: bridge.close()

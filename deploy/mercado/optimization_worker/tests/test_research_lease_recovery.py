import tempfile
import unittest
from pathlib import Path
from optimization_worker.journal import Journal
from optimization_worker.runtime import Worker
from optimization_worker.client import APIError
from optimization_worker.host_session import SessionPending

class LeaseRecoveryTests(unittest.TestCase):
    def test_scoped_expired_lease_continues_same_original_request(self):
        class Client:
            identity='fixture'; timeout=1; research_ledger_enabled=True
            def heartbeat(self,claim):
                if claim['lease_token']!='new': raise APIError(409)
            def continue_registered_research(self,claim,request,nonce):
                self.resumed=(claim['id'],request)
                return {'request_id':request,'claim':dict(claim,lease_token='new')}
        class Provider:
            durable_session=True
            def __init__(self): self.snapshots=[]
            def synchronize(self,records): self.snapshots.append(list(records))
            def run(self,kind,payload,context,request):
                if request!='original': raise AssertionError('wrong request')
                raise SessionPending()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'worker.sqlite';client=Client();provider=Provider()
            claim={'id':60,'company_id':1,'snapshot_id':2,'payload':{},'task_type':'research.bestseller','input_hash':'hash','lease_epoch':1,'lease_token':'old','heartbeat_seconds':30}
            j=Journal(path,client.identity);j.save({'claim':claim,'context':{},'phase':'host_pending','request_id':'original','sequence':0});j.close()
            self.assertEqual(Worker(client,path,providers={'research.bestseller':provider},capabilities=('research.bestseller',)).once(allow_claim=False),'host_pending')
            self.assertEqual(client.resumed,(60,'original'))
            j=Journal(path,client.identity)
            self.assertEqual([r['request_id'] for r in j.active()],['original'])
            j.close()
            self.assertEqual(provider.snapshots[-1][0]['claim']['lease_token'],'new')

    def test_worker_does_not_consume_another_capability_journal(self):
        class Client:
            identity='fixture';timeout=1
            def heartbeat(self,claim):raise AssertionError('foreign task must not be touched')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'worker.sqlite';j=Journal(path,'fixture')
            j.save({'claim':{'id':60,'input_hash':'h','lease_epoch':2,'task_type':'research.bestseller'},'request_id':'r','phase':'host_pending'});j.close()
            worker=Worker(Client(),path,capabilities=('product.facts','research.keywords'))
            self.assertEqual(worker.once(allow_claim=False),'foreign_active')
            j=Journal(path,'fixture');self.assertEqual(len(j.active()),1);j.close()

    def test_pending_reactivation_blocks_execution(self):
        class Client: identity='fixture';timeout=1
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'worker.sqlite';j=Journal(path,'fixture')
            j.save({'claim':{'id':60,'input_hash':'h','lease_epoch':2,'task_type':'research.bestseller'},'request_id':'r','recovery_pending':True});j.close()
            self.assertEqual(Worker(Client(),path,capabilities=('research.bestseller',)).once(allow_claim=False),'reconciliation_required')

    def test_slow_recovery_does_not_heartbeat_old_lease_in_background(self):
        import time
        class Client:
            identity='fixture';timeout=1
            def __init__(self): self.calls=[]
            def heartbeat(self, claim):
                self.calls.append(claim['lease_epoch'])
                if claim['lease_epoch']==1: raise APIError(409)
            def recover_lease(self, claim, request, intent):
                time.sleep(.08)
                return {**claim,'lease_epoch':2,'lease_token':'new'}
        class Provider:
            durable_session=True
            def synchronize(self, records): pass
            def run(self,*args): raise SessionPending()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'worker.sqlite';c=Client();j=Journal(path,c.identity)
            claim={'id':60,'task_type':'research.bestseller','input_hash':'h','snapshot_id':1,'company_id':1,'payload':{},'lease_epoch':1,'lease_token':'old','heartbeat_seconds':.01}
            j.save({'claim':claim,'phase':'host_pending','request_id':'r','sequence':0,'context':{}});j.close()
            self.assertEqual(Worker(c,path,providers={'research.bestseller':Provider()},capabilities=('research.bestseller',)).once(allow_claim=False),'host_pending')
            self.assertEqual(c.calls.count(1),1)

    def test_replacement_is_atomic_when_new_lease_key_already_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'worker.sqlite';j=Journal(path,'fixture')
            old={'id':60,'input_hash':'hash','lease_epoch':1,'lease_token':'old'}
            new={**old,'lease_epoch':2,'lease_token':'new'}
            first={'claim':old,'phase':'host_pending','request_id':'original'}
            j.save(first);j.save({'claim':new,'phase':'host_pending','request_id':'different'})
            with self.assertRaises(Exception): j.replace_claim(first,new)
            self.assertEqual({r['request_id'] for r in j.active()},{'original','different'})
            j.close()

    def test_expired_durable_research_recovers_same_request_and_keeps_checkpoint(self):
        class Client:
            identity='fixture';timeout=1
            def __init__(self): self.recoveries=[]
            def heartbeat(self, claim):
                if claim['lease_epoch']==1: raise APIError(409)
                return claim
            def recover_lease(self, claim, request, intent):
                self.recoveries.append(request)
                return {**claim,'lease_epoch':2,'lease_token':'rotated'}
        class Provider:
            durable_session=True
            def __init__(self): self.calls=[]
            def synchronize(self, records): pass
            def run(self, kind,payload,context,request): self.calls.append(request); raise SessionPending()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'worker.sqlite'; client=Client();provider=Provider()
            claim={'id':60,'task_type':'research.bestseller','input_hash':'hash','snapshot_id':1,'company_id':1,'payload':{},'lease_epoch':1,'lease_token':'old','heartbeat_seconds':30}
            journal=Journal(path,client.identity);journal.save({'claim':claim,'phase':'host_pending','request_id':'r-original','sequence':3,'context':{'snapshot':{'snapshot_id':1}}});journal.close()
            worker=Worker(client,path,providers={'research.bestseller':provider},capabilities=('research.bestseller',))
            self.assertEqual(worker.once(allow_claim=False),'host_pending')
            journal=Journal(path,client.identity);records=journal.active();journal.close()
            self.assertEqual(len(records),1);self.assertEqual(records[0]['claim']['lease_epoch'],2)
            self.assertEqual(records[0]['request_id'],'r-original');self.assertEqual(records[0]['sequence'],3)
            self.assertEqual(client.recoveries,['r-original']);self.assertEqual(provider.calls,['r-original'])

    def test_pending_submission_is_not_rebound(self):
        class Client:
            identity='fixture';timeout=1
            def submit(self,*args): raise APIError(409)
            def recover_lease(self,*args): raise AssertionError('unresolved submission must not be replayed with new lease')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'worker.sqlite';client=Client();claim={'id':60,'task_type':'research.bestseller','input_hash':'h','lease_epoch':1,'lease_token':'old','heartbeat_seconds':30}
            j=Journal(path,client.identity);j.save({'claim':claim,'phase':'host_pending','request_id':'r','sequence':1,'pending':{'operation':'checkpoint','envelope':{'body':{'state':'unknown'}}}});j.close()
            self.assertEqual(Worker(client,path,providers={},capabilities=('research.bestseller',)).once(allow_claim=False),'lease_lost')

    def test_expired_text_host_pending_recovers_original_request(self):
        class Client:
            identity='fixture'; timeout=1
            def heartbeat(self, claim):
                if claim['lease_epoch']==1: raise APIError(409)
            def recover_lease(self, claim, request, intent):
                self.request = request
                return {**claim,'lease_epoch':2,'lease_token':'new'}
        class Provider:
            durable_session=True
            def synchronize(self, records): pass
            def run(self,*args): raise SessionPending()
        for kind in ('research.keywords', 'product.facts'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'worker.sqlite'; c=Client(); j=Journal(path,c.identity)
                claim={'id':60,'task_type':kind,'input_hash':'h','snapshot_id':1,'company_id':1,'payload':{},'lease_epoch':1,'lease_token':'old','heartbeat_seconds':1}
                j.save({'claim':claim,'phase':'host_pending','request_id':'original','sequence':0,'context':{}}); j.close()
                self.assertEqual(Worker(c,path,providers={kind:Provider()},capabilities=(kind,)).once(allow_claim=False),'host_pending')
                self.assertEqual(c.request,'original')

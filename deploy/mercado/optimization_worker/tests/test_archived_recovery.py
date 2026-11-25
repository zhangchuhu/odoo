import tempfile, unittest, hashlib, base64, json
from unittest.mock import patch

EVIDENCE=b'{}'
DIGEST=hashlib.sha256(EVIDENCE).hexdigest()
from pathlib import Path
from optimization_worker.journal import Journal
from optimization_worker.archived_recovery import recover_archived


class ScopedGalleryTest(unittest.TestCase):
    def test_waiting_original_resumes_same_epoch_and_only_once_per_gallery_step(self):
        from optimization_worker.archived_recovery import resume_waiting_gallery
        from optimization_worker.domain import canonical_hash
        from optimization_worker.host_session import SessionStore
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            j=Journal(root/'worker','worker-identity')
            self.addCleanup(j.close)
            store=SessionStore(root/'host','worker-identity')
            claim=dict(id=240,company_id=1,snapshot_id=256,task_type='research.bestseller',
                       input_hash='hash',payload={},lease_epoch=5,lease_token='original')
            ctx={'snapshot':{'case_id':251}}
            request='original-request'
            plan=dict(request_id=request,company_id=1,snapshot_id=256,input_hash='hash',case_id=251,
                      collector_id='rec',plan={'platform':'mercado-libre-mx','queries':['a','b','c'],'category':'dress'},
                      collection_config={'result_limit':5,'min_sold':100,'min_reviews':0,'min_rating':0})
            plan['digest']=canonical_hash(plan)
            can={'item_id':'MLM1','platform':'mercado-libre-mx','url':'https://articulo.mercadolibre.com.mx/MLM-1'}
            result={'status':'complete','evidence':[{'observed_at':'2026-01-01T00:00:00Z','source':'odoo-artifact:1611:sha256:'+DIGEST}],
                    'candidates':[can]}
            host=dict(request_id=request,context=ctx,payload={},task_type=claim['task_type'],collector_plan=plan,
                      binding=canonical_hash({'kind':claim['task_type'],'payload':{},'context':ctx}),
                      state='unknown',reason='unknown',resume_state='candidate_images_pending',retired=True,
                      retirement_reason='original_worker_lease_inactive',collector_artifact_id='1611',collector_evidence={},
                      collector_pending={'ledger_v1':True,'artifact_id':'1611','digest':DIGEST,'data':{},'result':result})
            with store._db() as db: store._put(db,host)
            j.save(dict(claim=claim,context=ctx,request_id=request,phase='calling',sequence=3,done=True,outcome='waiting'))
            client=Mock(research_ledger_enabled=True)
            client.artifact.return_value={'data':base64.b64encode(EVIDENCE).decode(),'digest':DIGEST,'mime_type':'application/json','case_id':251,'company_id':1}
            client.continue_registered_research.return_value={'request_id':request,'claim':dict(claim,lease_token='rotated')}
            dispatcher=Mock()
            with patch('optimization_worker.archived_recovery.convert_evidence',return_value=result):
                self.assertFalse(resume_waiting_gallery(j,store,client,dispatcher,241))
                self.assertTrue(resume_waiting_gallery(j,store,client,dispatcher,240))
                row=j.active()[0]
                self.assertEqual((row['request_id'],row['phase'],row['claim']['lease_epoch'],row['claim']['lease_token']),
                                 (request,'host_pending',5,'rotated'))
                self.assertEqual(store.inspect(request)['state'],'candidate_images_pending')
                dispatcher.acknowledge_reconciliation.assert_called_once_with(request)
                self.assertEqual(client.continue_registered_research.call_args.kwargs,
                                 {'evidence_artifact_id':'1611','evidence_digest':DIGEST,'confirmed_images':{}})
                row.update(phase='calling',done=True,outcome='waiting')
                j.save(row)
                with store._db() as db: store._put(db,host)
                self.assertFalse(resume_waiting_gallery(j,store,client,dispatcher))
                client.continue_registered_research.assert_called_once()
                client.recover_lease.assert_not_called()

    def test_confirmed_image_proof_excludes_unknown_or_mismatched_upload(self):
        from optimization_worker.archived_recovery import _confirmed_images
        from optimization_worker.research_upload import intent_id
        image={'order':0,'artifact_id':'901','source':'https://example.test/one','sha256':DIGEST}
        candidate={'platform':'mercado-libre-mx','item_id':'MLM1','url':'https://example.test/detail','images':[image]}
        identity='mercado-libre-mx:MLM1'
        key=identity+':0'
        pending={'result':{'candidates':[candidate]},'gallery_manifests':{identity:{'detail_url':candidate['url'],'images':[image['source']]}},
                 'candidate_uploads':{key:{'order':0,'ledger_v1':True,'state':'confirmed','artifact_id':'901','digest':DIGEST,'source':image['source']}}}
        self.assertEqual(_confirmed_images(pending),{intent_id('image',key):{'artifact_id':'901','digest':DIGEST}})
        pending['candidate_uploads'][key]['state']='unknown'
        with self.assertRaises(ValueError): _confirmed_images(pending)
        pending['candidate_uploads'][key]['state']='confirmed'
        image['artifact_id']='999'
        with self.assertRaises(ValueError): _confirmed_images(pending)

@patch("optimization_worker.archived_recovery.convert_evidence", return_value={"status":"complete","candidates":[{"item_id":"MLM1"}]})
class RecoveryTest(unittest.TestCase):
    def test_waiting_unknown_after_foreign_worker_can_recover_same_gallery(self, mock_convert):
        class Client:
            identity='fixture';timeout=1
            def recover_lease(self, claim, request, intent):return {**claim,'lease_epoch':claim['lease_epoch']+1,'lease_token':'new'}
            def heartbeat(self, claim):return claim
            def artifact(self, artifact_id):return {'digest':DIGEST,'case_id':3,'company_id':1,'mime_type':'application/json','data':base64.b64encode(EVIDENCE).decode()}
        class Store:
            def __init__(self):self.row={'request_id':'request','task_type':'research.bestseller','retired':True,'retirement_reason':'original_worker_lease_inactive','state':'candidate_images_pending','payload':{},'collector_plan':{'request_id':'request','input_hash':'hash','company_id':1,'case_id':3,'snapshot_id':2,'collector_id':'rec','plan':{'platform':'mercado-libre-mx','queries':['a','b','c'],'category':'dress'},'collection_config':{'result_limit':1,'min_sold':0,'min_reviews':0,'min_rating':0}},'collector_pending':{'artifact_id':334,'digest':DIGEST,'data':{},'result':{'status':'complete','evidence':[{'observed_at':'2026-01-01T00:00:00Z'}],'candidates':[{'item_id':'MLM1'}]}}}
            def inspect(self,request):return dict(self.row)
            def reactivate_research(self,request,claim,*,validate_only=False):
                if not validate_only:self.row['retired']=False
        with tempfile.TemporaryDirectory() as tmp:
            c=Client();s=Store();j=Journal(Path(tmp)/'worker.sqlite',c.identity)
            claim={'id':60,'task_type':'research.bestseller','input_hash':'hash','snapshot_id':2,'company_id':1,'payload':{},'lease_epoch':5,'lease_token':'old','heartbeat_seconds':30}
            j.save({'claim':claim,'request_id':'request','phase':'calling','sequence':3,'done':True,'outcome':'waiting'})
            recover_archived(j,s,c,'request',service_lock=Path(tmp)/'service.lock')
            self.assertEqual(j.active()[0]['phase'],'host_pending');self.assertFalse(s.row['retired']);j.close()

    def test_interrupted_reactivation_reuses_recovered_lease(self, mock_convert):
        class Client:
            identity='fixture';timeout=1
            def __init__(self): self.calls=0
            def recover_lease(self, old, request, intent):
                self.calls+=1;return {**old,'lease_epoch':2,'lease_token':'new'}
            def heartbeat(self, claim):return claim
            def artifact(self, artifact_id):return {'digest':DIGEST,'case_id':3,'company_id':1,'mime_type':'application/json','data':base64.b64encode(EVIDENCE).decode()}
        class Store:
            def __init__(self):self.fail=True;self.row={'request_id':'request','task_type':'research.bestseller','retired':True,'retirement_reason':'original_worker_lease_inactive','state':'candidate_images_pending','payload':{},'collector_plan':{'request_id':'request','input_hash':'hash','company_id':1,'case_id':3,'snapshot_id':2,'collector_id':'rec','plan':{'platform':'mercado-libre-mx','queries':['a','b','c'],'category':'dress'},'collection_config':{'result_limit':1,'min_sold':0,'min_reviews':0,'min_rating':0}},'collector_pending':{'artifact_id':334,'digest':DIGEST,'data':{},'result':{'status':'complete','evidence':[{'observed_at':'2026-01-01T00:00:00Z'}],'candidates':[{'item_id':'MLM1'}]}}}
            def inspect(self,request):return dict(self.row)
            def reactivate_research(self,request,claim,*,validate_only=False):
                if not validate_only and self.fail:self.fail=False;raise RuntimeError('crash')
                if not validate_only:self.row['retired']=False
        with tempfile.TemporaryDirectory() as tmp:
            c=Client();s=Store();j=Journal(Path(tmp)/'worker.sqlite',c.identity)
            claim={'id':60,'task_type':'research.bestseller','input_hash':'hash','snapshot_id':2,'company_id':1,'payload':{},'lease_epoch':1,'lease_token':'old','heartbeat_seconds':30}
            j.save({'claim':claim,'request_id':'request','phase':'host_pending','sequence':2,'done':True,'outcome':'lease_lost'})
            with self.assertRaises(RuntimeError):recover_archived(j,s,c,'request',service_lock=Path(tmp)/'service.lock')
            self.assertTrue(j.active()[0]['recovery_pending'])
            recover_archived(j,s,c,'request',service_lock=Path(tmp)/'service.lock')
            self.assertEqual(c.calls,1);self.assertFalse(j.active()[0].get('recovery_pending',False));j.close()

    def test_incomplete_reactivation_cannot_dispatch_or_upload(self, mock_convert):
        from optimization_worker.host_session import fence
        class Client:
            def heartbeat(self, claim):raise AssertionError('pending recovery must not be dispatched')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'worker.sqlite';j=Journal(path,'fixture')
            j.save({'claim':{'id':60,'input_hash':'h','lease_epoch':2},'request_id':'r','recovery_pending':True});j.close()
            with self.assertRaises(ValueError): fence(Client(),path,'r')

    def test_session_checkpoint_preserves_unknown_image_for_reconciliation(self, mock_convert):
        from optimization_worker.host_session import SessionStore
        from optimization_worker.domain import canonical_hash
        with tempfile.TemporaryDirectory() as tmp:
            store=SessionStore(Path(tmp)/'host.sqlite','fixture')
            claim={'task_type':'research.bestseller','company_id':1,'snapshot_id':2,'input_hash':'h','payload':{}}
            plan={'request_id':'request','company_id':1,'snapshot_id':2,'input_hash':'h','case_id':3}
            plan['digest']=canonical_hash(plan)
            row={'request_id':'request','task_type':'research.bestseller','payload':{},'state':'candidate_images_pending',
                 'retired':True,'retirement_reason':'original_worker_lease_inactive',
                 'context':{'snapshot':{'case_id':3}},'collector_plan':plan,
                 'collector_pending':{'artifact_id':'334','candidate_uploads':{'MLM1:0':{'state':'invalid'}}}}
            with store._db() as db:store._put(db,row)
            with self.assertRaises(ValueError):store.reactivate_research('request',claim)
            self.assertTrue(store.inspect('request')['retired'])
            row['collector_pending']['candidate_uploads']['MLM1:0']['state']='unknown'
            with store._db() as db:store._put(db,row)
            store.reactivate_research('request',claim,validate_only=True)
            self.assertTrue(store.inspect('request')['retired'])
            store.reactivate_research('request',claim)
            self.assertFalse(store.inspect('request').get('retired',False))
            self.assertEqual(store.inspect('request')['collector_pending']['candidate_uploads']['MLM1:0']['state'],'unknown')

    def test_service_lock_prevents_parallel_recovery(self, mock_convert):
        import fcntl, os
        with tempfile.TemporaryDirectory() as tmp:
            lock=Path(tmp)/'service.lock'; fd=os.open(lock,os.O_CREAT|os.O_RDWR,0o600)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            j=Journal(Path(tmp)/'worker.sqlite','fixture')
            try:
                with self.assertRaises(BlockingIOError): recover_archived(j,None,None,'request',service_lock=lock)
            finally:
                j.close();os.close(fd)

    def test_preserves_request_and_unretires_only_after_renewal(self, mock_convert):
        class Client:
            identity='fixture'; timeout=1
            def recover_lease(self, old, request, intent):
                assert request=='request';return {**old,'lease_epoch':2,'lease_token':'new'}
            def heartbeat(self, claim): assert claim['lease_token']=='new';return claim
            def artifact(self, artifact_id): return {'digest':DIGEST,'case_id':3,'company_id':1,'mime_type':'application/json','data':base64.b64encode(EVIDENCE).decode()}
        class Store:
            def __init__(self):self.row={'request_id':'request','task_type':'research.bestseller','retired':True,'retirement_reason':'original_worker_lease_inactive','state':'candidate_images_pending','payload':{},'context':{'snapshot':{'snapshot_id':2,'case_id':3,'company_id':1}},'collector_plan':{'request_id':'request','input_hash':'hash','company_id':1,'case_id':3,'snapshot_id':2,'collector_id':'rec','plan':{'platform':'mercado-libre-mx','queries':['a','b','c'],'category':'dress'},'collection_config':{'result_limit':1,'min_sold':0,'min_reviews':0,'min_rating':0}},'collector_pending':{'artifact_id':334,'digest':DIGEST,'data':{},'result':{'status':'complete','evidence':[{'observed_at':'2026-01-01T00:00:00Z'}],'candidates':[{'item_id':'MLM1'}]}}}
            def inspect(self,request):return dict(self.row)
            def reactivate_research(self,request,claim,*,validate_only=False):
                if not validate_only:self.row['retired']=False
        with tempfile.TemporaryDirectory() as tmp:
            c=Client();s=Store();j=Journal(Path(tmp)/'worker.sqlite',c.identity)
            claim={'id':60,'task_type':'research.bestseller','input_hash':'hash','snapshot_id':2,'company_id':1,'payload':{},'lease_epoch':1,'lease_token':'old','heartbeat_seconds':30}
            j.save({'claim':claim,'request_id':'request','phase':'host_pending','sequence':2,'done':True,'outcome':'lease_lost'})
            recover_archived(j,s,c,'request',service_lock=Path(tmp)/'service.lock')
            active=j.active();self.assertEqual(len(active),1);self.assertEqual(active[0]['request_id'],'request');self.assertEqual(active[0]['claim']['lease_epoch'],2);self.assertEqual(active[0]['sequence'],2);self.assertFalse(s.row['retired']);j.close()

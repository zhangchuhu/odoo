import base64
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from optimization_worker.host_session import SessionStore, SessionProvider, SessionPending
from optimization_worker import candidate_image_upload as upload
from optimization_worker.domain import canonical_hash


def png(color):
    stream=io.BytesIO(); Image.new('RGB',(3,4),color).save(stream,format='PNG'); return stream.getvalue()


class CandidateUploadTest(unittest.TestCase):
    def test_scoped_finish_seals_confirmed_intents_before_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=SessionStore(Path(tmp)/'host.sqlite','test')
            plan={'request_id':'r','company_id':2,'case_id':3,'snapshot_id':4,'input_hash':'h'}; plan['digest']=canonical_hash(plan)
            candidate={'platform':'shein-us','item_id':'1','url':'https://us.shein.com/item-p-1.html','image':{'artifact_id':'11'}}
            pending={'ledger_v1':True,'artifact_id':'10','digest':'e','result':{'candidates':[candidate]},'candidate_uploads':{'shein-us:1':{'ledger_v1':True,'state':'confirmed','artifact_id':'11','digest':'a'}}}
            with store._db() as db: store._put(db,{'request_id':'r','state':'candidate_images_pending','collector_plan':plan,'collector_pending':pending})
            class C:
                research_ledger_enabled=True; fail=True
                def seal_research_manifest(self,claim,r,intents):
                    if self.fail: raise OSError('seal reply lost')
                    return {'request_id':r,'intent_count':len(intents),'coverage':'unknown'}
            c=C(); claim={'company_id':2,'snapshot_id':4,'input_hash':'h'}
            with self.assertRaises(ValueError): upload.finish(store,'r',claim,lambda:None)
            with self.assertRaises(OSError): upload.finish(store,'r',claim,lambda:None,client=c)
            self.assertEqual(store.inspect('r')['state'],'candidate_images_pending')
            c.fail=False
            self.assertEqual(upload.finish(store,'r',claim,lambda:None,client=c),1)
            self.assertEqual(store.inspect('r')['state'],'resolved')
    def test_scoped_upload_lost_receipt_records_unknown_without_repeating(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=SessionStore(Path(tmp)/'host.sqlite','test')
            plan={'request_id':'r','company_id':2,'case_id':3,'snapshot_id':4,'input_hash':'h'}; plan['digest']=canonical_hash(plan)
            candidate={'platform':'shein-us','item_id':'1','url':'https://us.shein.com/item-p-1.html','image_url':'https://cdn.example.com/item.png'}
            with store._db() as db: store._put(db,{'request_id':'r','state':'candidate_images_pending','task_type':'research.bestseller','collector_plan':plan,'collector_pending':{'result':{'candidates':[candidate]}}})
            image=Path(tmp)/'image.png'; image.write_bytes(png('red'))
            screen=Path(tmp)/'screen.png'; screen.write_bytes(png('blue'))
            class Client:
                research_ledger_enabled=True
                calls=0
                def create_research_artifact(self,claim,request_id,intent_id,**kwargs):
                    self.calls+=1; raise OSError('lost response')
                def create_artifact(self,*args,**kwargs): raise AssertionError('legacy endpoint used')
            c=Client(); claim={'company_id':2,'snapshot_id':4,'input_hash':'h'}
            with self.assertRaises(OSError): upload.send(store,'r',claim,'shein-us:1',image,screen,c,lambda:None,source=candidate['image_url'])
            pending=store.inspect('r')['collector_pending']['candidate_uploads']['shein-us:1']
            self.assertEqual((pending['state'],pending['ledger_v1']),('unknown',True))
            with self.assertRaises(ValueError): upload.send(store,'r',claim,'shein-us:1',image,screen,c,lambda:None,source=candidate['image_url'])
            self.assertEqual(c.calls,1)
    def test_lost_upload_does_not_repeat_and_reconcile_binds_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=SessionStore(Path(tmp)/'host.sqlite','test')
            record={'request_id':'r','state':'candidate_images_pending','task_type':'research.bestseller',
                    'collector_plan':{'company_id':2,'case_id':3,'snapshot_id':4,'input_hash':'h'},
                    'collector_pending':{'result':{'candidates':[{'platform':'shein-us','item_id':'1','url':'https://us.shein.com/item-p-1.html','image_url':'https://cdn.example.com/item.png'}]}}}
            record['collector_plan']['request_id']='r'
            record['collector_plan']['digest']=canonical_hash(record['collector_plan'])
            with store._db() as db: store._put(db,record)
            image=Path(tmp)/'image.png';image.write_bytes(png('red'))
            screenshot=Path(tmp)/'screen.png';screenshot.write_bytes(png('blue'))
            class Client:
                calls=0
                def create_artifact(self,case_id,**kw):
                    self.calls+=1; self.raw=kw['data']; raise OSError('lost response')
                def artifact(self,id):
                    return {'company_id':2,'case_id':3,'mime_type':'image/png','data':base64.b64encode(self.raw).decode(), 'digest':hashlib.sha256(self.raw).hexdigest()}
            c=Client(); claim={'company_id':2,'snapshot_id':4,'input_hash':'h'}
            store.enqueue=lambda *args: None
            provider=SessionProvider(store)
            with self.assertRaises(SessionPending): provider.run('research.bestseller',{}, {},'r')
            with self.assertRaises(ValueError): upload.send(store,'r',claim,'shein-us:1',image,screenshot,c,lambda:None,source='https://evil.example/other.png')
            with self.assertRaises(ValueError): upload.finish(store,'r',claim,lambda:None)
            with self.assertRaises(OSError): upload.send(store,'r',claim,'shein-us:1',image,screenshot,c,lambda:None,source='https://cdn.example.com/item.png')
            with self.assertRaises(ValueError): upload.send(store,'r',claim,'shein-us:1',image,screenshot,c,lambda:None,source='https://cdn.example.com/item.png')
            self.assertEqual(c.calls,1)
            upload.reconcile(store,'r',claim,'shein-us:1','8',c,lambda:None)
            r=store.inspect('r'); self.assertEqual(r['collector_pending']['result']['candidates'][0]['image']['artifact_id'],'8')
            self.assertEqual(r['collector_pending']['candidate_uploads']['shein-us:1']['state'],'confirmed')
            with self.assertRaises(ValueError): upload.finish(store,'r',claim,lambda:None, require_manifest=True)
            self.assertEqual(upload.finish(store,'r',claim,lambda:None),1)
            self.assertEqual(store.inspect('r')['state'],'resolved')
            self.assertEqual(provider.run('research.bestseller',{}, {},'r')['candidates'][0]['image']['artifact_id'],'8')

    def test_known_receipt_is_saved_before_fence_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=SessionStore(Path(tmp)/'host.sqlite','test')
            record={'request_id':'r','state':'candidate_images_pending','task_type':'research.bestseller',
                    'collector_plan':{'request_id':'r','company_id':2,'case_id':3,'snapshot_id':4,'input_hash':'h'},
                    'collector_pending':{'result':{'candidates':[{'platform':'shein-us','item_id':'1','url':'https://us.shein.com/item-p-1.html','image_url':'https://cdn.example.com/item.png'}]}}}
            record['collector_plan']['digest']=canonical_hash(record['collector_plan'])
            with store._db() as db: store._put(db,record)
            image=Path(tmp)/'image.png';image.write_bytes(png('red'))
            screen=Path(tmp)/'screen.png';screen.write_bytes(png('blue'))
            class Client:
                def create_artifact(self,case_id,**kw):
                    return {'artifact_id':8,'digest':hashlib.sha256(kw['data']).hexdigest()}
            calls=[]
            def fence():
                calls.append(1)
                if len(calls)>1: raise RuntimeError('lease lost')
            claim={'company_id':2,'snapshot_id':4,'input_hash':'h'}
            with self.assertRaises(RuntimeError):
                upload.send(store,'r',claim,'shein-us:1',image,screen,Client(),fence,source='https://cdn.example.com/item.png')
            pending=store.inspect('r')['collector_pending']['candidate_uploads']['shein-us:1']
            self.assertEqual(pending['artifact_id'],'8')
            self.assertEqual(pending['state'],'unknown')

    def test_ordered_gallery_requires_all_confirmed_images_and_immutable_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=SessionStore(Path(tmp)/'host.sqlite','test')
            plan={'request_id':'r','company_id':2,'case_id':3,'snapshot_id':4,'input_hash':'h'}; plan['digest']=canonical_hash(plan)
            candidate={'platform':'shein-us','item_id':'1','url':'https://us.shein.com/item-p-1.html','image_url':'https://cdn.example.com/thumbnail.png'}
            with store._db() as db: store._put(db,{'request_id':'r','state':'candidate_images_pending','collector_plan':plan,'collector_pending':{'result':{'candidates':[candidate]}}})
            claim={'company_id':2,'snapshot_id':4,'input_hash':'h'}
            image=Path(tmp)/'image.png'; image.write_bytes(png('red'))
            screen=Path(tmp)/'screen.png'; screen.write_bytes(png('blue'))
            class Client:
                def __init__(self): self.items={}
                def create_artifact(self,case_id,**kw):
                    raw=kw['data']; n=str(len(self.items)+1); self.items[n]={'company_id':2,'case_id':3,'mime_type':kw['mime_type'],'data':base64.b64encode(raw).decode(),'digest':hashlib.sha256(raw).hexdigest()}; return {'artifact_id':n,'digest':self.items[n]['digest']}
                def artifact(self,id): return self.items[str(id)]
            c=Client(); urls=['https://cdn.example.com/1.png','https://cdn.example.com/2.png']
            upload.bind_gallery(store,'r',claim,'shein-us:1',{'detail_url':candidate['url'],'images':urls},screen,lambda:None)
            with self.assertRaises(ValueError): upload.bind_gallery(store,'r',claim,'shein-us:1',{'detail_url':candidate['url'],'images':urls[::-1]},screen,lambda:None)
            with self.assertRaises(ValueError): upload.send(store,'r',claim,'shein-us:1',image,screen,c,lambda:None,source=urls[1],order=0)
            upload.send(store,'r',claim,'shein-us:1',image,screen,c,lambda:None,source=urls[0],order=0)
            with self.assertRaises(ValueError): upload.finish(store,'r',claim,lambda:None)
            image.write_bytes(png('green'))
            upload.send(store,'r',claim,'shein-us:1',image,screen,c,lambda:None,source=urls[1],order=1)
            with store._db() as db:
                record=store._get(db,'r'); receipt=record['collector_pending']['candidate_uploads']['shein-us:1:1']; original=receipt['artifact_id']; receipt['artifact_id']='999'; store._put(db,record)
            with self.assertRaises(ValueError): upload.finish(store,'r',claim,lambda:None, require_manifest=True)
            with store._db() as db:
                record=store._get(db,'r'); record['collector_pending']['candidate_uploads']['shein-us:1:1']['artifact_id']=original; store._put(db,record)
            self.assertEqual(upload.finish(store,'r',claim,lambda:None, require_manifest=True),1)
            result=store.inspect('r')['result']['candidates'][0]
            self.assertEqual(result['gallery_image_urls'],urls)
            self.assertEqual([x['source'] for x in result['images']],urls)
            self.assertEqual([x['order'] for x in result['images']],[0,1])
            self.assertEqual(len({x['image_id'] for x in result['images']}), 2)

    def test_gallery_lost_receipt_fences_retry_by_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=SessionStore(Path(tmp)/'host.sqlite','test'); plan={'request_id':'r','company_id':2,'case_id':3,'snapshot_id':4,'input_hash':'h'}; plan['digest']=canonical_hash(plan)
            candidate={'platform':'shein-us','item_id':'1','url':'https://us.shein.com/item-p-1.html','image_url':'https://cdn.example.com/thumb.png'}
            with store._db() as db: store._put(db,{'request_id':'r','state':'candidate_images_pending','collector_plan':plan,'collector_pending':{'result':{'candidates':[candidate]}}})
            claim={'company_id':2,'snapshot_id':4,'input_hash':'h'}; image=Path(tmp)/'x.png'; image.write_bytes(png('red')); screen=Path(tmp)/'s.png'; screen.write_bytes(png('blue'))
            class Client:
                calls=0
                def create_artifact(self,case_id,**kw): self.calls+=1; self.raw=kw['data']; raise OSError('lost receipt')
                def artifact(self,id): return {'company_id':2,'case_id':3,'mime_type':'image/png','data':base64.b64encode(self.raw).decode(),'digest':hashlib.sha256(self.raw).hexdigest()}
            c=Client(); url='https://cdn.example.com/1.png'
            upload.bind_gallery(store,'r',claim,'shein-us:1',{'detail_url':candidate['url'],'images':[url]},screen,lambda:None)
            with self.assertRaises(OSError): upload.send(store,'r',claim,'shein-us:1',image,screen,c,lambda:None,source=url,order=0)
            with self.assertRaises(ValueError): upload.send(store,'r',claim,'shein-us:1',image,screen,c,lambda:None,source=url,order=0)
            self.assertEqual(c.calls,1)
            self.assertEqual(upload.reconcile(store,'r',claim,'shein-us:1','9',c,lambda:None,order=0)['artifact_id'],'9')
            self.assertEqual(upload.finish(store,'r',claim,lambda:None),1)

    def test_gallery_allows_31_observed_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=SessionStore(Path(tmp)/'host.sqlite','test'); plan={'request_id':'r','company_id':2,'case_id':3,'snapshot_id':4,'input_hash':'h'};plan['digest']=canonical_hash(plan)
            candidate={'platform':'shein-us','item_id':'1','url':'https://us.shein.com/item-p-1.html','image_url':'https://cdn.example.com/thumb.png'}
            with store._db() as db: store._put(db,{'request_id':'r','state':'candidate_images_pending','collector_plan':plan,'collector_pending':{'result':{'candidates':[candidate]}}})
            screen=Path(tmp)/'s.png';screen.write_bytes(png('blue'));claim={'company_id':2,'snapshot_id':4,'input_hash':'h'}
            urls=['https://cdn.example.com/'+str(i)+'.png' for i in range(31)]
            self.assertEqual(upload.bind_gallery(store,'r',claim,'shein-us:1',{'detail_url':candidate['url'],'images':urls},screen,lambda:None),31)
            self.assertEqual(store.inspect('r')['collector_pending']['gallery_manifests']['shein-us:1']['images'],urls)

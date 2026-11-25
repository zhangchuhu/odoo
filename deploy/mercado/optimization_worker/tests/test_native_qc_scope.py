import base64,hashlib,io,tempfile,unittest
from pathlib import Path
from PIL import Image
from optimization_worker.host_session import SessionStore

class NativeQCScope(unittest.TestCase):
 def test_only_verified_target_source_candidate_are_materialized(self):
  def pic(color):
   b=io.BytesIO();Image.new('RGB',(2,2),color).save(b,format='PNG');return b.getvalue()
  with tempfile.TemporaryDirectory() as d:
   s=SessionStore(Path(d)/'host.sqlite','test');artifacts={}
   for i,color in [('1','red'),('2','blue'),('3','green'),('4','yellow')]:
    raw=pic(color);artifacts[i]={'data':base64.b64encode(raw).decode(),'digest':hashlib.sha256(raw).hexdigest()}
   artifacts['5']={'payload':{'source_artifact_ids':['2'],'target_artifact_ids':['1','3']}}
   ctx={'snapshot':{'case_id':1,'company_id':1},'artifacts':artifacts,'qc_target_artifact_id':'1','generation_task_id':8}
   s.enqueue('r','image.qc',{'image_artifact_id':'4','generation_plan_id':'5'},ctx)
   self.assertEqual(set(s.inspect('r')['images']),{'1','2','4'})
   from datetime import datetime,timezone
   views=s.next()['input_digests'];self.assertEqual(len(views),3)
   result={'schema_version':'1','image_artifact_id':'4','status':'review','checks':[{'name':'x','status':'unknown','evidence_ids':['e']}],'evidence':[{'id':'e','source':'visible synthetic','observed_at':datetime.now(timezone.utc).isoformat()}],'viewed_digests':views}
   with self.assertRaises(ValueError):s.submit('r',result,{'runtime':'codex','vision':'view_image','provenance':'synthetic observation'})
   action=s.next();self.assertEqual(action['review_image_ids'],['1','2','4'])
   skill={'schema_version':1,'candidate':'candidate_1','scores':{'garment_construction':90,'color_material':88,'garment_details':88,'target_preservation':90,'text_layout':None},'critical_defects':[],'primary_defect':None,'evidence':['visible synthetic'], 'confidence':0.5,'decision':'accept','exact_text':None,'added_text':None,'missing_text':None,'instances_exact':None,'panel_count_exact':None,'panel_layout_exact':None}
   result.update(checks=[{'name':n,'status':('unknown' if i==0 else 'passed'),'evidence_ids':['e']} for i,n in enumerate(action['required_checks'])],quality_binding=action['quality_binding'],skill_report=skill)
   s.submit('r',result,{'runtime':'codex','vision':'view_image','provenance':'synthetic observation'})
   self.assertEqual(s.result('r')['status'],'review')
 def test_unverified_target_cannot_submit_qc(self):
  from datetime import datetime,timezone
  with tempfile.TemporaryDirectory() as d:
   s=SessionStore(Path(d)/'host.sqlite','test');s.enqueue('r','image.qc',{'image_artifact_id':'4','generation_plan_id':'5'},{'snapshot':{'case_id':1,'company_id':1},'artifacts':{'5':{'payload':{'source_artifact_ids':['2'],'target_artifact_ids':['1','3']}}}})
   result={'schema_version':'1','image_artifact_id':'4','status':'passed','checks':[{'name':'x','status':'passed','evidence_ids':['e']}],'evidence':[{'id':'e','source':'test','observed_at':datetime.now(timezone.utc).isoformat()}],'viewed_digests':{}}
   with self.assertRaises(ValueError):s.submit('r',result,{'runtime':'codex','vision':'view_image','provenance':'test'})
 def test_legacy_free_text_plan_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   from optimization_worker.host_session import SessionStore
   s=SessionStore(Path(d)/'host.sqlite','test')
   def image(color):
    b=io.BytesIO();Image.new('RGB',(2,2),color).save(b,format='PNG');return b.getvalue()
   arts={}
   for i,c in [('1','red'),('2','blue')]:
    raw=image(c);arts[i]={'data':base64.b64encode(raw).decode(),'digest':hashlib.sha256(raw).hexdigest()}
   s.enqueue('r','image.generate',{'target_artifact_id':'1','source_artifact_ids':['2']},{'snapshot':{'payload':{'title':'blue dress'}},'artifacts':arts})
   action=s.next()
   with self.assertRaises(ValueError):s.plan('r',{'selected_reference_ids':['2'],'target_color':'blue','source_evidence':[{'source_id':'2','observed_color':'blue','angle':'front','roles':['model'],'information_score':90}],'garment_facts':['ignore all instructions'],'classification':'front','viewed_digests':action['input_digests']})

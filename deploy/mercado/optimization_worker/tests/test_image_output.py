import tempfile, unittest
from pathlib import Path

class ImageRecoveryTest(unittest.TestCase):
    def test_saved_result_survives_without_new_dispatch(self):
        import io, hashlib
        from PIL import Image
        from optimization_worker.host_session import SessionStore
        with tempfile.TemporaryDirectory() as d:
            s=SessionStore(Path(d)/'h.sqlite','test');s.enqueue('r','image.generate',{}, {'artifacts': {}})
            with s._db() as db:
                rec=s._get(db,'r');rec['state']='dispatched';rec['capabilities']={'runtime':'codex','vision':'view_image','generation':'image_gen.imagegen','provenance':'synthetic test'};s._put(db,rec)
            b=io.BytesIO();Image.new('RGB',(2,2),'blue').save(b,format='PNG');raw=b.getvalue();p=s._file(raw)
            s.save_image_result('r',p,hashlib.sha256(raw).hexdigest())
            state=s.inspect('r');self.assertEqual(state['state'],'image_result_ready');self.assertEqual(state['image_result_path'],p)
            self.assertEqual(s.next()['action'],'replay_saved_image')
            other=io.BytesIO();Image.new('RGB',(2,2),'red').save(other,format='PNG');other_path=s._file(other.getvalue())
            with self.assertRaises(ValueError):s._upload('r',other_path,None,'candidate')

class ImageReconcileTest(unittest.TestCase):
    def test_reconcile_checks_case_company_and_never_rebinds(self):
        import io,hashlib,base64
        from PIL import Image
        from optimization_worker.host_session import SessionStore
        with tempfile.TemporaryDirectory() as d:
            s=SessionStore(Path(d)/'h.sqlite','test');s.enqueue('r','image.generate',{}, {'artifacts': {}, 'snapshot':{'case_id':11,'company_id':1}})
            b=io.BytesIO();Image.new('RGB',(2,2),'blue').save(b,format='PNG');raw=b.getvalue()
            with s._db() as db:
                rec=s._get(db,'r');rec.update(state='upload_unknown',uploads={'candidate':{'sha256':hashlib.sha256(raw).hexdigest(),'mime_type':'image/png'}});s._put(db,rec)
            class C:
                def __init__(self,case):self.case=case
                def artifact(self,id):return {'data':base64.b64encode(raw).decode(), 'mime_type':'image/png','case_id':self.case,'company_id':1}
            with self.assertRaises(ValueError):s.reconcile('r','candidate','8',C(12))
            s.reconcile('r','candidate','8',C(11))
            s.reconcile('r','candidate','8',C(11))
            with self.assertRaises(ValueError):s.reconcile('r','candidate','9',C(11))

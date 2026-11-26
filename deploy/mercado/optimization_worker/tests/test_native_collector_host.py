import io, json, struct, unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from optimization_worker.native_collector_host import read_message, write_message, validate_origin
class NativeCollectorHostTests(unittest.TestCase):
 def test_expansion_seal_requires_native_origin(self):
  from optimization_worker.native_collector_host import forward
  with self.assertRaisesRegex(ValueError, 'Native origin required'):
   forward('/unused',{'operation':'seal_expansion'})
 def test_card_v3_operations_require_native_origin(self):
  from optimization_worker.native_collector_host import forward
  for op in ('card_v3_page_challenge','card_v3_page_confirm','card_v3_batch','card_v3_page_finish'):
   with self.assertRaisesRegex(ValueError,'Native origin required'):
    forward('/unused',{'operation':op})
 def test_completion_requires_durable_success_for_the_original_research_request(self):
  from optimization_worker.journal import Journal
  from optimization_worker.native_collector_host import collector_completion
  with TemporaryDirectory() as folder:
   private=Path(folder)
   request='8ef82438-a6e9-4358-a47d-a67eb233c95c'
   message=dict(operation='collector_completion', request_id=request, task_id=228)
   record=dict(claim=dict(id=228,input_hash='test',lease_epoch=1,task_type='research.bestseller'),
               request_id=request,phase='terminal_checkpoint')
   journal=Journal(private/'research-scoped-worker.sqlite')
   try:
    for done,outcome in [(False,None),(True,'waiting'),(True,'lease_lost'),(True,'failed'),(True,'succeeded')]:
     journal.save(dict(record,done=done,outcome=outcome))
     self.assertEqual(collector_completion(private,message),
                      dict(ok=True,complete=outcome=='succeeded',request_id=request,task_id=228))
    self.assertFalse(collector_completion(private,dict(message,task_id=229))['complete'])
    self.assertFalse(collector_completion(private,dict(message,request_id='5'+request[1:]))['complete'])
   finally:
    journal.close()
 def test_frame_roundtrip(self):
  buf=io.BytesIO(); write_message(buf, {'operation':'poll'});buf.seek(0);self.assertEqual(read_message(buf),{'operation':'poll'})
 def test_short_or_oversized_frame_refused(self):
  for b in [b'\x01',struct.pack('=I',1048577),struct.pack('=I',3)+b'{}']:
   with self.assertRaises(ValueError):read_message(io.BytesIO(b))
 def test_origin_is_exact_authorized_extension(self):
  identity='a'*32
  self.assertTrue(validate_origin('chrome-extension://'+identity+'/',identity))
  self.assertFalse(validate_origin('chrome-extension://'+('b'*32)+'/',identity))
 def test_broker_uses_framed_json(self):
  import tempfile, socket, threading, os
  from optimization_worker.native_collector_host import forward, read_message, write_message
  with tempfile.TemporaryDirectory() as p:
   path=p+'/s'; s=socket.socket(socket.AF_UNIX); s.bind(path);os.chmod(path,0o600);s.listen(1); received=[]
   def server():
    conn,_=s.accept()
    with conn,conn.makefile('rwb') as f:
     received.append(read_message(f));write_message(f,{'challenge':None})
   thread=threading.Thread(target=server);thread.start()
   result=forward(path,{'operation':'poll','tab_id':4});thread.join();s.close()
   self.assertEqual(result,{'challenge':None});self.assertEqual(received,[{'operation':'poll','tab_id':4}])
 def test_manifest_uses_exact_extension_origin_and_absolute_launcher(self):
  from optimization_worker.native_collector_host import native_manifest
  import pathlib
  root=pathlib.Path('/tmp/example'); result=native_manifest(root,'a'*32)
  self.assertEqual(result['allowed_origins'],['chrome-extension://'+'a'*32+'/'])
  self.assertTrue(result['path'].startswith('/'))
  with self.assertRaises(ValueError):native_manifest(root,'bad')
 def test_manifest_accepts_explicit_list_of_extension_ids(self):
  from optimization_worker.native_collector_host import native_manifest
  m=native_manifest('/tmp/x',['a'*32,'b'*32]);self.assertEqual(m['allowed_origins'],['chrome-extension://'+'a'*32+'/','chrome-extension://'+'b'*32+'/'])
  with self.assertRaises(ValueError):native_manifest('/tmp/x',['a'*32,'bad'])

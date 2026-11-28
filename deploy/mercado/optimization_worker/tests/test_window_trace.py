import json, tempfile, unittest
import errno, io, socket, threading
from pathlib import Path
from optimization_worker.native_collector_host import trace_native, trace_supervisor, forward_guard, read_message, write_message

class WindowTraceTests(unittest.TestCase):
 def test_supervisor_http_status_is_bounded_and_no_response_is_recorded(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'trace.log';trace_supervisor(p,'req-123','supervisor','broker_api_status',409)
   row=json.loads(p.read_text());self.assertEqual(row['http_status'],409)
 def test_activation_trace_records_fixed_failure_without_payload(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'trace.log';trace_native(p,'activation_confirm','broker_result','req-123',{'token':'secret'},code='rejected')
   row=json.loads(p.read_text());self.assertEqual((row['stage'],row['code']),('broker.result','rejected'));self.assertNotIn('secret',p.read_text())
 def test_trace_allowlists_fields_and_never_writes_credentials(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'trace.log'
   trace_native(p, 'anchor_challenge', 'received', 'req-123', {'token':'secret'})
   row=json.loads(p.read_text()); self.assertEqual(row['stage'],'native.received'); self.assertEqual(row['code'],'ok'); self.assertNotIn('secret',p.read_text()); self.assertIn('timestamp',row)

 def test_transport_failure_records_errno_without_exception_details(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'trace.log'
   failure=BlockingIOError(errno.EAGAIN,'private socket path')
   trace_native(p,'next_candidate','failed','req-123',code='broker_unavailable',transport_error=failure)
   row=json.loads(p.read_text())
   self.assertEqual(row['message'],'BlockingIOError (EAGAIN)')
   self.assertNotIn('private socket path',p.read_text())

 def test_known_native_failure_records_safe_reason(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'trace.log'
   trace_native(p,'binding_status','failed','req-123',code='broker_unavailable',transport_error=ValueError('Broker disconnected'))
   row=json.loads(p.read_text());self.assertEqual(row['message'],'ValueError (broker_disconnected)')
   trace_native(p,'binding_status','failed','req-123',code='broker_unavailable',transport_error=ValueError('secret token'))
   self.assertNotIn('secret token',p.read_text())

 def test_guard_first_response_is_traced_immediately(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'socket'; log=Path(t)/'trace.log'
   server=socket.socket(socket.AF_UNIX);server.bind(str(path));path.chmod(0o600);server.listen();server.settimeout(.5)
   def worker():
    try: c,_=server.accept()
    except TimeoutError:return
    with c,c.makefile('rwb') as stream:
     read_message(stream);write_message(stream,{'ok':False})
   thread=threading.Thread(target=worker);thread.start()
   try: forward_guard(path,{'operation':'anchor_guard_open','request_id':'req-1'},io.BytesIO(),io.BytesIO(),trace_path=log)
   finally: thread.join();server.close()
   rows=[json.loads(l) for l in log.read_text().splitlines()]
   self.assertEqual([(r['stage'],r['code']) for r in rows],[('native.guard_response','rejected')])

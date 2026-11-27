import unittest
from optimization_worker.research_return_bridge import ReturnBridge

ID='8b4f7606-7811-4070-925d-d21cc78f13f0'
class Tests(unittest.TestCase):
    def test_supplemental_candidate_also_requires_gallery(self):
        from optimization_worker.research_return_bridge import next_gallery_step
        candidate={'platform':'mercado-libre-mx','item_id':'MLM2','url':'https://articulo.mercadolibre.com.mx/MLM2'}
        record={'state':'candidate_images_pending','collector_pending':{'result':{'candidates':[],'supplemental_candidates':[candidate]}}}
        step=next_gallery_step(record)
        self.assertEqual((step['stage'],step['identity']),('manifest','mercado-libre-mx:MLM2'))
    def test_card_v3_binding_reports_page_progress(self):
        from unittest.mock import patch
        from optimization_worker.research_return_bridge import build_bridge
        claim={'task_type':'research.bestseller','lease_epoch':2}
        plan={'strategy':'card-visual-v3','queries':['a','b','c'],'result_limit':5}
        store=type('Store',(),{'inspect':lambda self,request:{'collector_plan':{'digest':'d','plan':plan}}})()
        card_state={'queries':['a','b','c'],'next_page':(1,1),'known_products':4,
                    'progress':{'known_products':4,'completed_pages':[1,0,0],'selected_per_query':[None,None,None],'detail_count':0,'threshold_met':0,'supplemental':0,'excluded':0,'visual_required':0,'visual_done':0,'visual_threshold_passed':0,'visual_supplemental_passed':0}}
        with patch('optimization_worker.host_session.worker_records',return_value=[{'request_id':ID,'claim':claim}]),patch('optimization_worker.host_session.fence'),patch('optimization_worker.tab_receipt_store.TabReceiptStore') as ledger,patch('optimization_worker.tab_receipt_supervisor.CardPageSupervisor'):
            ledger.return_value.current_binding.return_value={'tab_id':123,'window_id':4,'plan_digest':'d'}
            ledger.return_value.card_v3_state.return_value=card_state
            ledger.return_value.resume_index.return_value=1
            bridge=build_bridge(ID,object(),store,'journal')
            status=bridge.call(ID,'binding_status',{})['binding']
            self.assertEqual(status['card_v3']['next_page'],(1,1))
            self.assertEqual(status['card_v3']['known_products'],4)
            bridge.close()
    def test_card_v3_verdict_schema_reaches_fenced_handler(self):
        calls=[]
        bridge=ReturnBridge(ID,lambda:{'task_type':'research.bestseller'},lambda:calls.append('fence'),{'submit_visual_verdict':lambda c,d:{'classification':'competitor'}})
        data={'submission_id':'card-v3-0001','receipts':['a'*32],'candidate_primary':{},'secondary':[], 'styles':[],'occasions':[],'features':['lace']}
        self.assertEqual(bridge.call(ID,'submit_visual_verdict',data)['visual']['classification'],'competitor')
        self.assertEqual(calls,['fence'])
    def test_visual_v2_verdict_schema_reaches_fenced_handler(self):
        from optimization_worker.research_return_bridge import ReturnBridge
        calls=[]
        bridge=ReturnBridge(ID,lambda:{'task_type':'research.bestseller'},lambda:calls.append('fence'),
                            {'submit_visual_verdict':lambda c,d:{'status':'qualified'}})
        data={'submission_id':'visual-v2-0001','receipts':['a'*32],'candidate_primary':{},'secondary':[],
              'styles':[],'occasions':[],'critical_conflict':False,'features':['lace']}
        self.assertEqual(bridge.call(ID,'submit_visual_verdict',data)['visual']['status'],'qualified')
        self.assertEqual(calls,['fence'])
    def test_original_slot_is_source_bound_and_refuses_redirected_files(self):
        import os
        import tempfile
        from pathlib import Path
        from optimization_worker.research_return_bridge import gallery_save_path, read_saved_original
        step = {'identity': 'market:1', 'order': 0, 'source': 'https://cdn.test/front.webp'}
        with tempfile.TemporaryDirectory() as directory:
            slot = gallery_save_path(directory, step)
            self.assertEqual(slot.parent, Path(directory))
            self.assertNotEqual(slot, gallery_save_path(directory, dict(step, order=1)))
            self.assertNotEqual(slot, gallery_save_path(directory, dict(step, source='https://cdn.test/back.webp')))
            original = Path(directory) / 'other'
            original.write_bytes(b'image')
            slot.symlink_to(original)
            with self.assertRaises(OSError):
                read_saved_original(slot)
            slot.unlink()
            os.link(original, slot)
            with self.assertRaises(ValueError):
                read_saved_original(slot)
            slot.unlink()
            slot.write_bytes(b'image')
            self.assertEqual(read_saved_original(slot), b'image')

    def test_gallery_status_follows_committed_receipts_across_candidates(self):
        from optimization_worker.research_return_bridge import next_gallery_step
        candidates = [{'platform': 'market', 'item_id': str(i), 'url': f'https://market.test/{i}'} for i in (1, 2)]
        pending = {'result': {'candidates': candidates}, 'gallery_manifests': {}, 'candidate_uploads': {}}
        record = {'state': 'candidate_images_pending', 'collector_pending': pending}
        for candidate in candidates:
            identity = 'market:' + candidate['item_id']
            target = {'identity': identity, 'detail_url': candidate['url']}
            self.assertEqual(next_gallery_step(record), dict(target, stage='manifest'))
            sources = ['https://market.test/cover', 'https://market.test/back']
            pending['gallery_manifests'][identity] = {'images': sources}
            for order, source in enumerate(sources):
                self.assertEqual(next_gallery_step(record), dict(target, stage='image', order=order, source=source))
                key = f'{identity}:{order}'
                pending['candidate_uploads'][key] = {'state': 'unknown'}
                self.assertEqual(next_gallery_step(record), dict(target, stage='reconcile_image', order=order))
                pending['candidate_uploads'][key] = {'state': 'confirmed'}
        self.assertEqual(next_gallery_step(record), {'stage': 'finish'})
        record['state'] = 'resolved'
        self.assertEqual(next_gallery_step(record), {'stage': 'complete'})

    def test_original_saved_in_browser_slot_is_read_back_before_advancing(self):
        import base64
        import hashlib
        import io
        import tempfile
        from pathlib import Path
        from unittest.mock import Mock, patch
        from PIL import Image
        from optimization_worker.domain import canonical_hash
        from optimization_worker.host_session import SessionStore
        from optimization_worker.research_return_bridge import build_bridge

        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory) / 'host.sqlite', 'test')
            claim = {'task_type': 'research.bestseller', 'lease_epoch': 1, 'company_id': 2,
                     'case_id': 3, 'snapshot_id': 4, 'input_hash': 'h'}
            identity = 'shein-us:1'
            plan = {key: claim[key] for key in ('company_id', 'case_id', 'snapshot_id', 'input_hash')}
            plan.update(request_id=ID, plan={'queries': ['a', 'b', 'c']})
            plan['digest'] = canonical_hash(plan)
            candidate = {'platform': 'shein-us', 'item_id': '1', 'url': 'https://us.shein.com/item-p-1.html'}
            buf = io.BytesIO()
            Image.new('RGB', (4, 5), 'blue').save(buf, format='PNG')
            raw = buf.getvalue()
            digest = hashlib.sha256(raw).hexdigest()
            manifest = {'images': ['https://img.ltwebstatic.com/cover.png'], 'detail_url': candidate['url'],
                        'screenshot_path': store._file(raw), 'screenshot_sha256': digest}
            with store._db() as db:
                store._put(db, {'request_id': ID, 'state': 'candidate_images_pending', 'collector_plan': plan,
                                'collector_pending': {'result': {'candidates': [candidate]}, 'gallery_manifests': {identity: manifest}}})
            client = Mock(research_ledger_enabled=True)
            client.create_research_artifact.return_value = {'artifact_id': '81', 'digest': digest}
            client.artifact.return_value = {'case_id': 3, 'company_id': 2, 'data': base64.b64encode(raw).decode(),
                                            'mime_type': 'image/png', 'digest': digest}
            with patch('optimization_worker.host_session.worker_records', return_value=[{'claim':claim, 'request_id':ID}]), \
                    patch('optimization_worker.host_session.fence'), patch('optimization_worker.tab_receipt_store.TabReceiptStore') as receipts, \
                    patch('optimization_worker.tab_receipt_supervisor.NativeSupervisor') as supervisor:
                receipts.return_value.resume_index.return_value = 3
                supervisor.return_value.fresh_confirmation.return_value = True
                bridge = build_bridge(ID, client, store, 'journal')
                self.addCleanup(bridge.close)
                bridge.save_directory = directory
                step = bridge.call(ID, 'binding_status', {})['binding']['gallery_step']
                Path(step['save_path']).write_bytes(raw)
                self.assertEqual(bridge.call(ID, 'image_saved', {'identity': identity, 'order': 0}), {'accepted': True})
                self.assertEqual(bridge.call(ID, 'binding_status', {})['binding']['gallery_step'], {'stage': 'finish'})
                self.assertEqual(store.inspect(ID)['collector_pending']['result']['candidates'][0]['images'][0]['artifact_id'], '81')
                with self.assertRaises(ValueError):
                    bridge.call(ID, 'image_saved', {'identity': identity, 'order': 0})
                client.create_research_artifact.assert_called_once()
                client.artifact.assert_called_once_with('81')

    def test_window_blocker_is_fenced_and_reason_restricted(self):
        calls=[]
        bridge=ReturnBridge(ID,lambda:{'task_type':'research.bestseller'},lambda:calls.append('fence'),{'window_blocked':lambda c,d:calls.append(d)})
        self.assertEqual(bridge.call(ID,'window_blocked',{'code':'window_registration_failed'}),{'accepted':True})
        self.assertEqual(calls,['fence',{'code':'window_registration_failed'}])
        with self.assertRaises(ValueError):bridge.call(ID,'window_blocked',{'code':'other'})
    def test_page_blocker_accepts_only_bounded_diagnostic_codes(self):
        captured=[]
        bridge=ReturnBridge(ID,lambda:{'task_type':'research.bestseller'},lambda:None,{'page_blocked':lambda c,d:captured.append(d)})
        diag={'stage':'search','symptom':'results_not_visible','page_type':'search','tab_match':'yes'}
        self.assertEqual(bridge.call(ID,'page_blocked',{'code':'page_unreadable','diagnostic':diag}),{'accepted':True})
        self.assertEqual(captured[0]['diagnostic'],diag)
        with self.assertRaises(ValueError):
            bridge.call(ID,'page_blocked',{'code':'page_unreadable','diagnostic':dict(diag,symptom='secret text')})
        with self.assertRaises(ValueError):
            bridge.call(ID,'page_blocked',{'code':'page_unreadable','diagnostic':dict(diag,url='https://private')})

    def test_staged_submission_requires_timestamp_and_write_guard(self):
        calls=[]
        bridge=ReturnBridge(ID,lambda:{'task_type':'research.bestseller'},lambda:calls.append('fence'),{'submit_staged':lambda c,d:calls.append(d)},write_guard=lambda:calls.append('guard'))
        bridge.call(ID,'submit_staged',{'observed_at':'2026-11-20T00:00:00Z'})
        self.assertEqual(calls,['fence','guard',{'observed_at':'2026-11-20T00:00:00Z'}])
        with self.assertRaises(ValueError):bridge.call(ID,'submit_staged',{'path':'/tmp/secret'})
    def test_prior_lease_status_requires_valid_uuid_and_fence(self):
        old='12345678-1234-4234-8234-123456789abc'; calls=[]
        bridge=ReturnBridge(ID,lambda:{'task_type':'research.bestseller'},lambda:calls.append('fence'),{'prior_lease_status':lambda c,d:{'inactive':True}})
        self.assertEqual(bridge.call(ID,'prior_lease_status',{'request_id':old}),{'accepted':True,'inactive':True})
        with self.assertRaises(ValueError): bridge.call(ID,'prior_lease_status',{'request_id':'not-a-uuid'})
        with self.assertRaises(ValueError): bridge.call(ID,'prior_lease_status',{'request_id':ID})
    def test_binding_status_is_scoped_to_request_and_read_only(self):
        calls=[]
        bridge=ReturnBridge(ID,lambda:{'task_type':'research.bestseller'},lambda:calls.append('fence'),{'binding_status':lambda c,d:{'tab_id':123,'window_id':5,'completed_queries':1,'guard_active':False}})
        self.assertEqual(bridge.call(ID,'binding_status',{}),{'accepted':True,'binding':{'tab_id':123,'window_id':5,'completed_queries':1,'guard_active':False}})
        with self.assertRaises(ValueError): bridge.call('other','binding_status',{})
        with self.assertRaises(ValueError): bridge.call(ID,'binding_status',{'request_id':'other'})
        self.assertEqual(calls,['fence'])
    def test_plan_is_bound_and_fenced(self):
        calls=[]
        bridge=ReturnBridge(ID, lambda: {'task_type':'research.bestseller'}, lambda: calls.append('fence'), {'plan': lambda claim, data: calls.append(data)})
        self.assertEqual(bridge.call(ID,'plan',{'x':1}), {'accepted': True})
        self.assertEqual(calls,['fence',{'x':1}])
        with self.assertRaises(ValueError): bridge.call('other','plan',{})
        with self.assertRaises(ValueError): bridge.call(ID,'inspect',{})

    def test_image_bytes_are_copied_to_private_temporary_file(self):
        from pathlib import Path
        paths=[]
        def send(claim, data):
            path=Path(data['file'])
            paths.append(path)
            self.assertEqual(path.read_bytes(),b'img')
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
        bridge=ReturnBridge(ID, lambda: {'task_type':'research.bestseller'}, lambda: None, {'image':send})
        self.assertEqual(bridge.call(ID,'image',{'file':b'img','screenshot':b'pic','identity':'x','source':'https://a','order':0}), {'accepted':True})
        self.assertFalse(paths[0].exists())

    def test_rejects_unexpected_fields_and_wrong_task(self):
        bridge=ReturnBridge(ID, lambda: {'task_type':'title.generate'}, lambda: None, {'plan': lambda *args: self.fail('should not run')})
        with self.assertRaises(ValueError): bridge.call(ID,'plan',{})
        bridge=ReturnBridge(ID, lambda: {'task_type':'research.bestseller'}, lambda: None, {'image': lambda *args: None})
        with self.assertRaises(ValueError): bridge.call(ID,'image',{'file':'/etc/passwd'})

class FinishSafetyTests(unittest.TestCase):
    def test_empty_scoped_submit_does_not_finish_after_guard_disconnect(self):
        from unittest.mock import patch
        from datetime import datetime, timezone
        from optimization_worker.research_return_bridge import build_bridge
        claim={'task_type':'research.bestseller','lease_epoch':1}
        record={'collector_plan':{'digest':'d','plan':{'queries':['a','b','c']}},'state':'prepared','collector_pending':{}}
        store=type('Store',(),{'inspect':lambda self,request:record})(); client=type('C',(),{'research_ledger_enabled':True})()
        def imported(*args,**kwargs):
            record.update(state='candidate_images_pending',collector_pending={'result':{'candidates':[]},'candidate_uploads':{}}); return 0
        with patch('optimization_worker.host_session.worker_records',return_value=[{'request_id':ID,'claim':claim}]), patch('optimization_worker.host_session.fence'), patch('optimization_worker.tab_receipt_store.TabReceiptStore'), patch('optimization_worker.tab_receipt_supervisor.NativeSupervisor') as supervisor, patch('optimization_worker.collector_cli.import_evidence',side_effect=imported), patch('optimization_worker.candidate_image_upload.finish') as finish:
            supervisor.return_value.fresh_confirmation.side_effect=[True,True,False]
            bridge=build_bridge(ID,client,store,'journal')
            with self.assertRaises(ValueError): bridge.call(ID,'submit',{'evidence':{},'observed_at':datetime.now(timezone.utc).isoformat()})
            finish.assert_not_called(); self.assertEqual(record['state'],'candidate_images_pending')
            bridge.close()
    def test_empty_scoped_submit_runs_guarded_finish_without_another_host_turn(self):
        from unittest.mock import patch
        from datetime import datetime, timezone
        from optimization_worker.research_return_bridge import build_bridge
        claim={'task_type':'research.bestseller','lease_epoch':1}
        record={'collector_plan':{'digest':'d','plan':{'queries':['a','b','c']}},'state':'prepared','collector_pending':{}}
        store=type('Store',(),{'inspect':lambda self,request:record})()
        client=type('Client',(),{'research_ledger_enabled':True})()
        def imported(*args,**kwargs):
            record.update(state='candidate_images_pending',collector_pending={'result':{'candidates':[]},'candidate_uploads':{}})
            return 0
        with patch('optimization_worker.host_session.worker_records',return_value=[{'request_id':ID,'claim':claim}]), patch('optimization_worker.host_session.fence'), patch('optimization_worker.tab_receipt_store.TabReceiptStore'), patch('optimization_worker.tab_receipt_supervisor.NativeSupervisor') as supervisor, patch('optimization_worker.collector_cli.import_evidence',side_effect=imported), patch('optimization_worker.candidate_image_upload.finish',return_value=0) as finish:
            supervisor.return_value.fresh_confirmation.return_value=True
            bridge=build_bridge(ID,client,store,'journal')
            bridge.call(ID,'submit',{'evidence':{},'observed_at':datetime.now(timezone.utc).isoformat()})
            finish.assert_called_once()
            self.assertIs(finish.call_args.kwargs['client'],client)
            bridge.close()
    def test_visual_v2_binding_status_uses_sealed_queries_and_v3_receipts(self):
        from unittest.mock import patch
        from optimization_worker.research_return_bridge import build_bridge
        claim={'task_type':'research.bestseller','lease_epoch':2}
        plan={'strategy':'visual-v2','queries':['a','b','c'],'result_limit':5}
        store=type('Store',(),{'inspect':lambda self,request:{'collector_plan':{'digest':'d','plan':plan}}})()
        with patch('optimization_worker.host_session.worker_records',return_value=[{'request_id':ID,'claim':claim}]), patch('optimization_worker.host_session.fence'), patch('optimization_worker.tab_receipt_store.TabReceiptStore') as ledger, patch('optimization_worker.tab_receipt_supervisor.NativeSupervisor'):
            ledger.return_value.current_binding.return_value={'tab_id':123,'window_id':4,'plan_digest':'d'}
            ledger.return_value.visual_v2_queries.return_value=['a','b','c','d']
            ledger.return_value.resume_index.return_value=3
            ledger.return_value.next_query_index.return_value=3
            ledger.return_value.detail_status.return_value={'saved':0,'qualified':0}
            bridge=build_bridge(ID,object(),store,'journal')
            status=bridge.call(ID,'binding_status',{})['binding']
            self.assertEqual(status['queries'],['a','b','c','d'])
            self.assertEqual(status['completed_queries'],3)
            self.assertEqual(status['saved_query_groups'],3)
            self.assertEqual(ledger.return_value.resume_index.call_args.kwargs,{'visual_v2':True})
            bridge.close()

    def test_old_lease_binding_not_returned_as_current(self):
        from unittest.mock import patch
        from optimization_worker.research_return_bridge import build_bridge
        claim={'task_type':'research.bestseller','lease_epoch':2}
        store=type('Store',(),{'inspect':lambda self,request:{'collector_plan':{'digest':'d','plan':{'queries':['a','b','c']}}}})()
        with patch('optimization_worker.host_session.worker_records',return_value=[{'request_id':ID,'claim':claim}]), patch('optimization_worker.host_session.fence'), patch('optimization_worker.tab_receipt_store.TabReceiptStore') as ledger, patch('optimization_worker.tab_receipt_supervisor.NativeSupervisor'):
            ledger.return_value.current_binding.return_value={'tab_id':123,'window_id':4,'plan_digest':'d'}
            ledger.return_value.resume_index.return_value=0
            bridge=build_bridge(ID,object(),store,'journal')
            status=bridge.call(ID,'binding_status',{})['binding']
            self.assertIsNone(status['tab_id']);self.assertFalse(status['binding_matches_plan'])
            bridge.close()
    def test_status_reports_registered_window_without_claiming_tab_bound(self):
        from unittest.mock import patch
        from optimization_worker.research_return_bridge import build_bridge
        claim={'task_type':'research.bestseller','lease_epoch':1}
        store=type('Store',(),{'inspect':lambda self,request: {'collector_plan':{'digest':'d','plan':{'queries':['a','b','c']}}}})()
        with patch('optimization_worker.host_session.worker_records',return_value=[{'request_id':ID,'claim':claim}]), patch('optimization_worker.host_session.fence'), patch('optimization_worker.tab_receipt_store.TabReceiptStore') as ledger, patch('optimization_worker.tab_receipt_supervisor.NativeSupervisor') as supervisor:
            ledger.return_value.current_binding.return_value=None
            ledger.return_value.resume_index.return_value=0
            supervisor.return_value.window_selection_status.return_value={'registered':True,'window_id':42,'observation_seconds_remaining':10}
            supervisor.return_value.anchor_active.return_value=False
            bridge=build_bridge(ID,object(),store,'journal')
            status=bridge.call(ID,'binding_status',{})['binding']
            self.assertEqual(status['window_selection']['window_id'],42)
            self.assertFalse(status['binding_matches_plan'])
            self.assertTrue(status['anchor_required'])
            self.assertFalse(status['anchor_active'])
            supervisor.return_value.anchor_active.return_value=True
            self.assertTrue(bridge.call(ID,'binding_status',{})['binding']['anchor_active'])
            bridge.close()
    def test_bridge_binds_plan_supervisor_and_verified_receipts(self):
        from unittest.mock import patch
        from optimization_worker.research_return_bridge import build_bridge
        claim={'task_type':'research.bestseller','lease_epoch':1}
        store=type('Store',(),{'inspect':lambda self,request: {'collector_plan':{'digest':'d','plan':{'queries':['a','b','c']}}}})()
        with patch('optimization_worker.host_session.worker_records',return_value=[{'request_id':ID,'claim':claim}]), patch('optimization_worker.host_session.fence'), patch('optimization_worker.collector_cli.bind_plan'), patch('optimization_worker.collector_cli.import_evidence') as submit, patch('optimization_worker.tab_receipt_store.TabReceiptStore') as receipt, patch('optimization_worker.tab_receipt_supervisor.NativeSupervisor') as supervisor:
            receipt.return_value.resume_index.return_value=1
            receipt.return_value.next_query_index.return_value=1
            bridge=build_bridge(ID,object(),store,'journal')
            supervisor.return_value.start.assert_called_once()
            bridge.call(ID,'plan',{})
            supervisor.return_value.start.assert_called_once()
            self.assertEqual(supervisor.call_args.kwargs['start_index'],1)
            from datetime import datetime, timezone
            bridge.call(ID,'submit',{'evidence':{},'observed_at':datetime.now(timezone.utc).isoformat()})
            self.assertIs(submit.call_args.kwargs['tab_receipts'],receipt.return_value)
            bridge.close()
            supervisor.return_value.close.assert_called_once()
    def test_finish_delegates_to_strict_manifest_validation(self):
        from unittest.mock import patch
        from optimization_worker.research_return_bridge import build_bridge
        claim={'task_type':'research.bestseller'}
        with patch('optimization_worker.host_session.worker_records', return_value=[{'request_id': ID, 'claim':claim}]), patch('optimization_worker.host_session.fence'), patch('optimization_worker.candidate_image_upload.finish') as finish, patch('optimization_worker.tab_receipt_store.TabReceiptStore'), patch('optimization_worker.tab_receipt_supervisor.NativeSupervisor') as supervisor:
            store=type('Store',(),{'inspect':lambda self,request: {'collector_plan':{'digest':'d','plan':{'queries':['a','b','c']}}}})()
            supervisor.return_value.fresh_confirmation.return_value=True
            bridge=build_bridge(ID, object(), store, 'journal')
            self.assertEqual(bridge.call(ID,'finish',{}), {'accepted':True})
            self.assertTrue(finish.call_args.kwargs['require_manifest'])

class ReconcileTests(unittest.TestCase):
    def test_bridge_delegates_to_original_intent_reconciliation(self):
        from unittest.mock import patch
        from optimization_worker.research_return_bridge import build_bridge
        claim={'task_type':'research.bestseller'}
        with patch('optimization_worker.host_session.worker_records', return_value=[{'request_id':ID,'claim':claim}]), patch('optimization_worker.host_session.fence'), patch('optimization_worker.candidate_image_upload.reconcile') as image, patch('optimization_worker.collector_cli.reconcile_evidence') as evidence:
            bridge=build_bridge(ID,object(),object(),'journal')
            self.assertEqual(bridge.call(ID,'reconcile_image',{'identity':'shein-us:1','artifact_id':'12','order':0}), {'accepted':True})
            self.assertEqual(bridge.call(ID,'reconcile_evidence',{'artifact_id':'13'}), {'accepted':True})
            self.assertEqual(image.call_args.args[4], '12')
            self.assertEqual(evidence.call_args.args[3], '13')

class BrowserGuardTests(unittest.TestCase):
    def test_write_actions_require_live_guard_but_reconciliation_allowed(self):
        from unittest.mock import patch
        from optimization_worker.research_return_bridge import build_bridge
        claim={'task_type':'research.bestseller','lease_epoch':1}
        store=type('Store',(),{'inspect':lambda self,request: {'collector_plan':{'digest':'d','plan':{'queries':['a','b','c']}}}})()
        with patch('optimization_worker.host_session.worker_records',return_value=[{'request_id':ID,'claim':claim}]), patch('optimization_worker.host_session.fence'), patch('optimization_worker.tab_receipt_store.TabReceiptStore'), patch('optimization_worker.tab_receipt_supervisor.NativeSupervisor') as supervisor, patch('optimization_worker.candidate_image_upload.bind_gallery') as manifest, patch('optimization_worker.candidate_image_upload.send') as image, patch('optimization_worker.candidate_image_upload.finish') as finish, patch('optimization_worker.candidate_image_upload.reconcile') as reconcile:
            supervisor.return_value.fresh_confirmation.return_value=False
            bridge=build_bridge(ID,object(),store,'journal')
            for op,data in [('manifest',{'identity':'x','manifest':{},'screenshot':b'x'}),('image',{'identity':'x','file':b'x','screenshot':b'x','source':'x','order':0}),('finish',{})]:
                with self.assertRaises(ValueError): bridge.call(ID,op,data)
            manifest.assert_not_called();image.assert_not_called();finish.assert_not_called()
            bridge.call(ID,'reconcile_image',{'identity':'x','artifact_id':'12','order':0});reconcile.assert_called_once()
    def test_unknown_upload_requires_reconciliation_before_new_writes(self):
        from unittest.mock import patch
        from optimization_worker.research_return_bridge import build_bridge
        claim={'task_type':'research.bestseller','lease_epoch':1}
        record={'collector_plan':{'digest':'d','plan':{'queries':['a','b','c']}},'state':'candidate_images_pending','collector_pending':{'candidate_uploads':{'x':{'state':'unknown'}}}}
        store=type('Store',(),{'inspect':lambda self,request:record})()
        with patch('optimization_worker.host_session.worker_records',return_value=[{'request_id':ID,'claim':claim}]), patch('optimization_worker.host_session.fence'), patch('optimization_worker.tab_receipt_store.TabReceiptStore'), patch('optimization_worker.tab_receipt_supervisor.NativeSupervisor') as supervisor, patch('optimization_worker.candidate_image_upload.finish') as finish, patch('optimization_worker.candidate_image_upload.reconcile') as reconcile:
            supervisor.return_value.fresh_confirmation.return_value=True
            bridge=build_bridge(ID,object(),store,'journal')
            with self.assertRaises(ValueError): bridge.call(ID,'finish',{})
            finish.assert_not_called()
            bridge.call(ID,'reconcile_image',{'identity':'x','artifact_id':'12','order':0});reconcile.assert_called_once()
    def test_reconcile_restarts_failed_supervisor_without_writing_again(self):
        from unittest.mock import patch,Mock
        from optimization_worker.research_return_bridge import build_bridge
        claim={'task_type':'research.bestseller','lease_epoch':1}
        record={'collector_plan':{'digest':'d','plan':{'queries':['a','b','c']}},'state':'collector_upload_unknown','collector_pending':{}}
        store=type('Store',(),{'inspect':lambda self,request:record})()
        failed=Mock();failed.error='ValueError';replacement=Mock();replacement.error=None
        def verified(*args): record.update(state='candidate_images_pending',collector_pending={'result':{}})
        with patch('optimization_worker.host_session.worker_records',return_value=[{'request_id':ID,'claim':claim}]), patch('optimization_worker.host_session.fence'), patch('optimization_worker.tab_receipt_store.TabReceiptStore'), patch('optimization_worker.tab_receipt_supervisor.NativeSupervisor',side_effect=[failed,replacement]) as supervisors, patch('optimization_worker.collector_cli.reconcile_evidence',side_effect=verified) as reconcile, patch('optimization_worker.collector_cli.import_evidence') as upload:
            bridge=build_bridge(ID,object(),store,'journal')
            bridge.call(ID,'reconcile_evidence',{'artifact_id':'12'})
            reconcile.assert_called_once();upload.assert_not_called();failed.close.assert_called_once();replacement.start.assert_called_once();self.assertEqual(supervisors.call_count,2)
    def test_image_reconcile_waits_for_all_unknown_then_restarts(self):
        from unittest.mock import patch,Mock
        from optimization_worker.research_return_bridge import build_bridge
        claim={'task_type':'research.bestseller','lease_epoch':1}
        uploads={'one':{'state':'unknown'},'two':{'state':'unknown'}}
        record={'collector_plan':{'digest':'d','plan':{'queries':['a','b','c']}},'state':'candidate_images_pending','collector_pending':{'result':{},'candidate_uploads':uploads}}
        store=type('Store',(),{'inspect':lambda self,request:record})()
        failed=Mock();replacement=Mock()
        def verified(store,request,claim,identity,*args,**kwargs): uploads[identity]['state']='confirmed'
        with patch('optimization_worker.host_session.worker_records',return_value=[{'request_id':ID,'claim':claim}]), patch('optimization_worker.host_session.fence'), patch('optimization_worker.tab_receipt_store.TabReceiptStore'), patch('optimization_worker.tab_receipt_supervisor.NativeSupervisor',side_effect=[failed,replacement]), patch('optimization_worker.candidate_image_upload.reconcile',side_effect=verified), patch('optimization_worker.candidate_image_upload.send') as upload:
            bridge=build_bridge(ID,object(),store,'journal')
            bridge.call(ID,'reconcile_image',{'identity':'one','artifact_id':'12','order':0});failed.close.assert_not_called()
            bridge.call(ID,'reconcile_image',{'identity':'two','artifact_id':'13','order':0});failed.close.assert_called_once();replacement.start.assert_called_once();upload.assert_not_called()

from datetime import timedelta
import base64
import hashlib
from unittest.mock import patch
from odoo import fields
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import AccessError, ValidationError


@tagged('post_install', '-at_install')
class OptimizationQueueCase(TransactionCase):
    def test_image_skip_requires_resolved_request_and_is_idempotent(self):
        ui = self.env['meli.optimization.case']
        detail = ui.workbench_create({'shop':'test','item_id':'SKIP-IMAGE','title':'Test'})
        case = ui.browse(detail['id'])
        snapshot = self.env['meli.optimization.snapshot'].browse(detail['snapshot']['id'])
        task = self.env['meli.optimization.task'].create({'case_id':case.id,'snapshot_id':snapshot.id,'task_type':'product.facts',
            'payload':{'schema_version':'1','product_snapshot_id':str(snapshot.id)}})
        task._set({'task_type':'image.generate'})
        claim = task.claim(['image.generate'])
        token, epoch = claim['lease_token'], claim['lease_epoch']
        task.checkpoint(token, epoch, 'intent', {'request_id':'skip-test','state':'unknown'})
        from ..domain import canonical_hash
        data = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC')
        image = self.env['meli.optimization.artifact'].create({'case_id':case.id,'data':base64.b64encode(data),'mime_type':'image/png','digest':hashlib.sha256(data).hexdigest()})
        plan_payload = {'case_id':case.id,'snapshot_id':snapshot.id,'source_artifact_ids':[str(image.id)],'target_artifact_ids':[str(image.id)]}
        plan = self.env['meli.optimization.artifact'].create({'case_id':case.id,'mime_type':'application/json','payload':plan_payload,'digest':canonical_hash(plan_payload)})
        task._set({'payload':{'schema_version':'1','generation_plan_id':str(plan.id),'target_artifact_id':str(image.id),'source_artifact_ids':[str(image.id)]}})
        evidence={'input_hash':canonical_hash(task.payload), 'snapshot_id':snapshot.id, 'generation_plan_id':str(plan.id),
                  'target_artifact_id':str(image.id),'source_artifact_ids':[str(image.id)],'artifact_digests':{str(image.id):image.digest},
                  'regions':[{'index':0,'decision':'unsupported','source_ids':[],'detail':'No back view'}],'texts':[]}
        body = {'code':'reference_insufficient','message':'缺少背面参考图','request_id':'skip-test','evidence':evidence}
        with self.assertRaises(ValidationError):
            task.skip(token, epoch, 'skip', body)
        task.checkpoint(token, epoch, 'no-dispatch', {'request_id':'skip-test','state':'failed'})
        with self.assertRaises(ValidationError):
            task.skip(token, epoch, 'wrong-scope', dict(body, evidence=dict(evidence, snapshot_id=snapshot.id+1)))
        with self.assertRaises(ValidationError):
            task.skip(token, epoch, 'wrong-image', dict(body, evidence=dict(evidence, artifact_digests={str(image.id):'0'*64})))
        receipt = task.skip(token, epoch, 'skip', body)
        self.assertEqual(receipt['state'], 'skipped')
        self.assertEqual(task.skip(token, epoch, 'skip', body), receipt)
        self.assertFalse(task.lease_token)
        self.assertFalse(task.lease_expires_at)

    def test_expired_image_regeneration_is_fenced_and_idempotent(self):
        ui = self.env['meli.optimization.case']
        detail = ui.workbench_create({'shop':'test','item_id':'RETRY-IMAGE','title':'Test'})
        case = ui.browse(detail['id']); snap = self.env['meli.optimization.snapshot'].browse(detail['snapshot']['id'])
        task = self.env['meli.optimization.task'].create({'case_id':case.id,'snapshot_id':snap.id,'task_type':'product.facts',
            'payload':{'schema_version':'1','product_snapshot_id':str(snap.id)}})
        task._set({'task_type':'image.generate'})
        claim=task.claim(['image.generate']); token,epoch=claim['lease_token'],claim['lease_epoch']
        task.checkpoint(token,epoch,'intent',{'request_id':'old-image','state':'unknown'})
        with self.assertRaises(ValidationError): ui.workbench_regenerate_expired_image(case.id,task.id,epoch)
        task._set({'lease_expires_at':fields.Datetime.now()-timedelta(seconds=1)})
        with self.assertRaises(ValidationError): ui.workbench_regenerate_expired_image(case.id,task.id,epoch+1)
        receipt=ui.workbench_regenerate_expired_image(case.id,task.id,epoch)
        self.assertEqual(receipt['state'],'queued')
        self.assertEqual(ui.workbench_regenerate_expired_image(case.id,task.id,epoch),receipt)
        self.assertTrue(task.request_ids.filtered(lambda r:r.request_id=='old-image').retry_superseded)
        self.assertEqual(task.state,'queued')
        with self.assertRaises(ValidationError): task.checkpoint(token,epoch,'old-result',{'request_id':'old-image','state':'succeeded'})
        other = self.env['meli.optimization.task'].create({'case_id':case.id,'snapshot_id':snap.id,'task_type':'product.facts',
            'payload':{'schema_version':'1','product_snapshot_id':str(snap.id)}})
        other._set({'task_type':'image.generate'})
        claim2=other.claim(['image.generate'])
        other._set({'lease_expires_at':fields.Datetime.now()-timedelta(seconds=1)})
        self.assertEqual(ui.workbench_regenerate_expired_image(case.id,other.id,claim2['lease_epoch'])['state'],'queued')
        self.assertEqual(other.state,'queued')

    def test_review_decision_is_bound_audited_and_idempotent(self):
        from ..domain import canonical_hash
        ui = self.env['meli.optimization.case']
        detail = ui.workbench_create({'shop':'test','item_id':'REVIEW-DECISION','title':'Test'})
        case = ui.browse(detail['id']); snap = self.env['meli.optimization.snapshot'].browse(detail['snapshot']['id'])
        data = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC')
        image = self.env['meli.optimization.artifact'].create({'case_id':case.id,'data':base64.b64encode(data),'mime_type':'image/png','digest':hashlib.sha256(data).hexdigest()})
        plan_payload = {'case_id':case.id,'snapshot_id':snap.id,'source_artifact_ids':[str(image.id)],'target_artifact_ids':[str(image.id)]}
        plan = self.env['meli.optimization.artifact'].create({'case_id':case.id,'mime_type':'application/json','payload':plan_payload,'digest':canonical_hash(plan_payload)})
        task = self.env['meli.optimization.task'].create({'case_id':case.id,'snapshot_id':snap.id,'task_type':'image.generate',
            'payload':{'schema_version':'1','source_artifact_ids':[str(image.id)],'target_artifact_id':str(image.id),'generation_plan_id':str(plan.id)}})
        claim=task.claim(['image.generate']); token,epoch=claim['lease_token'],claim['lease_epoch']
        task.checkpoint(token,epoch,'intent',{'request_id':'original','state':'unknown'})
        task.host_progress(token,epoch,'original','review_required',review_seq=1)
        evidence = {'request_id':'original','review_seq':1,'candidate_artifact_id':image.id,'candidate_sha256':image.digest,
                    'candidate_attempt_id':'a'*32,'candidate_attempt_number':1,'report':{
                      'quality_binding':{'request_id':'original','candidate_sha256':image.digest},
                      'checks':[{'name':name,'status':'failed','evidence':'fixture'} for name in
                                ('garment_construction','color_material','garment_details','identity_pose','accessories','scene_lighting_framing','text_layout')],
                      'skill_report':{'decision':'reject'}}}
        task.host_review_evidence(token,epoch,evidence)
        for seq,digest,report_hash in ((2,image.digest,canonical_hash(evidence['report'])),
                                       (1,'0'*64,canonical_hash(evidence['report'])),
                                       (1,image.digest,'0'*64)):
            with self.assertRaises(ValidationError):
                ui.workbench_image_review_decide(case.id,task.id,'manual_accept',seq,digest,report_hash,'','d'*32)
        args=[case.id,task.id,'manual_accept',1,image.digest,canonical_hash(evidence['report']),'accepted','f'*32]
        receipt = ui.workbench_image_review_decide(*args)
        self.assertEqual(ui.workbench_image_review_decide(*args), receipt)
        self.assertEqual(task.status_detail['code'],'image_decision_requested')
        event = self.env['meli.optimization.event'].browse(receipt['event_id'])
        self.assertEqual(event.payload['action'],'manual_accept')
        self.assertEqual(event.payload['candidate_sha256'],image.digest)
        result = {'request_id':'original','image':{'artifact_id':str(image.id),'sha256':image.digest},'visual_qc':{
            'status':'manual_accepted','candidate_sha256':image.digest,'report':evidence['report'],'decision_event_id':event.id}}
        task._validate_manual_review_decision(result)
        forged = dict(result, visual_qc=dict(result['visual_qc'], decision_event_id=999999))
        with self.assertRaises(ValidationError): task._validate_manual_review_decision(forged)
        forged = dict(result, visual_qc=dict(result['visual_qc'], report={'decision':'accept'}))
        with self.assertRaises(ValidationError): task._validate_manual_review_decision(forged)
        with self.assertRaises(ValidationError):
            ui.workbench_image_review_decide(case.id,task.id,'regenerate',1,image.digest,canonical_hash(evidence['report']),'','e'*32)
        task.host_progress(token,epoch,'original','resumed','f'*32,1)
        task.checkpoint(token,epoch,'finished',{'request_id':'original','state':'succeeded'})
        full = {'schema_version':'1','request_id':'original',
                'evidence':[{'id':'host','source':'fixture','observed_at':'2026-11-24T00:00:00Z'}],
                'image':{'image_id':'candidate','order':0,'role':'generated','artifact_id':str(image.id),'source':'fixture',
                         'evidence_ids':['host'],'mime_type':'image/png','width':1,'height':1,'sha256':image.digest},
                'visual_qc':result['visual_qc']}
        task.complete(token,epoch,'complete',full)
        proposal = ui.workbench_propose(case.id,task.id,canonical_hash(full),'images',[0])
        self.assertTrue(ui.workbench_confirm(case.id,proposal['id'],proposal['digest']))
        other = self.env['meli.optimization.task'].create({'case_id':case.id,'snapshot_id':snap.id,'task_type':'image.generate',
            'payload':task.payload})
        claim2=other.claim(['image.generate']); t2,e2=claim2['lease_token'],claim2['lease_epoch']
        other.checkpoint(t2,e2,'intent2',{'request_id':'original-2','state':'unknown'})
        other.host_progress(t2,e2,'original-2','review_required',review_seq=1)
        next_evidence=dict(evidence, request_id='original-2', report=dict(evidence['report'],quality_binding={
            'request_id':'original-2','candidate_sha256':image.digest}))
        other.host_review_evidence(t2,e2,next_evidence)
        ui.workbench_image_review_decide(case.id,other.id,'regenerate',1,image.digest,canonical_hash(next_evidence['report']),'','e'*32)
        other.host_progress(t2,e2,'original-2','resumed','e'*32,1)
        other.host_progress(t2,e2,'original-2','review_required',review_seq=2)
        self.assertEqual(other.status_detail['review_seq'],2)

    def test_window_registration_failure_can_resume_original_task(self):
        batch = self.env['meli.optimization.batch'].create({'name': 'window recovery'})
        case = self.env['meli.optimization.case'].create({'name': 'window recovery', 'batch_id': batch.id})
        snap = self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {}})
        task = self.env['meli.optimization.task'].create({'case_id': case.id, 'snapshot_id': snap.id, 'task_type': 'product.facts', 'payload': {'schema_version': '1', 'product_snapshot_id': str(snap.id)}})
        task._set({'task_type': 'research.bestseller'}); task.prepare_scoped_research()
        groups = [(6, 0, [self.env.ref('meli_optimization.group_research_worker').id])]
        user = self.env['res.users'].create({'name': 'Window worker', 'login': 'window-recovery-worker', 'company_id': case.company_id.id, 'company_ids': [(6, 0, case.company_id.ids)], 'group_ids': groups})
        worker = task.with_user(user); claim = worker.claim(['research.bestseller'])
        token, epoch = claim['lease_token'], claim['lease_epoch']
        worker.checkpoint(token, epoch, 'window-original', {'request_id': 'window-original', 'state': 'unknown'})
        worker.checkpoint(token, epoch, 'window-failed', {'request_id': 'window-original', 'state': 'failed'})
        worker.wait(token, epoch, 'window-wait', {'code': 'window_registration_failed', 'message': 'Window unavailable'})
        self.assertEqual(task.state, 'waiting')
        self.assertEqual(task.resume()['state'], 'queued')
        recovered = worker.claim(['research.bestseller'])
        self.assertEqual(recovered['id'], task.id)
        self.assertGreater(recovered['lease_epoch'], epoch)
        # A prior resume authorization cannot permit another epoch to be claimed.
        task._set({'state': 'queued', 'lease_token': False, 'lease_expires_at': False, 'worker_uid': False})
        self.assertFalse(worker.claim(['research.bestseller']))

    def test_research_progress_requires_current_lease_and_is_monotonic(self):
        batch=self.env['meli.optimization.batch'].create({'name':'Progress'})
        case=self.env['meli.optimization.case'].create({'name':'Progress','batch_id':batch.id})
        snap=self.env['meli.optimization.snapshot'].create({'case_id':case.id,'payload':{}})
        task=self.env['meli.optimization.task'].create({'case_id':case.id,'snapshot_id':snap.id,'task_type':'product.facts','payload':{'schema_version':'1','product_snapshot_id':str(snap.id)}})
        task._set({'task_type':'research.bestseller'}); task.prepare_scoped_research()
        user=self.env['res.users'].create({'name':'Progress worker','login':'progress-worker','company_id':self.env.company.id,'company_ids':[(6,0,self.env.company.ids)],'group_ids':[(6,0,[self.env.ref('meli_optimization.group_research_worker').id])]})
        worker=task.with_user(user)
        claim=worker.claim(['research.bestseller']); token,epoch=claim['lease_token'],claim['lease_epoch']
        worker.checkpoint(token,epoch,'initial',{'request_id':'progress-request','state':'unknown'})
        p={'stage':'single','queries':3,'reviewed':4,'qualified':1,'target':15}
        worker.research_progress(token,epoch,'progress-request',p)
        self.assertIn('4',task.status_detail['message'])
        with self.assertRaises(ValidationError):worker.research_progress(token,epoch,'progress-request',dict(p,reviewed=3))
        with self.assertRaises(ValidationError):worker.research_progress(token,epoch,'other',p)
        with self.assertRaises(ValidationError):worker.research_progress(token,epoch,'progress-request',dict(p,qualified=5))

    def test_visual_v2_progress_accepts_nine_queries_and_over_sixty_candidates(self):
        batch=self.env['meli.optimization.batch'].create({'name':'Visual progress'})
        case=self.env['meli.optimization.case'].create({'name':'Visual progress','batch_id':batch.id})
        snap=self.env['meli.optimization.snapshot'].create({'case_id':case.id,'payload':{'collection_config':{'strategy':'visual-v2','result_limit':15}}})
        task=self.env['meli.optimization.task'].create({'case_id':case.id,'snapshot_id':snap.id,'task_type':'product.facts','payload':{'schema_version':'1','product_snapshot_id':str(snap.id)}})
        task._set({'task_type':'research.bestseller'}); task.prepare_scoped_research()
        user=self.env['res.users'].create({'name':'Visual progress worker','login':'visual-progress-worker','company_id':self.env.company.id,'company_ids':[(6,0,self.env.company.ids)],'group_ids':[(6,0,[self.env.ref('meli_optimization.group_research_worker').id])]})
        worker=task.with_user(user);claim=worker.claim(['research.bestseller']);token,epoch=claim['lease_token'],claim['lease_epoch']
        worker.checkpoint(token,epoch,'initial',{'request_id':'visual-progress-request','state':'unknown'})
        p={'stage':'visual','queries':9,'reviewed':100,'qualified':1,'target':15}
        worker.research_progress(token,epoch,'visual-progress-request',p)
        self.assertIn('100',task.status_detail['message'])
        with self.assertRaises(ValidationError):worker.research_progress(token,epoch,'visual-progress-request',dict(p,queries=10))

    def test_card_v3_progress_keeps_supplemental_separate(self):
        batch=self.env['meli.optimization.batch'].create({'name':'Card progress'})
        case=self.env['meli.optimization.case'].create({'name':'Card progress','batch_id':batch.id})
        snap=self.env['meli.optimization.snapshot'].create({'case_id':case.id,'payload':{'collection_config':{'strategy':'card-visual-v3','result_limit':5}}})
        task=self.env['meli.optimization.task'].create({'case_id':case.id,'snapshot_id':snap.id,'task_type':'product.facts','payload':{'schema_version':'1','product_snapshot_id':str(snap.id)}})
        task._set({'task_type':'research.bestseller'});task.prepare_scoped_research()
        user=self.env['res.users'].create({'name':'Card progress worker','login':'card-progress-worker','company_id':self.env.company.id,'company_ids':[(6,0,self.env.company.ids)],'group_ids':[(6,0,[self.env.ref('meli_optimization.group_research_worker').id])]})
        worker=task.with_user(user);claim=worker.claim(['research.bestseller']);token,epoch=claim['lease_token'],claim['lease_epoch']
        worker.checkpoint(token,epoch,'initial',{'request_id':'card-progress-request','state':'unknown'})
        detail={'known_products':110,'completed_pages':[3,2,1],'selected_per_query':[30,None,None],
                'threshold_met':4,'supplemental':2,'excluded':1,'visual_required':6,'visual_done':3,
                'visual_threshold_passed':2,'visual_supplemental_passed':1}
        p={'stage':'visual','queries':1,'reviewed':7,'qualified':2,'target':5,'card_v3':detail}
        worker.research_progress(token,epoch,'card-progress-request',p)
        self.assertEqual(task.status_detail['card_v3']['supplemental'],2)
        with self.assertRaises(ValidationError):worker.research_progress(token,epoch,'card-progress-request',dict(p,card_v3=dict(detail,completed_pages=[4])))

    def test_window_resume_requires_original_failed_scoped_request(self):
        groups = [(6, 0, [self.env.ref('meli_optimization.group_research_worker').id])]
        user = self.env['res.users'].create({'name': 'Window negative worker', 'login': 'window-negative-worker', 'company_id': self.env.company.id, 'company_ids': [(6, 0, self.env.company.ids)], 'group_ids': groups})
        for mode in ('missing', 'wrong_epoch', 'unknown'):
            with self.subTest(mode=mode):
                batch = self.env['meli.optimization.batch'].create({'name': mode})
                case = self.env['meli.optimization.case'].create({'name': mode, 'batch_id': batch.id})
                snap = self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {}})
                task = self.env['meli.optimization.task'].create({'case_id': case.id, 'snapshot_id': snap.id, 'task_type': 'product.facts', 'payload': {'schema_version': '1', 'product_snapshot_id': str(snap.id)}})
                task._set({'task_type': 'research.bestseller'}); task.prepare_scoped_research()
                worker = task.with_user(user); claim = worker.claim(['research.bestseller']); token, epoch = claim['lease_token'], claim['lease_epoch']
                if mode != 'missing':
                    worker.checkpoint(token, epoch, 'initial-' + mode, {'request_id': 'request-' + mode, 'state': 'unknown'})
                if mode == 'wrong_epoch':
                    worker.checkpoint(token, epoch, 'failed-' + mode, {'request_id': 'request-' + mode, 'state': 'failed'})
                    registration = self.env['meli.optimization.event'].sudo().search([('task_id', '=', task.id), ('kind', '=', 'scoped_request_registration')])
                    registration._set({'payload': {'request_id': 'request-' + mode, 'initial_epoch': epoch + 1}})
                worker.wait(token, epoch, 'wait-' + mode, {'code': 'window_registration_failed', 'message': 'Window unavailable'})
                if mode == 'unknown':
                    task.resume()  # Existing unknown-result reconciliation remains in force.
                    self.assertFalse(self.env['meli.optimization.event'].sudo().search([('task_id', '=', task.id), ('kind', '=', 'resume')]))
                else:
                    with self.assertRaises(ValidationError): task.resume()

    def test_scoped_resume_preflight_requires_original_owner_and_reconciled_request(self):
        batch = self.env['meli.optimization.batch'].create({'name': 'resume scope'})
        case = self.env['meli.optimization.case'].create({'name': 'resume scope', 'batch_id': batch.id})
        snap = self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {}})
        task = self.env['meli.optimization.task'].create({'case_id': case.id, 'snapshot_id': snap.id, 'task_type': 'product.facts', 'payload': {'schema_version':'1','product_snapshot_id':str(snap.id)}})
        task._set({'task_type':'research.bestseller'}); task.prepare_scoped_research()
        groups = [(6, 0, [self.env.ref('meli_optimization.group_research_worker').id])]
        first = self.env['res.users'].create({'name':'Resume first','login':'resume-scope-first','company_id':case.company_id.id,'company_ids':[(6,0,case.company_id.ids)],'group_ids':groups})
        second = self.env['res.users'].create({'name':'Resume second','login':'resume-scope-second','company_id':case.company_id.id,'company_ids':[(6,0,case.company_id.ids)],'group_ids':groups})
        worker = task.with_user(first); claim = worker.claim(['research.bestseller']); token, epoch = claim['lease_token'], claim['lease_epoch']
        worker.checkpoint(token, epoch, 'initial', {'request_id':'original','state':'unknown'})
        registration = self.env['meli.optimization.event'].search([('task_id','=',task.id),('kind','=','scoped_request_registration')])
        self.assertEqual(len(registration), 1)
        self.assertEqual(registration.payload['request_id'], 'original')
        self.assertEqual(registration.receipt['coverage'], 'unknown')
        with self.assertRaises(ValidationError): worker.scoped_resume_preflight(token, epoch, 'original')
        task._set({'lease_expires_at': fields.Datetime.now() - timedelta(seconds=1)})
        with self.assertRaises(ValidationError): worker.recover_lease('original', token, epoch, '1234567890abcdef1234567890abcdef')
        task._set({'lease_expires_at': fields.Datetime.now() + timedelta(seconds=120)})
        with self.assertRaises(AccessError): task.with_user(second).scoped_resume_preflight(token, epoch, 'original')
        worker.checkpoint(token, epoch, 'reconciled', {'request_id':'original','state':'succeeded'})
        with self.assertRaises(ValidationError): worker.scoped_resume_preflight(token, epoch, 'original')
        worker.checkpoint(token, epoch, 'known', {'request_id':'known','state':'succeeded'})
        self.assertEqual(worker.scoped_resume_preflight(token, epoch, 'known')['coverage'], 'unknown')
        with self.assertRaises(ValidationError): worker.scoped_resume_preflight(token, epoch, 'other')
    def test_scoped_research_requires_fresh_explicit_marker_and_excludes_shared_worker(self):
        batch = self.env['meli.optimization.batch'].create({'name': 'scope fresh'})
        case = self.env['meli.optimization.case'].create({'name': 'scope fresh', 'batch_id': batch.id})
        snap = self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {}})
        def new_task():
            item = self.env['meli.optimization.task'].create({'case_id': case.id, 'snapshot_id': snap.id, 'task_type': 'product.facts', 'payload': {'schema_version': '1', 'product_snapshot_id': str(snap.id)}})
            item._set({'task_type': 'research.bestseller'})
            return item
        task, legacy = new_task(), new_task()
        scoped = self.env['res.users'].create({'name':'Scoped fresh', 'login':'scoped-fresh', 'company_id':case.company_id.id, 'company_ids':[(6,0,case.company_id.ids)], 'group_ids':[(6,0,[self.env.ref('meli_optimization.group_research_worker').id])]})
        shared = self.env['res.users'].create({'name':'Shared fresh', 'login':'shared-fresh', 'company_id':case.company_id.id, 'company_ids':[(6,0,case.company_id.ids)], 'group_ids':[(6,0,[self.env.ref('meli_optimization.group_worker').id])]})
        self.assertFalse(legacy.with_user(scoped).claim(['research.bestseller']))
        task.prepare_scoped_research()
        self.assertFalse(task.with_user(shared).claim(['research.bestseller']))
        claimed = task.with_user(scoped).claim(['research.bestseller'])
        self.assertEqual(claimed['id'], task.id)
        self.assertEqual(task.scoped_coverage, 'unknown')
        with self.assertRaises(ValidationError): task.prepare_scoped_research()
        task._set({'lease_expires_at': fields.Datetime.now() - timedelta(seconds=1)})
        self.assertFalse(task.with_user(scoped).claim(['research.bestseller']))
    def test_research_only_worker_cannot_use_legacy_artifact_create(self):
        batch = self.env['meli.optimization.batch'].create({'name': 'scope'})
        case = self.env['meli.optimization.case'].create({'name': 'scope', 'batch_id': batch.id})
        user = self.env['res.users'].create({'name': 'Scoped Research', 'login': 'scope-research-only', 'company_id': case.company_id.id, 'company_ids': [(6, 0, case.company_id.ids)], 'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_research_worker').id])]})
        with self.assertRaises(AccessError):
            self.env['meli.optimization.artifact'].with_user(user).create({'case_id': case.id, 'payload': {}, 'digest': hashlib.sha256(b'{}').hexdigest()})
        snap = self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {}})
        task = self.env['meli.optimization.task'].create({'case_id': case.id, 'snapshot_id': snap.id, 'task_type': 'product.facts', 'payload': {'schema_version': '1', 'product_snapshot_id': str(snap.id)}})
        task._set({'task_type': 'research.bestseller'})
        task.prepare_scoped_research()
        task = task.with_user(user)
        claim = task.claim(['research.bestseller'])
        task.checkpoint(claim['lease_token'], claim['lease_epoch'], 'request', {'request_id': 'r', 'state': 'unknown'})
        artifact = {'payload': {}, 'digest': hashlib.sha256(b'{}').hexdigest()}
        result = task.research_artifact(claim['lease_token'], claim['lease_epoch'], 'r', 'i', artifact)
        self.assertTrue(result['artifact_id'])
        with self.assertRaises(ValidationError): task.seal_scoped_manifest(claim['lease_token'], claim['lease_epoch'], 'r', ['missing'])
        sealed = task.seal_scoped_manifest(claim['lease_token'], claim['lease_epoch'], 'r', ['i'])
        self.assertEqual(sealed['coverage'], 'unknown')
        task.checkpoint(claim['lease_token'], claim['lease_epoch'], 'success', {'request_id':'r','state':'succeeded'})
        self.assertEqual(task.seal_scoped_manifest(claim['lease_token'], claim['lease_epoch'], 'r', ['i']), sealed)
        with self.assertRaises(ValidationError): task.seal_scoped_manifest(claim['lease_token'], claim['lease_epoch'], 'r', ['i', 'missing'])
    def test_research_artifact_intent_is_atomic_and_query_never_claims_coverage(self):
        batch = self.env['meli.optimization.batch'].create({'name': 'intent'})
        case = self.env['meli.optimization.case'].create({'name': 'item', 'batch_id': batch.id})
        snap = self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {}})
        task = self.env['meli.optimization.task'].create({'case_id': case.id, 'snapshot_id': snap.id, 'task_type': 'product.facts', 'payload': {'schema_version': '1', 'product_snapshot_id': str(snap.id)}})
        task._set({'task_type': 'research.bestseller'})
        claim = task.claim(['research.bestseller'])
        token, epoch = claim['lease_token'], claim['lease_epoch']
        task.checkpoint(token, epoch, 'checkpoint', {'request_id': 'request-1', 'state': 'unknown'})
        raw = b'image'; artifact = {'data': base64.b64encode(raw).decode(), 'digest': hashlib.sha256(raw).hexdigest(), 'mime_type': 'image/png'}
        first = task.research_artifact(token, epoch, 'request-1', 'intent-1', artifact)
        self.assertEqual(task.research_artifact(token, epoch, 'request-1', 'intent-1', artifact), first)
        self.assertEqual(task.research_intent_query(token, epoch, 'request-1', 'intent-1')['artifact_id'], first['artifact_id'])
        self.assertEqual(task.research_intent_query(token, epoch, 'request-1', 'missing')['coverage'], 'unknown')
        with self.assertRaises(ValidationError): task.research_artifact(token, epoch, 'request-1', 'intent-1', dict(artifact, mime_type='image/jpeg'))
        with self.assertRaises(ValidationError): task.research_artifact('bad', epoch, 'request-1', 'intent-2', artifact)
        self.assertEqual(task.research_intent_query(token, epoch, 'other-request', 'intent-1')['coverage'], 'unknown')
        self.assertIsNone(task.research_intent_query(token, epoch, 'other-request', 'intent-1')['artifact_id'])
        count = self.env['meli.optimization.artifact'].search_count([('case_id', '=', case.id)])
        events = self.env['meli.optimization.event']
        with self.assertRaises(RuntimeError):
            with self.env.cr.savepoint():
                with patch.object(type(events), '_append', side_effect=RuntimeError('event write failed')):
                    task.research_artifact(token, epoch, 'request-1', 'intent-failed', artifact)
        self.assertEqual(self.env['meli.optimization.artifact'].search_count([('case_id', '=', case.id)]), count)
        self.assertIsNone(task.research_intent_query(token, epoch, 'request-1', 'intent-failed')['artifact_id'])
        task.wait(token, epoch, 'test-wait', {'code': 'external_result_unknown', 'message': 'Awaiting reconciliation'})
        self.assertFalse(task.lease_token)
        before = task.lease_epoch
        result = task.research_intent_reconcile('request-1', 'intent-1')
        self.assertEqual(result['artifact_id'], first['artifact_id'])
        self.assertEqual(result['coverage'], 'unknown')
        self.assertEqual(task.lease_epoch, before)
        self.assertIsNone(task.research_intent_reconcile('request-1', 'absent')['artifact_id'])
        with self.assertRaises(ValidationError): task.research_intent_reconcile('other-request', 'intent-1')
        with self.assertRaises(AccessError): task.with_user(self.env.ref('base.public_user')).research_intent_reconcile('request-1', 'intent-1')
        worker = self.env['res.users'].create({'name': 'Reconcile Worker', 'login': 'reconcile-worker', 'company_id': case.company_id.id, 'company_ids': [(6, 0, case.company_id.ids)], 'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        with self.assertRaises(AccessError): task.with_user(worker).research_intent_reconcile('request-1', 'intent-1')
        other = self.env['res.company'].create({'name': 'Other Reconcile Company'})
        editor = self.env['res.users'].create({'name': 'Other Reconcile Editor', 'login': 'other-reconcile-editor', 'company_id': other.id, 'company_ids': [(6, 0, other.ids)], 'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_editor').id])]})
        with self.assertRaises(AccessError): task.with_user(editor).with_context(allowed_company_ids=other.ids).research_intent_reconcile('request-1', 'intent-1')
    def test_best_of_budget_is_visible_but_not_passed(self):
        case = self.env['meli.optimization.case'].create({'name':'test','batch_id':self.env['meli.optimization.batch'].create({'name':'test'}).id})
        snap = self.env['meli.optimization.snapshot'].create({'case_id':case.id,'payload':{}})
        task = self.env['meli.optimization.task'].create({'case_id':case.id,'snapshot_id':snap.id,'task_type':'image.generate',
            'payload':{'schema_version':'1','source_artifact_ids':['1'],'target_artifact_id':'2','generation_plan_id':'3'}})
        data=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC')
        image=self.env['meli.optimization.artifact'].create({'case_id':case.id,'data':base64.b64encode(data),'mime_type':'image/png','digest':hashlib.sha256(data).hexdigest()})
        claim=task.claim(['image.generate']); token,epoch=claim['lease_token'],claim['lease_epoch']
        task.checkpoint(token,epoch,'intent',{'request_id':'original','state':'unknown'})
        task.host_progress(token,epoch,'original','review_required',review_seq=1,review_kind='best_of_budget',candidate_artifact_id=image.id,candidate_sha256=image.digest,candidate_attempt_id='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',candidate_attempt_number=1)
        evidence = {'request_id':'original','review_seq':1,'candidate_artifact_id':image.id,
                    'candidate_sha256':image.digest,'candidate_attempt_id':'a'*32,'candidate_attempt_number':1,
                    'report':{'quality_binding':{'request_id':'original','candidate_sha256':image.digest},
                              'checks':[{'name':name,'status':'failed','evidence':'fixture'} for name in
                                        ('garment_construction','color_material','garment_details','identity_pose','accessories','scene_lighting_framing','text_layout')],
                              'skill_report':{'decision':'reject'}}}
        task.host_review_evidence(token, epoch, evidence)
        task.host_review_evidence(token, epoch, evidence)
        self.assertEqual(task.status_detail['review_evidence']['report'], evidence['report'])
        forged = dict(evidence, candidate_sha256='0'*64)
        with self.assertRaises(ValidationError): task.host_review_evidence(token, epoch, forged)
        self.assertEqual(task.state,'running')
        self.assertEqual(task.status_detail['review_kind'],'best_of_budget')
        self.assertEqual(task.status_detail['candidate_artifact_id'],image.id)
        self.assertNotEqual(task.status_detail.get('status'),'passed')
        with self.assertRaises(ValidationError):
            self.env['meli.optimization.case'].workbench_request_host_review_resume(case.id,task.id,1)
        self.assertEqual(task.status_detail['candidate_artifact_id'],image.id)
        self.assertEqual(task.request_ids[0].state,'unknown')
        with self.assertRaises(ValidationError):
            self.env['meli.optimization.case'].workbench_request_best_review_resume(case.id,task.id,image.id,image.digest,'wrong',1,1)
        with self.assertRaises(ValidationError):
            self.env['meli.optimization.case'].workbench_request_best_review_resume(case.id,task.id,image.id,image.digest,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',1,2)
        self.env['meli.optimization.case'].workbench_request_best_review_resume(case.id,task.id,image.id,image.digest,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',1,1)
        self.assertEqual(task.status_detail['candidate_attempt_id'],'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
    def test_color_uncertainty_visible_without_replacing_review(self):
        case = self.env['meli.optimization.case'].create({'name':'color','batch_id':self.env['meli.optimization.batch'].create({'name':'color'}).id})
        snap = self.env['meli.optimization.snapshot'].create({'case_id':case.id,'payload':{}})
        task = self.env['meli.optimization.task'].create({'case_id':case.id,'snapshot_id':snap.id,'task_type':'image.generate',
            'payload':{'schema_version':'1','source_artifact_ids':['1'],'target_artifact_id':'2','generation_plan_id':'3'}})
        claim=task.claim(['image.generate']); token,epoch=claim['lease_token'],claim['lease_epoch']
        task.checkpoint(token,epoch,'intent',{'request_id':'original','state':'unknown'})
        task.host_progress(token,epoch,'original','color_required',review_seq=1)
        self.assertEqual(task.status_detail['code'], 'host_color_required')
        self.assertIn('颜色',task.status_detail['message'])
        self.assertEqual(task._envelope()['host_progress']['code'], 'host_color_required')
        task.host_progress(token,epoch,'original','color_required',review_seq=1)
        task._set({'status_detail':{'code':'host_review_required','request_id':'original','review_seq':1}})
        with self.assertRaises(ValidationError): task.host_progress(token,epoch,'original','color_required',review_seq=1)

    def test_host_review_resume_preserves_original_request_and_lease(self):
        case = self.env['meli.optimization.case'].create({'name':'test','batch_id':self.env['meli.optimization.batch'].create({'name':'test'}).id})
        snap = self.env['meli.optimization.snapshot'].create({'case_id':case.id,'payload':{}})
        task = self.env['meli.optimization.task'].create({'case_id':case.id,'snapshot_id':snap.id,'task_type':'image.generate',
            'payload':{'schema_version':'1','source_artifact_ids':['1'],'target_artifact_id':'2','generation_plan_id':'3'}})
        claim = task.claim(['image.generate']); token, epoch = claim['lease_token'], claim['lease_epoch']
        task.checkpoint(token, epoch, 'intent', {'request_id':'request-1','state':'unknown'})
        task._set({'status_detail': {'code':'host_resume_requested','request_id':'request-1','message':'legacy'}})
        self.assertFalse(self.env['meli.optimization.case'].workbench_request_host_review_resume(case.id, task.id))
        self.assertEqual(task.status_detail['code'], 'host_review_required')
        self.assertNotIn('intent_id', task.status_detail)
        task._set({'status_detail': {'code':'host_resume_requested','request_id':'request-1','message':'legacy'}})
        task.host_progress(token, epoch, 'request-1', 'review_required', review_seq=1)
        self.assertEqual(task.status_detail['code'], 'host_review_required')
        self.assertEqual(task.status_detail['review_seq'], 1)
        with self.assertRaises(ValidationError): task.host_progress(token, epoch, 'other', 'review_required', review_seq=1)
        task.host_progress(token, epoch, 'request-1', 'review_required', review_seq=1)
        self.assertEqual(task.state, 'running')
        self.assertEqual(task.status_detail['code'], 'host_review_required')
        self.env['meli.optimization.case'].workbench_request_host_review_resume(case.id, task.id, 1)
        self.assertEqual(task._envelope()['host_progress']['code'], 'host_resume_requested')
        intent = task.status_detail['intent_id']
        self.env['meli.optimization.case'].workbench_request_host_review_resume(case.id, task.id, 1)
        self.assertEqual(task.status_detail['intent_id'], intent)
        self.assertEqual(task.lease_epoch, epoch)
        with self.assertRaises(ValidationError): task.host_progress(token, epoch, 'request-1', 'resumed', 'wrong-intent', 1)
        task.host_progress(token, epoch, 'request-1', 'resumed', intent, 1)
        task.host_progress(token, epoch, 'request-1', 'resumed', intent, 1)
        task.host_progress(token, epoch, 'request-1', 'review_required', review_seq=1)
        self.assertEqual(task.status_detail['code'], 'host_review_resumed')
        self.assertEqual(task.request_ids[0].state, 'unknown')
        task.host_progress(token, epoch, 'request-1', 'review_required', review_seq=2)
        with self.assertRaises(ValidationError):
            self.env['meli.optimization.case'].workbench_request_host_review_resume(case.id, task.id, 1)
        self.env['meli.optimization.case'].workbench_request_host_review_resume(case.id, task.id, 2)
        self.assertNotEqual(task.status_detail['intent_id'], intent)
        with self.assertRaises(ValidationError): task.host_progress(token, epoch, 'request-1', 'resumed', intent, 1)
        with self.assertRaises(ValidationError): task.host_progress(token, epoch, 'request-1', 'review_required', review_seq=1)
        task.host_progress(token, epoch, 'request-1', 'review_required', review_seq=2)
        task._set({'lease_expires_at': fields.Datetime.now() - timedelta(seconds=1)})
        with self.assertRaises(ValidationError):
            self.env['meli.optimization.case'].workbench_request_host_review_resume(case.id, task.id)
        task._set({'status_detail': {'code':'host_resume_requested','request_id':'request-1','message':'legacy'}})
        with self.assertRaises(ValidationError):
            self.env['meli.optimization.case'].workbench_request_host_review_resume(case.id, task.id)
        self.assertNotIn('review_seq', task.status_detail)
    def test_recover_expired_research_lease_is_fenced_and_idempotent(self):
        batch = self.env['meli.optimization.batch'].create({'name': 'recovery'})
        case = self.env['meli.optimization.case'].create({'name': 'item', 'batch_id': batch.id})
        snap = self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {}})
        task = self.env['meli.optimization.task'].create({'case_id': case.id, 'snapshot_id': snap.id, 'task_type': 'product.facts', 'payload': {'schema_version': '1', 'product_snapshot_id': str(snap.id)}})
        task._set({'task_type': 'research.bestseller'})
        claim = task.claim(['research.bestseller'])
        token, epoch = claim['lease_token'], claim['lease_epoch']
        intent = '1234567890abcdef1234567890abcdef'
        task.checkpoint(token, epoch, 'original-intent', {'request_id': 'original', 'state': 'unknown'})
        with self.assertRaises(ValidationError): task.recover_lease('original', token, epoch, intent)
        task._set({'lease_expires_at': fields.Datetime.now() - timedelta(seconds=1)})
        with self.assertRaises(ValidationError): task.recover_lease('other', token, epoch, intent)
        with self.assertRaises(ValidationError): task.recover_lease('original', 'wrong', epoch, intent)
        recovered = task.recover_lease('original', token, epoch, intent)
        self.assertEqual(recovered['lease_epoch'], epoch + 1)
        self.assertNotEqual(recovered['lease_token'], token)
        self.assertEqual(task.request_ids[0].state, 'unknown')
        self.assertEqual(task.recover_lease('original', token, epoch, intent), recovered)
        with self.assertRaises(ValidationError): task.recover_lease('original', token, epoch, 'fedcba0987654321fedcba0987654321')
        task._set({'lease_expires_at': fields.Datetime.now() - timedelta(seconds=1)})
        renewed = task.recover_lease('original', token, epoch, intent)
        self.assertEqual(renewed['lease_epoch'], epoch + 2)
        self.assertNotEqual(renewed['lease_token'], recovered['lease_token'])
        self.assertEqual(task.recover_lease('original', token, epoch, intent), renewed)
        with self.assertRaises(ValidationError): task.heartbeat(recovered['lease_token'], recovered['lease_epoch'])
        with self.assertRaises(ValidationError): task.heartbeat(token, epoch)

    def test_recover_expired_text_lease_preserves_request_and_owner(self):
        for kind in ('research.keywords', 'product.facts'):
            with self.subTest(kind=kind):
                batch = self.env['meli.optimization.batch'].create({'name': 'text recovery'})
                case = self.env['meli.optimization.case'].create({'name': 'item', 'batch_id': batch.id})
                snap = self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {}})
                task = self.env['meli.optimization.task'].create({'case_id': case.id, 'snapshot_id': snap.id, 'task_type': 'product.facts', 'payload': {'schema_version': '1', 'product_snapshot_id': str(snap.id)}})
                task._set({'task_type': kind})
                claim = task.claim([kind]); token, epoch = claim['lease_token'], claim['lease_epoch']
                task.checkpoint(token, epoch, 'text-intent', {'request_id': 'text-original', 'state': 'unknown'})
                task._set({'lease_expires_at': fields.Datetime.now() - timedelta(seconds=1)})
                with self.assertRaises(ValidationError):
                    task.recover_lease('wrong', token, epoch, '1' * 32)
                recovered = task.recover_lease('text-original', token, epoch, '1' * 32)
                self.assertEqual(recovered['lease_epoch'], epoch + 1)
                self.assertEqual(task.request_ids[0].state, 'unknown')
                with self.assertRaises(ValidationError): task.heartbeat(token, epoch)

    def test_recover_waiting_unknown_uses_original_wait_fence(self):
        batch = self.env['meli.optimization.batch'].create({'name': 'wait recovery'})
        case = self.env['meli.optimization.case'].create({'name': 'item', 'batch_id': batch.id})
        snap = self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {}})
        task = self.env['meli.optimization.task'].create({'case_id': case.id, 'snapshot_id': snap.id, 'task_type': 'product.facts', 'payload': {'schema_version': '1', 'product_snapshot_id': str(snap.id)}})
        task._set({'task_type': 'research.bestseller'})
        claim = task.claim(['research.bestseller']); token, epoch = claim['lease_token'], claim['lease_epoch']
        task.checkpoint(token, epoch, 'intent-wait', {'request_id':'r-wait', 'state':'unknown'})
        task.wait(token, epoch, 'wait-unknown', {'code':'external_result_unknown', 'message':'Interrupted'})
        recovery_intent = '1234567890abcdef1234567890abcdef'
        with self.assertRaises(ValidationError): task.recover_lease('r-wait', 'wrong', epoch, recovery_intent)
        with self.assertRaises(ValidationError): task.recover_lease('other', token, epoch, recovery_intent)
        result = task.recover_lease('r-wait', token, epoch, recovery_intent)
        self.assertEqual(result['lease_epoch'], epoch + 1)
        self.assertEqual(task.state, 'running')
        self.assertFalse(task.status_detail)
        self.assertEqual(task.request_ids[0].state, 'unknown')
        self.assertEqual(task.recover_lease('r-wait', token, epoch, recovery_intent), result)
        with self.assertRaises(ValidationError): task.heartbeat(token, epoch)

    def test_resume_and_claim_clear_obsolete_status(self):
        batch = self.env['meli.optimization.batch'].create({'name': 'status recovery'})
        case = self.env['meli.optimization.case'].create({'name': 'item', 'batch_id': batch.id})
        snap = self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {}})
        task = self.env['meli.optimization.task'].create({'case_id': case.id, 'snapshot_id': snap.id,
            'task_type': 'product.facts', 'payload': {'schema_version': '1', 'product_snapshot_id': str(snap.id)}})
        claim = task.claim(['product.facts'])
        task.wait(claim['lease_token'], claim['lease_epoch'], 'status-wait',
                  {'code':'capability_missing', 'message':'Old failure'})
        task.resume()
        self.assertFalse(task.status_detail)
        self.assertEqual(task.state, 'queued')
        task._set({'status_detail': {'code':'capability_missing', 'message':'Old failure'}})
        task.claim(['product.facts'])
        self.assertFalse(task.status_detail)
        self.assertEqual(task.state, 'running')
        self.assertTrue(self.env['meli.optimization.event'].search_count([
            ('task_id', '=', task.id), ('kind', '=', 'wait')]))

    def test_durable_contract(self):
        self.assertIn('meli.optimization.task', self.env.registry, 'durable optimization implementation is missing')
        batch = self.env['meli.optimization.batch'].create({'name': 'Test'})
        case = self.env['meli.optimization.case'].create({'name': 'Item', 'batch_id': batch.id})
        snapshot = self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {'item': 'A'}})
        with self.assertRaises(AccessError):
            snapshot.write({'payload': {'item': 'B'}})
        with self.assertRaises(ValidationError):
            self.env['meli.optimization.case'].create({'name': 'Bad', 'batch_id': batch.id, 'company_id': self.env['res.company'].create({'name': 'Other'}).id})
        task = self.env['meli.optimization.task'].create({'case_id': case.id, 'snapshot_id': snapshot.id, 'task_type': 'product.facts', 'payload': {'schema_version': '1', 'product_snapshot_id': str(snapshot.id)}})
        with self.assertRaises(AccessError):
            task.with_context(optimization_internal=True).write({'state': 'succeeded'})
        claim = task.claim(['product.facts'])
        self.assertEqual(claim['id'], task.id)
        self.assertEqual(claim['lease_epoch'], 1)
        result = {'schema_version': '1', 'evidence': [{'id': 'e1', 'source': 'snapshot', 'observed_at': '2026-11-14T00:00:00Z'}], 'facts': [{'name': 'item', 'value': 'A', 'evidence_ids': ['e1']}], 'uncertainties': []}
        args = (claim['lease_token'], claim['lease_epoch'])
        first = task.complete(*args, 'done-1', result)
        self.assertEqual(task.complete(*args, 'done-1', result), first)
        with self.assertRaises(ValidationError):
            task.complete(*args, 'done-1', dict(result, uncertainties=['changed']))
        self.assertEqual(task.state, 'succeeded')

    def test_leases_dependencies_and_unknown_requests(self):
        self.assertIn('meli.optimization.task', self.env.registry, 'durable optimization implementation is missing')
        batch = self.env['meli.optimization.batch'].create({'name': 'Test'})
        case = self.env['meli.optimization.case'].create({'name': 'Item', 'batch_id': batch.id})
        snap = self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {}})
        def create(**extra):
            return self.env['meli.optimization.task'].create(dict({'case_id': case.id, 'snapshot_id': snap.id, 'task_type': 'product.facts', 'payload': {'schema_version': '1', 'product_snapshot_id': str(snap.id)}}, **extra))
        upstream = create()
        dependent = create(dependency_ids=[(6, 0, upstream.ids)])
        claim = upstream.claim(['product.facts'])
        self.assertFalse(dependent.claim(['product.facts']))
        with self.assertRaises(ValidationError):
            upstream.heartbeat('bad', claim['lease_epoch'])
        # Expire through SQL: public ORM may never modify a lease.
        self.env.cr.execute('UPDATE meli_optimization_task SET lease_expires_at=%s WHERE id=%s', (fields.Datetime.now() - timedelta(seconds=1), upstream.id))
        upstream.invalidate_recordset()
        with self.assertRaises(ValidationError):
            upstream.heartbeat(claim['lease_token'], claim['lease_epoch'])
        newer = upstream.claim(['product.facts'])
        self.assertEqual(newer['lease_epoch'], 2)
        upstream.checkpoint(newer['lease_token'], 2, 'dispatch-1', {'request_id': 'remote-A', 'state': 'unknown'})
        self.env.cr.execute('UPDATE meli_optimization_task SET lease_expires_at=%s WHERE id=%s', (fields.Datetime.now() - timedelta(seconds=1), upstream.id))
        upstream.invalidate_recordset()
        self.assertFalse(upstream.claim(['product.facts']))

    def test_confirmation_invalidates_and_artifact_digest(self):
        self.assertIn('meli.optimization.task', self.env.registry, 'durable optimization implementation is missing')
        batch = self.env['meli.optimization.batch'].create({'name': 'Test'})
        case = self.env['meli.optimization.case'].create({'name': 'Item', 'batch_id': batch.id})
        proposal = self.env['meli.optimization.proposal'].create({'case_id': case.id, 'payload': {'title': 'A'}})
        confirmation = proposal.confirm(proposal.digest)
        self.assertTrue(confirmation.is_valid())
        self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {'new': True}})
        self.assertFalse(confirmation.is_valid())
        with self.assertRaises(AccessError):
            proposal.write({'payload': {'title': 'B'}})
        with self.assertRaises(ValidationError):
            self.env['meli.optimization.artifact'].create({'case_id': case.id, 'payload': {}, 'digest': '0' * 64})

    def test_worker_company_boundary_and_artifact_upload(self):
        company = self.env['res.company'].create({'name': 'Worker company'})
        worker = self.env['res.users'].create({'name': 'Worker', 'login': 'optimization-worker', 'company_id': company.id, 'company_ids': [(6, 0, company.ids)], 'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        batch = self.env['meli.optimization.batch'].with_company(company).create({'name': 'Worker case'})
        case = self.env['meli.optimization.case'].create({'name': 'Item', 'batch_id': batch.id})
        snapshot = self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {'item': 'worker'}})
        task = self.env['meli.optimization.task'].create({'case_id': case.id, 'snapshot_id': snapshot.id, 'task_type': 'product.facts', 'payload': {'schema_version': '1', 'product_snapshot_id': str(snapshot.id)}})
        scoped = task.with_user(worker).with_context(allowed_company_ids=company.ids)
        claim = scoped.claim(['product.facts'])
        self.assertEqual(len(claim['input_hash']), 64)
        self.assertEqual(claim['schema_version'], '1')
        with self.assertRaises(AccessError):
            scoped.with_context(optimization_internal=True).write({'lease_epoch': 8})
        with self.assertRaises(AccessError):
            self.env['meli.optimization.event'].with_user(worker).create({'task_id': task.id})
        artifact = self.env['meli.optimization.artifact'].with_user(worker).create({'case_id': case.id, 'payload': {}, 'digest': '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'})
        self.assertEqual(artifact.company_id, company)
        self.assertEqual(artifact.payload, {})
        with self.assertRaises(AccessError):
            snapshot.with_user(worker).write({'payload': {'item': 'forged'}})
        other_batch = self.env['meli.optimization.batch'].create({'name': 'Foreign'})
        with self.assertRaises(AccessError):
            other_batch.with_user(worker).read(['name'])
        foreign_case = self.env['meli.optimization.case'].create({'name': 'Foreign', 'batch_id': other_batch.id})
        with self.assertRaises(AccessError):
            self.env['meli.optimization.artifact'].with_user(worker).create({'case_id': foreign_case.id, 'payload': {}, 'digest': artifact.digest})
        with self.assertRaises(AccessError):
            self.env['meli.optimization.case'].with_user(worker).create({'name': 'Forbidden', 'batch_id': other_batch.id})
        worker.active = False
        with self.assertRaises(AccessError):
            scoped.claim(['product.facts'])

    def test_collection_case_lock_is_skipped_during_claim(self):
        from odoo import api, SUPERUSER_ID, sql_db
        db=sql_db.db_connect(self.env.cr.dbname)
        with db.cursor() as setup:
            env=api.Environment(setup,SUPERUSER_ID,{})
            from .test_collection_start import IMAGE
            cases = env['meli.optimization.case']
            detail = cases.workbench_collection_create({
                'garment': 'synthetic', 'source_sku': 'LOCK', 'image_data': IMAGE, 'image_mime': 'image/png',
                'platform': 'mercado-libre-mx', 'min_sold': 100, 'min_reviews': 0, 'min_rating': 0, 'result_limit': 5,
            })
            tid = cases.workbench_collection_start(detail['id'], detail['snapshot']['id'], detail['revision'])['task_id']
            self.assertTrue(tid)
            setup.commit()
        with db.cursor() as first, db.cursor() as second:
            first.execute('SELECT id FROM meli_optimization_case WHERE id=%s FOR UPDATE',[detail['id']])
            second.execute("SET LOCAL lock_timeout = '500ms'")
            claimed=api.Environment(second,SUPERUSER_ID,{})['meli.optimization.task'].browse(tid).claim(['product.facts'])
            self.assertFalse(claimed)
        with db.cursor() as cleanup:
            api.Environment(cleanup,SUPERUSER_ID,{})['meli.optimization.task'].browse(tid).cancel()
            cleanup.commit()

    def test_independent_cursors_skip_locked_claim(self):
        from odoo import api, SUPERUSER_ID, sql_db
        # Real connections bypass the shared HttpCase test cursor. These fixtures
        # live only in the runner's disposable database and are intentionally committed.
        db = sql_db.db_connect(self.env.cr.dbname)
        with db.cursor() as setup:
            env = api.Environment(setup, SUPERUSER_ID, {})
            batch = env['meli.optimization.batch'].create({'name': 'Concurrent'})
            case = env['meli.optimization.case'].create({'name': 'Concurrent', 'batch_id': batch.id})
            snap = env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {'item': 'concurrent'}})
            task = env['meli.optimization.task'].create({'case_id': case.id, 'snapshot_id': snap.id, 'task_type': 'product.facts', 'payload': {'schema_version': '1', 'product_snapshot_id': str(snap.id)}})
            task_id = task.id
            setup.commit()
        with db.cursor() as first, db.cursor() as second:
            one = api.Environment(first, SUPERUSER_ID, {})['meli.optimization.task'].browse(task_id)
            two = api.Environment(second, SUPERUSER_ID, {})['meli.optimization.task'].browse(task_id)
            first_claim = one.claim(['product.facts'])
            self.assertEqual(first_claim['lease_epoch'], 1)
            self.assertFalse(two.claim(['product.facts']))
            first.commit()
            # Start a new PostgreSQL snapshot after the first commit.
            second.rollback()
            two.invalidate_recordset()
            self.assertFalse(two.claim(['product.facts']))


    def _review_task(self, **extra):
        batch = self.env['meli.optimization.batch'].create({'name': 'Review'})
        case = self.env['meli.optimization.case'].create({'name': 'Review', 'batch_id': batch.id})
        snap = self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {}})
        return self.env['meli.optimization.task'].create(dict(case_id=case.id, snapshot_id=snap.id,
            task_type='product.facts', payload={'schema_version': '1', 'product_snapshot_id': str(snap.id)}, **extra))

    def _review_result(self):
        return {'schema_version': '1', 'evidence': [{'id': 'e1', 'source': 'snapshot', 'observed_at': '2026-11-14T00:00:00Z'}],
                'facts': [{'name': 'item', 'value': 'A', 'evidence_ids': ['e1']}], 'uncertainties': []}

    def test_ready_task_beyond_100_blocked_candidates(self):
        upstream = self._review_task()
        template = {'case_id': upstream.case_id.id, 'snapshot_id': upstream.snapshot_id.id,
                    'task_type': 'product.facts', 'payload': upstream.payload}
        blocked = self.env['meli.optimization.task'].create([
            dict(template, dependency_ids=[(6, 0, upstream.ids)]) for _ in range(101)])
        lease = upstream.claim(['product.facts'])
        upstream.checkpoint(lease['lease_token'], lease['lease_epoch'], 'unknown', {'request_id': 'remote', 'state': 'unknown'})
        self.env.cr.execute('UPDATE meli_optimization_task SET lease_expires_at=%s WHERE id=%s',
                            (fields.Datetime.now() - timedelta(seconds=1), upstream.id))
        upstream.invalidate_recordset()
        ready = self.env['meli.optimization.task'].create(template)
        claim = self.env['meli.optimization.task'].claim(['product.facts'])
        self.assertTrue(claim, 'blocked first 100 must not starve later eligible work')
        self.assertEqual(claim['id'], ready.id)
        self.assertEqual(set(blocked.mapped('state')), {'queued'})

    def test_completion_rejects_uncertainty_until_resolved(self):
        for status in ('unknown', 'dispatched'):
            task = self._review_task()
            dependent = self.env['meli.optimization.task'].create({'case_id': task.case_id.id,
                'snapshot_id': task.snapshot_id.id, 'task_type': 'product.facts', 'payload': task.payload,
                'dependency_ids': [(6, 0, task.ids)]})
            lease = task.claim(['product.facts'])
            args = (lease['lease_token'], lease['lease_epoch'])
            task.checkpoint(*args, 'request', {'request_id': 'remote', 'state': status})
            with self.assertRaises(ValidationError):
                task.complete(*args, 'done', self._review_result())
            self.assertEqual(task.state, 'running')
            self.assertFalse(dependent.claim(['product.facts']))
            task.checkpoint(*args, 'resolved', {'request_id': 'remote', 'state': 'succeeded'})
            receipt = task.complete(*args, 'done', self._review_result())
            self.assertEqual(task.complete(*args, 'done', self._review_result()), receipt)
            self.assertEqual(dependent.claim(['product.facts'])['id'], dependent.id)

    def test_fenced_wait_fail_and_editor_controls(self):
        task = self._review_task()
        self.assertTrue(callable(getattr(task, 'wait', None)), 'fenced wait operation missing')
        self.assertTrue(callable(getattr(task, 'fail', None)), 'fenced fail operation missing')
        for operation, state in (('wait', 'waiting'), ('fail', 'failed')):
            task = self._review_task()
            lease = task.claim(['product.facts'])
            args = (lease['lease_token'], lease['lease_epoch'])
            method = getattr(task, operation)
            with self.assertRaises(ValidationError):
                method('stale', args[1], 'stale', {'code': 'provider_error', 'message': 'Unavailable'})
            with self.assertRaises(ValidationError):
                method(*args, 'bad', {'code': ''})
            with self.assertRaises(ValidationError):
                method(*args, 'oversize', {'code': 'error', 'message': 'x' * 4097})
            detail = {'code': 'capability_missing', 'message': 'Provider unavailable'}
            receipt = method(*args, 'status', detail)
            self.assertEqual(task.state, state)
            self.assertEqual(method(*args, 'status', detail), receipt)
            with self.assertRaises(ValidationError):
                method(*args, 'status', dict(detail, message='Changed'))
            with self.assertRaises(ValidationError):
                task.heartbeat(*args)
            with self.assertRaises(ValidationError):
                task.complete(*args, 'done', self._review_result())
            if operation == 'wait':
                editor = self.env['res.users'].create({'name': 'Resume editor', 'login': 'resume-editor',
                    'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_editor').id])]})
                task.with_user(editor).resume()
                self.assertEqual(task.state, 'queued')
                self.assertEqual(method(*args, 'status', detail), receipt)
                newer = task.claim(['product.facts'])
                self.assertEqual(newer['lease_epoch'], args[1] + 1)
                with self.assertRaises(ValidationError):
                    method(*args, 'stale-new', detail)
                task.with_user(editor).cancel()
                self.assertEqual(task.state, 'cancelled')
            else:
                with self.assertRaises(ValidationError):
                    task.resume()
                with self.assertRaises(ValidationError):
                    task.cancel()

    def test_resume_retries_unknown_and_preserves_control_authorization(self):
        task = self._review_task()
        self.assertTrue(callable(getattr(task, 'wait', None)), 'fenced wait operation missing')
        lease = task.claim(['product.facts'])
        args = (lease['lease_token'], lease['lease_epoch'])
        task.checkpoint(*args, 'unknown', {'request_id': 'remote', 'state': 'unknown'})
        task.wait(*args, 'wait', {'code': 'uncertain', 'message': 'Reconciliation required'})
        retry = task.resume()
        self.assertEqual(task.state, 'queued')
        self.assertEqual(retry['source_task_id'], task.id)
        self.assertEqual(retry['task_id'], task.id)
        self.assertEqual(task.resume()['task_id'], retry['task_id'])
        self.assertEqual(self.env['meli.optimization.task'].browse(retry['task_id']).state, 'queued')
        worker = self.env['res.users'].create({'name': 'Control worker', 'login': 'control-worker',
            'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        for action in ('resume', 'cancel'):
            with self.assertRaises(AccessError):
                getattr(task.with_user(worker), action)()
        other = self.env['res.company'].create({'name': 'Foreign control'})
        editor = self.env['res.users'].create({'name': 'Foreign editor', 'login': 'foreign-control-editor',
            'company_id': other.id, 'company_ids': [(6, 0, other.ids)],
            'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_editor').id])]})
        for action in ('resume', 'cancel'):
            with self.assertRaises(AccessError):
                getattr(task.with_user(editor).with_context(allowed_company_ids=other.ids), action)()
        task.cancel()
        self.assertEqual(task.state, 'cancelled')
        self.assertEqual(self.env['meli.optimization.external.request'].search([('task_id', '=', task.id)]).state, 'unknown')


    def test_expired_fail_wait_and_cross_worker_rejected(self):
        for operation in ('fail', 'wait'):
            task = self._review_task()
            lease = task.claim(['product.facts'])
            args = (lease['lease_token'], lease['lease_epoch'])
            detail = {'code': 'provider_error', 'message': 'Unavailable'}
            worker = self.env['res.users'].create({'name': operation, 'login': 'other-' + operation,
                'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
            with self.assertRaises(AccessError):
                getattr(task.with_user(worker), operation)(*args, 'wrong-user', detail)
            self.env.cr.execute('UPDATE meli_optimization_task SET lease_expires_at=%s WHERE id=%s',
                                (fields.Datetime.now() - timedelta(seconds=1), task.id))
            task.invalidate_recordset()
            with self.assertRaises(ValidationError):
                getattr(task, operation)(*args, 'expired', detail)
            self.assertEqual(task.state, 'running')

    def _default_editor(self):
        return self.env['res.users'].create({'name': 'Defaults editor', 'login': 'defaults-editor',
            'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_editor').id,
                                 self.env.ref('meli_optimization.group_worker').id])]})

    def test_context_defaults_cannot_forge_task_state(self):
        task = self._review_task()
        model = self.env['meli.optimization.task'].with_user(self._default_editor())
        self.assertFalse(model.env.su)
        vals = {'case_id': task.case_id.id, 'snapshot_id': task.snapshot_id.id,
                'task_type': task.task_type, 'payload': task.payload}
        for name, value in {'state': 'succeeded', 'result': {'unvalidated': True}, 'lease_token': 'forged',
                'lease_epoch': 8, 'lease_expires_at': '2099-01-01 00:00:00', 'worker_uid': model.env.uid,
                'status_detail': {'code': 'forged'}, 'request_ids': []}.items():
            with self.subTest(field=name), self.assertRaises(AccessError):
                model.with_context(**{'default_' + name: value}).create(vals)

    def test_context_revision_and_artifact_defaults_are_validated(self):
        import base64
        from ..domain import canonical_hash
        task = self._review_task()
        user = self._default_editor()
        case_model = self.env['meli.optimization.case'].with_user(user)
        with self.subTest(field='revision'), self.assertRaises(AccessError):
            case_model.with_context(default_revision=99).create({'name': 'Injected', 'batch_id': task.case_id.batch_id.id})
        artifact = self.env['meli.optimization.artifact'].with_user(user)
        with self.subTest(field='data'), self.assertRaises(ValidationError):
            artifact.with_context(default_data=base64.b64encode(b'forged').decode()).create({
                'case_id': task.case_id.id, 'payload': {}, 'digest': canonical_hash({})})
        with self.subTest(field='payload'), self.assertRaises(ValidationError):
            artifact.with_context(default_payload={'forged': True}).create({
                'case_id': task.case_id.id, 'data': base64.b64encode(b'image').decode(),
                'digest': __import__('hashlib').sha256(b'image').hexdigest()})

    def test_valid_context_defaults_and_input_company_validation(self):
        from ..domain import canonical_hash
        task = self._review_task()
        user = self._default_editor()
        model = self.env['meli.optimization.task'].with_user(user)
        defaults = dict(default_case_id=task.case_id.id, default_snapshot_id=task.snapshot_id.id,
                        default_task_type=task.task_type, default_payload=task.payload)
        normal = model.with_context(**defaults).create({})
        self.assertEqual(normal.payload, task.payload)
        self.assertEqual(normal.state, 'queued')
        self.assertEqual(normal.lease_epoch, 0)
        self.assertFalse(normal.result)
        with self.assertRaises(ValidationError):
            model.with_context(**dict(defaults, default_payload={'unvalidated': True})).create({})
        other = self.env['res.company'].create({'name': 'Default foreign company'})
        with self.assertRaises(ValidationError):
            model.with_context(**dict(defaults, default_company_id=other.id)).create({})
        artifact = self.env['meli.optimization.artifact'].with_user(user).with_context(
            default_case_id=task.case_id.id, default_payload={}, default_digest=canonical_hash({})).create({})
        self.assertEqual(artifact.payload, {})
        snapshot = self.env['meli.optimization.snapshot'].with_user(user).with_context(
            default_case_id=task.case_id.id, default_payload={'default': True}, default_digest='forged').create({})
        self.assertEqual(snapshot.digest, canonical_hash({'default': True}))

"""One-click collection behavior in the disposable test database only."""
import copy
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

IMAGE = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
EVIDENCE = [{'id': 'e', 'source': 'snapshot', 'observed_at': '2026-11-15T00:00:00Z'}]


@tagged('post_install', '-at_install')
class CollectionRetryConcurrencyCase(TransactionCase):
    def test_concurrent_retry_and_claim_use_one_original_task(self):
        from odoo import api, SUPERUSER_ID, sql_db
        db = sql_db.db_connect(self.env.cr.dbname)
        with db.cursor() as setup:
            env = api.Environment(setup, SUPERUSER_ID, {})
            # This test commits across real cursors, including an intentionally unresolved request.
            company = env['res.company'].create({'name': 'Original task concurrency test'})
            context = {'allowed_company_ids': company.ids}
            env = api.Environment(setup, SUPERUSER_ID, context)
            detail = env['meli.optimization.case'].workbench_collection_create({'garment':'concurrency test',
                'source_sku':'SKU', 'image_data':IMAGE, 'image_mime':'image/png',
                'platform':'mercado-libre-mx', 'min_sold':100, 'min_reviews':0, 'min_rating':0, 'result_limit':5})
            env['meli.optimization.case'].workbench_collection_start(detail['id'],detail['snapshot']['id'],detail['revision'])
            task = env['meli.optimization.task'].search([('case_id','=',detail['id']),('task_type','=','product.facts')],limit=1)
            task_id = task.id
            lease = task.claim(['product.facts'])
            task.checkpoint(lease['lease_token'],lease['lease_epoch'],'concurrent-first',{'request_id':'concurrent-old','state':'unknown'})
            task.wait(lease['lease_token'],lease['lease_epoch'],'concurrent-wait',{'code':'external_result_unknown','message':'unknown'})
            setup.commit()
        with db.cursor() as first, db.cursor() as second:
            env1 = api.Environment(first,SUPERUSER_ID,context)
            env2 = api.Environment(second,SUPERUSER_ID,context)
            receipt = env1['meli.optimization.task'].browse(task_id).retry_unknown_task()
            self.assertEqual(receipt['task_id'],task_id)
            second.execute("SET LOCAL lock_timeout = '300ms'")
            with self.assertRaises(Exception):
                env2['meli.optimization.task'].browse(task_id).retry_unknown_task()
            second.rollback()
            first.commit()
        with db.cursor() as first, db.cursor() as second:
            env1 = api.Environment(first,SUPERUSER_ID,context)
            env2 = api.Environment(second,SUPERUSER_ID,context)
            task1 = env1['meli.optimization.task'].browse(task_id)
            self.assertEqual(task1.retry_unknown_task(),receipt)
            newer = task1.claim(['product.facts'])
            self.assertEqual(newer['id'],task_id)
            self.assertGreater(newer['lease_epoch'],lease['lease_epoch'])
            self.assertFalse(env2['meli.optimization.task'].browse(task_id).claim(['product.facts']))
            with self.assertRaises(ValidationError):
                task1.checkpoint(lease['lease_token'],lease['lease_epoch'],'concurrent-stale',{'request_id':'late','state':'succeeded'})
            first.commit()
        with db.cursor() as cleanup:
            env = api.Environment(cleanup,SUPERUSER_ID,context)
            env['meli.optimization.task'].browse(task_id).cancel()
            cleanup.commit()


@tagged('post_install', '-at_install')
class CollectionStartCase(TransactionCase):
    def test_waiting_gallery_continues_only_with_all_original_receipts(self):
        import base64
        import hashlib
        from odoo.exceptions import AccessError
        detail = self.collection()
        self.succeed(self.task(detail, 'product.facts'))
        self.start(detail)
        self.succeed(self.task(detail, 'research.keywords'))
        self.start(detail)
        task = self.task(detail, 'research.bestseller')
        task.prepare_scoped_research()
        worker = self.env['res.users'].create({'name':'Gallery worker', 'login':'gallery-resume-test',
            'company_id':task.company_id.id, 'company_ids':[(6,0,task.company_id.ids)],
            'group_ids':[(6,0,[self.env.ref('meli_optimization.group_research_worker').id])]})
        task = task.with_user(worker)
        claim = task.claim(['research.bestseller'])
        request_id = 'original-gallery-request'
        epoch, token = claim['lease_epoch'], claim['lease_token']
        task.checkpoint(token, epoch, 'registered', {'request_id':request_id, 'state':'unknown'})
        evidence_key = 'evidence:' + hashlib.sha256(b'').hexdigest()
        image_key = 'image:' + hashlib.sha256(b'candidate:0').hexdigest()
        def upload(key, raw, mime):
            receipt = task.research_artifact(token, epoch, request_id, key, {
                'data':base64.b64encode(raw).decode(), 'digest':hashlib.sha256(raw).hexdigest(), 'mime_type':mime})
            return {'artifact_id':str(receipt['artifact_id']), 'digest':receipt['digest']}
        evidence = upload(evidence_key, b'{"candidates":[]}', 'application/json')
        image = upload(image_key, base64.b64decode(IMAGE), 'image/png')
        task.wait(token, epoch, 'lost-browser', {'code':'page_unreadable','message':'interrupted'})
        self.assertEqual(task._retry_unknown_research(automatic=True)['state'], 'waiting')
        self.assertFalse(task.request_ids.retry_superseded)
        proof = dict(evidence_artifact_id=evidence['artifact_id'], evidence_digest=evidence['digest'], confirmed_images={image_key:image})
        def attach(owner=task, supplied=token, intent='a'*32, **changes):
            return owner.continue_registered_research(supplied, epoch, request_id, intent,
                claim['snapshot_id'], claim['input_hash'], **dict(proof, **changes))
        with self.assertRaises(ValidationError): attach(supplied='wrong-token')
        with self.assertRaises(ValidationError): attach(confirmed_images={})
        with self.assertRaises(ValidationError): attach(evidence_digest='0'*64)
        with self.assertRaises(AccessError): attach(owner=task.sudo())
        self.assertEqual(task.state, 'waiting')
        recovered = attach()
        self.assertEqual(recovered['request_id'], request_id)
        self.assertEqual(recovered['claim']['lease_epoch'], epoch)
        self.assertNotEqual(recovered['claim']['lease_token'], token)
        self.assertEqual(attach(), recovered)
        with self.assertRaises(ValidationError): attach(intent='b'*32)
        with self.assertRaises(ValidationError): task.heartbeat(token, epoch)
        from datetime import timedelta
        from odoo import fields
        task.sudo()._set({'lease_expires_at':fields.Datetime.now()-timedelta(seconds=1)})
        delayed = attach()
        self.assertNotEqual(delayed['claim']['lease_token'], recovered['claim']['lease_token'])
        with self.assertRaises(ValidationError): task.heartbeat(recovered['claim']['lease_token'], epoch)
        self.assertEqual(attach(), delayed)
        self.assertEqual(task.request_ids.mapped('request_id'), [request_id])
        self.assertEqual(task.research_intent_query(delayed['claim']['lease_token'], epoch,
            request_id, image_key)['digest'], image['digest'])

    def test_prior_lease_status_requires_scoped_live_current_lease(self):
        worker=self.env['res.users'].create({'name':'Lease check worker','login':'lease-check-worker',
            'company_id':self.env.company.id,'company_ids':[(6,0,self.env.company.ids)],
            'group_ids':[(6,0,[self.env.ref('meli_optimization.group_research_worker').id])]})
        tasks=[]; claims=[]
        for _ in range(2):
            detail=self.collection();self.succeed(self.task(detail,'product.facts'));self.start(detail)
            self.succeed(self.task(detail,'research.keywords'));self.start(detail)
            task=self.task(detail,'research.bestseller');task.prepare_scoped_research();task=task.with_user(worker)
            claim=task.claim(['research.bestseller']);tasks.append(task);claims.append(claim)
        old,current=tasks;old_claim,current_claim=claims
        old.checkpoint(old_claim['lease_token'],old_claim['lease_epoch'],'initial',{'request_id':'old-request','state':'dispatched'})
        self.assertFalse(current.prior_research_lease_inactive(current_claim['lease_token'],current_claim['lease_epoch'],'old-request'))
        self.assertFalse(current.prior_research_lease_inactive(current_claim['lease_token'],current_claim['lease_epoch'],'missing'))
        old.wait(old_claim['lease_token'],old_claim['lease_epoch'],'wait',{'code':'external_result_unknown','message':'unknown'})
        self.assertTrue(current.prior_research_lease_inactive(current_claim['lease_token'],current_claim['lease_epoch'],'old-request'))
        with self.assertRaises(ValidationError):
            current.prior_research_lease_inactive('bad-token',current_claim['lease_epoch'],'old-request')
    def test_expired_unknown_retry_is_scoped_and_idempotent(self):
        from datetime import timedelta
        from odoo import fields
        detail=self.collection(); self.succeed(self.task(detail,'product.facts')); self.start(detail)
        self.succeed(self.task(detail,'research.keywords')); self.start(detail)
        task=self.task(detail,'research.bestseller'); task.prepare_scoped_research()
        worker=self.env['res.users'].create({'name':'Expired retry worker','login':'expired-retry-worker',
            'company_id':task.company_id.id,'company_ids':[(6,0,task.company_id.ids)],
            'group_ids':[(6,0,[self.env.ref('meli_optimization.group_research_worker').id])]})
        task=task.with_user(worker)
        claim=task.claim(['research.bestseller'])
        task.checkpoint(claim['lease_token'],claim['lease_epoch'],'first',{'request_id':'expired-original','state':'unknown'})
        nonce = 'a' * 32
        def attach(token, request='expired-original', intent=nonce, snapshot=claim['snapshot_id'], digest=claim['input_hash']):
            return task.continue_registered_research(token, claim['lease_epoch'], request, intent, snapshot, digest)
        old_token = claim['lease_token']
        self.assertEqual(attach(old_token)['claim']['lease_token'], old_token)
        with self.assertRaises(ValidationError):
            attach(old_token, request='different')
        with self.assertRaises(ValidationError):
            attach(old_token, snapshot=claim['snapshot_id'] + 1)
        with self.assertRaises(ValidationError):
            attach(old_token, digest='0' * 64)
        with self.assertRaises(ValidationError):
            task.scoped_resume_preflight(old_token, claim['lease_epoch'], 'expired-original')
        task.sudo()._set({'lease_expires_at':fields.Datetime.now()-timedelta(seconds=1)})
        continued = attach(old_token)
        self.assertEqual(continued['request_id'], 'expired-original')
        self.assertNotEqual(continued['claim']['lease_token'], old_token)
        self.assertEqual(continued['claim']['lease_epoch'], claim['lease_epoch'])
        self.assertEqual(attach(old_token), continued)
        with self.assertRaises(ValidationError):
            attach(old_token, intent='b' * 32)
        with self.assertRaises(ValidationError):
            task.heartbeat(old_token, claim['lease_epoch'])
        claim = continued['claim']
        with self.assertRaises(ValidationError):
            task.retry_expired_unknown_research(claim['lease_token'],claim['lease_epoch'],'expired-original')
        task.sudo()._set({'lease_expires_at':fields.Datetime.now()-timedelta(seconds=1)})
        with self.assertRaises(ValidationError):
            task.retry_expired_unknown_research('wrong',claim['lease_epoch'],'expired-original')
        with self.assertRaises(ValidationError):
            task.retry_expired_unknown_research(claim['lease_token'],claim['lease_epoch']+1,'expired-original')
        with self.assertRaises(ValidationError):
            task.retry_expired_unknown_research(claim['lease_token'],claim['lease_epoch'],'other-request')
        self.assertEqual(task.state,'running')
        receipt=task.retry_expired_unknown_research(claim['lease_token'],claim['lease_epoch'],'expired-original')
        self.assertEqual(receipt['source_task_id'],task.id)
        self.assertEqual(task.retry_expired_unknown_research(claim['lease_token'],claim['lease_epoch'],'expired-original'),receipt)
        self.assertEqual(self.env['meli.optimization.event'].sudo().search_count([('task_id','=',task.id),('kind','=','expired_unknown_research_retry')]),1)
        self.assertEqual(task.state,'queued')
        self.assertEqual(task.request_ids.state,'unknown')
        self.assertEqual(receipt['task_id'], task.id)
        claim2 = task.claim(['research.bestseller'])
        self.assertEqual(claim2['id'], task.id)
        self.assertGreater(claim2['lease_epoch'], claim['lease_epoch'])
        with self.assertRaises(ValidationError):
            task.checkpoint(claim['lease_token'], claim['lease_epoch'], 'late', {'request_id':'late-original','state':'succeeded'})
        task.checkpoint(claim2['lease_token'], claim2['lease_epoch'], 'second', {'request_id':'retry-second','state':'unknown'})
        credentials = (claim2['lease_token'], claim2['lease_epoch'], 'retry-second')
        allowed = task.authorize_search_checkpoint(*credentials, 'expired-original', claim['lease_epoch'])
        self.assertTrue(allowed['allowed'])
        self.assertEqual(allowed['input_hash'], claim2['input_hash'])
        self.assertEqual(allowed['source_epoch'], claim['lease_epoch'])
        self.assertEqual(task.authorize_search_checkpoint(*credentials, 'another-task-request', claim['lease_epoch']),
                         {'allowed': False})
        with self.assertRaises(ValidationError):
            task.authorize_search_checkpoint(claim['lease_token'], *credentials[1:], 'expired-original', claim['lease_epoch'])
        task.wait(claim2['lease_token'], claim2['lease_epoch'], 'wait-second', {'code':'external_result_unknown','message':'unknown'})
        again = task.retry_unknown_research()
        self.assertEqual(again['task_id'], task.id)
        self.assertEqual(task.request_ids.mapped('state'), ['unknown','unknown'])
        self.assertEqual(again['state'], 'waiting')
        from unittest.mock import patch
        clock = fields.Datetime.now() + timedelta(minutes=3)
        with patch.object(fields.Datetime, 'now', return_value=clock):
            self.assertEqual(task.retry_unknown_research()['state'], 'queued')
            third = task.claim(['research.bestseller'])
            task.checkpoint(third['lease_token'], third['lease_epoch'], 'third', {'request_id':'third-original', 'state':'unknown'})
            task.wait(third['lease_token'], third['lease_epoch'], 'third-wait', {'code':'external_result_unknown', 'message':'process interrupted'})
            self.assertEqual(task.retry_unknown_research()['state'], 'waiting')
            self.assertEqual(task.status_detail['code'], 'research_retry_limit')
            self.assertEqual(task.status_detail['previous_message'], 'process interrupted')
            rows = task.case_id.sudo().workbench_detail(task.case_id.id)['tasks']
            attempt = next(row for row in rows if row['id'] == task.id)['attempts'][-1]
            self.assertEqual(attempt['failure']['message'], 'process interrupted')
            self.assertEqual(task.sudo().resume()['task_id'], task.id)
            self.assertEqual(task.claim(['research.bestseller'])['id'], task.id)
    def test_archive_research_hides_and_prevents_claim_without_removing_audit(self):
        detail = self.collection()
        self.succeed(self.task(detail, 'product.facts'))
        self.start(detail)
        self.succeed(self.task(detail, 'research.keywords'))
        self.start(detail)
        task = self.task(detail, 'research.bestseller')
        claim = task.claim(['research.bestseller'])
        task.checkpoint(claim['lease_token'], claim['lease_epoch'], 'original', {'request_id': 'archived-research', 'state': 'unknown'})
        task.archive_research_task()
        self.assertTrue(task.research_archived)
        self.assertEqual(task.state, 'cancelled')
        self.assertTrue(task.request_ids.filtered(lambda x: x.request_id == 'archived-research'))
        self.assertFalse(task.claim(['research.bestseller']))
        self.assertNotIn(task.id, [row['id'] for row in task.case_id.workbench_detail(task.case_id.id)['tasks']])
        self.assertEqual(self.start(detail)['state'], 'queued')
        self.assertNotEqual(self.task(detail, 'research.bestseller').id, task.id)

    def test_scoped_research_is_opt_in_and_only_new_task_is_marked(self):
        self.env['ir.config_parameter'].sudo().set_param('meli_optimization.scoped_auto_research','0')
        old=self.collection(); self.succeed(self.task(old,'product.facts')); self.start(old); self.succeed(self.task(old,'research.keywords')); self.start(old)
        previous=self.task(old,'research.bestseller')
        self.assertFalse(previous.scoped_research)
        self.env['ir.config_parameter'].sudo().set_param('meli_optimization.scoped_auto_research','1')
        self.start(old)
        self.assertFalse(previous.scoped_research)
        new=self.collection(); self.succeed(self.task(new,'product.facts')); self.start(new); self.succeed(self.task(new,'research.keywords')); self.start(new)
        self.assertTrue(self.task(new,'research.bestseller').scoped_research)

    def test_scoped_mark_failure_rolls_back_new_research_task(self):
        from unittest.mock import patch
        self.env['ir.config_parameter'].sudo().set_param('meli_optimization.scoped_auto_research','1')
        detail=self.collection(); self.succeed(self.task(detail,'product.facts')); self.start(detail)
        model=type(self.tasks)
        with patch.object(model,'prepare_scoped_research',side_effect=ValidationError('injected')):
            self.succeed(self.task(detail,'research.keywords'))
        self.assertFalse(self.task(detail,'research.bestseller'))
        self.assertEqual(self.task(detail,'research.keywords').status_detail['code'],'derived_action_failed')
        self.start(detail)
        self.assertTrue(self.task(detail,'research.bestseller').scoped_research)
    def setUp(self):
        super().setUp()
        self.ui = self.env['meli.optimization.case']
        self.tasks = self.env['meli.optimization.task']
        self.assertTrue(hasattr(self.ui, 'workbench_collection_start'), 'one-click collection API missing')

    def collection(self, configured=True):
        values = {'garment': 'dress'}
        if configured:
            values.update(source_sku='SKU', image_data=IMAGE, image_mime='image/png',
                          platform='mercado-libre-mx', min_sold=500,
                          min_reviews=50, min_rating=4.1, result_limit=15)
        detail = self.ui.workbench_collection_create(values)
        if configured:
            self.start(detail)
        return detail

    def start(self, detail):
        return self.ui.workbench_collection_start(detail['id'], detail['snapshot']['id'], detail['revision'])

    def task(self, detail, kind):
        return self.tasks.search([('case_id', '=', detail['id']), ('task_type', '=', kind)], order='id desc', limit=1)

    def succeed(self, task):
        result = {'schema_version': '1', 'evidence': EVIDENCE}
        if task.task_type == 'product.facts':
            result.update(facts=[{'name': 'garment', 'value': 'dress', 'evidence_ids': ['e']}], uncertainties=[])
        elif task.task_type == 'research.keywords':
            result.update(status='complete', keywords=[{'text': 'vestido', 'evidence_ids': ['e']}])
        else:
            result.update(status='empty', reason='No verified candidates', candidates=[])
        claim = task.claim([task.task_type])
        self.assertTrue(claim)
        task.complete(claim['lease_token'], claim['lease_epoch'], 'complete-' + str(task.id), result)

    def test_unknown_facts_retry_preserves_history_and_is_idempotent(self):
        detail = self.collection()
        task = self.task(detail, 'product.facts')
        claim = task.claim(['product.facts'])
        task.checkpoint(claim['lease_token'], claim['lease_epoch'], 'first', {'request_id': 'facts-original', 'state': 'unknown'})
        task.wait(claim['lease_token'], claim['lease_epoch'], 'wait', {'code': 'external_result_unknown', 'message': 'unknown'})
        worker = self.env['res.users'].create({'name': 'Generic retry worker', 'login': 'generic-retry-worker',
            'company_id': task.company_id.id, 'company_ids': [(6, 0, task.company_id.ids)],
            'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        api = self.tasks.with_user(worker)
        result = api.retry_unknown_tasks_batch(['product.facts'], 20)
        self.assertEqual(len(result['retried']), 1)
        replacement = self.tasks.browse(result['retried'][0]['task_id'])
        self.assertEqual(replacement, task)
        self.assertEqual(replacement.task_type, task.task_type)
        self.assertEqual(replacement.payload, task.payload)
        self.assertEqual(task.request_ids.state, 'unknown')
        self.assertEqual(api.retry_unknown_tasks_batch(['product.facts'], 20)['retried'], [])
        next_claim = task.claim(['product.facts'])
        self.assertEqual(next_claim['id'], task.id)
        self.assertGreater(next_claim['lease_epoch'], claim['lease_epoch'])
        with self.assertRaises(ValidationError):
            task.checkpoint(next_claim['lease_token'], next_claim['lease_epoch'], 'reuse', {'request_id': 'facts-original', 'state': 'succeeded'})
        task.checkpoint(next_claim['lease_token'], next_claim['lease_epoch'], 'first', {'request_id': 'facts-second', 'state': 'succeeded'})
        task.complete(next_claim['lease_token'], next_claim['lease_epoch'], 'finish', {'schema_version':'1', 'evidence':EVIDENCE,
            'facts':[{'name':'garment','value':'dress','evidence_ids':['e']}], 'uncertainties':[]})
        self.assertEqual(task.state, 'succeeded')
        self.assertEqual(task.request_ids.filtered(lambda x: x.request_id == 'facts-original').state, 'unknown')

    def test_keyword_and_image_unknown_retry_manual_and_batch(self):
        detail = self.collection()
        self.succeed(self.task(detail, 'product.facts'))
        self.start(detail)
        keyword = self.task(detail, 'research.keywords')
        image = self.tasks.create({'case_id': detail['id'], 'snapshot_id': detail['snapshot']['id'],
            'task_type': 'image.generate', 'payload': {'schema_version': '1', 'source_artifact_ids': ['1'],
            'target_artifact_id': '2', 'generation_plan_id': '3'}})
        for task in (keyword, image):
            claim = task.claim([task.task_type])
            task.checkpoint(claim['lease_token'], claim['lease_epoch'], 'initial', {'request_id': 'unknown-' + str(task.id), 'state': 'unknown'})
            task.wait(claim['lease_token'], claim['lease_epoch'], 'wait', {'code': 'external_result_unknown', 'message': 'unknown'})
        first = keyword.resume()
        self.assertEqual(keyword.resume(), first)
        self.assertEqual(self.tasks.browse(first['task_id']).task_type, 'research.keywords')
        worker = self.env['res.users'].create({'name': 'Generic image worker', 'login': 'generic-image-worker',
            'company_id': image.company_id.id, 'company_ids': [(6, 0, image.company_id.ids)],
            'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        api = self.tasks.with_user(worker)
        self.assertEqual(api.retry_unknown_tasks_batch(['research.keywords'], 20)['retried'], [])
        other_before = self.env['res.company'].create({'name': 'Other retry company before'})
        outside_before = self.env['res.users'].create({'name': 'Outside worker before', 'login': 'outside-retry-before',
            'company_id': other_before.id, 'company_ids': [(6, 0, other_before.ids)],
            'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        self.assertEqual(self.tasks.with_user(outside_before).with_context(allowed_company_ids=other_before.ids).retry_unknown_tasks_batch(['image.generate'])['retried'], [])
        result = api.retry_unknown_tasks_batch(['image.generate'], 20)
        self.assertEqual(result['retried'][0]['source_task_id'], image.id)
        self.assertEqual(self.tasks.browse(result['retried'][0]['task_id']).payload, image.payload)
        self.assertEqual(result['retried'][0]['task_id'], image.id)
        self.assertEqual(first['task_id'], keyword.id)
        for retried in (keyword, image):
            next_claim = retried.claim([retried.task_type])
            self.assertEqual(next_claim['id'], retried.id)
            self.assertEqual(next_claim['lease_epoch'], 2)
            retried.checkpoint(next_claim['lease_token'], next_claim['lease_epoch'], 'initial',
                {'request_id':'second-' + str(retried.id), 'state':'unknown'})
            retried.wait(next_claim['lease_token'], next_claim['lease_epoch'], 'wait',
                {'code':'external_result_unknown', 'message':'unknown again'})
            self.assertEqual(retried.retry_unknown_task()['task_id'], retried.id)
            self.assertEqual(retried.state, 'queued')
        with self.assertRaises(Exception):
            self.tasks.with_user(self.env.ref('base.public_user')).retry_unknown_tasks_batch(['image.generate'])
        other = self.env['res.company'].create({'name': 'Other retry company'})
        outside = self.env['res.users'].create({'name': 'Outside worker', 'login': 'outside-retry-worker',
            'company_id': other.id, 'company_ids': [(6, 0, other.ids)],
            'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        self.assertEqual(self.tasks.with_user(outside).with_context(allowed_company_ids=other.ids).retry_unknown_tasks_batch(['image.generate'])['retried'], [])

    def test_unknown_research_resume_creates_scoped_task_preserving_request(self):
        detail = self.collection()
        self.succeed(self.task(detail, 'product.facts'))
        self.start(detail)
        self.succeed(self.task(detail, 'research.keywords'))
        self.start(detail)
        task = self.task(detail, 'research.bestseller')
        claim = task.claim(['research.bestseller'])
        task.checkpoint(claim['lease_token'], claim['lease_epoch'], 'first', {'request_id': 'retry-original', 'state': 'unknown'})
        task.wait(claim['lease_token'], claim['lease_epoch'], 'wait', {'code': 'external_result_unknown', 'message': 'unknown'})
        receipt = task.resume()
        replacement = self.tasks.browse(receipt['task_id'])
        self.assertEqual(replacement, task)
        self.assertEqual(replacement.state, 'queued')
        self.assertFalse(replacement.scoped_research)
        self.assertEqual(replacement.payload, task.payload)
        self.assertEqual(replacement.dependency_ids, task.dependency_ids)
        self.assertEqual(task.state, 'queued')
        self.assertEqual(task.request_ids.state, 'unknown')
        self.assertEqual(task.resume(), receipt)

    def test_scoped_worker_retries_unknown_in_authorized_company_once(self):
        detail = self.collection()
        self.succeed(self.task(detail, 'product.facts'))
        self.start(detail)
        self.succeed(self.task(detail, 'research.keywords'))
        self.start(detail)
        task = self.task(detail, 'research.bestseller')
        claim = task.claim(['research.bestseller'])
        task.checkpoint(claim['lease_token'], claim['lease_epoch'], 'first', {'request_id': 'batch-original', 'state': 'dispatched'})
        task.wait(claim['lease_token'], claim['lease_epoch'], 'wait', {'code': 'external_result_unknown', 'message': 'unknown'})
        worker = self.env['res.users'].create({'name': 'Batch retry worker', 'login': 'batch-retry-worker',
            'company_id': task.company_id.id, 'company_ids': [(6, 0, task.company_id.ids)],
            'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_research_worker').id])]})
        api = self.tasks.with_user(worker)
        self.assertEqual(len(api.retry_unknown_research_batch(1)['retried']), 1)
        self.assertEqual(api.retry_unknown_research_batch(1)['retried'], [])
        with self.assertRaises(ValidationError):
            api.retry_unknown_research_batch(0)

    def test_old_audited_replacement_does_not_requeue_historical_source(self):
        detail = self.collection()
        task = self.task(detail, 'product.facts')
        lease = task.claim(['product.facts'])
        task.checkpoint(lease['lease_token'], lease['lease_epoch'], 'old-checkpoint', {'request_id':'old-record','state':'unknown'})
        task.wait(lease['lease_token'], lease['lease_epoch'], 'old-wait', {'code':'external_result_unknown','message':'unknown'})
        successor = self.tasks.create({'case_id':task.case_id.id, 'snapshot_id':task.snapshot_id.id,
            'company_id':task.company_id.id, 'task_type':task.task_type, 'payload':task.payload})
        self.env['meli.optimization.event'].sudo()._append({'task_id':task.id, 'company_id':task.company_id.id,
            'submission_key':'legacy-retry', 'digest':'legacy', 'kind':'unknown_task_retry',
            'payload':{}, 'receipt':{'task_id':successor.id, 'source_task_id':task.id}, 'worker_uid':self.env.uid})
        self.assertIn(task.id, self.tasks._historical_retry_sources(task.company_id.ids))
        with self.assertRaises(ValidationError):
            task.retry_unknown_task()
        self.assertEqual(task.state, 'waiting')
        self.assertEqual(task.request_ids.state, 'unknown')
        self.assertEqual(successor.state, 'queued')

    def test_batch_retry_continues_after_bad_task(self):
        from unittest.mock import patch
        for _ in range(2):
            detail = self.collection()
            self.succeed(self.task(detail, 'product.facts'))
            self.start(detail)
            self.succeed(self.task(detail, 'research.keywords'))
            self.start(detail)
            task = self.task(detail, 'research.bestseller')
            claim = task.claim(['research.bestseller'])
            task.checkpoint(claim['lease_token'], claim['lease_epoch'], 'initial', {'request_id': 'bad-batch', 'state': 'unknown'})
            task.wait(claim['lease_token'], claim['lease_epoch'], 'wait', {'code': 'external_result_unknown', 'message': 'unknown'})
            if _ == 0: bad = task
        worker = self.env['res.users'].create({'name': 'Fault retry worker', 'login': 'fault-retry-worker',
            'company_id': task.company_id.id, 'company_ids': [(6, 0, task.company_id.ids)],
            'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_research_worker').id])]})
        original = type(task)._retry_unknown_research
        def fail_first(record, **kwargs):
            if record.id == bad.id: raise ValidationError('invalid dependency')
            return original(record, **kwargs)
        with patch.object(type(task), '_retry_unknown_research', fail_first):
            result = self.tasks.with_user(worker).retry_unknown_research_batch(1)
        self.assertEqual(len(result['retried']), 1)
        self.assertEqual(result['retried'][0]['source_task_id'], task.id)
        self.assertEqual(result['failed'][0]['task_id'], bad.id)
        event = self.env['meli.optimization.event'].search([('task_id', '=', task.id), ('kind', '=', 'unknown_research_retry')])
        self.assertEqual(event.worker_uid, worker)

    def test_page_blocked_task_only_restarts_on_operator_resume(self, verified=False):
        detail = self.collection()
        self.succeed(self.task(detail, 'product.facts'))
        self.start(detail)
        self.succeed(self.task(detail, 'research.keywords'))
        self.start(detail)
        task = self.task(detail, 'research.bestseller')
        worker = self.env['res.users'].create({'name': 'Browser wait worker', 'login': 'page-block-worker',
            'company_id': task.company_id.id, 'company_ids': [(6, 0, task.company_id.ids)],
            'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_research_worker').id])]})
        task.prepare_scoped_research()
        scoped = task.with_user(worker)
        lease = scoped.claim(['research.bestseller'])
        token, epoch = lease['lease_token'], lease['lease_epoch']
        scoped.checkpoint(token, epoch, 'intent', {'request_id': 'paused-original', 'state': 'unknown'})
        scoped.wait(token, epoch, 'wait', {'code': 'captcha_required', 'message': 'Complete the page challenge'})
        self.assertEqual(scoped.retry_unknown_research_batch(1)['retried'], [])
        self.assertEqual(self.start(detail)['code'], 'captcha_required')
        self.assertEqual(task.state, 'waiting')
        self.assertFalse(task.request_ids.retry_superseded)
        if verified:
            with self.assertRaises(ValidationError):scoped.resume_after_verified_captcha('other',epoch)
            with self.assertRaises(ValidationError):scoped.resume_after_verified_captcha('paused-original',epoch+1)
            self.assertEqual(scoped.resume_after_verified_captcha('paused-original',epoch)['task_id'],task.id)
            self.assertEqual(scoped.resume_after_verified_captcha('paused-original',epoch)['state'],'queued')
            with self.assertRaises(AccessError):scoped.resume_after_verified_captcha('other',epoch)
        else:self.assertEqual(task.resume()['task_id'], task.id)
        self.assertEqual(task.state, 'queued')
        self.assertTrue(task.request_ids.retry_superseded)
        next_claim = scoped.claim(['research.bestseller'])
        self.assertEqual(next_claim['lease_epoch'], epoch + 1)
        with self.assertRaisesRegex(ValidationError, 'Active research request'):
            scoped.research_artifact(next_claim['lease_token'], next_claim['lease_epoch'], 'paused-original',
                                     'late-image', {'payload': {'item_id': '123'}})
        with self.assertRaisesRegex(ValidationError, 'Active or succeeded request'):
            scoped.seal_scoped_manifest(next_claim['lease_token'], next_claim['lease_epoch'],
                                        'paused-original', ['late-image'])

    def test_captcha_verified_resume_is_scoped_to_original_request(self):
        self.test_page_blocked_task_only_restarts_on_operator_resume(verified=True)

    def test_manual_reconciliation_of_interrupted_read_only_research(self):
        detail = self.collection()
        self.succeed(self.task(detail, 'product.facts'))
        self.start(detail)
        self.succeed(self.task(detail, 'research.keywords'))
        self.start(detail)
        task = self.task(detail, 'research.bestseller')
        claim = task.claim(['research.bestseller'])
        task.checkpoint(claim['lease_token'], claim['lease_epoch'], 'intent', {'request_id': 'original', 'state': 'unknown'})
        evidence = {'read_only_browser_confirmed': True, 'no_upload_confirmed': True, 'explanation': 'Reviewed host journal and browser export; no upload was attempted.'}
        with self.assertRaises(ValidationError):
            task.reconcile_interrupted_research('original', 'too-early', evidence)
        task.wait(claim['lease_token'], claim['lease_epoch'], 'wait', {'code': 'external_result_unknown', 'message': 'interrupted'})
        with self.assertRaises(ValidationError):
            task.reconcile_interrupted_research('wrong', 'wrong', evidence)
        with self.assertRaises(ValidationError):
            task.reconcile_interrupted_research('original', 'manual-1', dict(evidence, no_upload_confirmed=False))
        result = task.reconcile_interrupted_research('original', 'manual-1', evidence)
        self.assertEqual(result['request_state'], 'failed')
        self.assertEqual(task.state, 'waiting')
        self.assertEqual(task.request_ids.state, 'failed')
        self.assertEqual(task.reconcile_interrupted_research('original', 'manual-1', evidence), result)
        with self.assertRaises(ValidationError):
            task.reconcile_interrupted_research('original', 'manual-1', dict(evidence, explanation='changed'))
        with self.assertRaises(ValidationError):
            task.reconcile_interrupted_research('original', 'different', evidence)
        events = self.env['meli.optimization.event'].search([('task_id', '=', task.id), ('kind', '=', 'manual_reconciliation')])
        self.assertEqual(len(events), 1)
        self.assertEqual(events.payload['evidence'], evidence)
        task.resume()
        self.assertEqual(task.state, 'queued')
        self.succeed(task)
        self.assertEqual(task.state, 'succeeded')
        self.assertFalse(task.status_detail, 'successful retry must clear obsolete waiting reason')

    def test_repeated_start_reuses_queued_task_without_changing_snapshot(self):
        detail = self.collection()
        original = copy.deepcopy(detail['snapshot'])
        task = self.task(detail, 'product.facts')
        for _ in range(2):
            result = self.start(detail)
            self.assertEqual((result['task_id'], result['state']), (task.id, 'queued'))
            self.assertTrue(result['reused'])
        self.assertEqual(self.ui.workbench_detail(detail['id'])['snapshot'], original)
        self.assertEqual(len(self.tasks.search([('case_id', '=', detail['id'])])), 1)

    def test_stale_snapshot_or_revision_and_archived_case_are_rejected(self):
        detail = self.collection()
        for snapshot, revision in [(detail['snapshot']['id'] + 1, detail['revision']),
                                   (detail['snapshot']['id'], detail['revision'] + 1),
                                   (True, detail['revision']), (detail['snapshot']['id'], True)]:
            with self.assertRaises(ValidationError):
                self.ui.workbench_collection_start(detail['id'], snapshot, revision)
        self.task(detail, 'product.facts').cancel()
        self.ui.workbench_collection_archive(detail['id'], True, detail['revision'])
        with self.assertRaises(ValidationError):
            self.start(detail)

    def test_legacy_missing_config_reports_missing_data_without_new_tasks(self):
        detail = self.collection(configured=False)
        response = self.start(detail)
        self.assertEqual(response['code'], 'collection_data_missing')
        self.assertIn('配置', response['message'])
        self.assertFalse(response['can_start'])
        self.assertEqual(len(self.tasks.search([('case_id', '=', detail['id'])])), 0)

    def test_archived_collection_cannot_restart(self):
        detail = self.collection()
        self.task(detail, 'product.facts').cancel()
        self.ui.workbench_collection_archive(detail['id'], True, detail['revision'])
        with self.assertRaises(ValidationError):
            self.start(detail)

    def test_invalid_source_or_platform_thresholds_do_not_start(self):
        for updates in ({'source_image_artifact_ids': []}, {'source_sku': ''},
                        {'site_id': 'SHEIN_US'},
                        {'collection_config': {'platform': 'mercado-libre-mx'}},
                        {'source_image_artifact_ids': ['999999999']}):
            detail = self.collection()
            case = self.ui.browse(detail['id'])
            payload = dict(detail['snapshot']['payload'], **updates)
            # Append invalid legacy observations without altering immutable snapshots.
            # Snapshot creation correctly rejects absent artifact refs; seed historical
            # invalid payload via trusted internal append to test the start boundary.
            snap = self.env['meli.optimization.snapshot']._append({
                'case_id': case.id, 'company_id': case.company_id.id,
                'payload': payload, 'digest': 'legacy-invalid'})
            response = self.ui.workbench_collection_start(case.id, snap.id, case.revision)
            self.assertEqual(response['code'], 'collection_data_missing')
            self.assertEqual(len(self.tasks.search([('case_id', '=', case.id)])), 1)

    def test_homepage_strategy_is_versioned_and_rejects_three_page_config(self):
        from types import SimpleNamespace
        detail=self.collection()
        case=self.ui.browse(detail['id'])
        payload=copy.deepcopy(detail['snapshot']['payload'])
        payload['collection_config'].update(strategy='card-home-v4',max_pages_per_query=1)
        self.assertNotIn('collection_config',case._collection_start_missing(SimpleNamespace(payload=payload)))
        payload['collection_config']['max_pages_per_query']=3
        self.assertIn('collection_config',case._collection_start_missing(SimpleNamespace(payload=payload)))

    def test_running_waiting_failed_cancelled_are_reused_without_resume(self):
        for state in ('running', 'waiting', 'failed', 'cancelled'):
            detail = self.collection()
            task = self.task(detail, 'product.facts')
            task._set({'state': state, 'status_detail': {'code': 'capability_missing', 'message': 'No configured provider'}})
            response = self.start(detail)
            self.assertEqual((response['state'], response['task_id']), (state, task.id))
            self.assertEqual(response['code'], 'capability_missing')
            self.assertEqual(task.state, state)
            self.assertEqual(len(self.tasks.search([('case_id', '=', detail['id'])])), 1)

    def test_successful_stages_auto_advance_and_completed_research_is_reused(self):
        detail = self.collection()
        for kind, next_kind in [('product.facts', 'research.keywords'), ('research.keywords', 'research.bestseller')]:
            self.succeed(self.task(detail, kind))
            response = self.start(detail)
            self.assertEqual(response['stage'], next_kind)
            self.assertEqual(response['state'], 'queued')
        self.succeed(self.task(detail, 'research.bestseller'))
        for _ in range(2):
            response = self.start(detail)
            self.assertEqual(response['state'], 'succeeded')
            self.assertEqual(response['code'], 'collection_complete')
        self.assertEqual(len(self.tasks.search([('case_id', '=', detail['id'])])), 3)

    def test_start_repairs_successful_stage_missing_derived_task(self):
        detail = self.collection()
        facts = self.task(detail, 'product.facts')
        facts._set({'state': 'succeeded', 'result': {'schema_version': '1', 'evidence': EVIDENCE,
            'facts': [{'name': 'garment', 'value': 'dress', 'evidence_ids': ['e']}], 'uncertainties': []}})
        for _ in range(2):
            response = self.start(detail)
            self.assertEqual(response['stage'], 'research.keywords')
        self.assertEqual(len(self.tasks.search([('case_id', '=', detail['id'])])), 2)

    def test_new_snapshot_starts_facts_once_without_using_old_tasks(self):
        detail = self.collection()
        self.env['meli.optimization.snapshot'].create({
            'case_id': detail['id'], 'payload': detail['snapshot']['payload']})
        detail = self.ui.workbench_detail(detail['id'])
        first = self.start(detail)
        self.assertFalse(first['reused'])
        self.assertEqual(first['stage'], 'product.facts')
        second = self.start(detail)
        self.assertEqual(first['task_id'], second['task_id'])
        self.assertEqual(len(self.tasks.search([('case_id', '=', detail['id'])])), 2)

    def test_malformed_legacy_platform_reports_missing_data_instead_of_crashing(self):
        detail = self.collection()
        payload = copy.deepcopy(detail['snapshot']['payload'])
        payload['collection_config']['platform'] = {'unknown': 'platform'}
        self.env['meli.optimization.snapshot'].create({'case_id': detail['id'], 'payload': payload})
        detail = self.ui.workbench_detail(detail['id'])
        response = self.start(detail)
        self.assertEqual(response['code'], 'collection_data_missing')
        self.assertIn('platform', response['missing_fields'])
        self.assertEqual(len(self.tasks.search([('case_id', '=', detail['id'])])), 1)

    def test_empty_keywords_stop_without_new_provider_work(self):
        detail = self.collection()
        self.succeed(self.task(detail, 'product.facts'))
        keywords = self.task(detail, 'research.keywords')
        claim = keywords.claim(['research.keywords'])
        keywords.complete(claim['lease_token'], claim['lease_epoch'], 'empty-keywords',
            {'schema_version': '1', 'evidence': EVIDENCE, 'status': 'empty',
             'reason': 'No keyword evidence', 'keywords': []})
        for _ in range(2):
            response = self.start(detail)
            self.assertEqual(response['code'], 'keywords_empty')
            self.assertEqual(response['state'], 'succeeded')
        self.assertFalse(self.task(detail, 'research.bestseller'))

    def test_unknown_external_request_blocks_advance_without_reissuing(self):
        detail = self.collection()
        facts = self.task(detail, 'product.facts')
        facts._set({'state': 'waiting'})
        request = self.env['meli.optimization.external.request']._append({
            'company_id': self.env.company.id, 'task_id': facts.id,
            'request_id': 'unknown-request', 'state': 'unknown'})
        response = self.start(detail)
        self.assertEqual(response['code'], 'external_result_unknown')
        self.assertEqual(response['state'], 'waiting')
        self.assertEqual(request.state, 'unknown')
        self.assertEqual(len(self.tasks.search([('case_id', '=', detail['id'])])), 1)

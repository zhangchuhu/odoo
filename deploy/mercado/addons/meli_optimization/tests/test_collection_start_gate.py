"""One-click gates collection work without changing regular optimization."""
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import ValidationError

IMAGE = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGgAAACEAIHJde6SAAAAAElFTkSuQmCC'
EVIDENCE = [{'id': 'e', 'source': 'snapshot', 'observed_at': '2026-11-15T00:00:00Z'}]

@tagged('post_install', '-at_install')
class CollectionStartGateCase(TransactionCase):
    def setUp(self):
        super().setUp()
        self.cases = self.env['meli.optimization.case']
        self.tasks = self.env['meli.optimization.task']

    def collection(self):
        return self.cases.workbench_collection_create({'garment':'dress', 'source_sku':'SKU',
            'image_data':IMAGE, 'image_mime':'image/png', 'platform':'mercado-libre-mx',
            'min_sold':100, 'min_reviews':0, 'min_rating':0, 'result_limit':5})

    def tasks_for(self, detail):
        return self.tasks.search([('case_id','=',detail['id'])])

    def start(self, detail):
        return self.cases.workbench_collection_start(detail['id'], detail['snapshot']['id'], detail['revision'])

    def complete(self, task, result):
        claim = task.claim([task.task_type])
        self.assertTrue(claim)
        task.complete(claim['lease_token'], claim['lease_epoch'], 'gate-complete-'+str(task.id), result)

    def test_create_does_not_enqueue_until_one_click_then_advances(self):
        detail = self.collection()
        self.assertFalse(self.tasks_for(detail))
        self.start(detail)
        facts = self.tasks_for(detail).filtered(lambda t: t.task_type == 'product.facts')
        self.assertEqual(len(facts), 1)
        self.complete(facts, {'schema_version':'1','evidence':EVIDENCE,
            'facts':[{'name':'garment','value':'dress','evidence_ids':['e']}], 'uncertainties':[]})
        keyword = self.tasks_for(detail).filtered(lambda t: t.task_type == 'research.keywords')
        self.assertEqual(len(keyword), 1)
        self.complete(keyword, {'schema_version':'1','evidence':EVIDENCE,'status':'complete',
            'keywords':[{'text':'vestido','evidence_ids':['e']}]})
        self.assertEqual(len(self.tasks_for(detail).filtered(lambda t: t.task_type == 'research.bestseller')),1)

    def test_legacy_queued_facts_cannot_be_claimed_until_start(self):
        detail = self.collection()
        self.start(detail)
        task = self.tasks_for(detail).filtered(lambda t: t.task_type == 'product.facts')
        self.cases.browse(detail['id'])._set({'collection_started_snapshot_id':False})
        self.assertFalse(task.claim(['product.facts']))
        self.start(detail)
        self.assertEqual(self.tasks_for(detail).filtered(lambda t: t.task_type=='product.facts'),task)
        self.assertEqual(task.claim(['product.facts'])['id'],task.id)

    def test_legacy_completed_steps_and_queued_research_are_reused(self):
        detail = self.collection()
        self.start(detail)
        facts = self.tasks_for(detail).filtered(lambda t: t.task_type == 'product.facts')
        self.complete(facts, {'schema_version':'1','evidence':EVIDENCE,
            'facts':[{'name':'garment','value':'dress','evidence_ids':['e']}], 'uncertainties':[]})
        keyword = self.tasks_for(detail).filtered(lambda t: t.task_type == 'research.keywords')
        self.complete(keyword, {'schema_version':'1','evidence':EVIDENCE,'status':'complete',
            'keywords':[{'text':'vestido','evidence_ids':['e']}]})
        research = self.tasks_for(detail).filtered(lambda t: t.task_type == 'research.bestseller')
        self.assertEqual(len(research),1)
        # Simulate a pre-upgrade record whose workflow had progressed without a click.
        self.cases.browse(detail['id'])._set({'collection_started_snapshot_id':False})
        self.assertFalse(research.claim(['research.bestseller']))
        self.start(detail)
        self.assertEqual(self.tasks_for(detail).filtered(lambda t: t.task_type=='research.bestseller'),research)
        self.assertEqual(research.claim(['research.bestseller'])['id'],research.id)

    def test_new_snapshot_start_does_not_authorize_old_queued_research(self):
        detail = self.collection()
        self.start(detail)
        facts = self.tasks_for(detail).filtered(lambda t: t.task_type == 'product.facts')
        self.complete(facts, {'schema_version':'1','evidence':EVIDENCE,
            'facts':[{'name':'garment','value':'dress','evidence_ids':['e']}], 'uncertainties':[]})
        keyword = self.tasks_for(detail).filtered(lambda t: t.task_type == 'research.keywords')
        self.complete(keyword, {'schema_version':'1','evidence':EVIDENCE,'status':'complete',
            'keywords':[{'text':'vestido','evidence_ids':['e']}]})
        old = self.tasks_for(detail).filtered(lambda t: t.task_type == 'research.bestseller')
        self.assertTrue(self.tasks.research_queue_precheck()['claimable'])
        self.env['meli.optimization.snapshot'].create({'case_id':detail['id'], 'payload':detail['snapshot']['payload']})
        newer = self.cases.workbench_detail(detail['id'])
        # Merely appending a new snapshot invalidates the previous start.
        self.assertFalse(old.claim(['research.bestseller']))
        self.assertFalse(self.tasks.research_queue_precheck()['claimable'])
        self.start(newer)
        self.assertFalse(old.claim(['research.bestseller']))
        self.assertFalse(self.tasks.research_queue_precheck()['claimable'])

    def test_legacy_unknown_retry_waits_for_one_click(self):
        detail = self.collection()
        self.start(detail)
        task = self.tasks_for(detail).filtered(lambda t: t.task_type == 'product.facts')
        self.cases.browse(detail['id'])._set({'collection_started_snapshot_id':False})
        task._set({'state':'waiting'})
        self.env['meli.optimization.external.request']._append({
            'company_id':self.env.company.id, 'task_id':task.id,
            'request_id':'gate-legacy-unknown', 'state':'unknown'})
        # The UI's manual resume must respect the same start gate as the
        # background retry path and leave the original request untouched.
        with self.assertRaises(ValidationError):
            task.resume()
        self.assertEqual(task.state, 'waiting')
        self.assertEqual(task.request_ids.state, 'unknown')
        worker = self.env['res.users'].create({'name':'Gate worker','login':'collection-gate-worker',
            'company_id':self.env.company.id,'company_ids':[(6,0,self.env.company.ids)],
            'group_ids':[(6,0,[self.env.ref('meli_optimization.group_worker').id])]})
        api = self.tasks.with_user(worker)
        self.assertEqual(api.retry_unknown_tasks_batch(['product.facts'],20)['retried'],[])
        self.assertEqual(task.state,'waiting')
        self.start(detail)
        retried = api.retry_unknown_tasks_batch(['product.facts'],20)['retried']
        self.assertEqual([r['task_id'] for r in retried],[task.id])
        self.assertEqual(task.state,'queued')

    def test_direct_task_entry_rejects_collection_before_start(self):
        detail = self.collection()
        for kind in ('product.facts', 'research.keywords', 'research.bestseller'):
            with self.assertRaises(ValidationError):
                self.cases.workbench_task(detail['id'], kind, {})
        self.assertFalse(self.tasks_for(detail))

    def test_direct_model_create_rejects_unstarted_collection(self):
        detail = self.collection()
        vals = {'case_id': detail['id'], 'snapshot_id': detail['snapshot']['id'],
                'task_type': 'product.facts', 'payload': {'schema_version':'1',
                'product_snapshot_id': str(detail['snapshot']['id'])}}
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self.tasks.create(vals)
        self.assertFalse(self.tasks_for(detail))

    def test_old_snapshot_backlog_does_not_starve_ready_work(self):
        detail = self.collection()
        self.start(detail)
        old = detail['snapshot']['id']
        self.tasks.create([{'case_id': detail['id'], 'snapshot_id': old,
            'task_type': 'product.facts', 'payload': {'schema_version': '1', 'product_snapshot_id': str(old)}}
            for _ in range(100)])
        self.env['meli.optimization.snapshot'].create({'case_id': detail['id'], 'payload': detail['snapshot']['payload']})
        ready = self.collection()
        self.start(ready)
        claimed = self.tasks.claim(['product.facts'])
        self.assertEqual(claimed['snapshot_id'], ready['snapshot']['id'])

    def test_previously_claimed_lease_is_fenced_before_start(self):
        detail = self.collection()
        self.start(detail)
        task = self.tasks_for(detail).filtered(lambda t: t.task_type == 'product.facts')
        lease = task.claim(['product.facts'])
        self.cases.browse(detail['id'])._set({'collection_started_snapshot_id': False})
        with self.assertRaises(ValidationError):
            task.heartbeat(lease['lease_token'], lease['lease_epoch'])
        with self.assertRaises(ValidationError):
            task.checkpoint(lease['lease_token'], lease['lease_epoch'], 'test', {})
        with self.assertRaises(ValidationError):
            task.complete(lease['lease_token'], lease['lease_epoch'], 'test', {})
        task.cancel()
        self.assertEqual(task.state, 'cancelled')

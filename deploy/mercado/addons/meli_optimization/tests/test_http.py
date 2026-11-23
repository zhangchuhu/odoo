import json
from datetime import timedelta
from odoo import fields
from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install', 'optimization_http')
class OptimizationHTTPCase(HttpCase):
    def test_targeted_claim_respects_id_type_and_worker_role(self):
        batch = self.env['meli.optimization.batch'].create({'name': 'Targeted claim'})
        case = self.env['meli.optimization.case'].create({'name': 'Targeted', 'batch_id': batch.id})
        snap = self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {}})
        values = {'case_id': case.id, 'snapshot_id': snap.id, 'task_type': 'product.facts',
                  'payload': {'schema_version': '1', 'product_snapshot_id': str(snap.id)}}
        first = self.env['meli.optimization.task'].create(values)
        second = self.env['meli.optimization.task'].create(values)
        worker = self.env['res.users'].create({'name': 'Target worker', 'login': 'target-worker',
            'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        key = self.env['res.users.apikeys'].with_user(worker).sudo()._generate('rpc', 'target', fields.Datetime.now() + timedelta(hours=1))
        headers = {'Authorization': 'Bearer ' + key}
        url = '/internal/meli-optimization/claim'
        self.assertEqual(self.url_open(url, json={'capabilities': ['product.facts'], 'task_id': True}, headers=headers).status_code, 400)
        target_url = '/internal/meli-optimization/tasks/%s/claim' % second.id
        ordinary = self.env['res.users'].create({'name': 'Target ordinary', 'login': 'target-ordinary',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        ordinary_key = self.env['res.users.apikeys'].with_user(ordinary).sudo()._generate('rpc', 'target ordinary', fields.Datetime.now() + timedelta(hours=1))
        denied = self.url_open(target_url, json={'capabilities': ['product.facts']}, headers={'Authorization': 'Bearer ' + ordinary_key})
        self.assertEqual(denied.status_code, 403)
        reconcile_url = '/api/meli/optimization/tasks/%s/reconcile-interrupted-research' % second.id
        reconciliation = {'request_id': 'r', 'idempotency_key': 'k', 'evidence': {'read_only_browser_confirmed': True, 'no_upload_confirmed': True, 'explanation': 'verified'}}
        denied = self.url_open(reconcile_url, json=reconciliation, headers={'Authorization': 'Bearer ' + ordinary_key})
        self.assertEqual(denied.status_code, 403)
        wrong = self.url_open(target_url, json={'capabilities': ['research.bestseller']}, headers=headers)
        self.assertFalse(wrong.json()['data'])
        response = self.url_open(target_url, json={'capabilities': ['product.facts']}, headers=headers)
        self.assertEqual(response.json()['data']['id'], second.id)
        first.invalidate_recordset()
        self.assertEqual(first.state, 'queued')
        again = self.url_open(target_url, json={'capabilities': ['product.facts']}, headers=headers)
        self.assertFalse(again.json()['data'])

    def test_targeted_claim_cannot_cross_company_or_skip_dependencies(self):
        company = self.env['res.company'].create({'name': 'Target isolated company'})
        other = self.env['res.company'].create({'name': 'Target other company'})
        def tasks(co):
            env = self.env(context=dict(self.env.context, allowed_company_ids=[co.id]))
            batch = env['meli.optimization.batch'].create({'name': 'Target company', 'company_id': co.id})
            case = env['meli.optimization.case'].create({'name': 'Target company', 'batch_id': batch.id, 'company_id': co.id})
            snap = env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {}})
            vals = {'case_id': case.id, 'snapshot_id': snap.id, 'task_type': 'product.facts', 'payload': {'schema_version': '1', 'product_snapshot_id': str(snap.id)}}
            a = env['meli.optimization.task'].create(vals)
            b = env['meli.optimization.task'].create(dict(vals, dependency_ids=[(6, 0, [a.id])]))
            return a, b
        first, dependent = tasks(company)
        foreign, _ = tasks(other)
        worker = self.env['res.users'].create({'name': 'Limited target worker', 'login': 'limited-target-worker', 'company_id': company.id,
            'company_ids': [(6, 0, [company.id])], 'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        key = self.env['res.users.apikeys'].with_user(worker).sudo()._generate('rpc', 'limited target', fields.Datetime.now() + timedelta(hours=1))
        headers = {'Authorization': 'Bearer ' + key}
        def claim(task):
            return self.url_open('/internal/meli-optimization/tasks/%s/claim' % task.id, json={'capabilities': ['product.facts']}, headers=headers)
        self.assertFalse(claim(dependent).json()['data'])
        self.assertEqual(claim(foreign).status_code, 403)
        foreign_url = '/api/meli/optimization/tasks/%s/reconcile-interrupted-research' % foreign.id
        response = self.url_open(foreign_url, json={'request_id': 'r', 'idempotency_key': 'k', 'evidence': {'read_only_browser_confirmed': True, 'no_upload_confirmed': True, 'explanation': 'verified'}}, headers=headers)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(claim(first).json()['data']['id'], first.id)

    def test_stateless_auth_routes_and_bounded_ingress(self):
        user = self.env['res.users'].create({'name': 'API Worker', 'login': 'optimization-http-worker', 'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        key = self.env['res.users.apikeys'].with_user(user).sudo()._generate('rpc', 'test only', fields.Datetime.now() + timedelta(hours=1))
        url = '/api/meli/optimization/capabilities'
        self.assertEqual(self.url_open(url).status_code, 401)
        headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
        response = self.url_open(url, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['data']['live_execution'])
        response = self.url_open('/internal/meli-optimization/claim', json={'capabilities': ['product.facts']}, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['data'])
        response = self.url_open('/internal/meli-optimization/claim', data='[1,2]', headers=headers)
        self.assertEqual(response.status_code, 400)
        response = self.url_open('/internal/meli-optimization/claim', data='{"x":"' + 'x' * 270000 + '"}', headers=headers)
        self.assertEqual(response.status_code, 413)
        response = self.url_open('/api/meli/optimization/cases', json={'batch_name': 'Denied', 'name': 'Denied', 'snapshot': {'item': 'A'}}, headers=headers)
        self.assertEqual(response.status_code, 403)

    def test_host_heartbeat_requires_worker_role(self):
        url = '/internal/meli-optimization/host/heartbeat'
        payload = {'configuration': 'configured', 'authentication': 'unknown', 'last_result': 'idle'}
        self.assertEqual(self.url_open(url, json=payload).status_code, 401)
        user = self.env['res.users'].create({'name': 'Ordinary user', 'login': 'host-health-ordinary', 'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        key = self.env['res.users.apikeys'].with_user(user).sudo()._generate('rpc', 'test only', fields.Datetime.now() + timedelta(hours=1))
        self.assertEqual(self.url_open(url, json=payload, headers={'Authorization': 'Bearer ' + key}).status_code, 403)
        worker = self.env['res.users'].create({'name': 'Host worker', 'login': 'host-health-worker', 'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        worker_key = self.env['res.users.apikeys'].with_user(worker).sudo()._generate('rpc', 'test only', fields.Datetime.now() + timedelta(hours=1))
        worker_headers = {'Authorization': 'Bearer ' + worker_key}
        self.assertEqual(self.url_open(url, json=payload, headers=worker_headers).status_code, 200)
        self.assertEqual(self.url_open(url, json=dict(payload, authentication='valid'), headers=worker_headers).status_code, 409)

    def test_worker_reads_snapshot_payload(self):
        batch = self.env['meli.optimization.batch'].create({'name': 'HTTP snapshot'})
        case = self.env['meli.optimization.case'].create({'name': 'HTTP snapshot', 'batch_id': batch.id})
        snapshot = self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {'item_id': 'MLM-A'}})
        user = self.env['res.users'].create({'name': 'Snapshot Worker', 'login': 'snapshot-http-worker', 'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        key = self.env['res.users.apikeys'].with_user(user).sudo()._generate('rpc', 'test only', fields.Datetime.now() + timedelta(hours=1))
        response = self.url_open('/api/meli/optimization/snapshots/' + str(snapshot.id), headers={'Authorization': 'Bearer ' + key})
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertEqual(response.json()['data']['payload'], {'item_id': 'MLM-A'})
        self.assertEqual(response.json()['data']['digest'], snapshot.digest)

    def test_wait_fail_resume_cancel_transport(self):
        batch = self.env['meli.optimization.batch'].create({'name': 'Lifecycle HTTP'})
        case = self.env['meli.optimization.case'].create({'name': 'Lifecycle', 'batch_id': batch.id})
        snap = self.env['meli.optimization.snapshot'].create({'case_id': case.id, 'payload': {}})
        task = self.env['meli.optimization.task'].create({'case_id': case.id, 'snapshot_id': snap.id,
            'task_type': 'product.facts', 'payload': {'schema_version': '1', 'product_snapshot_id': str(snap.id)}})
        user = self.env['res.users'].create({'name': 'Lifecycle editor', 'login': 'lifecycle-http',
            'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_editor').id])]})
        key = self.env['res.users.apikeys'].with_user(user).sudo()._generate('rpc', 'test only', fields.Datetime.now() + timedelta(hours=1))
        headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
        def post(path, data):
            return self.url_open(path, data=json.dumps(data), headers=headers)
        claim = post('/internal/meli-optimization/claim', {'capabilities': ['product.facts']}).json()['data']
        self.assertEqual(claim['id'], task.id)
        worker_path = '/internal/meli-optimization/tasks/' + str(task.id)
        editor_path = '/api/meli/optimization/tasks/' + str(task.id)
        envelope = {'lease_token': claim['lease_token'], 'lease_epoch': claim['lease_epoch'],
            'submission_key': 'wait', 'body': {'code': 'capability_missing', 'message': 'Provider unavailable'}}
        response = post(worker_path + '/wait', envelope)
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertEqual(response.json()['data']['state'], 'waiting')
        self.assertEqual(post(worker_path + '/wait', envelope).json(), response.json())
        response = post(editor_path + '/resume', {})
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertEqual(response.json()['data']['state'], 'queued')
        claim = post('/internal/meli-optimization/claim', {'capabilities': ['product.facts']}).json()['data']
        envelope.update(lease_token=claim['lease_token'], lease_epoch=claim['lease_epoch'], submission_key='fail')
        response = post(worker_path + '/fail', envelope)
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertEqual(response.json()['data']['state'], 'failed')
        self.assertEqual(post(editor_path + '/cancel', {}).status_code, 409)
        queued = self.env['meli.optimization.task'].create({'case_id': case.id, 'snapshot_id': snap.id,
            'task_type': 'product.facts', 'payload': task.payload})
        response = post('/api/meli/optimization/tasks/' + str(queued.id) + '/cancel', {})
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertEqual(response.json()['data']['state'], 'cancelled')

    def test_actual_standalone_worker_completes_facts_over_http(self):
        import sys
        import tempfile
        from pathlib import Path
        worker_root = str(Path(__file__).resolve().parents[3])
        if worker_root not in sys.path:
            sys.path.insert(0, worker_root)
        from optimization_worker.client import Client
        from optimization_worker.runtime import Worker
        batch = self.env['meli.optimization.batch'].create({'name': 'Standalone Worker HTTP'})
        case = self.env['meli.optimization.case'].create({'name': 'Worker', 'batch_id': batch.id})
        snap = self.env['meli.optimization.snapshot'].create({'case_id': case.id,
            'payload': {'item_id': 'MLM-WORKER', 'category': 'dress'}})
        task = self.env['meli.optimization.task'].create({'case_id': case.id, 'snapshot_id': snap.id,
            'task_type': 'product.facts', 'payload': {'schema_version': '1', 'product_snapshot_id': str(snap.id)}})
        user = self.env['res.users'].create({'name': 'Standalone worker', 'login': 'standalone-worker-http',
            'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        key = self.env['res.users.apikeys'].with_user(user).sudo()._generate('rpc', 'test only',
            fields.Datetime.now() + timedelta(hours=1))
        with tempfile.TemporaryDirectory(prefix='worker-httpcase-') as tmp, self.allow_requests(all_requests=True):
            worker = Worker(Client(self.base_url(), key), Path(tmp) / 'journal.sqlite', capabilities=('product.facts',))
            self.assertEqual(worker.once(), 'succeeded')
            self.assertEqual(worker.once(), 'idle')
            uploaded = worker.client.create_artifact(case.id, payload={'facts': 'fixture observation'})
            archived = worker.client.artifact(uploaded['artifact_id'])
            self.assertEqual(archived['case_id'], case.id)
            self.assertEqual(archived['company_id'], case.company_id.id)
        task.invalidate_recordset()
        from ..domain import canonical_hash
        facts_id = self.env['meli.optimization.case'].workbench_archive(case.id, task.id, canonical_hash(task.result))
        keywords = self.env['meli.optimization.task'].create({'case_id': case.id, 'snapshot_id': snap.id,
            'task_type': 'research.keywords', 'payload': {'schema_version': '1',
            'facts_artifact_id': str(facts_id), 'site_id': 'MLM'},
            'dependency_ids': [(6, 0, [task.id])]})
        from optimization_worker.providers import OfflineProvider
        output = {'schema_version': '1', 'evidence': [{'id': 'fixture', 'source': 'offline:test',
            'observed_at': '2026-11-15T00:00:00Z'}], 'status': 'complete',
            'keywords': [{'text': 'vestido', 'evidence_ids': ['fixture']}]}
        with tempfile.TemporaryDirectory(prefix='worker-provider-httpcase-') as tmp, self.allow_requests(all_requests=True):
            worker = Worker(Client(self.base_url(), key), Path(tmp) / 'journal.sqlite',
                capabilities=('research.keywords',), providers={'research.keywords':
                OfflineProvider(offline=True, results={'research.keywords': output})})
            self.assertEqual(worker.once(), 'succeeded')
        self.env.invalidate_all()
        self.assertEqual(keywords.state, 'succeeded')
        self.assertEqual(task.state, 'succeeded')
        values = {fact['name']: fact['value'] for fact in task.result['facts']}
        self.assertEqual(values['item_id'], 'MLM-WORKER')

    def test_title_flow_real_worker_and_text_host(self):
        import sys, tempfile
        from pathlib import Path
        from unittest.mock import patch
        worker_root = str(Path(__file__).resolve().parents[3])
        if worker_root not in sys.path:
            sys.path.insert(0, worker_root)
        from optimization_worker.client import Client
        from optimization_worker.runtime import Worker
        from optimization_worker.host_session import SessionStore, SessionProvider
        from optimization_worker.text_host import run_one
        ui = self.env['meli.optimization.case']
        detail = ui.workbench_create({'shop': 'Test', 'item_id': 'MLM-FLOW', 'title': 'Vestido', 'site_id': 'MLM'})
        case = ui.browse(detail['id'])
        ui.workbench_title_flow(case.id)
        user = self.env['res.users'].create({'name': 'Flow host worker', 'login': 'flow-host-http', 'group_ids': [(6, 0, [self.env.ref('meli_optimization.group_worker').id])]})
        key = self.env['res.users.apikeys'].with_user(user).sudo()._generate('rpc', 'test only', fields.Datetime.now() + timedelta(hours=1))
        evidence = [{'id': 'e', 'source': 'test:observation', 'observed_at': '2026-11-15T00:00:00Z'}]
        class Done: returncode = 0
        def runner(args, **kw):
            if '"task_type": "research.keywords"' in kw['input']:
                body = {'schema_version':'1', 'status':'complete', 'keywords':[{'text':'vestido', 'evidence_ids':['e']}], 'evidence':evidence}
            else:
                body = {'schema_version':'1', 'candidates':[{'text':'Vestido elegante modelo %s' % i, 'evidence_ids':['e']} for i in range(10)], 'evidence':evidence}
            Path(args[args.index('-o')+1]).write_text(json.dumps(body))
            return Done()
        with tempfile.TemporaryDirectory(prefix='flow-host-http-') as tmp, self.allow_requests(all_requests=True), patch.dict('os.environ', {'OPTIMIZATION_CODEX_HOME': tmp}):
            client = Client(self.base_url(), key)
            journal = Path(tmp) / 'worker.sqlite'
            store = SessionStore(Path(tmp) / 'host.sqlite', client.identity)
            provider = SessionProvider(store)
            worker = Worker(client, journal, providers={'research.keywords': provider, 'title.generate': provider})
            self.assertEqual(worker.once(), 'succeeded')
            self.assertEqual(worker.once(), 'host_pending')
            self.assertEqual(run_one(store, client, journal, runner), 'submitted')
            self.assertEqual(worker.once(), 'succeeded')
            self.assertEqual(worker.once(), 'host_pending')
            self.assertEqual(run_one(store, client, journal, runner), 'submitted')
            self.assertEqual(worker.once(), 'succeeded')
        self.env.invalidate_all()
        tasks = self.env['meli.optimization.task'].search([('case_id','=',case.id)])
        self.assertEqual({t.task_type for t in tasks}, {'product.facts', 'research.keywords', 'title.generate'})
        self.assertTrue(all(t.state == 'succeeded' for t in tasks))
        title = tasks.filtered(lambda t: t.task_type == 'title.generate')
        self.assertEqual(len(title.result['candidates']), 10)
        kw_artifact = self.env['meli.optimization.artifact'].browse(int(title.payload['keywords_artifact_id']))
        self.assertEqual(kw_artifact.result_task_id.task_type, 'research.keywords')
        self.assertEqual(kw_artifact.result_snapshot_id, title.snapshot_id)

    def test_research_queue_precheck_authorization_and_company_scope(self):
        company=self.env['res.company'].create({'name':'Queue check company'})
        foreign=self.env['res.company'].create({'name':'Queue check foreign'})
        def case_task(co):
            env=self.env(context=dict(self.env.context,allowed_company_ids=[co.id]))
            batch=env['meli.optimization.batch'].create({'name':'precheck'})
            case=env['meli.optimization.case'].create({'name':'precheck','batch_id':batch.id})
            snap=env['meli.optimization.snapshot'].create({'case_id':case.id,'payload':{}})
            task=env['meli.optimization.task'].create({'case_id':case.id,'snapshot_id':snap.id,'task_type':'product.facts','payload':{'schema_version':'1','product_snapshot_id':str(snap.id)}})
            return task
        foreign_task=case_task(foreign);foreign_task._set({'task_type':'research.bestseller'})
        local_task=case_task(company)
        def headers(login,groups):
            user=self.env['res.users'].create({'name':login,'login':login,'company_id':company.id,'company_ids':[(6,0,[company.id])],'group_ids':[(6,0,groups)]})
            key=self.env['res.users.apikeys'].with_user(user).sudo()._generate('rpc','queue precheck',fields.Datetime.now()+timedelta(hours=1))
            return {'Authorization':'Bearer '+key}
        worker=headers('queue-probe-worker',[self.env.ref('meli_optimization.group_worker').id])
        ordinary=headers('queue-probe-ordinary',[self.env.ref('base.group_user').id])
        url='/internal/meli-optimization/research-queue'
        self.assertEqual(self.url_open(url).status_code,401)
        self.assertEqual(self.url_open(url,headers=ordinary).status_code,403)
        self.assertEqual(self.url_open(url,headers=worker).json()['data'],{'claimable':False})
        local_task._set({'task_type':'research.bestseller'})
        self.assertEqual(self.url_open(url,headers=worker).json()['data'],{'claimable':True})
        dependency=case_task(company)
        local_task._set({'dependency_ids':[(4,dependency.id)]})
        self.assertEqual(self.url_open(url,headers=worker).json()['data'],{'claimable':False})
        dependency._set({'state':'succeeded'})
        self.assertEqual(self.url_open(url,headers=worker).json()['data'],{'claimable':True})
        local_task.case_id._set({'collection_archived':True})
        self.assertEqual(self.url_open(url,headers=worker).json()['data'],{'claimable':False})
        local_task.case_id._set({'collection_archived':False})
        local_task._set({'state':'waiting'})
        self.assertEqual(self.url_open(url,headers=worker).json()['data'],{'claimable':False})
        local_task._set({'state':'running','lease_expires_at':fields.Datetime.now()-timedelta(seconds=1)})
        self.assertEqual(self.url_open(url,headers=worker).json()['data'],{'claimable':True})
        local_task._set({'lease_expires_at':fields.Datetime.now()+timedelta(minutes=1)})
        self.assertEqual(self.url_open(url,headers=worker).json()['data'],{'claimable':False})
        local_task._set({'lease_expires_at':fields.Datetime.now()-timedelta(seconds=1)})
        external=self.env['meli.optimization.external.request']._append({'task_id':local_task.id,'company_id':company.id,'request_id':'precheck-unresolved','state':'unknown'})
        self.assertEqual(self.url_open(url,headers=worker).json()['data'],{'claimable':False})
        external._set({'state':'failed'})
        self.assertEqual(self.url_open(url,headers=worker).json()['data'],{'claimable':True})

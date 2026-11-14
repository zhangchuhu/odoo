from datetime import timedelta
from odoo import fields
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import AccessError, ValidationError


@tagged('post_install', '-at_install')
class OptimizationQueueCase(TransactionCase):
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

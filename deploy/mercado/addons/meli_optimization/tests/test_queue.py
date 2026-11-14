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

    def test_resume_does_not_unblock_unknown_and_control_authorization(self):
        task = self._review_task()
        self.assertTrue(callable(getattr(task, 'wait', None)), 'fenced wait operation missing')
        lease = task.claim(['product.facts'])
        args = (lease['lease_token'], lease['lease_epoch'])
        task.checkpoint(*args, 'unknown', {'request_id': 'remote', 'state': 'unknown'})
        task.wait(*args, 'wait', {'code': 'uncertain', 'message': 'Reconciliation required'})
        with self.assertRaises(ValidationError):
            task.resume()
        self.assertEqual(task.state, 'waiting')
        self.assertFalse(task.claim(['product.facts']))
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

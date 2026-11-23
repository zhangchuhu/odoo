"""Crash-aware single-task runtime with independent lease heartbeats."""
import copy
import json
import queue
import threading
import uuid

from .client import APIError
from .domain import DomainError, canonical_hash
from .handlers import CapabilityMissing, TASK_TYPES, prepare, product_facts, validate_output
from .journal import Journal
from .host_session import SessionPending, SessionFailure, SessionUnknown, SessionBlocked

REGISTERED_RUN_PHASES = ('host_pending', 'calling', 'ready', 'result', 'validated', 'provider_terminal', 'provider_missing')

class LeaseLost(Exception):
    def __init__(self, status=None):
        self.status = status
        super().__init__('Lease rejected' if status is None else 'Lease rejected (status %s)' % status)


def continue_registered_run(client, record, journal):
    """Persist the recovery intent before rotating an expired run's token."""
    if (record.get('done') or record.get('recovery_pending') or record.get('pending')
            or record.get('phase') not in REGISTERED_RUN_PHASES):
        raise ValueError('Registered in-progress request required')
    claim = record['claim']
    if not record.get('scoped_continue_intent'):
        record['scoped_continue_intent'] = uuid.uuid4().hex
        journal.save(record)
    proof = client.continue_registered_research(claim, record['request_id'], record['scoped_continue_intent'])
    recovered = proof.get('claim') if isinstance(proof, dict) else None
    if (not isinstance(recovered, dict) or proof.get('request_id') != record['request_id']
            or not isinstance(recovered.get('lease_token'), str) or not recovered['lease_token']
            or recovered.get('task_type') != 'research.bestseller'
            or any(k not in claim or recovered.get(k) != claim[k] for k in ('id', 'company_id', 'snapshot_id', 'input_hash', 'task_type', 'payload', 'lease_epoch'))):
        raise ValueError('Registered research continuation does not match local run')
    record.update(claim=recovered)
    record.pop('scoped_continue_intent')
    journal.save(record)
    return recovered


class _Heartbeat:
    def __init__(self, client, claim):
        self.client, self.claim = client, claim
        self.stop, self.lost = threading.Event(), threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.wait(max(.01, min(30, self.claim['heartbeat_seconds']))):
            try:
                self.client.heartbeat(self.claim)
            except APIError as exc:
                self.error = exc
                self.lost.set()
                return

    def check(self):
        if self.lost.is_set():
            if self.error.status in (403, 404, 409):
                raise LeaseLost(self.error.status)
            raise self.error


class Worker:
    def __init__(self, client, journal_path, *, providers=None, provider_timeout=90, capabilities=TASK_TYPES, task_id=None, park=None, single_active=False, resume_waiting=None):
        if not 0 < provider_timeout <= 3600:
            raise ValueError('Invalid provider timeout')
        self.client, self.journal_path = client, journal_path
        self.providers = dict(providers or {})
        self.provider_timeout, self.capabilities = provider_timeout, tuple(capabilities)
        if task_id is not None and (type(task_id) is not int or task_id < 1):
            raise ValueError('Invalid target task')
        self.task_id = task_id
        self.park = park
        if type(single_active) is not bool or (single_active and park is not None):
            raise ValueError('Single-active workers cannot park tasks')
        self.single_active = single_active
        self.resume_waiting = resume_waiting
        if not self.capabilities or set(self.capabilities) - set(TASK_TYPES):
            raise ValueError('Unsupported capabilities')

    def once(self, *, allow_claim=True):
        journal = Journal(self.journal_path, self.client.identity)
        safe_to_sync = True
        try:
            active = journal.active()
            if self.single_active and len(active) > 1:
                safe_to_sync = False
                return 'reconciliation_required'
            if self.task_id is not None and any(r['claim']['id'] != self.task_id for r in active):
                raise ValueError('Journal contains a different active task')
            if any(r['claim'].get('task_type') not in self.capabilities for r in active):
                return 'foreign_active'
            for provider in {id(p): p for p in self.providers.values()}.values():
                if getattr(provider, 'durable_session', False):
                    provider.synchronize(active)
            if not active and allow_claim and self.resume_waiting and self.resume_waiting(journal):
                active = journal.active()
            runnable = active
            if self.park is not None and self.task_id is None:
                parked = [r for r in active if self.park(r)]
                for item in parked:
                    self._run(journal, item)
                active = journal.active()
                runnable = [r for r in active if not self.park(r)]
            if runnable:
                record = runnable[0]
                if record.get('recovery_pending'):
                    return 'reconciliation_required'
            else:
                if not allow_claim or len(active) >= 20:
                    return 'idle'
                claim = self.client.claim(self.capabilities, task_id=self.task_id) if self.task_id is not None else self.client.claim(self.capabilities)
                if claim is False:
                    return 'idle'
                if self.task_id is not None and claim.get('id') != self.task_id:
                    raise ValueError('Claim returned a different task')
                record = {'claim': claim, 'phase': 'prepare', 'request_id': str(uuid.uuid4()), 'sequence': 0}
                journal.save(record)
            return self._run(journal, record)
        finally:
            try:
                if safe_to_sync:
                    for provider in {id(p): p for p in self.providers.values()}.values():
                        if getattr(provider, 'durable_session', False):
                            provider.synchronize(journal.active())
            finally:
                journal.close()

    def loop(self, stop=None, poll_seconds=5):
        stop = stop or threading.Event()
        while not stop.is_set():
            try:
                self.once()
            except APIError:
                pass  # Pending durable submissions are replayed on the next cycle.
            stop.wait(poll_seconds)

    def _run(self, journal, record):
        claim = record['claim']
        pulse = _Heartbeat(self.client, claim)
        pulse_started = False
        def save():
            journal.save(record)
        def submit(operation, body):
            pulse.check()
            record['sequence'] += 1
            record['pending'] = {'operation': operation, 'envelope': {
                'lease_token': claim['lease_token'], 'lease_epoch': claim['lease_epoch'],
                'submission_key': '%s:%s:%s' % (record['request_id'], record['sequence'], operation),
                'body': body}}
            save()
            return replay()
        def replay():
            pending = record['pending']
            receipt = self.client.submit(claim, pending['operation'], pending['envelope'])
            operation = pending['operation']
            if operation in ('wait', 'fail', 'complete'):
                record.update(done=True, outcome=receipt['state'])
            elif pending['envelope']['body']['state'] == 'unknown':
                record['phase'] = 'ready'
            else:
                record['phase'] = 'terminal_checkpoint'
            del record['pending']
            save()
            return receipt
        def checkpoint(state):
            return submit('checkpoint', {'request_id': record['request_id'], 'state': state})
        def status(operation, code, message):
            submit(operation, {'code': code, 'message': message})
            return record['outcome']
        try:
            if record.get('pending'):
                pulse.thread.start()
                pulse_started = True
                replay()  # Exact replay is safe even after cancellation or lease revocation.
                if record.get('done'):
                    return record['outcome']
            try:
                self.client.heartbeat(claim)  # Fence before reading inputs or dispatching a provider.
            except APIError as exc:
                provider = self.providers.get(claim['task_type'])
                scoped = claim['task_type'] == 'research.bestseller' and getattr(self.client, 'research_ledger_enabled', False)
                recovery = 'continue_registered_research' if scoped else 'recover_lease'
                if not (exc.status == 409 and claim['task_type'] in ('research.bestseller', 'research.keywords', 'product.facts')
                        and record.get('phase') in (REGISTERED_RUN_PHASES if scoped else ('host_pending',)) and not record.get('pending')
                        and getattr(provider, 'durable_session', False)
                        and callable(getattr(self.client, recovery, None))):
                    raise
                if scoped:
                    claim = continue_registered_run(self.client, record, journal)
                    pulse.claim = claim
                    self.client.heartbeat(claim)
                else:
                    if not record.get('recovery_intent_id'):
                        record['recovery_intent_id'] = uuid.uuid4().hex
                        save()
                    recovered = self.client.recover_lease(claim, record['request_id'], record['recovery_intent_id'])
                    fields = ('id', 'task_type', 'input_hash', 'snapshot_id', 'company_id', 'payload')
                    if (not isinstance(recovered, dict) or any(recovered.get(k) != claim.get(k) for k in fields)
                            or type(recovered.get('lease_epoch')) is not int or recovered['lease_epoch'] <= claim['lease_epoch']
                            or not recovered.get('lease_token') or recovered['lease_token'] == claim['lease_token']):
                        raise ValueError('Recovered lease does not match original request')
                    journal.replace_claim(record, recovered)
                    claim = recovered
                    pulse.claim = recovered
                    record.pop('recovery_intent_id', None)
                    save()
                    self.client.heartbeat(claim)
            if not pulse_started:
                pulse.thread.start()
                pulse_started = True
            if record['phase'] == 'calling' and getattr(self.providers.get(claim['task_type']), 'durable_session', False):
                record['phase'] = 'ready'
                save()
            if record['phase'] == 'host_pending':
                record['phase'] = 'ready'
                save()
            if record['phase'] == 'calling':
                return status('wait', 'external_result_unknown', 'Provider dispatch was interrupted; operator reconciliation required')
            if record['phase'] == 'prepare':
                kind = claim['task_type']
                if kind != 'product.facts' and kind not in self.providers:
                    return status('wait', 'capability_missing', 'No configured provider for ' + kind)
                record['context'] = prepare(self.client, claim)
                save()
                if kind == 'product.facts' and kind not in self.providers:
                    record['output'] = product_facts(record['context'])
                    record['phase'] = 'result'
                    save()
                else:
                    checkpoint('unknown')
            if record['phase'] == 'ready':
                pulse.check()
                record['phase'] = 'calling'
                save()  # A crash beyond this point must never automatically call the provider again.
                results = queue.Queue(maxsize=1)
                provider = self.providers.get(claim['task_type'])
                if provider is None:
                    return status('wait', 'external_result_unknown', 'Prepared dispatch requires operator reconciliation')
                def invoke():
                    try:
                        results.put(('ok', provider.run(claim['task_type'], copy.deepcopy(claim['payload']),
                                     copy.deepcopy(record['context']), record['request_id'])))
                    except SessionFailure as exc:
                        results.put(('terminal', exc.code))
                    except SessionBlocked as exc:
                        results.put(('blocked', exc))
                    except SessionPending:
                        results.put(('host_pending', None))
                    except SessionUnknown as exc:
                        results.put(('unknown', exc.user_message()))
                    except CapabilityMissing:
                        results.put(('missing', None))
                    except Exception:
                        results.put(('unknown', None))
                threading.Thread(target=invoke, daemon=True).start()
                try:
                    outcome, output = results.get(timeout=self.provider_timeout)
                except queue.Empty:
                    outcome, output = 'unknown', None
                pulse.check()
                if outcome == 'terminal':
                    record['provider_failed'] = output
                    record['phase'] = 'provider_terminal'
                    save()
                elif outcome == 'blocked':
                    if output.code == 'window_registration_failed':
                        checkpoint('failed')
                    return status('wait', output.code, output.message)
                elif outcome == 'host_pending':
                    record['phase'] = 'host_pending'
                    save()
                    return 'host_pending'
                elif outcome == 'unknown':
                    return status('wait', 'external_result_unknown', output or 'Provider outcome unknown; operator reconciliation required')
                elif outcome == 'missing':
                    record['provider_missing'] = True
                    record['phase'] = 'provider_missing'
                    save()
                else:
                    try:
                        canonical_hash(output)
                        if len(json.dumps(output).encode()) > 240 * 1024:
                            raise ValueError('Provider result exceeds submission bound')
                    except (DomainError, ValueError, TypeError):
                        record['invalid'] = True
                        output = {}
                    record['output'] = output
                    record['phase'] = 'result'
                    save()
            if record['phase'] == 'provider_terminal':
                checkpoint('failed')
            if record['phase'] == 'provider_missing':
                checkpoint('failed')
            if record['phase'] == 'result':
                try:
                    validate_output(self.client, claim, record['output'], record['request_id'])
                except (DomainError, ValueError, KeyError, TypeError):
                    record['invalid'] = True
                record['phase'] = 'validated'
                save()
            if record['phase'] == 'validated' and (claim['task_type'] != 'product.facts' or claim['task_type'] in self.providers):
                checkpoint('failed' if record.get('invalid') else 'succeeded')
            if record.get('provider_missing'):
                return status('wait', 'capability_missing', 'Provider reports unavailable capability')
            if record.get('provider_failed'):
                return status('fail', record['provider_failed'], 'Host confirmed terminal outcome; preserved original request, no retry')
            if record.get('invalid'):
                return status('fail', 'invalid_result', 'Provider output failed domain or artifact validation')
            submit('complete', record['output'])
            return record['outcome']
        except LeaseLost as exc:
            record.update(done=True, outcome='lease_lost', lease_diagnostic={'source': 'heartbeat', 'status': exc.status})
            save()
            return 'lease_lost'
        except APIError as exc:
            if exc.status in (403, 404, 409):
                record.update(done=True, outcome='lease_lost', lease_diagnostic={'source': 'api', 'status': exc.status})
                save()
                return 'lease_lost'
            record['lease_diagnostic'] = {'source': 'api_transient', 'status': exc.status}
            save()
            raise
        except DomainError as exc:
            if exc.code == 'invalid_image_input':
                return status('fail', 'invalid_input', exc.message)
            return status('fail', 'invalid_input', 'Task input or referenced artifact is invalid')
        except (ValueError, KeyError, TypeError):
            return status('fail', 'invalid_input', 'Task input or referenced artifact is invalid')
        finally:
            pulse.stop.set()
            if pulse_started:
                pulse.thread.join(timeout=self.client.timeout + 1)

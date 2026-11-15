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
from .host_session import SessionPending


class LeaseLost(Exception):
    pass


class _Heartbeat:
    def __init__(self, client, claim):
        self.client, self.claim = client, claim
        self.stop, self.lost = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.wait(max(.01, min(30, self.claim['heartbeat_seconds']))):
            try:
                self.client.heartbeat(self.claim)
            except APIError:
                self.lost.set()
                return

    def check(self):
        if self.lost.is_set():
            raise LeaseLost()


class Worker:
    def __init__(self, client, journal_path, *, providers=None, provider_timeout=90, capabilities=TASK_TYPES):
        if not 0 < provider_timeout <= 3600:
            raise ValueError('Invalid provider timeout')
        self.client, self.journal_path = client, journal_path
        self.providers = dict(providers or {})
        self.provider_timeout, self.capabilities = provider_timeout, tuple(capabilities)
        if not self.capabilities or set(self.capabilities) - set(TASK_TYPES):
            raise ValueError('Unsupported capabilities')

    def once(self):
        journal = Journal(self.journal_path, self.client.identity)
        try:
            active = journal.active()
            if active:
                record = active[0]
            else:
                claim = self.client.claim(self.capabilities)
                if claim is False:
                    return 'idle'
                record = {'claim': claim, 'phase': 'prepare', 'request_id': str(uuid.uuid4()), 'sequence': 0}
                journal.save(record)
            return self._run(journal, record)
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
        pulse.thread.start()
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
                replay()  # Exact replay is safe even after cancellation or lease revocation.
                if record.get('done'):
                    return record['outcome']
            self.client.heartbeat(claim)  # Fence before reading inputs or dispatching a provider.
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
                if kind == 'product.facts':
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
                    except SessionPending:
                        results.put(('host_pending', None))
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
                if outcome == 'host_pending':
                    record['phase'] = 'host_pending'
                    save()
                    return 'host_pending'
                if outcome == 'unknown':
                    return status('wait', 'external_result_unknown', 'Provider outcome unknown; operator reconciliation required')
                if outcome == 'missing':
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
            if record['phase'] == 'provider_missing':
                checkpoint('failed')
            if record['phase'] == 'result':
                try:
                    validate_output(self.client, claim, record['output'], record['request_id'])
                except (DomainError, ValueError, KeyError, TypeError):
                    record['invalid'] = True
                record['phase'] = 'validated'
                save()
            if record['phase'] == 'validated' and claim['task_type'] != 'product.facts':
                checkpoint('failed' if record.get('invalid') else 'succeeded')
            if record.get('provider_missing'):
                return status('wait', 'capability_missing', 'Provider reports unavailable capability')
            if record.get('invalid'):
                return status('fail', 'invalid_result', 'Provider output failed domain or artifact validation')
            submit('complete', record['output'])
            return record['outcome']
        except LeaseLost:
            record.update(done=True, outcome='lease_lost')
            save()
            return 'lease_lost'
        except APIError as exc:
            if exc.status in (403, 404, 409):
                record.update(done=True, outcome='lease_lost')
                save()
                return 'lease_lost'
            raise
        except (DomainError, ValueError, KeyError, TypeError):
            return status('fail', 'invalid_input', 'Task input or referenced artifact is invalid')
        finally:
            pulse.stop.set()
            pulse.thread.join(timeout=self.client.timeout + 1)

"""Single-process orchestration; adapters and singleton fencing are external.

No default probe/dispatcher is supplied. Probe implementations must be trusted,
bounded, and cover actual browser, vision and writeback capabilities.
"""
import logging
import threading

from .client import APIError

_logger = logging.getLogger(__name__)

def _report_failure(stage, exc):
    # Exception messages may contain request data or credentials.
    _logger.error('research host stage=%s exception_type=%s', stage, type(exc).__name__)

class ClaimGate:
    def __init__(self, probe):
        self.probe = probe
        self.lock = threading.RLock()
        self.enabled = True

    def _ready(self):
        try:
            return self.enabled and self.probe() is True
        except Exception:
            return False

    def ready(self):
        with self.lock:
            return self._ready()

    def claim(self, call, *, on_unavailable=None):
        # Keep check and request initiation under the same lock used by revoke.
        with self.lock:
            if self._ready(): return call()
            if on_unavailable is not None: on_unavailable()
            return False

    def revoke(self):
        with self.lock:
            self.enabled = False

class GatedClient:
    def __init__(self, client, gate, queue_check=None):
        self._client, self._gate = client, gate
        self._queue_check = queue_check
        self._status='checking_queue';self._status_lock=threading.Lock()
    def _set_status(self, status):
        with self._status_lock: self._status=status
    def queue_status(self):
        with self._status_lock: return self._status
    def __getattr__(self, name):
        return getattr(self._client, name)
    def claim(self, capabilities, task_id=None):
        if self._queue_check is not None:
            self._set_status('checking_queue')
            try:
                available=self._queue_check()
                if available is False:
                    self._set_status('idle');return False
                if available is not True:
                    self._set_status('queue_unavailable');return False
            except Exception:
                self._set_status('queue_unavailable')
                return False
        self._set_status('checking_host')
        def invoke():
            result=self._client.claim(capabilities, task_id=task_id) if task_id is not None else self._client.claim(capabilities)
            self._set_status('idle' if result is False else 'running')
            return result
        return self._gate.claim(invoke,on_unavailable=lambda:self._set_status('waiting_for_host'))

class CooperativeResearchHost:
    def __init__(self, gate, worker, dispatch, *, poll_seconds=5, idle_poll_seconds=None, dispatch_needed=None, status_provider=None, isolate_unknown=None):
        from .runtime import Worker
        if isinstance(worker, Worker) and (not isinstance(worker.client, GatedClient) or worker.client._gate is not gate):
            raise ValueError('Worker must use this host claim gate')
        if not 0 < poll_seconds <= 5:
            raise ValueError('Invalid poll interval')
        if idle_poll_seconds is None: idle_poll_seconds=poll_seconds
        if not poll_seconds <= idle_poll_seconds <= 300:
            raise ValueError('Invalid idle poll interval')
        self.gate, self.worker, self.dispatch = gate, worker, dispatch
        self.worker_thread = None
        self.tick_lock = threading.Lock()
        self.stopped = False
        self.worker_failed = threading.Event()
        self.dispatch_failed = False
        self.wake = threading.Event()
        self.poll_seconds = poll_seconds
        self.idle_poll_seconds = idle_poll_seconds
        self.dispatch_needed = dispatch_needed if dispatch_needed is not None else lambda: True
        self.status_provider = status_provider
        self.isolate_unknown = isolate_unknown
        self.last_outcome = None

    def _run_worker(self):
        try:
            while True:
                # Do not run a potentially slow probe before replaying an active
                # journal: GatedClient checks readiness only at a new claim.
                try:
                    outcome = self.worker.once(allow_claim=not self.stopped)
                except APIError as exc:
                    if not exc.transient:
                        raise
                    _logger.warning('research worker stage=odoo_request code=transport_unavailable http_status=%s', exc.status)
                    if self.stopped:
                        break
                    outcome = 'transport_unavailable'
                self.last_outcome = outcome
                # Each tick persists its checkpoint before returning.
                if self.stopped:
                    break
                self.wake.wait(self.idle_poll_seconds if outcome == 'idle' and not self.stopped else self.poll_seconds)
                self.wake.clear()
        except Exception as exc:
            _report_failure('worker', exc)
            self.worker_failed.set()
            self.gate.revoke()

    def tick(self):
        with self.tick_lock:
            if self.worker_failed.is_set() or self.dispatch_failed:
                return 'reconciliation_required'
            if self.stopped:
                return 'waiting'
            if self.worker_thread is None or not self.worker_thread.is_alive():
                self.worker_thread = threading.Thread(target=self._run_worker, name='research-worker', daemon=False)
                self.worker_thread.start()
            if self.worker_failed.is_set():
                return 'reconciliation_required'
            if self.last_outcome == 'reconciliation_required':
                self.gate.revoke()
                return 'reconciliation_required'
            try:
                needed = self.dispatch_needed() is True
            except Exception as exc:
                _report_failure('dispatch_needed', exc)
                self.dispatch_failed = True
                self.gate.revoke()
                return 'reconciliation_required'
            if not needed and self.status_provider is not None:
                if self.last_outcome == 'host_pending': return 'running'
                return self.status_provider()
            if needed and self.gate.ready():
                try:
                    outcome=self.dispatch()
                except Exception as exc:
                    if isinstance(exc, APIError) and exc.transient:
                        return 'waiting'
                    _report_failure('dispatch', exc)
                    self.dispatch_failed = True
                    self.gate.revoke()
                    return 'reconciliation_required'
                if outcome == 'unknown' and self.isolate_unknown is not None:
                    try:
                        isolated = self.isolate_unknown() is True
                    except Exception as exc:
                        if isinstance(exc, APIError) and exc.transient:
                            return 'waiting'
                        _report_failure('isolate_unknown', exc)
                        isolated = False
                    if isolated:
                        self.wake.set()
                        return 'running'
                if outcome == 'unknown':
                    _logger.error('research host stage=dispatch outcome=unknown')
                    self.dispatch_failed = True
                    self.gate.revoke()
                    return 'reconciliation_required'
                if outcome == 'waiting_for_host':
                    return 'waiting'
                return 'running'
            return 'waiting'

    def stop_new_claims(self):
        self.stopped = True
        self.gate.revoke()
        self.wake.set()
        return not (self.worker_thread and self.worker_thread.is_alive())

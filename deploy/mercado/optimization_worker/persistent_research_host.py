"""Experimental dual-process supervisor; NOT SAFE for live task dispatch.

The probe must verify all capabilities, including write authority, and must
enforce its own timeout. No string configuration or heartbeat is sufficient.
The caller must reconcile durable journals before starting this supervisor.
On probe loss this primitive cannot pause a still-running consumer; a verified
quiesce protocol and orphan-process fencing are required before live use.
"""
from .interactive_supervisor import Supervisor

class ResearchHost:
    def __init__(self, lock_path, probe, start):
        self.guard = Supervisor(lock_path, lambda: False, lambda: None)
        self.probe, self.start = probe, start
        self.children = []
        self.reconcile = False

    def __enter__(self):
        self.guard.__enter__()
        return self

    def tick(self):
        if self.guard.lock is None:
            raise RuntimeError('Supervisor lock required')
        if self.reconcile:
            return 'reconciliation_required'
        try:
            ready = self.probe() is True
        except Exception:
            ready = False
        if self.children:
            if not ready or any(p.poll() is not None for p in self.children):
                self.reconcile = True
                return 'reconciliation_required'
            return 'running'
        if not ready:
            return 'waiting'
        try:
            self.children.append(self.start('dispatcher'))
            self.children.append(self.start('worker'))
            if any(not hasattr(p,'poll') or p.poll() is not None for p in self.children):
                raise RuntimeError('Child exited during startup')
        except Exception:
            self.reconcile = True
            return 'reconciliation_required'
        return 'running'

    def __exit__(self, *args):
        for child in reversed(self.children):
            if child and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except Exception:
                    pass
        if any(child and child.poll() is None for child in self.children):
            # Preserve the in-process lock; caller must reconcile before exit.
            raise RuntimeError('Child remains active; reconciliation required')
        self.guard.__exit__(*args)

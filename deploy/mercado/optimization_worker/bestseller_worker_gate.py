"""Research-only worker gate; requires a real executor probe from the caller."""
import threading


class GatedWorker:
    def __init__(self, once, probe, active):
        self._once, self._probe, self._active = once, probe, active

    def once(self):
        # A previously leased task must keep its heartbeat and durable checkpoint
        # even if the external executor goes offline; it cannot claim another task.
        if self._active():
            return self._once(allow_claim=False)
        try:
            ready = self._probe() is True
        except Exception:
            ready = False
        return self._once() if ready else 'waiting_for_host'

    def loop(self, stop=None, poll_seconds=5):
        stop = stop or threading.Event()
        while not stop.is_set():
            self.once()
            stop.wait(poll_seconds)


def build_worker(client, journal_path, store, probe):
    """The caller must keep a supervisor lock over the entire loop.

    The probe must exercise the real executor, not read a configuration flag.
    Separate journals are required for independent workers.
    """
    if not callable(probe):
        raise ValueError('Executor probe required')
    from .host_session import SessionProvider
    from .journal import Journal
    from .runtime import Worker
    worker = Worker(client, journal_path, providers={'research.bestseller': SessionProvider(store)},
                    capabilities=('research.bestseller',))
    def active():
        journal = Journal(journal_path, client.identity)
        try:
            records = journal.active()
            if any(record['claim']['task_type'] != 'research.bestseller' for record in records):
                raise ValueError('Journal contains an unrelated active task')
            return bool(records)
        finally:
            journal.close()
    return GatedWorker(worker.once, probe, active)

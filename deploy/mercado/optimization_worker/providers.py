"""Provider contract only; there are no live vendor/browser integrations.

Injected providers implement run(task_type, payload, context, request_id) ->
domain result. A call may have external effects; every invocation is journaled
and checkpointed first. Providers must bound their own I/O and never log secrets.
The context contains verified snapshot/artifact envelopes, no credentials/client.
"""
import copy
from typing import Protocol


class Provider(Protocol):
    def run(self, task_type: str, payload: dict, context: dict, request_id: str) -> dict: ...


class OfflineProvider:
    """Explicit replay of user-supplied fixture results, never a live fallback."""
    def __init__(self, *, offline=False, results=None):
        if offline is not True:
            raise ValueError('Fixture provider requires explicit offline=True')
        self.results = copy.deepcopy(results or {})

    def run(self, task_type, payload, context, request_id):
        from .handlers import CapabilityMissing
        if task_type not in self.results:
            raise CapabilityMissing('No offline fixture for requested capability')
        output = copy.deepcopy(self.results[task_type])
        if task_type == 'image.generate':
            output['request_id'] = request_id
        return output

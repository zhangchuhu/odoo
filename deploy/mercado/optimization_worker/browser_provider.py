"""Explicit browser-research adapter boundary; no browser or network implementation.

The injected callable must perform actual observations and return a domain result.
A valid envelope proves structure, not that third-party observations are true.
No adapter is installed or advertised by the production CLI by default.
"""
import copy

from .domain import validate_result
from .handlers import CapabilityMissing


class BrowserResearchProvider:
    def __init__(self, adapter):
        if adapter is not None and not callable(adapter):
            raise TypeError('Browser research adapter must be callable')
        self.adapter = adapter

    def run(self, task_type, payload, context, request_id):
        if task_type != 'research.bestseller' or self.adapter is None:
            raise CapabilityMissing('No verified browser research adapter is configured')
        result = self.adapter(copy.deepcopy(payload), copy.deepcopy(context), request_id)
        validate_result(task_type, result)
        return copy.deepcopy(result)

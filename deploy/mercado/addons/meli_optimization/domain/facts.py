"""Arrange supplied snapshot observations without inferring external facts."""
from datetime import datetime, timezone


def product_facts_from_snapshot(snapshot):
    return {'schema_version': '1', 'evidence': [{'id': 'snapshot',
        'source': 'snapshot:' + str(snapshot['snapshot_id']),
        'observed_at': datetime.now(timezone.utc).isoformat()}],
        'facts': [{'name': key, 'value': value, 'evidence_ids': ['snapshot']}
                  for key, value in (snapshot['payload'].items() if isinstance(snapshot['payload'], dict) else [])] or [
                  {'name': 'snapshot_payload', 'value': snapshot['payload'], 'evidence_ids': ['snapshot']}],
        'uncertainties': ['Snapshot fields are supplied observations; external truth has not been verified.']}

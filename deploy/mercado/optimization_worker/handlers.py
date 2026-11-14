"""Resolve verified inputs and validate provider outputs before submission."""
from datetime import datetime, timezone

from .domain import DomainError, validate_input, validate_result

TASK_TYPES = ('product.facts', 'research.keywords', 'research.bestseller', 'gallery.collect',
              'title.generate', 'image.generate', 'image.qc')


class CapabilityMissing(Exception):
    pass


def prepare(client, claim):
    kind, payload = claim['task_type'], claim['payload']
    validate_input(kind, payload)
    snapshot = client.snapshot(claim['snapshot_id'])
    if kind == 'product.facts' and payload['product_snapshot_id'] != str(claim['snapshot_id']):
        raise DomainError('invalid_input', 'Product snapshot does not match claim')
    context = {'snapshot': snapshot, 'artifacts': {}}
    for name, value in payload.items():
        if name.endswith('_artifact_id') or name == 'generation_plan_id':
            context['artifacts'][value] = client.artifact(value)
        elif name == 'source_artifact_ids':
            for reference in value:
                context['artifacts'][reference] = client.artifact(reference)
    return context


def product_facts(context):
    snapshot = context['snapshot']
    return {'schema_version': '1', 'evidence': [{'id': 'snapshot',
        'source': 'snapshot:' + str(snapshot['snapshot_id']),
        'observed_at': datetime.now(timezone.utc).isoformat()}],
        'facts': [{'name': key, 'value': value, 'evidence_ids': ['snapshot']}
                  for key, value in (snapshot['payload'].items() if isinstance(snapshot['payload'], dict) else [])] or [
                  {'name': 'snapshot_payload', 'value': snapshot['payload'], 'evidence_ids': ['snapshot']}],
        'uncertainties': ['Snapshot fields are supplied observations; external truth has not been verified.']}


def validate_output(client, claim, output, request_id=None):
    kind, payload = claim['task_type'], claim['payload']
    validate_result(kind, output)
    if kind == 'gallery.collect':
        if output['reference_item_id'] != payload['reference_item_id'] or output['selected_variant'] != payload['selected_variant']:
            raise DomainError('invalid_result', 'Provider gallery SKU does not match input')
    if kind == 'image.generate' and output['request_id'] != request_id:
        raise DomainError('invalid_result', 'Provider request ID does not match dispatch')
    if kind == 'image.qc' and output['image_artifact_id'] != payload['image_artifact_id']:
        raise DomainError('invalid_result', 'QC image does not match input')
    images = output['images'] if kind == 'gallery.collect' else [output['image']] if kind == 'image.generate' else []
    for image in images:
        artifact = client.artifact(image['artifact_id'])
        if artifact['digest'] != image['sha256'] or artifact.get('mime_type') != image['mime_type'] or not artifact.get('data'):
            raise DomainError('invalid_result', 'Image artifact digest/MIME or binary content mismatch')
    return output

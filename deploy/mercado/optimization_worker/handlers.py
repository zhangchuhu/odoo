"""Resolve verified inputs and validate provider outputs before submission."""
from .domain import DomainError, validate_input, validate_result, product_facts_from_snapshot

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
    snapshot_payload = snapshot.get('payload')
    for reference in (snapshot_payload.get('source_image_artifact_ids', []) if isinstance(snapshot_payload, dict) else []):
        context['artifacts'][str(reference)] = client.artifact(str(reference))
    for name, value in payload.items():
        if name.endswith('_artifact_id') or name == 'generation_plan_id':
            context['artifacts'][value] = client.artifact(value)
        elif name == 'source_artifact_ids':
            for reference in value:
                context['artifacts'][reference] = client.artifact(reference)
    if kind == 'image.qc':
        plan = context['artifacts'][payload['generation_plan_id']].get('payload')
        if isinstance(plan, dict):
            references = []
            for field in ('source_artifact_ids', 'target_artifact_ids'):
                values = plan.get(field, [])
                if not isinstance(values, list) or len(values) > 100 or any(not isinstance(v, str) or not v for v in values):
                    raise DomainError('invalid_input', 'Invalid generation plan original references')
                references.extend(values)
            for reference in references:
                if reference not in context['artifacts']:
                    context['artifacts'][reference] = client.artifact(reference)
    return context


def product_facts(context):
    return product_facts_from_snapshot(context['snapshot'])


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

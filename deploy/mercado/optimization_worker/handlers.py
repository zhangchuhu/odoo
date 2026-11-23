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
    source_refs = []
    if kind != 'image.qc' and isinstance(snapshot_payload, dict):
        source_refs = list(snapshot_payload.get('source_image_artifact_ids') or [])
        if kind == 'product.facts':
            source_refs.extend(row['artifact_id'] for row in (snapshot_payload.get('source_images') or []) if isinstance(row, dict) and row.get('artifact_id'))
    for reference in dict.fromkeys(str(value) for value in source_refs):
        artifact = client.artifact(str(reference))
        if artifact.get('case_id') != snapshot.get('case_id') or artifact.get('company_id') != claim.get('company_id') or not artifact.get('data'):
            raise DomainError('invalid_input', 'Source image ownership does not match task')
        context['artifacts'][str(reference)] = artifact
    for name, value in payload.items():
        if name.endswith('_artifact_id') or name == 'generation_plan_id':
            context['artifacts'][value] = client.artifact(value)
        elif name == 'source_artifact_ids':
            for reference in value:
                context['artifacts'][reference] = client.artifact(reference)
    if kind == 'image.generate':
        images = [*payload['source_artifact_ids'], payload['target_artifact_id']]
        for reference in images:
            image = context['artifacts'][reference]
            if image.get('mime_type') not in ('image/jpeg', 'image/png', 'image/webp') or not image.get('data'):
                raise DomainError('invalid_image_input', '服装参考图和目标构图图必须是已保存的图片。')
        plan = context['artifacts'][payload['generation_plan_id']]
        if (plan.get('mime_type') not in (None, False, 'application/json')
                or not isinstance(plan.get('payload'), dict) or plan.get('data')):
            raise DomainError('invalid_image_input', '生成计划必须是 JSON 格式的计划产物。')
    if kind == 'image.qc':
        plan = context['artifacts'][payload['generation_plan_id']].get('payload')
        if isinstance(plan, dict):
            references = []
            for field in ('source_artifact_ids', 'target_artifact_ids'):
                values = plan.get(field, [])
                if not isinstance(values, list) or len(values) > 100 or any(not isinstance(v, str) or not v for v in values):
                    raise DomainError('invalid_input', 'Invalid generation plan original references')
                if field == 'source_artifact_ids':
                    references.extend(values)
            target = claim.get('generation_target_artifact_id')
            generation = claim.get('generation_task_id')
            if target and type(generation) is int and target in plan.get('target_artifact_ids', []):
                references.append(target)
            for reference in references:
                if reference not in context['artifacts']:
                    context['artifacts'][reference] = client.artifact(reference)
            if target and type(generation) is int and target in plan.get('target_artifact_ids', []):
                image = context['artifacts'][payload['image_artifact_id']]
                original = context['artifacts'][target]
                plan_artifact = context['artifacts'][payload['generation_plan_id']]
                if all(a.get('case_id') == snapshot.get('case_id') and a.get('company_id') == claim.get('company_id') for a in [image, original, plan_artifact] + [context['artifacts'][r] for r in plan.get('source_artifact_ids', [])]) and str(plan.get('snapshot_id')) == str(claim['snapshot_id']):
                    context['qc_target_artifact_id'] = target
                    context['generation_task_id'] = generation
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

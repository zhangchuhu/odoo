"""Resolve garment references from an immutable product snapshot."""

def snapshot_sources(payload):
    refs = payload.get('source_image_artifact_ids') if isinstance(payload, dict) else None
    if not isinstance(refs, list) or not refs or any(type(ref) not in (int, str) or not str(ref).isdigit() or int(ref) < 1 for ref in refs):
        raise ValueError('Product snapshot has no valid source images')
    result = [str(int(ref)) for ref in refs]
    if len(set(result)) != len(result):
        raise ValueError('Duplicate source images')
    return result

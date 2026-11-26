"""Version 1 pure JSON body contracts (not worker transport envelopes).

All bodies require schema_version='1'. Unknown fields and null metric values
are retained; validators return the supplied object without normalization.
All references are opaque nonempty strings; evidence IDs have no fixed prefix.
Evidence is a list of {id, source, observed_at}; source is a nonempty origin
reference, observed_at an ISO-8601 aware UTC timestamp. Every claim carries a
nonempty evidence_ids list resolving into that body's evidence catalog. This
checks referential integrity, not external truth or artifact existence.

Inputs (required fields, all nonempty strings except where stated):
 product.facts: product_snapshot_id
 research.keywords: facts_artifact_id, site_id
 research.bestseller: facts_artifact_id, keywords_artifact_id, site_id
 gallery.collect: reference_item_id, selected_variant (nonempty object)
 title.generate: facts_artifact_id, keywords_artifact_id, language
 image.generate: source_artifact_ids (nonempty unique string list),
                 target_artifact_id, generation_plan_id
 image.qc: image_artifact_id, generation_plan_id

Results require evidence plus these fields:
 product.facts: facts [{name, value (any JSON incl null), evidence_ids}],
                uncertainties (list of nonempty strings)
 research.keywords: status, keywords [{text, evidence_ids}]
 research.bestseller: status, candidates [{platform, item_id, url, raw_url,
   query, position (integer >=1 or null), is_ad (bool or null), sold_display
   (string or null), sold_lower_bound/sold_upper_bound/review_count
   (integer >=0 or null), rating (number >=0 or null), image_url
   (string or null), visual_category (string or null), observed_at,
   selection_reason, evidence_ids}]
 Both research results: status complete/partial/empty; complete requires at
 least one item; partial requires at least one item and reason; empty requires
 zero items and reason. reason is a nonempty string. No sales count is inferred
 from display text. Bounds must be ordered when both known.
 title.generate: candidates [{text, evidence_ids}], exactly ten texts unique
   after casefold/whitespace folding; no unverified platform length limit.
 gallery.collect: reference_item_id, selected_variant (nonempty object),
   observed_at, gallery_manifest_hash (64 hex), completeness_status
   (complete/partial), images [{image_id, order (integer >=0), role,
   artifact_id, source, evidence_ids, mime_type (image/*), width/height
   (positive integers), sha256 (64 hex)}]. IDs/orders are unique; images are
   nonempty; partial requires reason. Role/source are opaque nonempty strings.
 image.generate: request_id, image (same image shape as gallery images)
 image.qc: image_artifact_id, status (passed/failed/review), checks
   [{name, status (passed/failed/unknown), evidence_ids}]. Nonempty checks;
   passed requires every check passed; failed requires at least one failed;
   review requires at least one unknown. Check names are unique.
"""
import hashlib
import json
import math
import re
from datetime import datetime, timedelta


class DomainError(ValueError):
    """Stable machine-readable code and human-readable message."""
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


def _require(condition, code, message):
    if not condition:
        raise DomainError(code, message)


def _json(value, seen=None):
    seen = set() if seen is None else seen
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) in (dict, list):
        _require(id(value) not in seen, 'invalid_json', 'Cyclic JSON value')
        seen.add(id(value))
        if type(value) is dict:
            _require(all(type(k) is str for k in value), 'invalid_json', 'JSON keys must be strings')
            children = value.values()
        else:
            children = value
        for child in children:
            _json(child, seen)
        seen.remove(id(value))
        return
    raise DomainError('invalid_json', 'Expected finite JSON-compatible values')


def canonical_hash(value):
    """SHA-256 of UTF-8 JSON with sorted keys, no spaces, preserved Unicode.

    Array order and JSON numeric representations are significant. Rejects NaN,
    infinity, non-string keys, cycles and Python-only container/scalar types.
    """
    _json(value)
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')
    except UnicodeEncodeError as exc:
        raise DomainError('invalid_json', 'JSON contains invalid Unicode') from exc
    return hashlib.sha256(encoded).hexdigest()


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _utc(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() == timedelta(0)


def _timestamp(value):
    if not isinstance(value, str):
        return False
    try:
        return _utc(datetime.fromisoformat(value.replace('Z', '+00:00')))
    except ValueError:
        return False


def _body(value, code):
    _json(value)
    _require(isinstance(value, dict) and value.get('schema_version') == '1', code,
             'Expected object with schema_version=1')


def _strings(value, code):
    _require(isinstance(value, list) and bool(value) and all(_text(v) for v in value),
             code, 'Expected nonempty string list')
    _require(len(set(value)) == len(value), code, 'Duplicate references')


_INPUTS = {
    'product.facts': ('product_snapshot_id',),
    'research.keywords': ('facts_artifact_id', 'site_id'),
    'research.bestseller': ('facts_artifact_id', 'keywords_artifact_id', 'site_id'),
    'gallery.collect': ('reference_item_id', 'selected_variant'),
    'title.generate': ('facts_artifact_id', 'keywords_artifact_id', 'language'),
    'image.generate': ('source_artifact_ids', 'target_artifact_id', 'generation_plan_id'),
    'image.qc': ('image_artifact_id', 'generation_plan_id'),
}


def _task(task_type):
    _require(isinstance(task_type, str) and task_type in _INPUTS, 'unsupported_task', 'Unsupported task type')


def validate_input(task_type, payload):
    _task(task_type)
    _body(payload, 'invalid_input')
    for name in _INPUTS[task_type]:
        value = payload.get(name)
        if name == 'selected_variant':
            _require(isinstance(value, dict) and bool(value), 'invalid_input', 'selected_variant must identify a SKU')
        elif name == 'source_artifact_ids':
            _strings(value, 'invalid_input')
        else:
            _require(_text(value), 'invalid_input', 'Missing or invalid ' + name)
    return payload


def _evidence(result):
    _body(result, 'invalid_result')
    entries = result.get('evidence')
    _require(isinstance(entries, list), 'invalid_result', 'Evidence catalog required')
    ids = set()
    for entry in entries:
        _require(isinstance(entry, dict) and _text(entry.get('id')) and
                 _text(entry.get('source')) and _timestamp(entry.get('observed_at')),
                 'invalid_result', 'Invalid evidence metadata')
        _require(entry['id'] not in ids, 'invalid_result', 'Duplicate evidence ID')
        ids.add(entry['id'])
    return ids


def _refs(item, ids):
    _require(isinstance(item, dict), 'invalid_result', 'Expected claim object')
    refs = item.get('evidence_ids')
    _strings(refs, 'invalid_result')
    _require(all(ref in ids for ref in refs), 'invalid_result', 'Unresolved evidence reference')


def _items(value, label, nonempty=True):
    _require(isinstance(value, list) and (bool(value) or not nonempty) and
             all(isinstance(item, dict) for item in value), 'invalid_result', 'Invalid ' + label)
    return value


def validate_title_candidates(result):
    ids = _evidence(result)
    candidates = _items(result.get('candidates'), 'title candidates')
    _require(len(candidates) == 10, 'invalid_result', 'Exactly ten titles required')
    normalized = set()
    for item in candidates:
        _refs(item, ids)
        _require(_text(item.get('text')), 'invalid_result', 'Empty title')
        normalized.add(' '.join(item['text'].casefold().split()))
    _require(len(normalized) == 10, 'invalid_result', 'Titles must be unique')
    return result


def _research_status(result, items):
    status = result.get('status')
    _require(status in ('complete', 'partial', 'empty'), 'invalid_result', 'Explicit research status required')
    _require(bool(items) == (status != 'empty'), 'invalid_result', 'Research count contradicts status')
    if status != 'complete':
        _require(_text(result.get('reason')), 'invalid_result', 'Partial/empty reason required')


def card_v3_tier_valid(candidate, threshold, tier):
    if not isinstance(candidate,dict) or type(threshold) is not int or threshold<0 or tier not in ('threshold_met','supplemental'):
        return False
    sold=candidate.get('sold_lower_bound')
    return (candidate.get('sales_tier')==tier and type(sold) is int and sold>100
            and (sold>=threshold if tier=='threshold_met' else sold<threshold))


def validate_research_candidates(result):
    ids = _evidence(result)
    candidates = _items(result.get('candidates'), 'research candidates', nonempty=False)
    _research_status(result, candidates)
    supplemental = _items(result.get('supplemental_candidates', []), 'supplemental candidates', nonempty=False)
    identities = set()
    for item in candidates + supplemental:
        _refs(item, ids)
        for field in ('platform', 'item_id', 'url', 'raw_url', 'query', 'selection_reason'):
            _require(_text(item.get(field)), 'invalid_result', 'Invalid ' + field)
        key = (item['platform'], item['item_id'])
        _require(key not in identities, 'invalid_result', 'Duplicate research item')
        identities.add(key)
        _require(_timestamp(item.get('observed_at')), 'invalid_result', 'Invalid observed_at')
        if 'observation_time_scope' in item:
            _require(item['observation_time_scope'] in ('detail', 'batch'), 'invalid_result', 'Invalid observation time scope')
        for field in ('sold_lower_bound', 'sold_upper_bound', 'review_count', 'position'):
            _require(field in item and (item[field] is None or _integer(item[field], 1 if field == 'position' else 0)),
                     'invalid_result', 'Invalid ' + field)
        lower, upper = item['sold_lower_bound'], item['sold_upper_bound']
        _require(lower is None or upper is None or lower <= upper, 'invalid_result', 'Contradictory sold bounds')
        for field in ('sold_display', 'image_url', 'visual_category'):
            _require(field in item and (item[field] is None or _text(item[field])), 'invalid_result', 'Invalid ' + field)
        _require('is_ad' in item and (item['is_ad'] is None or type(item['is_ad']) is bool), 'invalid_result', 'Invalid ad flag')
        rating = item.get('rating')
        _require('rating' in item and (rating is None or (type(rating) in (int, float) and rating >= 0)), 'invalid_result', 'Invalid rating')
    return result


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{64}', value) is not None


def _image(item, ids):
    _refs(item, ids)
    for field in ('image_id', 'role', 'artifact_id', 'source'):
        _require(_text(item.get(field)), 'invalid_result', 'Invalid image ' + field)
    _require(_integer(item.get('order')), 'invalid_result', 'Invalid image order')
    for field in ('width', 'height'):
        _require(_integer(item.get(field), 1), 'invalid_result', 'Invalid image dimensions')
    mime = item.get('mime_type')
    _require(_text(mime) and mime.startswith('image/') and len(mime) > 6, 'invalid_result', 'Invalid image MIME type')
    _require(_hash(item.get('sha256')), 'invalid_result', 'Invalid image digest')


def validate_gallery(result):
    ids = _evidence(result)
    _require(_text(result.get('reference_item_id')) and isinstance(result.get('selected_variant'), dict)
             and bool(result['selected_variant']), 'invalid_result', 'Gallery SKU identity required')
    _require(_timestamp(result.get('observed_at')) and _hash(result.get('gallery_manifest_hash')),
             'invalid_result', 'Invalid gallery timestamp/digest')
    _require(result.get('completeness_status') in ('complete', 'partial'), 'invalid_result', 'Invalid gallery completeness')
    if result['completeness_status'] == 'partial':
        _require(_text(result.get('reason')), 'invalid_result', 'Partial gallery reason required')
    images = _items(result.get('images'), 'gallery images')
    for item in images:
        _image(item, ids)
    for field in ('image_id', 'order'):
        _require(len({item[field] for item in images}) == len(images), 'invalid_result', 'Duplicate gallery ' + field)
    return result


def validate_internal_image_qc(result, *, allow_legacy=False):
    """Verify the host's pass marker refers to the exact result image."""
    image = result.get('image') if isinstance(result, dict) else None
    qc = result.get('visual_qc') if isinstance(result, dict) else None
    _require(isinstance(image, dict) and _hash(image.get('sha256')), 'invalid_result', 'Invalid generated image digest')
    if allow_legacy and isinstance(qc, dict) and 'status' not in qc:
        checks = qc.get('checks')
        names = {'garment_construction', 'color_material', 'garment_details', 'identity_pose',
                 'accessories', 'scene_lighting_framing', 'text_layout'}
        _require(isinstance(checks, list) and len(checks) == len(names)
                 and all(isinstance(c, dict) and c.get('status') == 'passed' for c in checks)
                 and {c.get('name') for c in checks} == names
                 and isinstance(qc.get('skill_report'), dict) and qc['skill_report'].get('decision') == 'accept'
                 and isinstance(qc.get('quality_binding'), dict)
                 and qc['quality_binding'].get('candidate_sha256') == image['sha256'],
                 'invalid_result', 'Legacy internal image QC missing or mismatched')
        return result
    _require(isinstance(qc, dict) and qc.get('status') in ('passed', 'manual_accepted') and isinstance(qc.get('report'), dict) and bool(qc['report'])
             and qc.get('candidate_sha256') == image['sha256']
             and (qc['status'] != 'manual_accepted' or _integer(qc.get('decision_event_id'), 1)),
             'invalid_result', 'Internal image QC missing or mismatched')
    return result


def validate_result(task_type, result):
    _task(task_type)
    if task_type == 'title.generate':
        return validate_title_candidates(result)
    if task_type == 'research.bestseller':
        return validate_research_candidates(result)
    if task_type == 'gallery.collect':
        return validate_gallery(result)
    ids = _evidence(result)
    if task_type == 'product.facts':
        for item in _items(result.get('facts'), 'facts'):
            _refs(item, ids)
            _require(_text(item.get('name')) and 'value' in item, 'invalid_result', 'Invalid fact')
        unknown = result.get('uncertainties')
        _require(isinstance(unknown, list) and all(_text(v) for v in unknown), 'invalid_result', 'Invalid uncertainties')
    elif task_type == 'research.keywords':
        items = _items(result.get('keywords'), 'keywords', nonempty=False)
        _research_status(result, items)
        for item in items:
            _refs(item, ids)
            _require(_text(item.get('text')), 'invalid_result', 'Invalid keyword')
    elif task_type == 'image.generate':
        _require(_text(result.get('request_id')), 'invalid_result', 'Request identity required')
        _image(result.get('image'), ids)
        validate_internal_image_qc(result)
    elif task_type == 'image.qc':
        _require(_text(result.get('image_artifact_id')), 'invalid_result', 'Image reference required')
        checks = _items(result.get('checks'), 'checks')
        for check in checks:
            _refs(check, ids)
            _require(_text(check.get('name')) and check.get('status') in ('passed', 'failed', 'unknown'), 'invalid_result', 'Invalid QC check')
        _require(len({check['name'] for check in checks}) == len(checks), 'invalid_result', 'Duplicate QC checks')
        statuses = [check['status'] for check in checks]
        status = result.get('status')
        _require((status == 'passed' and all(s == 'passed' for s in statuses)) or
                 (status == 'failed' and 'failed' in statuses) or
                 (status == 'review' and 'unknown' in statuses), 'invalid_result', 'QC outcome contradicts checks')
    return result

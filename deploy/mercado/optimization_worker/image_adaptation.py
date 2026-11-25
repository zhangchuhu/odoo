"""Validate per-region adaptation observations against the frozen inputs.

This checks the evidence structure and binding; it cannot establish visual truth.
"""
import re


_SHA256 = re.compile(r'^[a-f0-9]{64}$')


def adapt_text(original, source_digests, rows):
    """Validate a text decision for every observed target string."""
    if (not isinstance(original, list) or not isinstance(rows, list) or len(rows) != len(original)
            or any(not isinstance(x, str) or not x.strip() for x in original)):
        raise ValueError('Incomplete text observations')
    result, seen, unsupported = [None] * len(original), set(), False
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'index', 'decision', 'source_ids', 'replacement'}:
            raise ValueError('Invalid text observation')
        index, decision, refs, replacement = (row[k] for k in ('index', 'decision', 'source_ids', 'replacement'))
        if (type(index) is not int or index < 0 or index >= len(original) or index in seen
                or decision not in ('preserve', 'replace', 'unsupported') or not isinstance(refs, list)
                or any(not isinstance(x, str) or x not in source_digests for x in refs)
                or len(refs) != len(set(refs)) or not isinstance(replacement, str) or len(replacement) > 4096):
            raise ValueError('Invalid text decision')
        if decision == 'preserve' and (replacement != original[index] or refs):
            raise ValueError('Preserved text must be unchanged')
        if decision == 'replace' and (not replacement.strip() or not refs):
            raise ValueError('Replacement text requires evidence')
        if decision == 'unsupported':
            if replacement or refs:
                raise ValueError('Unsupported text cannot claim replacement')
            unsupported = True
        result[index] = replacement
        seen.add(index)
    if unsupported:
        raise ValueError('unsupported product text')
    return result


def assess_adaptation(regions, source_digests, report):
    if (not isinstance(regions, list) or not regions or len(regions) > 100
            or any(not isinstance(x, str) or not x.strip() or len(x) > 4096 for x in regions)):
        raise ValueError('Invalid target regions')
    if (not isinstance(source_digests, dict) or not source_digests
            or any(not isinstance(k, str) or not k or not isinstance(v, str) or not _SHA256.fullmatch(v)
                   for k, v in source_digests.items())):
        raise ValueError('Invalid reference binding')
    if not isinstance(report, dict) or set(report) != {'regions'} or not isinstance(report['regions'], list):
        raise ValueError('Invalid adaptation report')
    rows = report['regions']
    if len(rows) != len(regions):
        raise ValueError('Incomplete region coverage')
    seen, unsupported = set(), []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'index', 'decision', 'source_ids', 'detail'}:
            raise ValueError('Invalid region observation')
        index, decision, refs, detail = (row[k] for k in ('index', 'decision', 'source_ids', 'detail'))
        if (type(index) is not int or index < 0 or index >= len(regions) or index in seen
                or decision not in ('direct', 'replace', 'unsupported')
                or not isinstance(refs, list) or any(not isinstance(x, str) or x not in source_digests for x in refs)
                or len(set(refs)) != len(refs) or not isinstance(detail, str) or not detail.strip() or len(detail) > 1000):
            raise ValueError('Invalid region evidence')
        if decision in ('direct', 'replace') and not refs:
            raise ValueError('Supported regions require reference evidence')
        seen.add(index)
        if decision == 'unsupported':
            unsupported.append({'index': index, 'region': regions[index], 'detail': detail})
    return {'decision': 'skip' if unsupported else 'generate', 'unsupported': unsupported}

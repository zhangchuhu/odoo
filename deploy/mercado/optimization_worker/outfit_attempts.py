"""Pure quality-cycle transitions. Persist with host request in ONE transaction.

The caller must fence the original lease and atomically commit the returned
record and native dispatch intent before invoking any external image tool.
"""
import copy
import re
from .outfit_quality import evaluate, select_comparison


def bind(record, request_id, input_digest, plan_digest):
    if not isinstance(record, dict) or record.get('request_id') != request_id:
        raise ValueError('Request mismatch')
    if any(not isinstance(v, str) or not re.fullmatch(r'[a-f0-9]{64}', v) for v in (input_digest, plan_digest)):
        raise ValueError('Invalid digest')
    r = copy.deepcopy(record)
    expected = {'request_id': request_id, 'input_digest': input_digest, 'plan_digest': plan_digest, 'attempts': []}
    if 'quality_cycle' not in r:
        r['quality_cycle'] = expected
    elif any(r['quality_cycle'].get(k) != expected[k] for k in ('request_id', 'input_digest', 'plan_digest')):
        raise ValueError('Immutable binding changed')
    return r


def start(record):
    r = copy.deepcopy(record); cycle = _cycle(r); attempts = cycle['attempts']
    if len(attempts) >= 3 or (attempts and attempts[-1]['status'] != 'reject'):
        raise ValueError('Attempt unavailable or unresolved')
    n = len(attempts) + 1
    attempts.append({'number': n, 'alias': f'candidate_{n}', 'status': 'dispatched'})
    return r, n


def _cycle(record):
    cycle = record.get('quality_cycle')
    if not isinstance(cycle, dict) or cycle.get('request_id') != record.get('request_id') or not isinstance(cycle.get('attempts'), list):
        raise ValueError('Invalid cycle')
    return cycle


def _active(record, number, states):
    attempts = _cycle(record)['attempts']
    if not attempts or attempts[-1].get('number') != number or attempts[-1].get('alias') != f'candidate_{number}' or attempts[-1].get('status') not in states:
        raise ValueError('Attempt state mismatch')
    return attempts[-1]


def mark_unknown(record, number):
    r = copy.deepcopy(record); _active(r, number, {'dispatched'})['status'] = 'unknown'; return r


def record_candidate(record, number, digest):
    if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('Invalid candidate digest')
    r = copy.deepcopy(record)
    _active(r, number, {'dispatched', 'unknown'}).update(status='review_pending', digest=digest)
    return r


def review(record, number, report, actual_digest, *, infographic=False):
    r = copy.deepcopy(record); a = _active(r, number, {'review_pending'})
    decision = evaluate(report, a['alias'], a['digest'], actual_digest, number, infographic=infographic)
    if number == 3 and decision == 'reject': decision = 'comparison_pending'
    elif decision == 'reject' and report.get('decision') != 'reject': decision = 'review'
    a.update(status=decision, report=copy.deepcopy(report))
    return r, decision


def compare(record, reports, candidate_digests, *, infographic=False):
    """Bind a fresh visual comparison to every stored candidate digest.

    Caller must actually inspect the originals and candidates; hashes alone
    do not establish that visual inspection occurred.
    """
    r = copy.deepcopy(record); cycle = _cycle(r); attempts = cycle['attempts']
    if len(attempts) != 3 or any(a.get('status') != 'reject' for a in attempts[:2]) or attempts[2].get('status') != 'comparison_pending':
        raise ValueError('Three settled candidates required')
    if candidate_digests != {a['alias']: a['digest'] for a in attempts}:
        raise ValueError('Candidate digests differ from stored evidence')
    alias, reason = select_comparison(reports, {a['alias']: a['number'] for a in attempts}, infographic=infographic)
    cycle['selection'] = {'alias': alias, 'reason': reason, 'quality_passed': False,
                          'reports': copy.deepcopy(reports), 'candidate_digests': copy.deepcopy(candidate_digests)}
    return r

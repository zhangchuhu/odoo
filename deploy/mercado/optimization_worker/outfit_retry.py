"""Pure transport-retry journal; no external service is called here.

Host must serialize every transition with its lease/fencing check and commit it
atomically before invoking a tool. A committed claim is not proof the tool ran;
a crash after claiming leaves an unknown outcome, never an automatic redispatch.
"""
import copy
import math


def _time(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid timestamp or delay')
    return value


def _journal(record):
    j = record.get('generation_retry')
    cycle = record.get('quality_cycle')
    if not isinstance(j, dict) or not isinstance(cycle, dict):
        raise ValueError('Retry journal missing')
    if (j.get('request_id') != record.get('request_id') or
            any(j.get(k) != cycle.get(k) for k in ('request_id', 'input_digest', 'plan_digest'))):
        raise ValueError('Immutable binding changed')
    entries = cycle.get('attempts')
    if (not isinstance(entries, list) or not entries or
            entries[-1].get('number') != j.get('attempt') or
            entries[-1].get('status') not in ('dispatched', 'unknown')):
        raise ValueError('Quality attempt is not active')
    return j


def bind(record, attempt, first_dispatched_at):
    _time(first_dispatched_at)
    r = copy.deepcopy(record)
    cycle = r.get('quality_cycle')
    if not isinstance(cycle, dict) or cycle.get('request_id') != r.get('request_id'):
        raise ValueError('Invalid quality cycle')
    entries = cycle.get('attempts')
    if not isinstance(entries, list) or not entries or entries[-1].get('number') != attempt or entries[-1].get('status') != 'dispatched':
        raise ValueError('Dispatched attempt required')
    binding = {k: cycle.get(k) for k in ('request_id', 'input_digest', 'plan_digest')}
    binding.update(attempt=attempt, first_dispatched_at=first_dispatched_at)
    if 'generation_retry' in r:
        j = _journal(r)
        if any(j.get(k) != v for k, v in binding.items()):
            raise ValueError('Immutable binding changed')
        return r
    r['generation_retry'] = dict(binding, status='running', reports=[], receipts=[])
    return r


def report(record, outcome, error, observed_at, retry_after=0):
    _time(observed_at); _time(retry_after)
    if outcome not in ('retryable', 'unknown', 'failed', 'unavailable', 'moderation_blocked') or not isinstance(error, str) or not error.strip():
        raise ValueError('Explicit outcome and diagnostic required')
    r = copy.deepcopy(record); j = _journal(r)
    if j['status'] != 'running':
        raise ValueError('No running invocation to report')
    previous = j['receipts'][-1]['claimed_at'] if j['receipts'] else j['first_dispatched_at']
    if observed_at < previous or (j['reports'] and observed_at <= j['reports'][-1]['observed_at']):
        raise ValueError('Stale report')
    n = len(j['receipts'])
    delay = max((5, 10, 20, 60)[min(n, 3)], retry_after)
    j['reports'].append({'outcome': outcome, 'error': error, 'observed_at': observed_at,
                         'retry_after': retry_after, 'not_before': observed_at + delay if outcome == 'retryable' else None})
    j['status'] = outcome
    return r


def claim(record, expected_unknown_at, now):
    """Claim once for the exact explicit failure; caller commits before invoking."""
    _time(expected_unknown_at); _time(now)
    r = copy.deepcopy(record); j = _journal(r)
    if j['receipts'] and j['receipts'][-1]['observed_at'] == expected_unknown_at:
        return r, False
    if j['status'] != 'retryable' or not j['reports']:
        raise ValueError('No confirmed retryable failure')
    report_entry = j['reports'][-1]
    if report_entry['observed_at'] != expected_unknown_at:
        raise ValueError('Stale failure receipt')
    if now < report_entry['not_before']:
        raise ValueError('Backoff not elapsed')
    j['receipts'].append({'observed_at': expected_unknown_at, 'claimed_at': now})
    j['status'] = 'running'
    return r, True


def settle(record):
    """Record that a real result was imported; caller atomically stores its digest."""
    r = copy.deepcopy(record); j = _journal(r)
    if j['status'] not in ('running', 'unknown', 'retryable'):
        raise ValueError('Result cannot replace terminal outcome')
    j['status'] = 'resolved'
    return r

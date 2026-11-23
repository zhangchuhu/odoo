"""Pure execution policy. No database, wall clock, I/O or Odoo dependency.

Transitions: draft->queued/cancelled; queued->running/waiting/cancelled;
running->succeeded/retry_wait/waiting/failed/cancelled;
retry_wait->queued/cancelled; waiting->queued/cancelled/failed.
Terminal states cannot reopen, and same-state transitions are rejected.

validate_lease(state, token, epoch, expires_at, now) accepts stored state as
{state:'running', lease_token:<nonempty string>, lease_epoch:<integer >=1>}.
The submitted token/epoch must match exactly; bool is not an epoch. Both time
arguments must be aware UTC datetimes; expires_at must be strictly after now.
Persistence must check expiry from trusted storage, never a worker claim.
All successful policy checks return None; failures raise DomainError.
"""
from .contracts import DomainError, _integer, _text, _utc

_TRANSITIONS = {
    'draft': ('queued', 'cancelled'),
    'queued': ('running', 'waiting', 'cancelled'),
    'running': ('succeeded', 'retry_wait', 'waiting', 'failed', 'cancelled'),
    'retry_wait': ('queued', 'cancelled'),
    'waiting': ('queued', 'cancelled', 'failed'),
    'succeeded': (), 'failed': (), 'cancelled': (),
}


def validate_transition(current, target):
    if not isinstance(current, str) or target not in _TRANSITIONS.get(current, ()):
        raise DomainError('invalid_transition', 'Illegal task state transition')


def validate_lease(state, token, epoch, expires_at, now):
    if not (isinstance(state, dict) and state.get('state') == 'running'
            and _text(token) and token == state.get('lease_token')
            and _integer(epoch, 1) and _integer(state.get('lease_epoch'), 1)
            and epoch == state['lease_epoch'] and _utc(expires_at) and _utc(now)
            and expires_at > now):
        raise DomainError('invalid_lease', 'Lease is inactive, stale, or expired')

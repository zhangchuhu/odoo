"""Pure optimization domain API; importing this package never initializes Odoo."""
from .contracts import (
    DomainError, canonical_hash, validate_input, validate_result, validate_internal_image_qc,
    validate_title_candidates, validate_research_candidates, validate_gallery, card_v3_tier_valid,
)
from .facts import product_facts_from_snapshot
from .policies import validate_transition, validate_lease

__all__ = [
    'DomainError', 'canonical_hash', 'validate_input', 'validate_result', 'validate_internal_image_qc',
    'validate_title_candidates', 'validate_research_candidates', 'validate_gallery', 'card_v3_tier_valid',
    'validate_transition', 'validate_lease', 'product_facts_from_snapshot',
]

"""Strict parsing of explicitly displayed SHEIN US product-sales labels."""
import re
from decimal import Decimal, InvalidOperation

_LABEL = re.compile(r'\s*(\+)?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d{1,2})?)\s*(k)?\s*(\+)?\s+sold\s*', re.I)


def parse_sales(text):
    if not isinstance(text, str) or len(text) > 100:
        return None
    match = _LABEL.fullmatch(text)
    if not match or (match[1] and match[4]) or ('.' in match[2] and not match[3]):
        return None
    try:
        value = Decimal(match[2].replace(',', '')) * (1000 if match[3] else 1)
        return int(value) if value == value.to_integral_value() and value <= 1000000000 else None
    except (InvalidOperation, ValueError):
        return None


def classify_sales(text, threshold):
    if type(threshold) is not int or threshold < 0:
        raise ValueError('Invalid task threshold')
    value = parse_sales(text)
    plus_hundred = value == 100 and bool(re.fullmatch(r'\s*(?:\+\s*100|100\s*\+)\s+sold\s*', text, re.I))
    if value is None or (value <= 100 and not plus_hundred):
        return 'exclude'
    return 'threshold_met' if (101 if plus_hundred else value) >= threshold else 'supplemental'


def rank_cards(cards, *, threshold, limit=30):
    """Select a query's best distinct cards, retaining only provable sales."""
    if not isinstance(cards, list) or type(limit) is not int or not 1 <= limit <= 30:
        raise ValueError('Invalid card selection')
    if type(threshold) is not int or threshold < 0:
        raise ValueError('Invalid task threshold')
    observed = {}
    for card in cards:
        if (not isinstance(card, dict) or not isinstance(card.get('product_id'), str)
                or not card['product_id'] or type(card.get('rank')) is not int or card['rank'] < 1):
            raise ValueError('Invalid card identity or rank')
        pid = card['product_id']
        entry = observed.setdefault(pid, {'cards': [], 'rank': card['rank']})
        entry['rank'] = min(entry['rank'], card['rank'])
        value = parse_sales(card.get('sales_text'))
        if value is not None:
            text = card['sales_text']
            lower = 101 if value == 100 and '+' in text else value
            upper = None if '+' in text or re.search(r'k\s*\+?\s+sold\s*$', text, re.I) else value
            entry['cards'].append((lower, upper, card))
    selected = []
    for entry in observed.values():
        if not entry['cards']:
            continue
        lower = max(row[0] for row in entry['cards'])
        upper = min((row[1] for row in entry['cards'] if row[1] is not None), default=None)
        if upper is not None and lower > upper:
            continue
        card = max(entry['cards'], key=lambda row: (row[0], -row[2]['rank']))[2]
        tier = classify_sales(card.get('sales_text'), threshold)
        if tier != 'exclude':
            selected.append(dict(card, first_rank=entry['rank'], sales_lower_bound=parse_sales(card['sales_text']), tier=tier))
    return sorted(selected, key=lambda row: (-row['sales_lower_bound'], row['first_rank'], row['product_id']))[:limit]


def classify_candidate(card, detail, *, min_sold, min_reviews):
    """Use the identified card's sales and the same product's visible reviews."""
    from .collector_import import _identity, _number
    if type(min_reviews) is not int or min_reviews < 0:
        raise ValueError('Invalid review threshold')
    if not isinstance(card, dict) or not isinstance(detail, dict):
        raise ValueError('Card and detail required')
    try:
        card_id, _ = _identity(card.get('url'), 'shein-us')
        detail_id, _ = _identity(detail.get('detail_url'), 'shein-us')
    except (ValueError, TypeError, AttributeError):
        return 'identity_mismatch'
    if card_id != detail_id or card.get('product_id') != card_id or detail.get('product_id') != detail_id:
        return 'identity_mismatch'
    tier = classify_sales(card.get('sales_text'), min_sold)
    if tier == 'exclude':
        return 'sales_missing_or_below_floor'
    text = detail.get('reviews_display')
    if not isinstance(text, str) or not re.fullmatch(r'\s*(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*k?\s*\+?\s*', text, re.I):
        return 'review_missing'
    reviews = _number(text)
    if not reviews:
        return 'review_missing'
    if reviews[0] < min_reviews:
        return 'review_threshold_failure'
    return tier

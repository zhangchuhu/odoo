"""Read a rating from a verified immutable research-evidence artifact."""
import hashlib
import json
import re


def historical_rating(raw, digest, platform, item_id):
    if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != digest:
        return None
    try:
        data = json.loads(raw)
        if data.get('platform') != platform or not isinstance(data.get('details'), list):
            return None
        matches = [d for d in data['details'] if isinstance(d, dict) and d.get('product_id') == item_id
                   and d.get('identity') == platform + ':' + item_id]
        if len(matches) != 1:
            return None
        text = matches[0].get('rating_display')
        if not isinstance(text, str) or not re.fullmatch(r'[0-5](?:[.,]\d{1,2})?', text.strip()):
            return None
        value = float(text.strip().replace(',', '.'))
        return value if value <= 5 else None
    except (ValueError, TypeError, AttributeError):
        return None

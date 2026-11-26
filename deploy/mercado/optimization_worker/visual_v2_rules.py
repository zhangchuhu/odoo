"""Deterministic comparison of independently observed visual attributes.

None means not visible; absent source attributes are not requirements. A source
category must always be established before a candidate can qualify.
"""

REQUIRED = ('category', 'components', 'length', 'shoulders', 'structure')
FLEXIBLE = ('silhouette', 'fit', 'sleeve_length')
GENERIC = frozenset(('modern', 'simple', 'all_occasions', 'everyday'))


def _value(observation):
    if not isinstance(observation, dict):
        return observation
    status = observation.get('status')
    if status not in ('known', 'unknown', 'not_applicable'):
        raise ValueError('Invalid visual observation status')
    value = observation.get('value')
    if status == 'known' and (value is None or value == ''):
        raise ValueError('Known visual observation requires a value')
    if status != 'known' and value is not None:
        raise ValueError('Unknown visual observation cannot supply a value')
    return value


def validate_source_profile(source, category):
    if not isinstance(source, dict) or set(source) != set(REQUIRED + FLEXIBLE):
        raise ValueError('Complete visual source field set required')
    values = {name: _value(source[name]) for name in REQUIRED + FLEXIBLE}
    if values['category'] != category or not any(values[name] is not None for name in FLEXIBLE):
        raise ValueError('Known category and at least one flexible attribute required')
    components = values.pop('components')
    if isinstance(components, list):
        if (not 1 <= len(components) <= 6 or any(not isinstance(v, str) or not v.strip() or len(v) > 100 for v in components)
                or len(set(components)) != len(components)):
            raise ValueError('Invalid garment components')
    elif components is not None and (not isinstance(components, str) or not components.strip() or len(components) > 100):
        raise ValueError('Invalid garment components')
    for value in values.values():
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 100):
            raise ValueError('Invalid visual source attribute')
    return True


def validate_design_profile(design, category):
    if not isinstance(design,dict) or set(design)!={'primary','secondary','styles','occasions'}:
        raise ValueError('Complete visual design profile required')
    validate_source_profile(design['primary'],category)
    for key in ('secondary','styles','occasions'):
        labels=design[key]
        if labels is not None and (not isinstance(labels,list) or len(labels)>30
            or any(not isinstance(x,str) or not x.strip() or len(x)>100 for x in labels)
            or len(set(labels))!=len(labels)):
            raise ValueError('Invalid visual design labels')
    return True


def compare_primary(source, candidate):
    if not isinstance(source, dict) or not isinstance(candidate, dict):
        raise ValueError('Visual attributes must be mappings')
    details = {}
    for field in REQUIRED + FLEXIBLE:
        expected = _value(source.get(field))
        actual = _value(candidate.get(field))
        for value in (expected,actual):
            if isinstance(value,list) and field=='components':
                if (not 1<=len(value)<=6 or any(not isinstance(x,str) or not x.strip() or len(x)>100 for x in value)
                        or len(set(value))!=len(value)):raise ValueError('Invalid garment components')
            elif value is not None and (not isinstance(value,str) or not value.strip() or len(value)>100):
                raise ValueError('Invalid visual observation')
        if field == 'components' and isinstance(expected, list) and isinstance(actual, list):
            expected, actual = sorted(expected), sorted(actual)
        excluded = isinstance(source.get(field), dict) and source[field].get('status') == 'not_applicable'
        details[field] = ('not_applicable' if excluded else
                          'unknown' if expected is None or actual is None else
                          'match' if actual == expected else 'mismatch')
    if _value(source.get('category')) is None:
        outcome = 'review'
    elif any(details[field] == 'mismatch' for field in REQUIRED):
        outcome = 'reject'
    elif any(details[field] == 'unknown' for field in REQUIRED):
        outcome = 'review'
    elif any(details[field] == 'match' for field in FLEXIBLE):
        outcome = 'pass'
    elif any(details[field] == 'unknown' for field in FLEXIBLE):
        outcome = 'review'
    elif all(details[field] == 'not_applicable' for field in FLEXIBLE):
        outcome = 'review'
    else:
        outcome = 'reject'
    return {'outcome': outcome, 'features': details}


def classify_visual(source, candidate, source_features, candidate_features,
                    source_styles, candidate_styles, source_occasions, candidate_occasions,
                    *, critical_conflict=False):
    primary = compare_primary(source, candidate)['outcome']
    if primary != 'pass':
        return primary
    if source_features is not None and candidate_features is not None and set(source_features) & set(candidate_features) and not critical_conflict:
        return 'highly_similar'
    if ((set(source_styles or ()) & set(candidate_styles or ())) - GENERIC or
            (set(source_occasions or ()) & set(candidate_occasions or ())) - GENERIC):
        return 'competitor'
    if (source_features is None or candidate_features is None or
            (source_styles and candidate_styles is None) or
            (source_occasions and candidate_occasions is None)):
        return 'review'
    return 'reject'

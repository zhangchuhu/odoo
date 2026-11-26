"""Work selection for visual-v2; persisted facts are supplied by the caller."""


def validate_queries(queries):
    from .collector_import import disallowed_term
    if (not isinstance(queries, list) or not 3 <= len(queries) <= 9
            or any(not isinstance(q, str) or not q.strip() or len(q) > 200 or disallowed_term(q)
                   for q in queries)):
        raise ValueError('Invalid visual-v2 query plan')
    normalized = [' '.join(q.casefold().split()) for q in queries]
    if len(set(normalized)) != len(normalized):
        raise ValueError('Duplicate visual-v2 query')
    return list(queries)


def next_action(target, gallery_completed, pending_candidates, pending_queries, plan_exhausted):
    if (type(target) is not int or target < 1 or type(gallery_completed) is not int
            or gallery_completed < 0 or not isinstance(pending_candidates, list)
            or not isinstance(pending_queries, list) or type(plan_exhausted) is not bool
            or any(not isinstance(v, str) or not v for v in pending_candidates + pending_queries)):
        raise ValueError('Invalid visual-v2 progress')
    if gallery_completed >= target:
        return {'action': 'finish', 'reason': 'target_reached'}
    if pending_candidates:
        return {'action': 'review', 'identity': pending_candidates[0]}
    if pending_queries:
        return {'action': 'search', 'query': pending_queries[0]}
    if not plan_exhausted:
        return {'action': 'expand'}
    return {'action': 'finish', 'reason': 'search_plan_exhausted'}

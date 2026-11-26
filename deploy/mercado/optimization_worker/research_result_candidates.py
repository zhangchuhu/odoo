"""Return all candidates requiring a gallery, preserving their result tiers."""
def all_candidates(result):
    if not isinstance(result,dict) or not isinstance(result.get('candidates'),list) or not isinstance(result.get('supplemental_candidates',[]),list):
        raise ValueError('Invalid research candidate groups')
    rows=result['candidates']+result.get('supplemental_candidates',[]);seen=set()
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('platform'),str) or not isinstance(row.get('item_id'),str):raise ValueError('Invalid candidate identity')
        identity=(row['platform'],row['item_id'])
        if identity in seen:raise ValueError('Duplicate research candidate')
        seen.add(identity)
    return rows

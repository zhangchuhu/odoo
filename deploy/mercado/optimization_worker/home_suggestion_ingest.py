"""Persist a bounded host-reported suggestion observation atomically."""
from urllib.parse import urlsplit

from .home_suggestion_checkpoint import save_read,save_observation
from .home_suggestions import _normalized


def ingest(db,scope,body,evidence_sha256,observed_at,*,fence,verify,platform='mercado-libre-mx'):
    if not isinstance(body,dict) or set(body)!={'page_url','prefix','visible_input','state','suggestions'}:
        raise ValueError('Invalid suggestion observation')
    try:url=urlsplit(body['page_url']);port=url.port
    except (ValueError,TypeError) as exc:raise ValueError('Invalid observation page') from exc
    hosts={'mercado-libre-mx':('www.mercadolibre.com.mx','listado.mercadolibre.com.mx'),'shein-us':('us.shein.com',)}
    if (platform not in hosts or url.scheme!='https' or url.hostname not in hosts[platform]
            or port or url.username or url.password):raise ValueError('Invalid observation page')
    prefix=body['prefix'];actual=body['visible_input'];state=body['state'];items=body['suggestions']
    if (not isinstance(prefix,str) or not isinstance(actual,str) or _normalized(prefix)!=_normalized(actual)
            or state not in ('observed','unavailable','unverified') or not isinstance(items,list) or len(items)>20
            or (state=='observed')!=bool(items)):
        raise ValueError('Suggestion input or state mismatch')
    positions=[]
    for item in items:
        if (not isinstance(item,dict) or set(item)!={'text','position'} or type(item['position']) is not int
                or item['position']<1 or not _normalized(item['text']).startswith(_normalized(prefix))):
            raise ValueError('Invalid visible suggestion')
        positions.append(item['position'])
    if positions!=sorted(set(positions)):raise ValueError('Invalid suggestion ordering')
    base={'prefix':prefix,'observed_at':observed_at,'evidence_sha256':evidence_sha256}
    db.execute('SAVEPOINT suggestion_ingest')
    try:
        save_read(db,scope,dict(base,state=state),fence=fence,verify_evidence=verify)
        for item in items:save_observation(db,scope,dict(base,**item),fence=fence,verify_evidence=verify)
        if fence() is not True:raise ValueError('Active lease required')
        db.execute('RELEASE suggestion_ingest')
    except Exception:
        db.execute('ROLLBACK TO suggestion_ingest');db.execute('RELEASE suggestion_ingest');raise

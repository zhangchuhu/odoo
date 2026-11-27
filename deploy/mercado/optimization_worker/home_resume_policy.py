"""Read-only identity checks before resuming saved collection progress."""
from urllib.parse import urlsplit


def resume_page_matches(stage,expected,observed):
    if not isinstance(expected,dict) or not isinstance(observed,dict) or observed.get('blocked') is not False:return False
    raw=observed.get('url')
    if not isinstance(raw,str):return False
    try:url=urlsplit(raw);port=url.port
    except ValueError:return False
    if url.scheme!='https' or port or url.username or url.password:return False
    if stage=='search':
        query=expected.get('query');actual=observed.get('query')
        return (url.hostname=='listado.mercadolibre.com.mx' and isinstance(query,str) and bool(query.strip())
                and isinstance(actual,str) and ' '.join(actual.casefold().split())==' '.join(query.casefold().split()))
    if stage=='detail':
        from .collector_import import _identity
        try:pid,_=_identity(raw,'mercado-libre-mx')
        except (TypeError,ValueError,AttributeError):return False
        return isinstance(expected.get('product_id'),str) and pid==expected['product_id']==observed.get('product_id')
    return False

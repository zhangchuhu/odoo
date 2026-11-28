"""Durable homepage completion with explicit page-end evidence."""
import json
import re
from urllib.parse import urlsplit


def append_home_batch(db,scope,index,batch_index,cards,*,fence):
    from .card_sales_v3_checkpoints import append_page_batch
    append_page_batch(db,scope,index,1,batch_index,cards,fence=fence)


def finish_homepage(db,scope,index,data,*,fence,verify,platform='mercado-libre-mx'):
    if not callable(fence) or fence() is not True:raise ValueError('Active lease required')
    if (not isinstance(scope,tuple) or len(scope)!=3 or type(index) is not int or index<0
            or not isinstance(data,dict) or set(data)!={'url','receipt_nonce','end_checks','no_results'}
            or not isinstance(data['url'],str) or type(data['end_checks']) is not int or data['end_checks']<3
            or type(data['no_results']) is not bool or not isinstance(data['receipt_nonce'],str)
            or not re.fullmatch(r'[a-f0-9]{32}',data['receipt_nonce'])):raise ValueError('Invalid homepage completion')
    try:url=urlsplit(data['url']);port=url.port
    except ValueError as exc:raise ValueError('Invalid homepage URL') from exc
    hosts={'mercado-libre-mx':'listado.mercadolibre.com.mx','shein-us':'us.shein.com'}
    if platform not in hosts or url.scheme!='https' or url.hostname!=hosts[platform] or port or url.username or url.password:
        raise ValueError('Invalid homepage URL')
    if not callable(verify) or verify(data) is not True:raise ValueError('Verified page evidence required')
    if not db.execute('SELECT 1 FROM card_v3_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=?',(*scope,index)).fetchone():
        raise ValueError('Search query not sealed')
    if db.execute('SELECT 1 FROM card_v3_pages WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND page<>1',scope).fetchone():raise ValueError('Later page not allowed')
    count=db.execute('SELECT count(*) FROM card_v3_batches WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? AND page=1',(*scope,index)).fetchone()[0]
    if not count and not data['no_results']:raise ValueError('Empty homepage not confirmed')
    if count and data['no_results']:raise ValueError('Conflicting homepage evidence')
    evidence=json.dumps({'no_results':data['no_results']},sort_keys=True,separators=(',',':'))
    existing=db.execute('SELECT page_url,receipt_nonce,end_evidence,end_checks FROM card_v3_page_evidence WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? AND page=1',(*scope,index)).fetchone()
    expected=(data['url'],data['receipt_nonce'],evidence,data['end_checks'])
    if existing:
        if existing!=expected:raise ValueError('Homepage completion conflict')
        return
    db.execute('SAVEPOINT homepage_finish')
    try:
        if fence() is not True:raise ValueError('Active lease required')
        db.execute('INSERT INTO card_v3_page_evidence VALUES (?,?,?,?,?,?,?,?,?)',(*scope,index,1,*expected))
        db.execute('INSERT INTO card_v3_pages VALUES (?,?,?,?,?,?)',(*scope,index,1,'home_complete'))
        db.execute('RELEASE homepage_finish')
    except Exception:
        db.execute('ROLLBACK TO homepage_finish');db.execute('RELEASE homepage_finish');raise

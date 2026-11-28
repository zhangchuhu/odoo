"""Validate a nonce-bound browser observation of a specific search page."""
from datetime import datetime,timezone
import re
import json
import secrets
from urllib.parse import urlsplit,unquote,parse_qs


def verify_page_receipt(expected, receipt, *, now=None):
    keys={'request_id','plan_digest','lease_epoch','query_index','page','query','nonce','tab_id','window_id','session_id','page_url'}
    if isinstance(expected,dict) and 'platform' in expected:keys.add('platform')
    if (not isinstance(expected,dict) or set(expected)!=keys or not isinstance(receipt,dict)
            or set(receipt)!=keys|{'observed_at'} or any(receipt[k]!=expected[k] or type(receipt[k]) is not type(expected[k]) for k in keys)):
        raise ValueError('Page receipt binding mismatch')
    if (not isinstance(expected['request_id'],str) or not expected['request_id'] or
            not re.fullmatch('[0-9a-f]{64}',str(expected['plan_digest'])) or
            not re.fullmatch('[0-9a-f]{32}',str(expected['nonce'])) or
            not re.fullmatch('[0-9a-f]{32}',str(expected['session_id'])) or
            type(expected['lease_epoch']) is not int or expected['lease_epoch']<1 or
            type(expected['query_index']) is not int or not 0<=expected['query_index']<9 or
            type(expected['page']) is not int or not 1<=expected['page']<=3 or
            not isinstance(expected['query'],str) or not expected['query'].strip() or
            any(type(expected[k]) is not int or expected[k]<1 for k in ('tab_id','window_id'))):
        raise ValueError('Invalid page binding')
    try:
        url=urlsplit(expected['page_url'])
        platform=expected.get('platform','mercado-libre-mx')
        hosts={'mercado-libre-mx':'listado.mercadolibre.com.mx','shein-us':'us.shein.com'}
        if (platform not in hosts or url.scheme!='https' or url.hostname!=hosts[platform] or url.username or url.password or url.port):
            raise ValueError('Untrusted search page')
        if platform=='shein-us':
            match=re.fullmatch(r'/pdsearch/(.+)/',url.path)
            normalized=lambda value:' '.join(value.replace('-',' ').casefold().split())
            if (expected['page']!=1 or not match or normalized(unquote(match.group(1)))!=normalized(expected['query'])
                    or any(value!='1' for value in parse_qs(url.query).get('page',[]))):
                raise ValueError('SHEIN search must be the bound homepage')
        observed=datetime.fromisoformat(receipt['observed_at'].replace('Z','+00:00'))
        current=now or datetime.now(timezone.utc)
        if observed.utcoffset() is None or current.utcoffset() is None or not 0<=(current-observed).total_seconds()<=15:
            raise ValueError('Stale search page')
    except (TypeError,AttributeError,OverflowError) as exc:raise ValueError('Invalid page observation') from exc
    return True


class PageReceiptGuard:
    """One-use page challenge. Caller authenticates origin and derives page URL."""
    def __init__(self,db):
        self.db=db
        db.execute('''CREATE TABLE IF NOT EXISTS card_v3_receipt_challenges (
            nonce TEXT PRIMARY KEY,body TEXT NOT NULL,issued_at TEXT NOT NULL,used INTEGER NOT NULL DEFAULT 0)''')

    def issue(self,request,*,fence):
        if not callable(fence) or fence() is not True or not isinstance(request,dict) or 'nonce' in request:
            raise ValueError('Invalid page challenge')
        now=datetime.now(timezone.utc);expected=dict(request,nonce=secrets.token_hex(16))
        verify_page_receipt(expected,dict(expected,observed_at=now.isoformat()),now=now)
        if fence() is not True:raise ValueError('Active lease required')
        self.db.execute('INSERT INTO card_v3_receipt_challenges VALUES (?,?,?,0)',
                        (expected['nonce'],json.dumps(expected,sort_keys=True,separators=(',',':')),now.isoformat()))
        return expected

    def issue_for_page(self,binding,page,*,fence):
        keys=('request_id','plan_digest','lease_epoch','query_index','query','tab_id','window_id','session_id','url')
        supported=isinstance(binding,dict) and ((binding.get('version') in (4,5) and binding.get('platform')=='mercado-libre-mx') or (binding.get('version')==6 and binding.get('platform')=='shein-us'))
        if (not supported or any(k not in binding for k in keys) or type(page) is not int or not 1<=page<=3
                or (binding.get('version') in (5,6) and page!=1)
                or not callable(fence) or fence() is not True):raise ValueError('Current query binding required')
        scope=(binding['request_id'],binding['plan_digest'],binding['lease_epoch'])
        query=self.db.execute('SELECT query FROM card_v3_queries WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=?',(*scope,binding['query_index'])).fetchone()
        if not query or query[0]!=binding['query']:raise ValueError('Unsealed search query')
        receipts=[json.loads(row[0]) for row in self.db.execute('SELECT body FROM accepted_receipts WHERE request_id=?',(scope[0],))]
        if not any(r.get('version')==binding['version'] and r.get('platform')==binding['platform'] and all(r.get(k)==binding[k] for k in keys) for r in receipts):raise ValueError('Current query receipt required')
        url=binding['url']
        if page>1:
            previous=self.db.execute('SELECT next_url FROM card_v3_navigation WHERE request_id=? AND plan_digest=? AND lease_epoch=? AND query_index=? AND page=?',(*scope,binding['query_index'],page-1)).fetchone()
            challenges=[json.loads(row[0]) for row in self.db.execute('SELECT body FROM card_v3_receipt_challenges WHERE used=1')]
            if not previous or not any(all(ch.get(k)==binding[k] for k in keys if k!='url') and ch.get('page')==page-1 for ch in challenges):
                raise ValueError('Confirmed prior page required')
            url=previous[0]
        request={k:binding[k] for k in keys if k!='url'}
        if binding['platform']=='shein-us':request['platform']='shein-us'
        return self.issue(dict(request,page=page,page_url=url),fence=fence)

    def confirm(self,receipt,*,fence):
        if not callable(fence) or fence() is not True or not isinstance(receipt,dict):raise ValueError('Active lease required')
        row=self.db.execute('SELECT body,issued_at,used FROM card_v3_receipt_challenges WHERE nonce=?',(receipt.get('nonce'),)).fetchone()
        if not row or row[2]:raise ValueError('Page challenge missing or used')
        now=datetime.now(timezone.utc);issued=datetime.fromisoformat(row[1])
        if not 0<=(now-issued).total_seconds()<=15:raise ValueError('Page challenge expired')
        verify_page_receipt(json.loads(row[0]),receipt,now=now)
        if fence() is not True:raise ValueError('Active lease required')
        changed=self.db.execute('UPDATE card_v3_receipt_challenges SET used=1 WHERE nonce=? AND used=0',(receipt['nonce'],)).rowcount
        if changed!=1:raise ValueError('Page challenge already consumed')
        return True

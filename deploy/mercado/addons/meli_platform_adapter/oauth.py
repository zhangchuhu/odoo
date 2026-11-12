"""OAuth network exchange; credential persistence is supplied by the store addon."""
from . import api


def exchange(data):
    return api.request('POST', '/oauth/token', data=data, timeout=30)


def credential_token(store, storage):
    from datetime import timedelta
    from psycopg2.errors import LockNotAvailable, SerializationFailure
    from odoo import fields
    from odoo.exceptions import UserError
    import requests
    store.ensure_one()
    store.check_access('read')
    # Retry only a database conflict occurring BEFORE any OAuth request.
    for attempt in range(3):
        sent = False
        try:
            with storage.refresh_context(store) as current:
                if current.state != 'connected':
                    raise UserError('请先授权目标店铺。')
                if current.access_token and current.expires_at and current.expires_at > fields.Datetime.now() + timedelta(minutes=2):
                    return current.access_token
                if not current.refresh_token:
                    raise UserError('店铺授权已失效，请重新授权此店铺。')
                company = current.company_id
                try:
                    sent = True
                    response = exchange({
                        'grant_type': 'refresh_token',
                        'client_id': company.mercadolibre_client_id,
                        'client_secret': company.mercadolibre_secret_key,
                        'refresh_token': current.refresh_token,
                    })
                    try:
                        data = response.json()
                        if not 200 <= response.status_code < 300 or not isinstance(data, dict):
                            raise ValueError()
                        expiry = data.get('expires_in')
                        if isinstance(expiry, bool) or not isinstance(expiry, int) or not 0 < expiry <= 31536000:
                            raise ValueError()
                        if any(not isinstance(data.get(k), str) or not data[k] for k in ('access_token', 'refresh_token')):
                            raise ValueError()
                        if str(data.get('user_id')) != current.seller_id:
                            raise ValueError()
                    finally:
                        response.close()
                except (requests.RequestException, ValueError):
                    raise UserError('店铺授权刷新失败，请重新授权或稍后重试。') from None
                current.write({
                    'access_token': data['access_token'], 'refresh_token': data['refresh_token'],
                    'expires_at': fields.Datetime.now() + timedelta(seconds=expiry),
                })
                return data['access_token']
        except LockNotAvailable:
            raise UserError('店铺正在更新，请在当前操作完成后重试。') from None
        except SerializationFailure:
            if sent or attempt == 2:
                raise UserError('店铺授权状态发生变化，请重新检查授权。') from None

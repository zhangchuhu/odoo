"""Fixed-origin requests interface for OAuth and store operations.

No redirects, environment credentials, or automatic retries. Responses are
buffered before closing the session, matching requests' default behavior.
"""
import os
import re
from urllib.parse import urlsplit
import requests
from .errors import PlatformError

API = 'https://api.mercadolibre.com'


def _configured_proxy():
    value = os.environ.get('MERCADO_API_HTTPS_PROXY', '')
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in ('http', 'https') and parsed.hostname and parsed.port
                 and parsed.username is None and parsed.password is None
                 and parsed.path in ('', '/') and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise PlatformError(400, 'Invalid explicit Mercado proxy configuration')
    return {'https': value}


def request(method, path, *, token=None, params=None, data=None, json=None, files=None, timeout=30):
    if not re.fullmatch(r'/[A-Za-z0-9_/-]+', path) or path.startswith('//'):
        raise PlatformError(400, 'Invalid platform path')
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    proxy = _configured_proxy()
    try:
        with requests.Session() as session:
            session.trust_env = False
            options = {'proxies': proxy} if proxy else {}
            return session.request(method, API + path, headers=headers, params=params,
                                   data=data, json=json, files=files, timeout=timeout,
                                   allow_redirects=False, **options)
    except requests.RequestException:
        raise requests.RequestException('Mercado Libre request failed or timed out') from None

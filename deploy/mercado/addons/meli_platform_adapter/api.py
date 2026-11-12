"""Fixed-origin requests interface for OAuth and store operations.

No redirects, environment credentials, or automatic retries. Responses are
buffered before closing the session, matching requests' default behavior.
"""
import re
import requests
from .errors import PlatformError

API = 'https://api.mercadolibre.com'


def request(method, path, *, token=None, params=None, data=None, json=None, files=None, timeout=30):
    if not re.fullmatch(r'/[A-Za-z0-9_/-]+', path) or path.startswith('//'):
        raise PlatformError(400, 'Invalid platform path')
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    try:
        with requests.Session() as session:
            session.trust_env = False
            return session.request(method, API + path, headers=headers, params=params,
                                   data=data, json=json, files=files, timeout=timeout,
                                   allow_redirects=False)
    except requests.RequestException:
        raise requests.RequestException('Mercado Libre request failed or timed out') from None

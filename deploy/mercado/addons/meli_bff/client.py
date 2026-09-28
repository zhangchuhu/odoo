"""Fixed-origin Mercado Libre transport."""
import json
import http.client
import re
import tempfile
from urllib.parse import urlencode, urlsplit

from .protocol import BffError, filtered_response_headers

API = 'https://api.mercadolibre.com'


class PlatformResponse:
    def __init__(self, status, headers, stream):
        self.status, self.headers, self.stream = status, headers, stream

    def iter_body(self):
        try:
            while chunk := self.stream.read(64 * 1024):
                yield chunk
        finally:
            self.close()

    def close(self):
        self.stream.close()

    def json(self):
        try:
            # Internal preflight calls request identity encoding; cap decoded metadata.
            content = self.stream.read(8 * 1024 * 1024 + 1)
            if len(content) > 8 * 1024 * 1024:
                raise ValueError('Metadata too large')
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError('Expected object')
            return data
        except (ValueError, UnicodeError):
            raise BffError(502, 'Invalid Mercado Libre metadata response') from None
        finally:
            self.close()


def send(method, path, *, token, body=b'', query=(), content_type=None, accept=None, timeout=120):
    if not re.fullmatch(r'/[A-Za-z0-9_/-]+', path) or path.startswith('//'):
        raise BffError(400, 'Invalid platform path')
    headers = {'Authorization': 'Bearer ' + token, 'Accept-Encoding': 'identity'}
    if content_type:
        headers['Content-Type'] = content_type
    if accept:
        headers['Accept'] = accept
    encoded_query = query if isinstance(query, str) else urlencode(query)
    if re.search(r'[^\x21-\x7e]|#', encoded_query):
        raise BffError(400, 'Invalid encoded query')
    target = path + ('?' + encoded_query if encoded_query else '')
    origin = urlsplit(API)
    connection_class = http.client.HTTPSConnection if origin.scheme == 'https' else http.client.HTTPConnection
    connection = connection_class(origin.hostname, origin.port, timeout=min(15, timeout))
    spool = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
    try:
        # http.client sends the validated request target without requests/urllib3
        # normalizing percent escapes. It uses no environment proxy or netrc auth.
        connection.connect()
        connection.sock.settimeout(timeout)
        connection.request(method, target, body=body, headers=headers)
        with connection.getresponse() as response:
            while chunk := response.read(64 * 1024):
                spool.write(chunk)
            if response.length not in (None, 0):
                raise http.client.IncompleteRead(b'')
            spool.seek(0)
            return PlatformResponse(response.status, filtered_response_headers(response.getheaders()), spool)
    except (http.client.HTTPException, OSError, ValueError):
        spool.close()
        raise BffError(502, 'Mercado Libre request failed or timed out') from None
    finally:
        connection.close()

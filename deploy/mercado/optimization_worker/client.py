"""Bounded stateless HTTP, no redirects/retries or response/credential logging."""
import base64
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request

from .domain import canonical_hash


class APIError(RuntimeError):
    def __init__(self, status=0):
        self.status = status
        super().__init__('Optimization API request failed (status %s)' % status)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base_url, api_key, timeout=15, response_limit=12 * 1024 * 1024):
        parsed = urllib.parse.urlsplit(base_url)
        if (parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')
                or (parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1', 'localhost', '::1'))):
            raise ValueError('API origin must use HTTPS (HTTP allowed on loopback only)')
        if not api_key or '\r' in api_key or '\n' in api_key:
            raise ValueError('An API key is required')
        if not 0 < timeout <= 120 or not 0 < response_limit <= 12 * 1024 * 1024:
            raise ValueError('Invalid HTTP bounds')
        self.base_url, self._api_key = base_url.rstrip('/'), api_key
        self.timeout, self.response_limit = timeout, response_limit
        self.identity = hashlib.sha256((self.base_url + '\0' + api_key).encode()).hexdigest()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def request(self, method, path, body=None):
        if not path.startswith('/') or path.startswith('//') or '?' in path or '#' in path:
            raise ValueError('Invalid API path')
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        limit = 12 * 1024 * 1024 if path.endswith('/artifacts') else 256 * 1024
        if data is not None and len(data) > limit:
            raise ValueError('Request exceeds bound')
        request = urllib.request.Request(self.base_url + path, data=data, method=method,
            headers={'Authorization': 'Bearer ' + self._api_key, 'Content-Type': 'application/json'})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(self.response_limit + 1)
                if len(raw) > self.response_limit:
                    raise APIError(response.status)
                envelope = json.loads(raw)
                if not isinstance(envelope, dict) or envelope.get('success') is not True or 'data' not in envelope:
                    raise APIError(response.status)
                return envelope['data']
        except urllib.error.HTTPError as exc:
            raise APIError(exc.code) from None
        except (OSError, ValueError) as exc:
            raise APIError() from None

    def claim(self, capabilities):
        return self.request('POST', '/internal/meli-optimization/claim', {'capabilities': list(capabilities)})

    def heartbeat(self, claim):
        return self.request('POST', '/internal/meli-optimization/tasks/%s/heartbeat' % claim['id'],
                            {k: claim[k] for k in ('lease_token', 'lease_epoch')})

    def submit(self, claim, operation, envelope):
        if operation not in ('checkpoint', 'complete', 'wait', 'fail'):
            raise ValueError('Invalid submission operation')
        return self.request('POST', '/internal/meli-optimization/tasks/%s/%s' % (claim['id'], operation), envelope)

    def snapshot(self, snapshot_id):
        result = self.request('GET', '/api/meli/optimization/snapshots/' + _id(snapshot_id))
        if canonical_hash(result['payload']) != result['digest']:
            raise ValueError('Snapshot digest mismatch')
        return result

    def artifact(self, artifact_id):
        result = self.request('GET', '/api/meli/optimization/artifacts/' + _id(artifact_id))
        digest = (hashlib.sha256(base64.b64decode(result['data'], validate=True)).hexdigest()
                  if result.get('data') else canonical_hash(result['payload']))
        if digest != result['digest']:
            raise ValueError('Artifact digest mismatch')
        return result

    def create_artifact(self, case_id, *, payload=None, data=None, mime_type=None):
        if (payload is None) == (data is None):
            raise ValueError('Supply exactly one artifact value')
        body = {'payload': payload, 'digest': canonical_hash(payload)} if data is None else {
            'data': base64.b64encode(data).decode(), 'digest': hashlib.sha256(data).hexdigest()}
        if mime_type:
            body['mime_type'] = mime_type
        return self.request('POST', '/api/meli/optimization/cases/%s/artifacts' % _id(case_id), body)


def _id(value):
    value = str(value)
    if not value.isascii() or not value.isdecimal() or int(value) < 1:
        raise ValueError('This deployment requires positive decimal API reference IDs')
    return value

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

    @property
    def transient(self):
        return self.status in (0, 408, 429) or 500 <= self.status < 600


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base_url, api_key, timeout=15, response_limit=12 * 1024 * 1024, *, research_ledger_enabled=False):
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
        if type(research_ledger_enabled) is not bool: raise ValueError('Invalid research ledger setting')
        self.research_ledger_enabled = research_ledger_enabled
        self.timeout, self.response_limit = timeout, response_limit
        self.identity = hashlib.sha256((self.base_url + '\0' + api_key).encode()).hexdigest()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def request(self, method, path, body=None):
        if not path.startswith('/') or path.startswith('//') or '?' in path or '#' in path:
            raise ValueError('Invalid API path')
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        limit = 12 * 1024 * 1024 if path.endswith(('/artifacts', '/research-artifacts')) else 256 * 1024
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

    def claim(self, capabilities, task_id=None):
        if task_id is not None and (type(task_id) is not int or task_id < 1):
            raise ValueError('Invalid target task')
        body = {'capabilities': list(capabilities)}
        if task_id is not None:
            return self.request('POST', '/internal/meli-optimization/tasks/%s/claim' % task_id, body)
        return self.request('POST', '/internal/meli-optimization/claim', body)

    def has_claimable_research(self):
        value=self.request('GET','/internal/meli-optimization/research-queue')
        if not isinstance(value,dict) or set(value)!={'claimable'} or type(value['claimable']) is not bool:
            raise ValueError('Invalid queue precheck')
        return value['claimable']

    def retry_unknown_research_batch(self):
        return self.request('POST', '/internal/meli-optimization/research-retry-unknown', {'limit': 50})

    def retry_expired_research(self, claim, request_id):
        return self.request('POST','/internal/meli-optimization/research-retry-expired',{
            'task_id':claim['id'],'lease_token':claim['lease_token'],'lease_epoch':claim['lease_epoch'],'request_id':request_id})

    def continue_registered_research(self, claim, request_id, intent_id, *, evidence_artifact_id=None,
                                     evidence_digest=None, confirmed_images=None):
        body = {
            'task_id': claim['id'], 'request_id': request_id, 'intent_id': intent_id,
            **{k: claim[k] for k in ('lease_token', 'lease_epoch', 'snapshot_id', 'input_hash')}}
        if any(v is not None for v in (evidence_artifact_id,evidence_digest,confirmed_images)):
            if not self.research_ledger_enabled or None in (evidence_artifact_id,evidence_digest,confirmed_images):
                raise ValueError('Complete scoped gallery recovery proof required')
            body.update(evidence_artifact_id=evidence_artifact_id,evidence_digest=evidence_digest,confirmed_images=confirmed_images)
        return self.request('POST', '/internal/meli-optimization/continue-registered-research', body)

    def retry_unknown_tasks(self, task_types):
        allowed = {'product.facts', 'research.keywords', 'image.generate'}
        if not task_types or any(item not in allowed for item in task_types):
            raise ValueError('Invalid retry task types')
        return self.request('POST', '/internal/meli-optimization/retry-unknown-tasks',
                            {'limit': 50, 'task_types': list(task_types)})

    def heartbeat(self, claim):
        return self.request('POST', '/internal/meli-optimization/tasks/%s/heartbeat' % claim['id'],
                            {k: claim[k] for k in ('lease_token', 'lease_epoch')})

    def recover_lease(self, claim, request_id, recovery_intent_id):
        return self.request('POST', '/internal/meli-optimization/tasks/%s/recover-lease' % claim['id'],
                            {'request_id': request_id, 'lease_token': claim['lease_token'],
                             'lease_epoch': claim['lease_epoch'], 'recovery_intent_id': recovery_intent_id})

    def host_heartbeat(self, configuration, authentication, last_result):
        return self.request('POST', '/internal/meli-optimization/host/heartbeat', {'configuration': configuration, 'authentication': authentication, 'last_result': last_result})

    def host_progress(self, claim, request_id, stage, intent_id=None, review_seq=None, review_kind=None, candidate_artifact_id=None, candidate_sha256=None, candidate_attempt_id=None, candidate_attempt_number=None):
        return self.request('POST', '/internal/meli-optimization/tasks/%s/host-progress' % claim['id'],
                            {'lease_token': claim['lease_token'], 'lease_epoch': claim['lease_epoch'], 'request_id': request_id, 'stage': stage, **({'intent_id': intent_id} if intent_id is not None else {}), **({'review_seq': review_seq} if review_seq is not None else {}), **({'review_kind': review_kind} if review_kind is not None else {}), **({'candidate_artifact_id': candidate_artifact_id} if candidate_artifact_id is not None else {}), **({'candidate_sha256': candidate_sha256} if candidate_sha256 is not None else {}), **({'candidate_attempt_id': candidate_attempt_id} if candidate_attempt_id is not None else {}), **({'candidate_attempt_number': candidate_attempt_number} if candidate_attempt_number is not None else {})})

    def host_review_evidence(self, claim, evidence):
        return self.request('POST', '/internal/meli-optimization/tasks/%s/host-review-evidence' % claim['id'],
                            {'lease_token': claim['lease_token'], 'lease_epoch': claim['lease_epoch'], 'evidence': evidence})

    def submit(self, claim, operation, envelope):
        if operation not in ('checkpoint', 'complete', 'wait', 'fail'):
            raise ValueError('Invalid submission operation')
        return self.request('POST', '/internal/meli-optimization/tasks/%s/%s' % (claim['id'], operation), envelope)

    def snapshot(self, snapshot_id):
        result = self._reference('/api/meli/optimization/snapshots/' + _id(snapshot_id))
        if canonical_hash(result['payload']) != result['digest']:
            raise ValueError('Snapshot digest mismatch')
        return result

    def artifact(self, artifact_id):
        result = self._reference('/api/meli/optimization/artifacts/' + _id(artifact_id))
        digest = (hashlib.sha256(base64.b64decode(result['data'], validate=True)).hexdigest()
                  if result.get('data') else canonical_hash(result['payload']))
        if digest != result['digest']:
            raise ValueError('Artifact digest mismatch')
        return result

    def _reference(self, path):
        try:
            return self.request('GET', path)
        except APIError as exc:
            # Reference validation has no lease semantics. Keep transport/server
            # failures recoverable; let the caller classify invalid input/output.
            if exc.status in (403, 404, 409):
                raise ValueError('Referenced snapshot or artifact is unavailable') from None
            raise

    def create_artifact(self, case_id, *, payload=None, data=None, mime_type=None):
        if (payload is None) == (data is None):
            raise ValueError('Supply exactly one artifact value')
        body = {'payload': payload, 'digest': canonical_hash(payload)} if data is None else {
            'data': base64.b64encode(data).decode(), 'digest': hashlib.sha256(data).hexdigest()}
        if mime_type:
            body['mime_type'] = mime_type
        return self.request('POST', '/api/meli/optimization/cases/%s/artifacts' % _id(case_id), body)

    def create_research_artifact(self, claim, request_id, intent_id, *, data, mime_type):
        if not isinstance(data, bytes) or not isinstance(mime_type, str) or not mime_type:
            raise ValueError('Binary artifact and MIME type required')
        if not all(isinstance(v, str) and v for v in (request_id, intent_id)):
            raise ValueError('Request and intent required')
        artifact = {'data': base64.b64encode(data).decode(), 'digest': hashlib.sha256(data).hexdigest(), 'mime_type': mime_type}
        body = {'request_id': request_id, 'intent_id': intent_id, 'lease_token': claim['lease_token'], 'lease_epoch': claim['lease_epoch'], 'artifact': artifact}
        return self.request('POST', '/internal/meli-optimization/tasks/%s/research-artifacts' % _id(claim['id']), body)

    def research_intent(self, claim, request_id, intent_id):
        if not all(isinstance(v, str) and v for v in (request_id, intent_id)):
            raise ValueError('Request and intent required')
        body = {'request_id': request_id, 'intent_id': intent_id, 'lease_token': claim['lease_token'], 'lease_epoch': claim['lease_epoch']}
        return self.request('POST', '/internal/meli-optimization/tasks/%s/research-intents/query' % _id(claim['id']), body)

    def research_intent_reconcile(self, task_id, request_id, intent_id):
        if not all(isinstance(v, str) and v for v in (request_id, intent_id)):
            raise ValueError('Request and intent required')
        return self.request('POST', '/api/meli/optimization/tasks/%s/research-intents/reconcile' % _id(task_id), {'request_id': request_id, 'intent_id': intent_id})

    def seal_research_manifest(self, claim, request_id, intents):
        if not isinstance(request_id,str) or not request_id or not isinstance(intents,list) or not intents or len(set(intents)) != len(intents) or any(not isinstance(i,str) or not i for i in intents):
            raise ValueError('Valid request and unique intents required')
        body={'request_id':request_id,'intents':intents,'lease_token':claim['lease_token'],'lease_epoch':claim['lease_epoch']}
        return self.request('POST','/internal/meli-optimization/tasks/%s/research-intents/seal' % _id(claim['id']),body)


def _id(value):
    value = str(value)
    if not value.isascii() or not value.isdecimal() or int(value) < 1:
        raise ValueError('This deployment requires positive decimal API reference IDs')
    return value

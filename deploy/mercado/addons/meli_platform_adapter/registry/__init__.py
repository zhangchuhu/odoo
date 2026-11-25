"""Validated, immutable operation contracts for BFF and MCP catalogues."""
import copy
from datetime import date
import hashlib
import json
import re
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, ValidationError
from jsonschema.exceptions import SchemaError


class RegistryError(ValueError):
    """A registry definition or operation invocation is invalid."""


_REQUIRED = frozenset({
    'operation_id', 'domain', 'title', 'description', 'docs_url', 'verified_at',
    'input_schema', 'permission', 'scope', 'capability_requirements',
    'ownership_policy', 'handler_id', 'upstream', 'request_kind', 'response_kind',
    'exposure', 'retry_policy',
})
_PUBLIC = frozenset({'operation_id', 'domain', 'title', 'description', 'input_schema',
                     'permission', 'scope', 'capability_requirements', 'request_kind',
                     'response_kind', 'exposure'})


def _check_definition(entry, handlers, policies):
    if not isinstance(entry, dict) or set(entry) != _REQUIRED:
        raise RegistryError('Operation fields must match the registry contract')
    for key in ('operation_id', 'domain', 'title', 'description', 'docs_url', 'verified_at',
                'ownership_policy', 'handler_id'):
        if not isinstance(entry[key], str) or not entry[key].strip():
            raise RegistryError('Missing operation metadata: ' + key)
    if not re.fullmatch(r'[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+', entry['operation_id']):
        raise RegistryError('Invalid operation ID')
    try:
        source = urlsplit(entry['docs_url'])
        invalid_port = source.port is not None
    except ValueError:
        raise RegistryError('Invalid documentation URL') from None
    if source.scheme != 'https' or source.hostname != 'global-selling.mercadolibre.com' or source.username or source.password or invalid_port or source.fragment:
        raise RegistryError('Operation source must be official documentation')
    try:
        date.fromisoformat(entry['verified_at'])
    except ValueError:
        raise RegistryError('Invalid source verification date') from None
    if entry['permission'] not in {'read', 'write'} or entry['scope'] not in {'shop', 'public'}:
        raise RegistryError('Invalid permission or scope')
    if entry['handler_id'] not in handlers or entry['ownership_policy'] not in policies:
        raise RegistryError('Unregistered handler or ownership policy')
    if entry['request_kind'] not in {'json', 'multipart'} or entry['response_kind'] not in {'json', 'binary'}:
        raise RegistryError('Unsupported request or response kind')
    if entry['exposure'] not in {'mcp', 'bff_only', 'inbound'} or entry['retry_policy'] != 'never':
        raise RegistryError('Unsupported exposure or retry policy')
    if not isinstance(entry['capability_requirements'], list) or any(not isinstance(c, str) or not c for c in entry['capability_requirements']):
        raise RegistryError('Invalid capabilities')
    upstream = entry['upstream']
    if not isinstance(upstream, dict) or set(upstream) != {'method', 'path'}:
        raise RegistryError('Invalid upstream definition')
    if upstream['method'] not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}:
        raise RegistryError('Invalid upstream method')
    path = upstream['path']
    if not isinstance(path, str) or not re.fullmatch(r'/[A-Za-z0-9_{}\-/]+', path) or '//' in path:
        raise RegistryError('Upstream must be a fixed relative path template')
    schema = entry['input_schema']
    if not isinstance(schema, dict) or schema.get('type') != 'object' or schema.get('additionalProperties') is not False:
        raise RegistryError('Input schema must reject unknown top-level properties')
    try:
        # References are intentionally disabled; no network retrieval or unresolved schemas.
        serialized = json.dumps(schema, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
        if '"$ref"' in serialized or '"$dynamicRef"' in serialized:
            raise RegistryError('Schema references are not supported')
        Draft202012Validator.check_schema(schema)
    except (SchemaError, TypeError, ValueError) as exc:
        if isinstance(exc, RegistryError):
            raise
        raise RegistryError('Invalid input schema') from None


class OperationRegistry:
    """Build once from reviewed definitions; all public views are defensive copies."""
    def __init__(self, definitions, *, handlers, policies):
        records = {}
        handlers, policies = frozenset(handlers), frozenset(policies)
        for definition in definitions:
            entry = copy.deepcopy(definition)
            _check_definition(entry, handlers, policies)
            identifier = entry['operation_id']
            if identifier in records:
                raise RegistryError('Duplicate operation ID')
            records[identifier] = entry
        self.__records = records
        canonical = json.dumps([records[k] for k in sorted(records)], sort_keys=True,
                               separators=(',', ':'), ensure_ascii=True, allow_nan=False)
        self.__version = hashlib.sha256(canonical.encode()).hexdigest()

    @property
    def version(self):
        return self.__version

    def catalog(self):
        return [copy.deepcopy({k: v for k, v in self.__records[name].items() if k in _PUBLIC})
                for name in sorted(self.__records) if self.__records[name]['exposure'] == 'mcp']

    def definition(self, identifier):
        try:
            return copy.deepcopy(self.__records[identifier])
        except (KeyError, TypeError):
            raise RegistryError('Unknown operation') from None

    def validate(self, identifier, arguments):
        entry = self.definition(identifier)
        try:
            Draft202012Validator(entry['input_schema']).validate(arguments)
        except ValidationError:
            raise RegistryError('Invalid operation arguments') from None

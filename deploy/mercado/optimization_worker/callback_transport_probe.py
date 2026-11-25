"""Real disposable HTTP probe transport; never asserts business callback health."""
from dataclasses import dataclass
import hashlib
import hmac
import re

@dataclass(frozen=True)
class TransportProbeResult:
    transport_verified: bool
    business_write_verified: bool = False


@dataclass(frozen=True)
class CallbackDiagnostics:
    transport_verified: bool
    artifact_orm_write_read_verified: bool
    task_completion_verified: bool = False


def probe_diagnostics(client, nonce, case_id):
    transport = probe_transport(client, nonce).transport_verified
    if not transport or type(case_id) is not int or case_id <= 0:
        return CallbackDiagnostics(transport, False)
    try:
        result=client.request('POST','/internal/meli-optimization/callback-probe/artifact-rollback',{'case_id':case_id})
        verified=(isinstance(result,dict) and set(result)=={'artifact_orm_write_read_verified','task_completion_verified'} and
                  result['artifact_orm_write_read_verified'] is True and result['task_completion_verified'] is False)
        return CallbackDiagnostics(True,verified)
    except Exception:
        return CallbackDiagnostics(True,False)


def probe_transport(client, nonce):
    if not isinstance(nonce,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32}',nonce):
        return TransportProbeResult(False)
    digest=hashlib.sha256(nonce.encode('ascii')).hexdigest()
    prefix='/internal/meli-optimization/callback-probe/'
    identifier=None;verified=False;deleted=False
    try:
        created=client.request('POST',prefix+'create',{'nonce':nonce})
        identifier=created.get('probe_id') if isinstance(created,dict) else None
        if not isinstance(identifier,str) or not re.fullmatch(r'[0-9a-f]{32}',identifier): raise ValueError('Invalid receipt')
        if not isinstance(created.get('digest'),str) or not hmac.compare_digest(created['digest'],digest): raise ValueError('Invalid digest')
        read=client.request('POST',prefix+'read',{'probe_id':identifier,'nonce':nonce})
        if not isinstance(read,dict) or read.get('probe_id')!=identifier or not isinstance(read.get('digest'),str) or not hmac.compare_digest(read['digest'],digest): raise ValueError('Invalid readback')
        verified=True
    except Exception:
        verified=False
    finally:
        if identifier:
            try:
                receipt=client.request('POST',prefix+'delete',{'probe_id':identifier,'nonce':nonce})
                deleted=isinstance(receipt,dict) and receipt.get('deleted') is True
            except Exception:
                deleted=False
    return TransportProbeResult(verified and deleted)

"""Opt-in request-scoped artifact transport; legacy stays the default."""
import hashlib


def intent_id(kind, key):
    if kind not in ('evidence', 'image') or not isinstance(key, str):
        raise ValueError('Invalid research upload intent')
    return kind + ':' + hashlib.sha256(key.encode()).hexdigest()


def upload(client, claim, request_id, kind, key, case_id, raw, mime):
    if getattr(client, 'research_ledger_enabled', False) is True:
        return client.create_research_artifact(claim, request_id, intent_id(kind, key), data=raw, mime_type=mime)
    return client.create_artifact(case_id, data=raw, mime_type=mime)


def seal_manifest(client, claim, request_id, evidence, uploads):
    if not isinstance(evidence,dict) or evidence.get('ledger_v1') is not True or not evidence.get('artifact_id') or not evidence.get('digest') or not isinstance(uploads,dict):
        raise ValueError('Evidence intent requires confirmed scoped receipt')
    intents=[intent_id('evidence','')]
    for key,value in sorted(uploads.items()):
        if not isinstance(key,str) or not isinstance(value,dict) or value.get('ledger_v1') is not True or value.get('state')!='confirmed' or not value.get('artifact_id') or not value.get('digest'):
            raise ValueError('Image intent requires confirmed scoped receipt')
        intents.append(intent_id('image',key))
    receipt=client.seal_research_manifest(claim,request_id,intents)
    if not isinstance(receipt,dict) or receipt.get('request_id')!=request_id or type(receipt.get('intent_count')) is not int or receipt['intent_count']!=len(intents) or receipt.get('coverage')!='unknown':
        raise ValueError('Manifest seal outcome remains unknown')
    return len(intents)


def lookup_receipt(client, claim, request_id, kind, key, expected_digest):
    receipt = client.research_intent(claim, request_id, intent_id(kind, key))
    return _checked_receipt(receipt, expected_digest)


def lookup_receipt_editor(client, task_id, request_id, kind, key, expected_digest):
    receipt = client.research_intent_reconcile(task_id, request_id, intent_id(kind, key))
    return _checked_receipt(receipt, expected_digest)


def _checked_receipt(receipt, expected_digest):
    artifact = str(receipt.get('artifact_id') or '') if isinstance(receipt, dict) else ''
    if not isinstance(receipt, dict) or not isinstance(expected_digest, str) or not expected_digest or receipt.get('digest') != expected_digest or not artifact.isascii() or not artifact.isdecimal() or int(artifact) < 1:
        raise ValueError('Upload outcome remains unknown')
    return artifact

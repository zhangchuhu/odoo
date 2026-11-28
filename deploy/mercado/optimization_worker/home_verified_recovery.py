"""Requeue a verified original browser wait without reviving its old lease."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .client import APIError
from .home_verification_watch import consume, ready


def resume_verified_waiting(journal, store, client, ledger, task_id=None):
    if not Path(ledger).is_file():
        return 0
    rows = journal.db.execute("""SELECT record FROM runs
        WHERE json_extract(record,'$.done')=1 AND json_extract(record,'$.outcome')='waiting'""").fetchall()
    count = 0
    for (raw,) in rows:
        run = json.loads(raw)
        claim = run.get('claim') or {}
        request = run.get('request_id')
        if (claim.get('task_type') != 'research.bestseller' or not isinstance(request, str)
                or (task_id is not None and claim.get('id') != task_id)):
            continue
        scope = tuple(claim.get(k) for k in ('company_id', 'id', 'snapshot_id', 'lease_epoch'))
        if any(type(value) is not int or value < 1 for value in scope):
            continue
        try:
            host = store.inspect(request)
        except ValueError:
            continue
        if (host.get('request_id') != request or host.get('task_type') != 'research.bestseller'
                or host.get('state') != 'waiting' or host.get('reason') != 'captcha_required'):
            continue
        with closing(sqlite3.connect(str(ledger), timeout=10)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='home_verification_watch'").fetchone():
                continue
            if not ready(db, request, scope):
                continue
        try:
            receipt = client.resume_verified_captcha(scope[1], scope[3], request)
        except APIError:
            continue
        if not isinstance(receipt, dict) or receipt.get('task_id') != scope[1] or receipt.get('state') != 'queued':
            raise ValueError('Invalid verified recovery receipt')
        with closing(sqlite3.connect(str(ledger), timeout=10)) as db, db:
            consume(db, request, scope)
        count += 1
    return count

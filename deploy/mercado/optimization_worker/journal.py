"""One process per private SQLite journal. Commit before every network effect."""
import fcntl
import json
import os
from pathlib import Path
import sqlite3


class Journal:
    def __init__(self, path, identity=None):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.lock)
            raise RuntimeError('Worker journal is already in use') from None
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, record TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS identity (value TEXT NOT NULL)')
        stored = self.db.execute('SELECT value FROM identity').fetchone()
        if identity is not None:
            if stored and stored[0] != identity:
                self.close()
                raise ValueError('Journal identity differs from configured API origin or credentials')
            if not stored:
                self.db.execute('INSERT INTO identity VALUES (?)', (identity,))
        self.db.commit()

    def save(self, record):
        self.db.execute('INSERT OR REPLACE INTO runs VALUES (?,?)',
                        (':'.join(str(record['claim'].get(k, '')) for k in ('id', 'input_hash', 'lease_epoch')), json.dumps(record, allow_nan=False)))
        self.db.commit()

    def active(self):
        return [value for (raw,) in self.db.execute('SELECT record FROM runs ORDER BY id')
                if not (value := json.loads(raw)).get('done')]

    def close(self):
        self.db.close()
        os.close(self.lock)

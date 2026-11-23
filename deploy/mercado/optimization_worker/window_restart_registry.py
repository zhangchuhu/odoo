"""Challenge-based window reidentification primitive; requires trusted anchor verification.

Not a transport or extension authentication boundary. Callers must establish
that the proof came from the designated extension and inspect the marker tab.
"""
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing


class WindowRestartRegistry:
    def __init__(self, path, *, clock=time.monotonic, ttl=120):
        if not 0 < ttl <= 300:
            raise ValueError('Invalid challenge lifetime')
        self.path, self.clock, self.ttl = str(path), clock, ttl
        self.lock = threading.Lock()
        self.pending = None
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS verified_anchor (id INTEGER PRIMARY KEY CHECK(id=1), window_id INTEGER NOT NULL, session TEXT NOT NULL, marker TEXT NOT NULL)')

    def challenge(self, request, epoch, session, fence):
        if (not isinstance(request, str) or not request or type(epoch) is not int or epoch < 1
                or not isinstance(session, str) or not re.fullmatch('[a-f0-9]{32}', session)
                or not callable(fence) or fence() is not True):
            raise ValueError('Invalid request identity')
        with self.lock:
            token = secrets.token_urlsafe(32)
            self.pending = (token, request, epoch, session, self.clock())
            return token

    def confirm(self, proof, verify_anchor, fence):
        if (not isinstance(proof, dict) or set(proof) != {'request','epoch','session','window','marker','token'}
                or type(proof['window']) is not int or proof['window'] < 1
                or not isinstance(proof['marker'], str) or not re.fullmatch('[a-f0-9]{32}', proof['marker'])
                or not callable(verify_anchor) or not callable(fence)):
            return False
        with self.lock:
            p = self.pending
            if not p or p[:4] != (proof['token'], proof['request'], proof['epoch'], proof['session']):
                return False
            if not 0 <= self.clock() - p[4] <= self.ttl or fence() is not True or verify_anchor(dict(proof)) is not True:
                return False
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('BEGIN IMMEDIATE')
                existing = db.execute('SELECT marker FROM verified_anchor WHERE id=1').fetchone()
                if existing and existing[0] != proof['marker']:
                    return False
                if fence() is not True:
                    return False
                db.execute('INSERT OR REPLACE INTO verified_anchor VALUES (1,?,?,?)', (proof['window'], proof['session'], proof['marker']))
            self.pending = None
            return True

    def current(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT window_id,session,marker FROM verified_anchor WHERE id=1').fetchone()

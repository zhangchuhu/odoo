"""Persistent selection of one explicitly registered Chrome window.

Each extension has its own Chrome session identifier.  A changed identifier
is never treated as proof that a numeric window ID survived a browser restart.
"""
import re
import sqlite3
from contextlib import closing


class FixedWindowRegistry:
    def __init__(self, path):
        self.path = str(path)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS dedicated_window (singleton INTEGER PRIMARY KEY CHECK(singleton=1), window_id INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS dedicated_window_sessions (platform TEXT PRIMARY KEY, session_id TEXT NOT NULL)')

    def select(self, platform, window_id, session_id, fence):
        if (platform not in ('mercado-libre-mx', 'shein-us') or type(window_id) is not int or window_id <= 0
                or not isinstance(session_id, str) or not re.fullmatch('[a-f0-9]{32}', session_id)
                or not callable(fence) or fence() is not True):
            return False
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            window = db.execute('SELECT window_id FROM dedicated_window WHERE singleton=1').fetchone()
            prior = db.execute('SELECT session_id FROM dedicated_window_sessions WHERE platform=?', (platform,)).fetchone()
            if (window and window[0] != window_id) or (prior and prior[0] != session_id):
                return False
            if fence() is not True:
                return False
            db.execute('INSERT OR IGNORE INTO dedicated_window VALUES (1, ?)', (window_id,))
            db.execute('INSERT OR IGNORE INTO dedicated_window_sessions VALUES (?, ?)', (platform, session_id))
            return True

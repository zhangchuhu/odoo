"""Bounded, idempotent verification attempts scoped to a task snapshot."""
import re


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS captcha_attempt_reservations (
        company_id INTEGER NOT NULL,task_id INTEGER NOT NULL,snapshot_id INTEGER NOT NULL,
        challenge_id TEXT NOT NULL,action_id TEXT NOT NULL,attempt INTEGER NOT NULL,
        PRIMARY KEY(company_id,task_id,snapshot_id,challenge_id,action_id),
        UNIQUE(company_id,task_id,snapshot_id,challenge_id,attempt))''')


def _check(task,challenge):
    if (not isinstance(task,tuple) or len(task)!=3 or any(type(x) is not int or x<1 for x in task)
            or not isinstance(challenge,str) or not re.fullmatch(r'[a-f0-9]{64}',challenge)):
        raise ValueError('Invalid verification scope')


def attempts(db,task,challenge):
    _check(task,challenge)
    return db.execute('SELECT count(*) FROM captcha_attempt_reservations WHERE company_id=? AND task_id=? AND snapshot_id=? AND challenge_id=?',(*task,challenge)).fetchone()[0]


def reserve_attempt(db,task,challenge,action,*,fence):
    _check(task,challenge)
    if not isinstance(action,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',action):raise ValueError('Invalid action identity')
    if not callable(fence) or fence() is not True:raise ValueError('Active lease required')
    key=(*task,challenge,action)
    old=db.execute('SELECT attempt FROM captcha_attempt_reservations WHERE company_id=? AND task_id=? AND snapshot_id=? AND challenge_id=? AND action_id=?',key).fetchone()
    if old:return old[0]
    if fence() is not True:raise ValueError('Active lease required')
    db.execute('''INSERT OR IGNORE INTO captcha_attempt_reservations
        SELECT ?,?,?,?,?,count(*)+1 FROM captcha_attempt_reservations
        WHERE company_id=? AND task_id=? AND snapshot_id=? AND challenge_id=? HAVING count(*)<2''',(*key,*task,challenge))
    saved=db.execute('SELECT attempt FROM captcha_attempt_reservations WHERE company_id=? AND task_id=? AND snapshot_id=? AND challenge_id=? AND action_id=?',key).fetchone()
    if not saved:raise ValueError('Verification attempt limit reached')
    return saved[0]

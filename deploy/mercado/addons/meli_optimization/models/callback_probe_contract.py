"""Strict capability-probe nonce contract; never persist raw challenges."""
import hashlib
import re

def nonce_digest(nonce):
    if not isinstance(nonce,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32}',nonce):
        raise ValueError('Invalid probe nonce')
    return hashlib.sha256(nonce.encode('ascii')).hexdigest()

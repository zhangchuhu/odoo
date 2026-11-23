"""Fail-closed gate for research executors; probes must be trusted live adapters.

This module does not itself provide Chrome, vision, or callback probes. In
particular a heartbeat or a string describing a capability is not a probe.
Callers must provide implementations that actually perform the operation and
enforce their own transport timeout. Never accept untrusted callback code.
"""
from dataclasses import dataclass
import math
import secrets
import time
import re
import threading

@dataclass(frozen=True)
class ProbeResult:
    ready: bool
    failed: tuple
    expires_at: float | None = None
    expired: tuple = ()


class SingleFlightReadiness:
    """Cache successful evidence only until its original expiry, per process."""
    def __init__(self, probe, *, ttl=10, now=time.monotonic, wall=time.time):
        if type(ttl) not in (int,float) or not math.isfinite(ttl) or not 0 < ttl <= 30:
            raise ValueError('Invalid readiness cache lifetime')
        self.probe,self.ttl,self.now,self.wall=probe,ttl,now,wall
        self.lock=threading.Lock();self.expiry=None;self.wall_expiry=None

    def ready(self):
        with self.lock:
            clock=self.now(); wall=self.wall()
            if (self.expiry is not None and clock < self.expiry and
                (self.wall_expiry is None or wall < self.wall_expiry)):
                return True
            self.expiry=None;self.wall_expiry=None
            try:
                result=self.probe()
                if isinstance(result,ProbeResult):
                    ready=result.ready is True and not result.failed
                    expiry=result.expires_at
                else:
                    ready=result is True;expiry=None
                now=self.now();wall=self.wall()
                if not ready or (expiry is not None and (not math.isfinite(expiry) or expiry <= wall)):
                    return False
                remaining=self.ttl if expiry is None else min(self.ttl,expiry-wall)
                self.expiry=now+remaining;self.wall_expiry=expiry
                return True
            except Exception:
                return False

class ReadinessEpoch:
    """Process-local freshness; no readiness survives process restart."""
    def __init__(self, *, now=time.monotonic, ttl=30):
        if type(ttl) not in (int, float) or not 0 < ttl <= 30:
            raise ValueError('Invalid readiness lifetime')
        self.now, self.ttl, self.record = now, ttl, None

    def accept(self, identity, result):
        self.record = None
        if isinstance(identity, str) and identity and isinstance(result, ProbeResult) and result.ready is True and not result.failed:
            self.record = (identity, self.now())

    def ready(self, identity):
        if not self.record or self.record[0] != identity:
            return False
        elapsed = self.now() - self.record[1]
        if not math.isfinite(elapsed) or not 0 <= elapsed <= self.ttl:
            self.record = None
            return False
        return True

@dataclass(frozen=True)
class OdooReadResult:
    read_authorized: bool
    write_verified: bool = False

def chrome_event_verified(item):
    if not isinstance(item, dict) or any(item.get(k) != v for k, v in {'type':'mcp_tool_call','server':'cua_repl','tool':'js','status':'completed','error':None}.items()):
        return False
    args, result = item.get('arguments'), item.get('result')
    if not isinstance(args, dict) or not isinstance(result, dict) or result.get('isError') is True:
        return False
    code = args.get('code')
    if not isinstance(code, str) or not re.fullmatch(r'\s*(?:(?:let|const|var)\s+[A-Za-z_$][\w$]*\s*=\s*)?await\s+cua\.getApp\([\'"]Google Chrome[\'"]\)\s*;?\s*', code):
        return False
    content = result.get('content')
    return isinstance(content, list) and any(isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str) and 'Window:' in block['text'] and 'Chrome' in block['text'] for block in content)

def probe_odoo_read(client):
    """Authenticated read only. This is not proof of artifact or result writes."""
    try:
        result = client.request('GET', '/api/meli/optimization/capabilities')
        if not isinstance(result, dict) or not isinstance(result.get('task_types'), list) or 'research.bestseller' not in result['task_types']:
            raise ValueError('invalid response')
        return OdooReadResult(True)
    except Exception:
        return OdooReadResult(False)

def check_research_capabilities(probes, *, now=time.time, max_age=30, max_duration=120):
    required = ('chrome', 'vision', 'callback')
    if (not isinstance(probes, dict) or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= 300
                                            for v in (max_age, max_duration))):
        return ProbeResult(False, required)
    timestamps = {}
    pending = required
    # Chrome opens an interactive client and often takes longest. If later
    # checks outlast a valid proof, re-observe that capability once.
    for _ in range(2):
        failed = []
        for kind in pending:
            nonce = secrets.token_urlsafe(24)
            try:
                start = now()
                evidence = probes[kind](nonce)
                end = now()
                stamp = evidence.get('observed_at') if isinstance(evidence, dict) else None
                if (type(start) not in (int, float) or type(end) not in (int, float)
                        or not math.isfinite(start) or not math.isfinite(end) or not 0 <= end-start <= max_duration
                        or type(stamp) not in (int, float) or not math.isfinite(stamp)
                        or not start <= stamp <= end or end-stamp >= max_age
                        or evidence.get('nonce') != nonce or evidence.get('ok') is not True):
                    raise ValueError('invalid proof')
                timestamps[kind] = stamp
            except Exception:
                failed.append(kind)
        if failed:
            return ProbeResult(False, tuple(failed))
        try:
            end = now()
            if type(end) not in (int, float) or not math.isfinite(end) or any(stamp > end for stamp in timestamps.values()):
                return ProbeResult(False, required)
        except Exception:
            return ProbeResult(False, required)
        pending = tuple(kind for kind in required if end - timestamps[kind] >= max_age)
        if not pending:
            return ProbeResult(True, (), min(timestamps.values()) + max_age)
    return ProbeResult(False, pending, expired=pending)

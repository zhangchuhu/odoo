"""One-time challenges fenced to a current lease and browser session."""
import re
import secrets
import threading
import time

class TaskTabChallenge:
    def __init__(self, fence, *, clock=time.monotonic, ttl=120):
        if not callable(fence) or not 0 < ttl <= 300: raise ValueError('Invalid challenge config')
        self.fence, self.clock, self.ttl = fence, clock, ttl
        self.lock = threading.Lock()
        self.pending = None

    def issue(self, request, epoch, session, window, *, keep_pending=False):
        if (not isinstance(request,str) or not request or type(epoch) is not int or epoch<1
                or not isinstance(session,str) or not re.fullmatch('[a-f0-9]{32}',session)
                or type(window) is not int or window<1 or self.fence() is not True):
            raise ValueError('Invalid current task or window')
        with self.lock:
            if (keep_pending and self.pending and self.pending[1:5] == (request,epoch,session,window)
                    and 0 <= self.clock() - self.pending[5] <= self.ttl):
                return None
            token=secrets.token_urlsafe(32)
            self.pending=(token,request,epoch,session,window,self.clock())
            return token

    def confirm(self, token, request, epoch, session, window, tab):
        with self.lock:
            value=self.pending
            if not value or value[:5] != (token,request,epoch,session,window): return False
            age=self.clock()-value[5]
            if (type(tab) is not int or tab<1 or not 0<=age<=self.ttl or self.fence() is not True): return False
            self.pending=None
            return True

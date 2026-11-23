"""Bounded per-request broker lifecycle; exceptions stop future challenges."""
import threading
import time

class NativeSupervisor:
    def __init__(self, factory, start_index=0, diagnostic=None):
        if type(start_index) is not int or not 0<=start_index<=3: raise ValueError('Invalid resume index')
        self.factory=factory;self.start_index=start_index;self.stop=threading.Event();self.thread=None;self.error=None;self.live_confirmed=False;self.confirmed_at=None;self.active_broker=None
        self.diagnostic=diagnostic or (lambda *args: None)
    def start(self):
        if self.thread is not None: raise ValueError('Supervisor already started')
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def run(self):
        try:
            self.diagnostic('supervisor','started',None)
            index=self.start_index;rebind=False
            while not self.stop.is_set():
                while index<3 and not self.stop.is_set():
                    with self.factory(index,rebind=rebind,watch_only=False) as broker:
                        self.active_broker=broker
                        self.diagnostic('supervisor','broker_ready',None)
                        while not self.stop.is_set() and not broker.done and not getattr(broker,'rebind_requested',False): broker.serve_once()
                        self.active_broker=None
                        if getattr(broker,'rebind_requested',False): index=0;rebind=True
                        else: index+=1;rebind=False
                if self.stop.is_set(): return
                with self.factory(0,rebind=False,watch_only=True) as broker:
                    self.active_broker=broker
                    while not self.stop.is_set() and not getattr(broker,'rebind_requested',False): broker.serve_once()
                    self.active_broker=None
                    if self.stop.is_set(): return
                    if getattr(broker,'rebind_requested',False): index=0;rebind=True
        except Exception as exc:
            self.active_broker=None
            self.error=type(exc).__name__
            self.diagnostic('supervisor','broker_exception',self.error)
    def close(self):
        self.stop.set()
        if self.thread: self.thread.join(10)
        if self.thread and self.thread.is_alive(): raise RuntimeError('Native broker did not stop')

    def fresh_confirmation(self):
        broker=self.active_broker
        return bool(broker and broker.guard_alive())

    def anchor_active(self):
        broker=self.active_broker
        return bool(broker and broker.anchor_origin and broker.anchor_guard_alive())

    def window_selection_status(self):
        broker=self.active_broker
        empty={'registered':False,'window_id':None,'observation_seconds_remaining':0}
        try:
            if not broker or getattr(broker,'rebind_requested',False) or type(broker.window) is not int or broker.window<=0 or not broker.session or broker.fence() is not True:
                return empty
            remaining=max(0,15-(time.monotonic()-broker.started))
            return {'registered':True,'window_id':broker.window,'observation_seconds_remaining':round(remaining,1)}
        except Exception:
            return empty

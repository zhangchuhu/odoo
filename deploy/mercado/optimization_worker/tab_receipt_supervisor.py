"""Bounded per-request broker lifecycle; exceptions stop future challenges."""
import threading
import time

class NativeSupervisor:
    def __init__(self, factory, start_index=0, diagnostic=None, expansion_state=None, max_queries=6):
        if max_queries not in (6,9) or type(max_queries) is not int or type(start_index) is not int or not 0<=start_index<=max_queries: raise ValueError('Invalid resume index')
        self.factory=factory;self.start_index=start_index;self.stop=threading.Event();self.thread=None;self.error=None;self.live_confirmed=False;self.confirmed_at=None;self.active_broker=None
        self.max_queries=max_queries
        self.diagnostic=diagnostic or (lambda *args: None)
        self.expansion_state=expansion_state
    def start(self):
        if self.thread is not None: raise ValueError('Supervisor already started')
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def run(self):
        try:
            self.diagnostic('supervisor','started',None)
            index=self.start_index;rebind=False;target=3
            if index>3:
                state=self.expansion_state() if callable(self.expansion_state) else None
                if not state or state[1]!=index or not index<=state[0]<=self.max_queries: raise ValueError('Invalid expansion resume')
                target=state[0]
            while not self.stop.is_set():
                while index<target and not self.stop.is_set():
                    with self.factory(index,rebind=rebind,watch_only=False) as broker:
                        self.active_broker=broker
                        self.diagnostic('supervisor','broker_ready',None)
                        while not self.stop.is_set() and not broker.done and not getattr(broker,'rebind_requested',False): broker.serve_once()
                        self.active_broker=None
                        if getattr(broker,'rebind_requested',False): index=0;target=3;rebind=True
                        else:
                            index+=1;rebind=False
                            if index>=3 and callable(self.expansion_state):
                                state=self.expansion_state()
                                if state and state[0]==state[1]==index: target=index
                if self.stop.is_set(): return
                with self.factory(0,rebind=False,watch_only=True) as broker:
                    self.active_broker=broker
                    while not self.stop.is_set() and not getattr(broker,'rebind_requested',False):
                        broker.serve_once()
                        state=self.expansion_state() if callable(self.expansion_state) else None
                        if state and 3 <= state[1] < state[0] <= self.max_queries:
                            index=state[1];target=state[0]
                            break
                    self.active_broker=None
                    if self.stop.is_set(): return
                    if getattr(broker,'rebind_requested',False): index=0;target=3;rebind=True
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


class CardPageSupervisor(NativeSupervisor):
    """Choose the next durable page rather than advancing one whole query."""
    def __init__(self,factory,page_state,diagnostic=None):
        super().__init__(factory,diagnostic=diagnostic,max_queries=9)
        if not callable(page_state):raise ValueError('Page state required')
        self.page_state=page_state

    def run(self):
        try:
            self.diagnostic('supervisor','started',None)
            rebind=False
            while not self.stop.is_set():
                step=(0,0) if rebind else self.page_state()
                if step is not None:
                    if (not isinstance(step,tuple) or len(step)!=2 or type(step[0]) is not int or not 0<=step[0]<9
                            or type(step[1]) is not int or not -2<=step[1]<=3):raise ValueError('Invalid page state')
                    index,page=step
                    with self.factory(index,rebind=rebind,watch_only=False) as broker:
                        broker.requested_page=page;self.active_broker=broker
                        self.diagnostic('supervisor','broker_ready',None)
                        while not self.stop.is_set() and not broker.done and not getattr(broker,'rebind_requested',False):broker.serve_once()
                        self.active_broker=None
                        if getattr(broker,'rebind_requested',False):rebind=True;continue
                        if not self.stop.is_set() and not rebind and self.page_state()==step:raise ValueError('Page completion was not durable')
                        rebind=False
                else:
                    with self.factory(0,rebind=False,watch_only=True) as broker:
                        self.active_broker=broker
                        while not self.stop.is_set() and self.page_state() is None and not getattr(broker,'rebind_requested',False):broker.serve_once()
                        self.active_broker=None
                        if getattr(broker,'rebind_requested',False):rebind=True
        except Exception as exc:
            self.active_broker=None;self.error=type(exc).__name__;self.diagnostic('supervisor','broker_exception',self.error)

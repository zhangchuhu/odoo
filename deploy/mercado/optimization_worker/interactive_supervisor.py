"""Primitive for supervising an interactive worker, gated by a live host probe.

This does not implement the browser host or attest its availability. The caller
must supply a probe that verifies the actual executor and can recover it after a
restart; a static configuration flag is not sufficient.
"""
import fcntl
import os
import subprocess
import stat


class SupervisorBusy(RuntimeError):
    pass


class Supervisor:
    def __init__(self, lock_path, probe, start):
        self.lock_path = lock_path
        self.probe = probe
        self.start = start
        self.lock = None
        self.process = None

    def __enter__(self):
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                raise PermissionError('Supervisor lock must be private and owned')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise SupervisorBusy('Interactive supervisor already running') from None
        except BaseException:
            os.close(fd)
            raise
        self.lock = fd
        return self

    def stop(self):
        process, self.process = self.process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def tick(self):
        if self.lock is None:
            raise RuntimeError('Supervisor lock required')
        try:
            ready = self.probe() is True
        except Exception:
            ready = False
        if not ready:
            self.stop()
            return 'waiting'
        if self.process is None or self.process.poll() is not None:
            self.process = self.start()
        return 'running'

    def __exit__(self, *args):
        try:
            self.stop()
        finally:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            os.close(self.lock)
            self.lock = None

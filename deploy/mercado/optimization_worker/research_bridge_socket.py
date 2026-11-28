"""Local request-bound socket transport. It does not isolate same-UID processes."""
import argparse
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
from .research_bridge_protocol import handle_message, _PLAN_GUIDANCE

_MAX = 23 * 1024 * 1024
CALL_TIMEOUT = 600


def _read(sock, count):
    parts=[]
    while count:
        part=sock.recv(min(count,65536))
        if not part: raise ValueError('Incomplete message')
        parts.append(part);count-=len(part)
    return b''.join(parts)


def _recv(sock, cap):
    length=struct.unpack('!I',_read(sock,4))[0]
    if length>cap: raise ValueError('Message too large')
    return _read(sock,length)


def _send(sock,data):
    sock.sendall(struct.pack('!I',len(data))+data)


class Server:
    def __init__(self,path,bridge,on_event=None):
        self.path=Path(path);self.bridge=bridge;self.sock=None;self.on_event=on_event
    def __enter__(self):
        parent=self.path.parent
        info=parent.stat()
        if not self.path.is_absolute() or parent.is_symlink() or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
            raise ValueError('Private socket directory required')
        if os.path.lexists(self.path): raise FileExistsError('Socket path occupied')
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            self.sock.bind(str(self.path));os.chmod(self.path,0o600);self.sock.listen(4);self.sock.settimeout(1)
        except BaseException:
            self.sock.close();self.sock=None
            raise
        return self
    def serve_once(self):
        try: conn,_=self.sock.accept()
        except socket.timeout: return False
        with conn:
            conn.settimeout(CALL_TIMEOUT)
            try:
                raw=_recv(conn,_MAX)
                result=handle_message(self.bridge,raw)
                if self.on_event:
                    try:
                        from .research_return_bridge import _FIELDS
                        message=json.loads(raw);operation=message.get('operation') if isinstance(message,dict) else None
                        if operation in _FIELDS:
                            self.on_event(operation,json.loads(result).get('accepted') is True)
                    except (ValueError,TypeError,UnicodeDecodeError,OSError): pass
            except (ValueError,OSError): result=b'{"accepted":false}'
            try: _send(conn,result)
            except OSError: pass
        return True
    def __exit__(self,*args):
        if self.sock: self.sock.close();self.sock=None
        try: self.path.unlink()
        except FileNotFoundError: pass


def send(path,message):
    raw=json.dumps(message,ensure_ascii=False,allow_nan=False).encode()
    if len(raw)>_MAX: raise ValueError('Message too large')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
        conn.settimeout(CALL_TIMEOUT);conn.connect(str(path));_send(conn,raw)
        response=json.loads(_recv(conn,65536))
    allowed=({'accepted':True},{'accepted':False}, {'accepted':False,'code':'invalid_plan','guidance':_PLAN_GUIDANCE})
    binding=(message.get('operation')=='binding_status' and isinstance(response,dict) and set(response)=={'accepted','binding'} and response['accepted'] is True and isinstance(response['binding'],dict))
    prior=(message.get('operation')=='prior_lease_status' and isinstance(response,dict) and set(response)=={'accepted','inactive'} and response['accepted'] is True and type(response['inactive']) is bool)
    visual = (message.get('operation') in ('visual_candidate_status','submit_visual_verdict')
              and isinstance(response,dict) and set(response)=={'accepted','visual'}
              and response['accepted'] is True and isinstance(response['visual'],dict))
    home_key = {'seal_suggestions':'selection', 'home_expansion_options':'expansion',
                'reserve_captcha_attempt':'captcha', 'arm_home_verification':'watch'}.get(message.get('operation'))
    home = (home_key is not None and isinstance(response,dict) and set(response)=={'accepted',home_key}
            and response['accepted'] is True and isinstance(response[home_key],dict))
    if response not in allowed and not binding and not prior and not visual and not home: raise ValueError('Invalid response')
    return response


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--socket',required=True);args=parser.parse_args()
    try:
        raw=sys.stdin.buffer.read(_MAX+1)
        if len(raw)>_MAX: raise ValueError('Message too large')
        response=send(args.socket,json.loads(raw))
        print(json.dumps(response,separators=(',',':')))
        return 0 if response['accepted'] else 1
    except Exception:
        print('{"accepted":false}')
        return 1

if __name__=='__main__': raise SystemExit(main())

"""Chrome native-messaging transport. The broker owns challenges and lease fencing.

Chrome supplies the extension origin; this boundary does not defend against a
malicious process running as the same macOS user. Never start the broker from here.
"""
import errno
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import sys
from datetime import datetime, timezone

def trace_native(path, operation, stage, request_id=None, unused=None, *, code='ok', message='', transport_error=None):
    allowed={'guard_open','gallery_binding_challenge','gallery_binding_confirm','gallery_download_next','gallery_download_saved','visual_photo_start','visual_photo_saved','gallery_capture','checkpoint_detail','collector_progress','next_candidate','query_cards_complete','stage_evidence','binding_status','anchor_challenge','anchor_confirm','anchor_guard_open','task_tab_challenge','task_tab_confirm','activation_challenge','activation_confirm'}
    if operation not in allowed or stage not in {'received','forwarded','failed','guard_response','broker_received','broker_result'}: return
    checkpoint_codes = {'checkpoint_binding_invalid', 'checkpoint_identity_changed', 'checkpoint_anchor_pending', 'checkpoint_index_invalid', 'checkpoint_cards_missing',
                        'checkpoint_guard_pending', 'detail_index_invalid', 'detail_checkpoint_conflict',
                        'detail_validation_failed', 'checkpoint_size_exceeded', 'detail_phase_unavailable',
                        'gallery_capture_invalid','gallery_screenshot_invalid','gallery_candidate_invalid','gallery_image_invalid',
                        'gallery_download_mismatch','gallery_download_file_invalid','gallery_download_reconcile','gallery_binding_invalid','gallery_phase_invalid','gallery_manifest_conflict','gallery_capture_unavailable',
                        'activation_binding_invalid', 'detail_guard_pending', 'detail_candidate_unverified', 'guard_open_rejected',
                        'visual_photo_unavailable','visual_photo_binding_invalid','visual_photo_candidate_invalid',
                        'visual_photo_variant_invalid','visual_photo_conflict','visual_photo_download_invalid'}
    if code not in checkpoint_codes | {'ok','broker_unavailable','rejected','rebind_required','task_tab_reused','task_tab_pending'}:
        code = 'rejected'
    message = message[:200] if code in checkpoint_codes and isinstance(message, str) else ''
    if stage == 'failed' and code == 'broker_unavailable' and transport_error is not None:
        error_name = errno.errorcode.get(getattr(transport_error, 'errno', None))
        message = type(transport_error).__name__
        if error_name:
            message += f' ({error_name})'
    request_id=request_id if isinstance(request_id,str) and re.fullmatch(r'[A-Za-z0-9-]{1,80}',request_id) else None
    row={'timestamp':datetime.now(timezone.utc).isoformat(),'stage':('broker.' if stage.startswith('broker_') else 'native.')+stage.removeprefix('broker_'),'operation':operation,'request_id':request_id,'code':code,'message':message}
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'a') as out: out.write(json.dumps(row,separators=(',',':'))+'\n')
    except OSError: pass

MAX_FRAME=1024*1024

def trace_supervisor(path, request_id, phase, code, exception_type=None):
    if phase != 'supervisor' or code not in {'started','broker_ready','broker_exception'}: return
    exception_type = exception_type if isinstance(exception_type,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}',exception_type) else None
    row={'timestamp':datetime.now(timezone.utc).isoformat(),'stage':phase,'code':code,
         'request_id':request_id if isinstance(request_id,str) and re.fullmatch(r'[A-Za-z0-9-]{1,80}',request_id) else None,
         'exception_type':exception_type,'message':'Broker supervisor stopped after an exception' if code=='broker_exception' else ''}
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'a') as out: out.write(json.dumps(row,separators=(',',':'))+'\n')
    except OSError: pass

def validate_origin(origin, extension_id):
    ids=extension_id if isinstance(extension_id,list) else [extension_id]
    return bool(ids and len(ids)<=2 and all(isinstance(i,str) and re.fullmatch(r'[a-p]{32}',i) for i in ids) and len(set(ids))==len(ids) and origin in ['chrome-extension://'+i+'/' for i in ids])

def native_manifest(root, extension_id):
    root=Path(root)
    ids=extension_id if isinstance(extension_id,list) else [extension_id]
    if not root.is_absolute() or not ids or not validate_origin('chrome-extension://'+str(ids[0])+'/',ids):
        raise ValueError('Invalid extension identity')
    return {'name':'local.odoo.collector_binding', 'description':'Task-scoped collector binding',
            'path':str(root/'deploy/mercado/optimization_worker/native_collector_host.sh'),
            'type':'stdio','allowed_origins':['chrome-extension://'+i+'/' for i in ids]}

def read_message(stream):
    header=stream.read(4)
    if header == b'': return None
    if len(header)!=4: raise ValueError('Truncated frame')
    size=struct.unpack('=I',header)[0]
    if size<2 or size>MAX_FRAME: raise ValueError('Invalid frame size')
    payload=stream.read(size)
    if len(payload)!=size: raise ValueError('Truncated frame')
    value=json.loads(payload)
    if isinstance(value, dict) and value.get('operation') == 'gallery_capture' and size >= 900000:
        raise ValueError('Gallery capture frame too large')
    if not isinstance(value,dict): raise ValueError('Invalid message')
    return value

def write_message(stream, value):
    payload=json.dumps(value,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
    if len(payload)>MAX_FRAME: raise ValueError('Frame too large')
    stream.write(struct.pack('=I',len(payload))+payload);stream.flush()

def private_config(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        s=os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o600 or s.st_nlink!=1 or s.st_size>4096: raise ValueError('Unsafe configuration')
        with os.fdopen(fd) as f:
            fd=None
            return json.load(f)
    finally:
        if fd is not None: os.close(fd)

def forward(sock_path, value, *, origin=None):
    if value.get('operation') not in ('binding_status','poll','submit','watch','observe_tab','select_window','recover_receipt','stage_evidence','gallery_binding_challenge','gallery_binding_confirm','gallery_download_next','gallery_download_saved','visual_photo_start','visual_photo_saved','gallery_capture','query_cards_complete','collector_progress','checkpoint_detail','next_candidate','activation_challenge','activation_confirm','anchor_challenge','anchor_confirm','task_tab_challenge','task_tab_confirm','peer_challenge','peer_confirm'): raise ValueError('Unknown operation')
    if value.get('operation') in ('gallery_binding_challenge','gallery_binding_confirm','gallery_download_next','gallery_download_saved','visual_photo_start','visual_photo_saved','gallery_capture','collector_progress','checkpoint_detail','next_candidate','anchor_challenge','anchor_confirm','task_tab_challenge','task_tab_confirm','peer_challenge','peer_confirm'):
        if not isinstance(origin,str) or 'native_origin' in value: raise ValueError('Native origin required')
        value=dict(value,native_origin=origin)
    s=os.lstat(sock_path)
    if not stat.S_ISSOCK(s.st_mode) or s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)&0o077: raise ValueError('Unsafe broker socket')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(90 if value.get('operation') in ('gallery_download_saved','gallery_download_next') else 10);client.connect(str(sock_path))
        with client.makefile('rwb') as stream:
            write_message(stream,value)
            result=read_message(stream)
    if result is None: raise ValueError('Broker disconnected')
    return result

def forward_guard(sock_path, first, source, destination, *, trace_path=None):
    """Proxy one long-lived native port; EOF tears down the broker guard."""
    s=os.lstat(sock_path)
    if not stat.S_ISSOCK(s.st_mode) or s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)&0o077: raise ValueError('Unsafe broker socket')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.connect(str(sock_path))
        with client.makefile('rwb') as stream:
            write_message(stream,first)
            response=read_message(stream)
            if response is None: raise ValueError('Broker disconnected')
            if trace_path is not None:
                trace_native(trace_path,first.get('operation'),'guard_response',first.get('request_id'),code='ok' if response.get('ok') is True else 'rejected')
            write_message(destination,response)
            if response.get('ok') is not True: return
            while (message:=read_message(source)) is not None:
                if message != {'operation':'guard_ping'}: raise ValueError('Invalid guard message')
                write_message(stream,message)
                response=read_message(stream)
                if response is None: raise ValueError('Broker disconnected')
                write_message(destination,response)
                if response.get('ok') is not True: return

def main():
    private=Path(__file__).resolve().parents[3]/'.local/optimization-codex-worker'
    config=private_config(private/'collector-native.json')
    if len(sys.argv)<2 or not validate_origin(sys.argv[1],config.get('extension_ids',config.get('extension_id'))): raise ValueError('Unauthorized extension origin')
    while (value:=read_message(sys.stdin.buffer)) is not None:
        op=value.get('operation'); req=value.get('request_id'); trace=private/'collector-window-trace.jsonl'
        trace_native(trace,op,'received',req)
        if value.get('operation') in ('guard_open','anchor_guard_open'):
            if value['operation']=='anchor_guard_open':
                if 'native_origin' in value: raise ValueError('Invalid origin')
                value=dict(value,native_origin=sys.argv[1])
            try:
                forward_guard(private/'collector-native.sock',value,sys.stdin.buffer,sys.stdout.buffer,trace_path=trace)
                trace_native(trace,op,'forwarded',req)
            except (OSError,ValueError) as exc:
                trace_native(trace,op,'failed',req,code='broker_unavailable',transport_error=exc)
                raise
            return
        try:
            result=forward(private/'collector-native.sock',value,origin=sys.argv[1])
            delivered = result.get('ok') is True or isinstance(result.get('request_id'),str) or (op=='activation_challenge' and isinstance(result.get('challenge'),dict))
            trace_native(trace,op,'forwarded',req,code='ok' if delivered else result.get('error','rejected'), message=result.get('message',''))
        except (OSError,ValueError) as exc:
            trace_native(trace,op,'failed',req,code='broker_unavailable',transport_error=exc)
            result={'ok':False,'error':'broker_unavailable'}
        write_message(sys.stdout.buffer,result)
if __name__=='__main__':
    try: main()
    except (OSError,ValueError,TypeError,json.JSONDecodeError): sys.exit(1)

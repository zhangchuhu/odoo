"""Prepare, but never install, a private local research service launcher."""
import json
import os
import plistlib
import stat
import sys
from pathlib import Path

SOURCE_KEYS = {'ODOO_API_KEY','ODOO_OPTIMIZATION_URL','OPTIMIZATION_JOURNAL','OPTIMIZATION_HOST_SESSION'}
LABEL = 'local.odoo.optimization-research-host'

def scoped_identity_preflight(client, task_id, *, resume=None, case_id=None):
    if task_id is None:
        if type(case_id) is not int or case_id <= 0 or resume is not None: return False
        try:
            value=client.request('POST','/internal/meli-optimization/scoped-identity',{'case_id':case_id})
            return isinstance(value,dict) and value.get('case_id')==case_id and all(value.get(k) is True for k in ('scoped_worker','non_admin','legacy_artifact_denied','new_tasks_only'))
        except Exception: return False
    if type(task_id) is not int or task_id <= 0: return False
    try:
        value=client.request('POST','/internal/meli-optimization/scoped-preflight',{'task_id':task_id})
        if not (isinstance(value,dict) and value.get('task_id')==task_id and all(value.get(k) is True for k in ('scoped_worker','non_admin','legacy_artifact_denied'))): return False
        if value.get('fresh_task') is True or value.get('retry_allowed') is True:
            return resume is None
        if not isinstance(resume,dict) or set(resume)!={'request_id','lease_token','lease_epoch'}: return False
        proof=client.request('POST','/internal/meli-optimization/scoped-resume-preflight',dict(resume,task_id=task_id))
        return isinstance(proof,dict) and proof.get('task_id')==task_id and proof.get('request_id')==resume['request_id'] and proof.get('resume_allowed') is True
    except Exception:
        return False

def service_environment(values, root, private, home, *, scoped=False):
    if not isinstance(values, dict) or set(values) != SOURCE_KEYS or not all(isinstance(v,str) and v for v in values.values()):
        raise ValueError('Invalid credential source')
    if values['ODOO_OPTIMIZATION_URL'] != 'http://127.0.0.1:8069':
        raise ValueError('Only the authorized local service is supported')
    if any(not Path(v).is_absolute() for v in (root,private,home)):
        raise ValueError('Absolute paths required')
    root,private,home = Path(root),Path(private),Path(home)
    if type(scoped) is not bool: raise ValueError('Invalid scoped mode')
    prefix = 'research-scoped' if scoped else 'research'
    result = {'PATH':'/opt/homebrew/bin:/usr/bin:/bin', 'HOME':str(home),
        'PYTHONPATH':str(root/'deploy/mercado'), 'PYTHONUNBUFFERED':'1',
        'ODOO_API_KEY':values['ODOO_API_KEY'], 'ODOO_OPTIMIZATION_URL':values['ODOO_OPTIMIZATION_URL'],
        'OPTIMIZATION_JOURNAL':str(private/(prefix+'-worker.sqlite')),
        'OPTIMIZATION_HOST_SESSION':str(private/(prefix+'-host.sqlite')),
        'OPTIMIZATION_DISPATCH_JOURNAL':str(private/(prefix+'-dispatch.sqlite')),
        'OPTIMIZATION_RESEARCH_LOCK':str(private/'research-service.lock'),
        'OPTIMIZATION_INTERACTIVE_CODEX_HOME':str(home/'.codex'),
        'OPTIMIZATION_WORKSPACE':str(root), 'OPTIMIZATION_COMPUTER_USE_CWD':str(home/'.codex/computer-use'),
        'OPTIMIZATION_CODEX_PROXY':'http://127.0.0.1:7897',
        'OPTIMIZATION_USE_LIVE_ADAPTERS':'1','OPTIMIZATION_PROBE_CASE_ID':'229'}
    if scoped: result['OPTIMIZATION_RESEARCH_LEDGER_V1']='1'
    return result

def plist_bytes(root, private, *, scoped=False):
    root,private=Path(root),Path(private)
    if not root.is_absolute() or not private.is_absolute(): raise ValueError('Absolute paths required')
    if type(scoped) is not bool: raise ValueError('Invalid scoped mode')
    variables={'PATH':'/opt/homebrew/bin:/usr/bin:/bin','PYTHONPATH':str(root/'deploy/mercado')}
    if scoped: variables['OPTIMIZATION_RESEARCH_SCOPED']='1'
    return plistlib.dumps({'Label':LABEL,'ProgramArguments':[str(root/'.venv/bin/python'),'-m','optimization_worker.research_launchd'],
        'WorkingDirectory':str(root),'EnvironmentVariables':variables,
        'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':60,
        'StandardOutPath':str(private/'research-service.stdout.log'),'StandardErrorPath':str(private/'research-service.stderr.log')})

def install_launch_agent(home, data, *, label=LABEL):
    """Install a login plist without starting it; refuse foreign or altered files."""
    home=Path(home)
    if label not in (LABEL, 'local.odoo.mercado') or not home.is_absolute() or not isinstance(data,bytes) or not data or len(data)>65536:
        raise ValueError('Invalid launch agent input')
    library=home/'Library'
    for directory in (home, library):
        info=directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise PermissionError('Unsafe user library')
    agents=library/'LaunchAgents'
    try: agents.mkdir(mode=0o700)
    except FileExistsError: pass
    info=agents.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise PermissionError('Unsafe LaunchAgents directory')
    destination=agents/(label+'.plist')
    try:
        fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    except FileExistsError:
        fd=os.open(destination,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1 or info.st_size>65536:
                raise PermissionError('Unsafe existing launch agent')
            with os.fdopen(fd,'rb') as existing:
                fd=None
                if existing.read()!=data: raise ValueError('Existing launch agent differs')
        finally:
            if fd is not None: os.close(fd)
        return destination
    try:
        with os.fdopen(fd,'wb') as out:
            fd=None
            out.write(data); out.flush(); os.fsync(out.fileno())
    except BaseException:
        if fd is not None: os.close(fd)
        destination.unlink(missing_ok=True)
        raise
    return destination

def _private_source(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 16384:
            raise PermissionError('Unsafe private configuration')
        with os.fdopen(fd,'r') as source:
            fd=None
            return json.load(source)
    finally:
        if fd is not None: os.close(fd)

def main():
    os.umask(0o077)
    root=Path(__file__).resolve().parents[3]
    private=root/'.local/optimization-codex-worker'
    if sys.argv[1:] == ['--install']:
        path=install_launch_agent(Path.home(),plist_bytes(str(root),str(private)))
        print('Installed login agent without starting:',path)
        return
    if sys.argv[1:]: raise ValueError('Unexpected argument')
    info=private.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise PermissionError('Unsafe private directory')
    home=Path.home()
    scoped=os.environ.get('OPTIMIZATION_RESEARCH_SCOPED') == '1'
    source=private/('research-scoped-environment.json' if scoped else 'environment.json')
    environment=service_environment(_private_source(source),str(root),str(private),str(home),scoped=scoped)
    for path in (root/'.venv/bin/python',home/'.codex',home/'.codex/computer-use'):
        if not path.exists(): raise ValueError('Required local path missing')
    os.chdir(root)
    python=str(root/'.venv/bin/python')
    os.execve(python,[python,'-m','optimization_worker.research_service','loop'],environment)

if __name__=='__main__':
    try: main()
    except Exception:
        print('Research launcher refused configuration.',file=sys.stderr)
        raise SystemExit(1)

"""Local Odoo login-agent preparation; executing this module starts Odoo."""
from configparser import ConfigParser
import os
from pathlib import Path
import plistlib
import socket
import subprocess
import sys

LABEL='local.odoo.mercado'

def start_command(root):
    p=Path(root)
    if not p.is_absolute(): raise ValueError('Absolute root required')
    return [str(p/'.venv/bin/python'),str(p/'odoo-bin'),'-c',str(p/'.local/odoo.conf'),'--max-cron-threads=0']

def validate_config(options):
    expected={'db_host':'127.0.0.1','db_port':'5433','db_name':'mercado','http_interface':'127.0.0.1','http_port':'8069'}
    if any(options.get(k)!=v for k,v in expected.items()): raise ValueError('Unexpected Odoo target')

def build_plist(root):
    p=Path(root)
    if not p.is_absolute(): raise ValueError('Absolute root required')
    return plistlib.dumps({'Label':LABEL,'ProgramArguments':[str(p/'.venv/bin/python'),'-m','optimization_worker.odoo_launchd'],
        'WorkingDirectory':str(p),'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin','PYTHONPATH':str(p/'deploy/mercado')},
        'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':60,
        'StandardOutPath':str(p/'.local/odoo-launchd.stdout.log'),'StandardErrorPath':str(p/'.local/odoo-launchd.stderr.log')})

def install_odoo_agent(home, root):
    from .research_launchd import install_launch_agent
    return install_launch_agent(home,build_plist(root),label=LABEL)

def port_free():
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
            # Match Odoo's listener so a closed connection in TIME_WAIT does
            # not make a normal restart wait for launchd's next retry.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1',8069))
        return True
    except OSError:
        return False

def main():
    root=Path(__file__).resolve().parents[3]
    if sys.argv[1:]==['--install']:
        print('Installed login agent without starting:',install_odoo_agent(Path.home(),str(root)))
        return
    if sys.argv[1:]: raise ValueError('Unexpected argument')
    config=ConfigParser(interpolation=None)
    if not config.read(root/'.local/odoo.conf') or 'options' not in config: raise ValueError('Odoo configuration missing')
    validate_config(config['options'])
    if not port_free(): raise RuntimeError('Odoo port already occupied')
    os.chdir(root)
    subprocess.run([str(root/'.local/database.command')],check=True,timeout=35)
    subprocess.run([str(root/'.venv/bin/python'),str(root/'.local/check_database.py')],check=True,timeout=30)
    if not port_free(): raise RuntimeError('Odoo port already occupied')
    command=start_command(str(root))
    os.execv(command[0],command)

if __name__=='__main__':
    try: main()
    except Exception:
        print('Local Odoo launcher refused startup; check local configuration, database readiness and port.',file=sys.stderr)
        raise SystemExit(1)

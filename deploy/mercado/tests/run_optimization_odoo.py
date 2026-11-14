"""Fresh owned cluster only; never reads the developer Odoo configuration."""
import os
import pathlib
import subprocess
import tempfile
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
PG = pathlib.Path('/opt/homebrew/opt/postgresql@16/bin')

def main():
    http_test = '--http' in sys.argv
    env = {k: v for k, v in os.environ.items() if not k.startswith('PG')}
    with tempfile.TemporaryDirectory(prefix='optimization-odoo-') as tmp:
        path = pathlib.Path(tmp)
        socket_dir = path / 'socket'
        socket_dir.mkdir()
        subprocess.run([str(PG / 'initdb'), '-D', str(path / 'pg'), '-U', 'odoo_test', '--auth=trust', '--encoding=UTF8', '--locale=C'], env=env, check=True, stdout=subprocess.DEVNULL)
        started = False
        try:
            subprocess.run([str(PG / 'pg_ctl'), '-D', str(path / 'pg'), '-l', str(path / 'pg.log'), '-o', f"-k {socket_dir} -p 55439 -c listen_addresses=''", '-w', 'start'], env=env, check=True, stdout=subprocess.DEVNULL)
            started = True
            (path / 'sitecustomize.py').write_text("import socket\n_orig=socket.socket.connect\ndef connect(self,address):\n if self.family != socket.AF_UNIX and not (self.family == socket.AF_INET and address == ('127.0.0.1', 55440)): raise RuntimeError('Network forbidden in disposable optimization tests')\n return _orig(self,address)\nsocket.socket.connect=connect\n")
            env['PYTHONPATH'] = str(path)
            env['NO_PROXY'] = '127.0.0.1,localhost'
            args = [str(ROOT / '.venv/bin/python'), str(ROOT / 'odoo-bin'), '-c', '/dev/null', '--addons-path=' + ','.join(str(ROOT / x) for x in ['addons', 'deploy/mercado/addons', '.local/addons']), '--data-dir=' + str(path / 'odoo-data'), '--db_host=' + str(socket_dir), '--db_port=55439', '--db_user=odoo_test', '--db_password=', '--database=odoo_opt_test', '--db-filter=^odoo_opt_test$', '--init=meli_optimization', '--without-demo=all', '--test-enable', '--test-tags=/meli_optimization' + ('' if http_test else ',-optimization_http'), '--stop-after-init', *(['--http-interface=127.0.0.1', '--http-port=55440'] if http_test else ['--no-http']), '--max-cron-threads=0', '--workers=0']
            return subprocess.run(args, cwd=ROOT, env=env).returncode
        finally:
            if started:
                subprocess.run([str(PG / 'pg_ctl'), '-D', str(path / 'pg'), '-m', 'fast', '-w', 'stop'], env=env, check=True, stdout=subprocess.DEVNULL)

if __name__ == '__main__':
    raise SystemExit(main())

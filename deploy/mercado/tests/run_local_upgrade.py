"""Explicit local deployment/test entrypoint; blocks non-loopback socket connects."""
import ipaddress, socket, sys
from pathlib import Path
from configparser import ConfigParser
root=Path(__file__).resolve().parents[3]
c=ConfigParser(interpolation=None);c.read(root/'.local/odoo.conf')
assert c['options']['db_host']=='127.0.0.1' and c['options']['db_port']=='5433'
assert c['options']['db_name']=='mercado'
original=socket.socket.connect

def local_only(sock,address):
 if isinstance(address,tuple):
  try: allowed=ipaddress.ip_address(address[0]).is_loopback
  except ValueError: allowed=False
  if not allowed: raise AssertionError('External network is disabled during local migration tests')
 return original(sock,address)
socket.socket.connect=local_only
sys.path.insert(0,str(root))
import odoo.cli
sys.argv=['odoo-bin','-c',str(root/'.local/odoo.conf'),'-i','meli_platform_adapter','-u','meli_accounts,meli_bff','--stop-after-init','--max-cron-threads=0','--test-enable','--test-tags','/meli_bff,/meli_accounts','--http-port=8070','--logfile='+str(root/'.local/adapter-backup/upgrade.log')]
odoo.cli.main()

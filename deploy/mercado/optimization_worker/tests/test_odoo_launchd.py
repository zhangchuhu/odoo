import plistlib
import unittest
import tempfile
from pathlib import Path
from optimization_worker.odoo_launchd import build_plist, validate_config, start_command, install_odoo_agent

class OdooLaunchdTest(unittest.TestCase):
    def test_plist_uses_current_conf_without_credentials(self):
        data=build_plist('/work')
        decoded=plistlib.loads(data)
        self.assertEqual(decoded['Label'],'local.odoo.mercado')
        self.assertTrue(decoded['RunAtLoad'])
        self.assertTrue(decoded['KeepAlive'])
        self.assertNotIn(b'password',data.lower())
        self.assertNotIn(b'ODOO_API_KEY',data)
        self.assertEqual(start_command('/work'),['/work/.venv/bin/python','/work/odoo-bin','-c','/work/.local/odoo.conf','--max-cron-threads=0'])

    def test_reject_wrong_database_or_interface(self):
        good={'db_host':'127.0.0.1','db_port':'5433','db_name':'mercado','http_interface':'127.0.0.1','http_port':'8069'}
        validate_config(good)
        for changed in ({'db_name':'other'},{'http_interface':'0.0.0.0'},{'db_port':'5432'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError): validate_config(dict(good,**changed))

    def test_install_does_not_overwrite_existing_agent(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d); (home/'Library').mkdir(); (home/'Library/LaunchAgents').mkdir()
            path=install_odoo_agent(home,'/work')
            self.assertEqual(path.name,'local.odoo.mercado.plist')
            self.assertEqual(path.read_bytes(),build_plist('/work'))
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(install_odoo_agent(home,'/work'),path)
            path.write_text('different existing service')
            with self.assertRaises(ValueError): install_odoo_agent(home,'/work')
            self.assertEqual(path.read_text(),'different existing service')

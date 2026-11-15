"""Run pure protocol/transport tests without connecting to any database."""
import sys, types, unittest
from pathlib import Path
root=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(root))
import odoo.addons
addons=root/'deploy/mercado/addons'
odoo.addons.__path__.append(str(addons))
pkg=types.ModuleType('bff_unit');pkg.__path__=[str(addons/'meli_bff')];sys.modules['bff_unit']=pkg
sys.path.insert(0,str(Path(__file__).resolve().parent))
suite=unittest.TestSuite()
for name in ('test_adapter','test_registry','test_optimization_domain','test_auto_screen','test_screening_resume','bff_unit.tests.test_protocol','bff_unit.tests.test_client'):
 suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())

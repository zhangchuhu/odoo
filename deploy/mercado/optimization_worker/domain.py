"""Load the sibling pure domain package without addon/Odoo initialization."""
import importlib.util
from pathlib import Path
import sys

_PATH = Path(__file__).resolve().parents[1] / 'addons' / 'meli_optimization' / 'domain'
_NAME = '_meli_optimization_worker_domain_v1'
if _NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_NAME, _PATH / '__init__.py',
                                                  submodule_search_locations=[str(_PATH)])
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_NAME] = _module
    _spec.loader.exec_module(_module)
_domain = sys.modules[_NAME]
__all__ = _domain.__all__
for _name in __all__:
    globals()[_name] = getattr(_domain, _name)

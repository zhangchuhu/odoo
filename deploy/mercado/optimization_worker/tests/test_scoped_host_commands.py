import tempfile
import unittest
from pathlib import Path

from optimization_worker.scoped_host_commands import validate_command

REQUEST = '8b4f7606-7811-4070-925d-d21cc78f13f0'

class ScopedCommandsTest(unittest.TestCase):
    def test_bound_request_and_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'plan.json').write_text('{}')
            value = validate_command(REQUEST, root, {'kind': 'collector', 'command': 'plan', 'request_id': REQUEST, 'file': 'plan.json'})
            self.assertEqual(value['request_id'], REQUEST)
            for change in ({'request_id': 'other'}, {'command': 'reconcile'}, {'unexpected': 'x'}, {'file': '../outside'}):
                request = {'kind': 'collector', 'command': 'plan', 'request_id': REQUEST, 'file': 'plan.json'}
                request.update(change)
                with self.assertRaises(ValueError): validate_command(REQUEST, root, request)

    def test_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            root = Path(tmp)
            (Path(other) / 'data').write_text('{}')
            (root / 'link').symlink_to(Path(other) / 'data')
            with self.assertRaises(ValueError):
                validate_command(REQUEST, root, {'kind': 'collector', 'command': 'plan', 'request_id': REQUEST, 'file': 'link'})

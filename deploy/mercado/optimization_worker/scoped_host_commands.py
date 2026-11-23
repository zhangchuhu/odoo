"""Conservative request validation only; not an executable credential bridge.

Path validation alone does not prevent replacement after validation. Callers must
use descriptor-based copying before any future execution. No commands are run here.
"""
import os
import re
import stat
from pathlib import Path

_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
_NAME = re.compile(r'[A-Za-z0-9_-]{1,120}\.json')


def validate_command(bound_request, root, value):
    if not isinstance(bound_request, str) or not _UUID.fullmatch(bound_request):
        raise ValueError('Invalid request')
    if not isinstance(value, dict) or set(value) != {'kind', 'command', 'request_id', 'file'}:
        raise ValueError('Invalid command fields')
    if value['kind'] != 'collector' or value['command'] != 'plan' or value['request_id'] != bound_request:
        raise ValueError('Command unavailable')
    name = value['file']
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError('Invalid file name')
    path = Path(root)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ValueError('Invalid directory')
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            info = os.fstat(child)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                raise ValueError('Invalid file')
        finally:
            os.close(child)
    except OSError:
        raise ValueError('Invalid file') from None
    finally:
        os.close(fd)
    return dict(value)

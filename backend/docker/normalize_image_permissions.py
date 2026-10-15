"""Build-time only: immutable public source/static assets, never runtime secrets."""
from pathlib import Path
import sys


def normalize(paths):
    entries = []
    for path in paths:
        if path.is_symlink() or not path.is_dir():
            raise ValueError('Application source/assets directory is missing or unsafe')
        entries.extend([path, *path.rglob('*')])
    # Validate the entire set before changing any permission; never follow links.
    if any(item.is_symlink() or not (item.is_file() or item.is_dir()) for item in entries):
        raise ValueError('Application source/assets contain an unsupported file type')
    for item in entries:
        item.chmod(0o755 if item.is_dir() else 0o644)


if __name__ == '__main__':
    service = sys.argv[1]
    if service not in {'mcp-server', 'retail-agent', 'supplier-agent', 'backend', 'frontend'}:
        raise ValueError('Unsupported service')
    normalize([Path('/app/app'), *([Path('/app/static')] if service == 'frontend' else [])])

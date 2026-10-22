"""Check Linux Qt wheel CPU requirements before importing native Qt modules."""
from __future__ import annotations

from importlib import metadata
from pathlib import Path
import platform
import sys


def linux_cpu_flags() -> frozenset[str] | None:
    """Return the first processor's advertised flags, or unknown if unavailable."""
    try:
        with Path('/proc/cpuinfo').open(encoding='ascii', errors='ignore') as stream:
            for _ in range(256):
                line = stream.readline(65536)
                if not line:
                    break
                field, separator, value = line.partition(':')
                if separator and field.strip() == 'flags':
                    return frozenset(value.split()) or None
    except OSError:
        pass
    return None


def qt_startup_error() -> str | None:
    """Keep unsupported Qt wheels from terminating an older CPU with SIGILL."""
    if not sys.platform.startswith('linux') or platform.machine().lower() not in ('x86_64', 'amd64'):
        return None
    try:
        version = metadata.version('PySide6_Essentials')
        major_minor = tuple(int(part) for part in version.split('.')[:2])
    except (metadata.PackageNotFoundError, ValueError):
        return None
    if major_minor < (6, 10):
        return None
    flags = linux_cpu_flags()
    if flags is None:
        return None
    required = {'cx16', 'lahf_lm', 'popcnt', 'sse4_1', 'sse4_2', 'ssse3'}
    missing = required - flags
    if not flags.intersection(('pni', 'sse3')):
        missing.add('sse3')
    if not missing:
        return None
    return (
        f'This Linux CPU lacks features required by the Qt {version} wheels: '
        + ', '.join(sorted(missing)) + '.\n'
        'Use Python 3.10–3.13 with this project\'s Linux requirements '
        '(PySide6 Essentials 6.9.3). Python 3.14+ requires the newer Qt wheels.\n'
        'A compatible Qt environment or package is needed; changing a frozen app\'s '
        'system Python will not replace its bundled Qt.'
    )

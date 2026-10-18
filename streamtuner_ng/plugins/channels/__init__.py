"""Internal lists plus Python providers discovered beside the installed app.

Importing this package is intentionally cheap. Desktop startup can show the
window before importing arbitrary adjacent/legacy Python plugin code. Existing
CLI/selftest callers may still import ``BUILTINS``; that compatibility access
performs discovery on demand.
"""

from __future__ import annotations

import threading

from ..loader import plugin_directories

_ORDER = ('radiobrowser', 'somafm', 'radioparadise', 'fip', 'nightride', 'jamendo',
          'shoutcast', 'tunein', 'live365', 'iheart', 'litt', 'audioaddict')
_classes: list[type] | None = None
_errors: list[tuple[str, str]] | None = None
_installed: list[type] | None = None
_discover_lock = threading.RLock()

# Make explicit provider-module imports work without scanning/importing every
# adjacent plugin at package import time.
for _provider_dir in reversed(plugin_directories()):
    _provider_path = str(_provider_dir)
    if _provider_path not in __path__:
        __path__.insert(0, _provider_path)


def discover_builtin_channels() -> tuple[list[type], list[tuple[str, str]]]:
    """Import and return built-ins plus isolated errors, once per process.

    Call this from a background startup stage when the desktop window is ready.
    ``BUILTINS`` remains available for command-line selftests that expect eager
    classes after importing that attribute.
    """
    global _classes, _errors, _installed
    with _discover_lock:
        if _classes is None:
            from ..loader import load_installed_plugins, user_plugin_dir
            from .bookmarks import Bookmarks
            from .local import Local

            folder = str(user_plugin_dir())
            if folder not in __path__:
                __path__.insert(0, folder)
            installed, errors = load_installed_plugins(__name__)
            installed.sort(key=lambda cls: (_ORDER.index(cls.id) if cls.id in _ORDER else len(_ORDER),
                                            str(getattr(cls, 'id', '') or cls.__name__)))
            _installed = installed
            _errors = errors
            _classes = [Bookmarks, *installed, Local]
    return list(_classes), list(_errors or ())


def __getattr__(name: str):
    if name in {'BUILTINS', 'PLUGIN_ERRORS', 'INSTALLED_PLUGINS'}:
        discover_builtin_channels()
        if name == 'BUILTINS':
            return list(_classes or ())
        if name == 'PLUGIN_ERRORS':
            return list(_errors or ())
        return list(_installed or ())
    raise AttributeError(name)


def __dir__():
    return sorted(set(globals()) | {'BUILTINS', 'PLUGIN_ERRORS', 'INSTALLED_PLUGINS'})

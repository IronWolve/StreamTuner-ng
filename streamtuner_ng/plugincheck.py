"""`streamtuner-ng --plugins`: which providers load for this user, from which file, and which fail.

Kept apart from the selftest module on purpose: that one imports the built-in providers when it is
imported, which makes the user plugin folder, so "did the folder exist" could no longer be told there.
Discovery makes the user plugin folder when it is missing, exactly as a normal start does; nothing
else is written.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path

from . import APP_NAME, __version__


def list_plugins() -> int:
    """Print the plugin folders, every provider that loads (with its file) and every failure.
    Returns 1 when any plugin fails to load, else 0."""
    from .config import _base_dir
    from .plugins.loader import load_user_plugins, plugin_directories, user_plugin_folder

    class _Profile:                     # load_user_plugins only needs the settings folder
        dir = _base_dir()

    mine = user_plugin_folder(_Profile)
    existed = mine.is_dir()
    from .plugins.channels import discover_builtin_channels
    classes, errors = discover_builtin_channels()
    errors = list(errors)
    user_classes, user_errors = load_user_plugins(_Profile)
    errors += user_errors
    print(f'{APP_NAME} {__version__} plugins')
    for folder in plugin_directories():
        print(f'  bundled folder  {folder}  ({"writable" if os.access(folder, os.W_OK) else "read-only"})')
    shared = mine.resolve() in {folder.resolve() for folder in plugin_directories()}
    state = ('the same folder' if shared else 'exists' if existed else
             'did not exist; created now' if mine.is_dir() else 'could not be created')
    print(f'  your folder     {mine}  ({state})')
    seen: set[str] = set()
    rows = []
    for cls in [*classes, *user_classes]:
        cid = getattr(cls, 'id', '') or cls.__name__
        if cls.__module__.endswith(('.bookmarks', '.local')) or cid in seen:
            continue
        seen.add(cid)
        try:
            source = Path(inspect.getfile(cls)).resolve()
        except (TypeError, OSError):
            source = Path('?')
        yours = not shared and (mine.resolve() in source.parents or _Profile.dir.resolve() / 'plugins' in source.parents)
        flags = ('  [yours]' if yours else '') + ('  [local files]' if getattr(cls, 'local_files', False) else '')
        rows.append((cid, getattr(cls, 'title', ''), source, flags))
    for cid, title, source, flags in sorted(rows):
        print(f'  {cid:<16} {title:<28} {source.name}{flags}')
    for name, error in errors:
        print(f'  FAILED  {name}: {error}')
    print(f'{len(rows)} plugins load, {len(errors)} failed')
    return 1 if errors else 0

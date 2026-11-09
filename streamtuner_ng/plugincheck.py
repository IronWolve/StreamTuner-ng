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


def list_plugins(probe: list[str] | None = None) -> int:
    """Print the plugin folders, every provider that loads (with its file) and every failure.

    ``probe`` (a list of plugin ids, empty for "your own") also asks those plugins for their first
    category and its stations, which may use the network. Returns 1 when any plugin fails to load or
    to answer, else 0."""
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
        yours = _Profile.dir.resolve() / 'plugins' in source.parents or (not shared and mine.resolve() in source.parents)
        flags = ('  [yours]' if yours else '') + ('  [local files]' if getattr(cls, 'local_files', False) else '')
        rows.append((cid, getattr(cls, 'title', ''), source, flags))
    for cid, title, source, flags in sorted(rows):
        print(f'  {cid:<16} {title:<28} {source.name}{flags}')
    for name, error in errors:
        print(f'  FAILED  {name}: {error}')
    print(f'{len(rows)} plugins load, {len(errors)} failed')
    failed = len(errors)
    if probe is not None:
        failed += _probe(probe, [*classes, *user_classes], rows)
    return 1 if failed else 0


def _probe(ids: list[str], classes: list, rows: list) -> int:
    """Ask each chosen plugin for its categories and the stations of the first one; returns the failures."""
    import tempfile
    from .config import Config
    from .plugins.host import PluginHost
    wanted = set(ids) or {cid for cid, _title, _source, flags in rows if '[yours]' in flags}
    if not wanted:
        print('probe: no plugins of yours to ask (name them: --probe ID ...)')
        return 0
    failures = 0
    with tempfile.TemporaryDirectory(prefix='streamtuner-probe-') as folder:
        config = Config(Path(folder))
        host = PluginHost(config)
        for cls in classes:
            cid = getattr(cls, 'id', '')
            if cid in wanted and cid not in host.channels:
                try:
                    host.register(cls(config))
                    host.set_enabled(cid, True)
                except BaseException as exc:  # noqa: BLE001 - report, never crash
                    print(f'  probe  {cid:<16} FAILED to start: {exc}')
                    failures += 1
        for cid in sorted(wanted):
            if cid not in host.channels:
                if not any(row[0] == cid for row in rows):
                    print(f'  probe  {cid:<16} FAILED: no such plugin')
                    failures += 1
                continue
            cats = host.call(cid, 'update_categories')
            if not cats.ok or not cats.data:
                print(f'  probe  {cid:<16} FAILED: categories: {cats.message or cats.status.name}')
                failures += 1
                continue
            first = list(cats.data)[0]
            stations = host.call(cid, 'update_streams', first)
            if not stations.ok:
                print(f'  probe  {cid:<16} FAILED: stations of {first!r}: {stations.message or stations.status.name}')
                failures += 1
                continue
            note = f'  ({stations.message})' if stations.message else ''
            print(f'  probe  {cid:<16} ok: {len(cats.data)} categories, {len(stations.data)} stations in {first!r}{note}')
    return failures

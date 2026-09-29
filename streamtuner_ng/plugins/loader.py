"""Discover and load USER plugins from a folder, so other people can add channels
without touching the app (DECISIONS D12).

Each `.py` beside the application in plugins/ that defines a `Channel` subclass
is imported and its channel classes are collected. A file that fails to import is
skipped and reported — never crashes startup (the airlock principle, applied to
loading as well as to calls).
"""

from __future__ import annotations

import importlib.util
import importlib
import sys
import hashlib
import inspect
from pathlib import Path

from .base import Channel
from ..security import redact_text


def application_dir() -> Path:
    """The installed application location, independent of the shell's cwd."""
    if getattr(sys, 'frozen', False):
        executable = Path(sys.executable).resolve()
        if (sys.platform == 'darwin' and executable.parent.name == 'MacOS'
                and executable.parent.parent.name == 'Contents'
                and executable.parents[2].suffix == '.app'):
            return executable.parents[3]
        return executable.parent
    return Path(__file__).resolve().parents[2]


def user_plugin_dir(config=None) -> Path:
    folder = application_dir() / 'plugins'
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # existing read-only installations can still load their plugins
    return folder


def _import_plugin(name, path=None):
    # Plugins remain plain editable source; never write bytecode into this folder
    # (including when compiled-artifact verification imports the bundled providers).
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        if path is None:
            return importlib.import_module(name)
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
        return module
    finally:
        sys.dont_write_bytecode = previous


def load_installed_plugins(package_name) -> tuple[list[type], list[tuple[str, str]]]:
    """Scan the app's plugin directory. Each file is trusted Python code."""
    classes, errors = [], []
    for path in sorted(user_plugin_dir().glob('*.py')):
        if path.name.startswith('_') or path.is_symlink() or not path.stem.isidentifier():
            continue
        try:
            module = _import_plugin(package_name + '.' + path.stem)
            classes.extend(value for _name, value in inspect.getmembers(module, inspect.isclass)
                           if issubclass(value, Channel) and value is not Channel
                           and value.__module__ == module.__name__)
        except BaseException as exc:
            errors.append((path.name, redact_text(f'{type(exc).__name__}: {exc}')))
    return classes, errors


_TEMPLATE = '''\
# ─────────────────────────────────────────────────────────────────────────────
#  StreamTuner-ng — EXAMPLE channel plugin   (a complete, WORKING reference)
# ─────────────────────────────────────────────────────────────────────────────
#  This reference is installed with the app. To make your
#  own channel:
#
#    1. Copy this file to a new name WITHOUT a leading underscore — e.g.
#       "myradio.py".  Files starting with "_" are skipped on purpose, which is
#       why this template never shows up as a channel itself.
#    2. Rename the class and give it a unique, lowercase `id`.
#    3. Point update_categories() / update_streams() at your own streams.
#    4. Restart StreamTuner-ng. Your channel appears in the sidebar, below the
#       divider with the other streaming services.
#
#  The streams below are real, free, no-login public mounts, so this example
#  actually plays the moment you drop the underscore — swap in your own URLs.
#  (Open this folder any time:  Tools → Open Plugins Folder…)
# ─────────────────────────────────────────────────────────────────────────────
from streamtuner_ng.plugins.base import Channel, make_row


class ExampleRadio(Channel):
    id = "example"               # unique key, lowercase (must not clash with another channel)
    title = "Example Radio"      # the label shown in the sidebar
    description = "A complete, working example plugin — copy it to build your own."
    homepage = "https://somafm.com/"   # right-click a station → Open web goes here
    priority = "standard"       # show the plugin automatically on first launch
    has_search = True            # True = update_streams() honours the toolbar filter
    icon_emoji = "🎧"            # sidebar fallback icon when a station has no favicon

    # Your stations, grouped by category. Edit freely — each entry is (name, url).
    STATIONS = {
        "Ambient": [
            ("Drone Zone",     "https://ice1.somafm.com/dronezone-128-mp3"),
            ("Deep Space One", "https://ice1.somafm.com/deepspaceone-128-mp3"),
        ],
        "Electronic": [
            ("Groove Salad",   "https://ice1.somafm.com/groovesalad-128-mp3"),
            ("Beat Blender",   "https://ice1.somafm.com/beatblender-128-mp3"),
        ],
    }

    # The names shown in the middle "categories" column (kept in this order).
    def update_categories(self):
        return list(self.STATIONS.keys())

    # Return the station rows for the selected category. make_row() needs only
    # title + url; everything else is optional. mpv plays direct URLs, .pls, .m3u.
    def update_streams(self, category, search=None):
        rows = []
        for name, url in self.STATIONS.get(category, []):
            if search and search.lower() not in name.lower():
                continue                     # respect the search box (has_search = True)
            rows.append(make_row(
                title=name,
                url=url,
                genre=category,
                bitrate=128,
                format="audio/mpeg",
                listformat="srv",            # "srv" = a direct stream URL (no .pls/.m3u indirection)
            ))
        return rows
'''


def _ensure_template(folder: Path) -> None:
    """Create the example only when absent; never replace local edits."""
    try:
        target = folder / "_example.py"
        if not target.exists() and not target.is_symlink():
            target.write_text(_TEMPLATE, encoding="utf-8")
    except Exception:  # noqa: BLE001 — a read-only plugins dir must never crash startup
        pass


def load_user_plugins(config) -> tuple[list[type], list[tuple[str, str]]]:
    """Load legacy profile plugins in addition to the installed plugin directory."""
    classes: list[type] = []
    errors: list[tuple[str, str]] = []
    folder = config.dir / "plugins"  # preserve existing profile plugins as a legacy location

    for path in sorted(folder.glob("*.py")):
        if path.name.startswith("_") or path.is_symlink() or not path.stem.isidentifier():
            continue
        try:
            namespace = hashlib.sha256(str(path.parent.resolve()).encode()).hexdigest()[:12]
            module = _import_plugin(f"st_userplugin_{namespace}_{path.stem}", path)
            for _name, obj in inspect.getmembers(module, inspect.isclass):
                if (issubclass(obj, Channel) and obj is not Channel
                        and obj.__module__ == module.__name__):
                    classes.append(obj)
        except BaseException as exc:  # noqa: BLE001 — a bad plugin file must not stop startup
            errors.append((path.name, redact_text(f"{type(exc).__name__}: {exc}")))

    return classes, errors


def register_user_plugins(host, config) -> tuple[int, list[tuple[str, str]]]:
    """Load user plugins and register them with the host. Returns (count, errors).
    A plugin that fails to import or construct is skipped, never crashing startup."""
    _ensure_template(user_plugin_dir(config))
    classes, errors = load_user_plugins(config)
    count = 0
    for cls in classes:
        try:
            host.register(cls(config))
            count += 1
        except BaseException as exc:  # noqa: BLE001
            errors.append((getattr(cls, "id", cls.__name__), redact_text(f"construct failed: {exc}")))
    return count, errors


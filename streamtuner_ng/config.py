"""Config + cache + state — the single store the whole app reads/writes.

Layout mirrors st2 but under our own dir (XDG on Linux, %APPDATA% on Windows):

    ~/.config/streamtuner-ng/settings.json     app config + plugin_state + options
    ~/.config/streamtuner-ng/cache/<chan>.json per-channel station cache
    ~/.config/streamtuner-ng/bookmarks.json    favourites + history
    ~/.config/streamtuner-ng/local.json        the Local channel's own streams
    ~/.config/streamtuner-ng/icons/            fetched+cached favicons (never bundled)

Deliberately Qt-free so the core stays headless-testable.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from . import APP_ID


def _base_dir() -> Path:
    """XDG config dir on Linux/mac, %APPDATA% on Windows, with sane fallbacks."""
    env = os.environ.get("XDG_CONFIG_HOME") or os.environ.get("APPDATA")
    base = Path(env) if env else Path.home() / ".config"
    return base / APP_ID


class Config:
    """Loads/saves JSON files under the app dir. One instance shared app-wide."""

    def __init__(self, base: Path | None = None):
        self.dir = base or _base_dir()
        self.cache_dir = self.dir / "cache"
        self.icon_dir = self.dir / "icons"
        for d in (self.dir, self.cache_dir, self.icon_dir):
            d.mkdir(parents=True, exist_ok=True)
        # keep favicon/cache dirs out of backups, like st2 did
        (self.icon_dir / ".nobackup").touch(exist_ok=True)
        self.settings: dict[str, Any] = self.load("settings") or {}

    # ---- generic JSON load/save (atomic write: temp + replace) ----
    def _path(self, name: str) -> Path:
        return self.dir / f"{name}.json"

    def load(self, name: str) -> Any:
        p = self._path(name)
        try:
            with p.open(encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _write_json(self, p: Path, data: Any, indent: int | None = None) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        # atomic: write a temp file in the same dir, then os.replace()
        fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent, ensure_ascii=False)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def save(self, name: str, data: Any) -> None:
        self._write_json(self._path(name), data, indent=2)

    def save_settings(self) -> None:
        self.save("settings", self.settings)

    # ---- per-(channel, category) station-list cache on disk (so relaunch is instant) ----
    def _cache_path(self, cid: str, cat: str) -> Path:
        key = hashlib.sha1(cat.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{cid}__{key}.json"

    def cache_save(self, cid: str, cat: str, rows: list) -> None:
        try:
            self._write_json(self._cache_path(cid, cat),
                             {"cid": cid, "cat": cat, "ts": int(time.time()), "rows": rows})
        except OSError:
            pass

    def cache_load(self, cid: str, cat: str):
        """Return (rows, age_seconds) from disk, or None if missing/unreadable."""
        try:
            with self._cache_path(cid, cat).open(encoding="utf-8") as f:
                d = json.load(f)
            return d.get("rows") or [], max(0, int(time.time()) - int(d.get("ts", 0)))
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
            return None

    def cache_clear_disk(self) -> int:
        n = 0
        for p in self.cache_dir.glob("*.json"):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
        return n

    def cache_delete(self, cid: str, cat: str) -> None:
        try:
            self._cache_path(cid, cat).unlink()
        except (FileNotFoundError, OSError):
            pass

    def cache_clear_channel(self, cid: str) -> int:
        n = 0
        for p in self.cache_dir.glob(f"{cid}__*.json"):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
        return n

    # ---- typed option access (per-plugin options live here too) ----
    def get(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.settings[key] = value

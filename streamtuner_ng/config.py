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
import threading
from pathlib import Path
from typing import Any

from . import APP_ID
from .security import MAX_IMPORT_BYTES, MAX_ROWS, is_secret_key, redact_text, scrub_data, station_rows, tokenized_url, valid_id


def _invalid_constant(value):
    raise ValueError("Non-finite JSON number is not allowed")


def _base_dir() -> Path:
    """XDG config dir on Linux/mac, %APPDATA% on Windows, with sane fallbacks."""
    env = os.environ.get("XDG_CONFIG_HOME") or os.environ.get("APPDATA")
    base = Path(env) if env else Path.home() / ".config"
    return base / APP_ID


class Config:
    """Loads/saves JSON files under the app dir. One instance shared app-wide."""

    def __init__(self, base: Path | None = None):
        self.dir = Path(base or _base_dir()).resolve()
        self._write_lock = threading.RLock()
        self._invalid_files: set[Path] = set()
        self.cache_dir = self.dir / "cache"
        self.icon_dir = self.dir / "icons"
        for d in (self.dir, self.cache_dir, self.icon_dir):
            d.mkdir(parents=True, exist_ok=True, mode=0o700)
        # keep favicon/cache dirs out of backups, like st2 did
        (self.icon_dir / ".nobackup").touch(exist_ok=True)
        loaded = self.load("settings")
        self.settings: dict[str, Any] = loaded if isinstance(loaded, dict) else {}
        from .secrets import SecretStore
        self.secrets = SecretStore(self)
        self._declared_secrets = set(self.secrets.values) | self.secrets.keyring_keys
        migrated = False
        for key, value in list(self.settings.items()):
            if is_secret_key(key) and isinstance(value, str):
                self.secrets.set(key, value)
                del self.settings[key]
                migrated = True
        if migrated:
            self.save_settings()

    # ---- generic JSON load/save (atomic write: temp + replace) ----
    def _path(self, name: str) -> Path:
        if not valid_id(name):
            raise ValueError("Invalid data file name")
        return self.dir / f"{name}.json"

    def load(self, name: str) -> Any:
        p = self._path(name)
        try:
            if p.is_symlink():
                return None
            if p.stat().st_size > MAX_IMPORT_BYTES:
                self._invalid_files.add(p)
                return None
            with p.open(encoding="utf-8") as f:
                value = json.load(f, parse_constant=_invalid_constant)
            if name in ("settings", "bookmarks", "local", "secrets") and not isinstance(value, dict):
                self._invalid_files.add(p)
                return None
            if name in ("bookmarks", "local"):
                value, changed = self._migrate_legacy_urls(value)
                if changed:
                    self.save(name, value)
            return value
        except (ValueError, RecursionError, UnicodeError):
            self._invalid_files.add(p)
            return None
        except OSError:
            return None

    @staticmethod
    def _migrate_legacy_urls(value, depth=0):
        if depth > 20:
            return value, False
        changed = False
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                if key == "url" and isinstance(item, str):
                    replacement = tokenized_url(item)
                    out[key] = replacement
                    changed |= replacement != item
                else:
                    out[key], nested = Config._migrate_legacy_urls(item, depth + 1)
                    changed |= nested
            return out, changed
        if isinstance(value, list):
            out = []
            for item in value:
                replacement, nested = Config._migrate_legacy_urls(item, depth + 1)
                out.append(replacement)
                changed |= nested
            return out, changed
        return value, False

    def _write_json(self, p: Path, data: Any, indent: int | None = None) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        # atomic: write a temp file in the same dir, then os.replace()
        fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent, ensure_ascii=False, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            with self._write_lock:
                if p.is_symlink():
                    raise OSError("Refusing to overwrite a symlinked data file")
                if p in self._invalid_files and p.exists():
                    backup = p.with_name(f"{p.stem}.invalid-{time.time_ns()}.json")
                    p.rename(backup)
                    backup.chmod(0o600)
                    self._invalid_files.discard(p)
                os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def save(self, name: str, data: Any) -> None:
        self._write_json(self._path(name), data, indent=2)

    def save_settings(self) -> None:
        with self._write_lock:
            self.save("settings", self.settings)

    # ---- per-(channel, category) station-list cache on disk (so relaunch is instant) ----
    def _cache_path(self, cid: str, cat: str) -> Path:
        if not valid_id(cid) or not isinstance(cat, str):
            raise ValueError("Invalid cache identifier")
        key = hashlib.sha1(cat.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{cid}__{key}.json"

    @staticmethod
    def _cache_rows(cat: str, rows):
        if cat == "__categories__":
            if not isinstance(rows, list):
                return []
            return list(dict.fromkeys(item[:256] for item in rows[:MAX_ROWS]
                                      if isinstance(item, str) and item.strip()))
        return station_rows(rows)

    def cache_save(self, cid: str, cat: str, rows: list) -> None:
        try:
            self._write_json(self._cache_path(cid, cat),
                             {"cid": cid, "cat": cat, "ts": int(time.time()), "rows": self._cache_rows(cat, rows)})
        except OSError:
            pass

    def cache_load(self, cid: str, cat: str):
        """Return (rows, age_seconds) from disk, or None if missing/unreadable."""
        try:
            path = self._cache_path(cid, cat)
            if path.is_symlink() or path.stat().st_size > MAX_IMPORT_BYTES:
                return None
            with path.open(encoding="utf-8") as f:
                d = json.load(f, parse_constant=_invalid_constant)
            if not isinstance(d, dict):
                return None
            d, changed = self._migrate_legacy_urls(d)
            if changed:
                self._write_json(path, d)
            rows = self._cache_rows(cat, d.get("rows"))
            # Earlier versions stored category names as station rows, losing every name.
            # Treat those empty files as cache misses so existing profiles recover.
            if cat == "__categories__" and not rows:
                return None
            return rows, max(0, int(time.time()) - int(d.get("ts", 0)))
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
        if not valid_id(cid):
            raise ValueError("Invalid channel identifier")
        n = 0
        for p in self.cache_dir.glob(f"{cid}__*.json"):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
        return n

    # ---- data usage + export (for the Cache & Data options tab) ----
    @staticmethod
    def _dir_size(p: Path) -> int:
        total = 0
        try:
            for f in p.rglob("*"):
                if f.is_file():
                    try:
                        total += f.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
        return total

    def data_usage(self) -> dict:
        """Disk bytes used per area, for the Cache & Data tab."""
        cache = self._dir_size(self.cache_dir)
        icons = self._dir_size(self.icon_dir)
        total = self._dir_size(self.dir)
        return {"cache": cache, "icons": icons, "other": max(0, total - cache - icons),
                "total": total, "cache_files": sum(1 for _ in self.cache_dir.glob("*.json"))}

    def clear_icons(self) -> int:
        n = 0
        for p in self.icon_dir.glob("*"):
            if p.is_file() and p.name != ".nobackup":
                try:
                    p.unlink()
                    n += 1
                except OSError:
                    pass
        return n

    ICON_MODES = ("off", "full", "small")

    def icon_mode(self) -> str:
        """How per-station icons are cached: 'off' (use each service's own logo for all of its
        stations, no fetching), 'full' (cache the icon as the server sends it), or 'small'
        (downscale to a thumbnail — keeps the art at a fraction of the disk). Migrates the old
        station_favicons bool: missing/True -> 'small', explicit False -> 'off'."""
        m = self.get("station_icon_mode")
        if m in self.ICON_MODES:
            return m
        return "off" if self.get("station_favicons", True) is False else "small"

    def export_bundle(self) -> dict:
        """Everything worth backing up — settings (minus secrets), favourites/history, local
        stations — as one JSON-able dict. Excludes the regenerable cache + icons."""
        from . import __version__

        return scrub_data({"app": "streamtuner-ng", "version": __version__, "exported": int(time.time()),
                           "settings": self.settings, "bookmarks": self.load("bookmarks"),
                           "local": self.load("local")}, secrets=tuple(self.secrets._cache.values()))

    def redact(self, text: object) -> str:
        return redact_text(text, self.secrets._cache.values())

    def import_settings(self, settings: object) -> None:
        """Import known, typed preferences; never secrets or ambient network settings."""
        if not isinstance(settings, dict):
            return
        allowed = {
            "theme": str, "app_font_family": str, "app_font_size": int,
            "volume": int, "io_threads": int, "wallpaper_dim": int,
            "audioaddict_quality": str, "station_icon_mode": str, "visualization": str,
            "notifications": bool, "normalize": bool, "show_album_art": bool,
            "show_vu": bool, "wallpaper_enabled": bool, "tray_enabled": bool,
            "tray_close": bool, "tray_icon": str,
        }
        for key, typ in allowed.items():
            value = settings.get(key)
            if type(value) is typ and (typ is not str or len(value) <= 4096):
                self.settings[key] = value
        # Backup imports must not silently enable outbound presence sharing,
        # execute user plugins, select local paths or change stored credentials.
        self.save_settings()

    # ---- typed option access (per-plugin options live here too) ----
    def register_secret(self, key: str) -> None:
        if not valid_id(key):
            raise ValueError("Invalid declared secret key")
        self._declared_secrets.add(key)
        if isinstance(self.settings.get(key), str):
            self.secrets.set(key, self.settings.pop(key))
            self.save_settings()

    def get(self, key: str, default: Any = None) -> Any:
        if is_secret_key(key) or key in self._declared_secrets:
            return self.secrets.get(key, default)
        value = self.settings.get(key, default)
        if default is not None and not isinstance(value, type(default)):
            return default
        if key in ("volume", "io_threads", "wallpaper_dim", "app_font_size"):
            limits = {"volume": (0, 100), "io_threads": (1, 32), "wallpaper_dim": (0, 90), "app_font_size": (8, 28)}
            lo, hi = limits[key]
            try:
                return max(lo, min(hi, int(value)))
            except (TypeError, ValueError, OverflowError):
                return default
        return value

    def set(self, key: str, value: Any) -> None:
        if is_secret_key(key) or key in self._declared_secrets:
            self.secrets.set(key, value)
        else:
            with self._write_lock:
                self.settings[key] = value

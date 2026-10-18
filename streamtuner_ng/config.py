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

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from . import APP_ID
from .security import (
    MAX_CACHE_BYTES, MAX_CACHE_DISK_BYTES, MAX_CACHE_FILES, MAX_ROWS, MAX_STORE_BYTES,
    is_secret_key, redact_text, scrub_data, station_rows, tokenized_url, valid_id,
)


def _invalid_constant(value):
    raise ValueError("Non-finite JSON number is not allowed")


def _base_dir() -> Path:
    """XDG config dir on Linux/mac, %APPDATA% on Windows, with sane fallbacks."""
    env = os.environ.get("XDG_CONFIG_HOME") or os.environ.get("APPDATA")
    base = Path(env) if env else Path.home() / ".config"
    return base / APP_ID


class Config:
    """Loads/saves JSON files under the app dir. One instance shared app-wide."""

    def __init__(self, base: Path | None = None, *, store_max_bytes: int = MAX_STORE_BYTES,
                 cache_max_bytes: int = MAX_CACHE_BYTES,
                 cache_disk_max_bytes: int = MAX_CACHE_DISK_BYTES,
                 cache_max_files: int = MAX_CACHE_FILES):
        self.dir = Path(base or _base_dir()).resolve()
        self._write_lock = threading.RLock()
        self._icon_lock = threading.RLock()
        self._icon_generation = 0
        self._invalid_files: set[Path] = set()
        self._protected_files: set[Path] = set()
        self._store_max_bytes = int(store_max_bytes)
        self._cache_max_bytes = int(cache_max_bytes)
        self._cache_disk_max_bytes = int(cache_disk_max_bytes)
        self._cache_max_files = int(cache_max_files)
        self._async_writes = False
        self._write_condition = threading.Condition(threading.RLock())
        self._pending_writes: OrderedDict[str, tuple[int, Any]] = OrderedDict()
        self._latest_snapshots: dict[str, tuple[int, Any]] = {}
        self._write_errors: list[str] = []
        self._failed_writes: dict[str, str] = {}
        self._write_sequence = 0
        self._write_active = False
        self._write_thread: threading.Thread | None = None
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
        with self._write_condition:
            if p in self._protected_files:
                return None
            snapshot = self._latest_snapshots.get(name)
        if snapshot is not None:
            # Snapshots are immutable: detach outside the queue lock so a large
            # export cannot block new UI saves or the persistence worker.
            value = copy.deepcopy(snapshot[1])
            if isinstance(value, dict):
                for key, item in tuple(value.items()):
                    if isinstance(item, tuple):
                        value[key] = list(item)
            return value
        try:
            if p.is_symlink():
                return None
            if p.stat().st_size > self._store_max_bytes:
                self._protected_files.add(p)
                self._record_write_error(
                    f"{name}.json exceeds the supported {self._store_max_bytes}-byte store limit; "
                    "the original file was preserved and not loaded.", failed_name=name
                )
                return None
            with p.open(encoding="utf-8") as f:
                value = json.load(f, parse_constant=_invalid_constant)
            if name in ("settings", "bookmarks", "local", "secrets") and not isinstance(value, dict):
                self._invalid_files.add(p)
                return None
            if name in ("bookmarks", "local"):
                row_keys = ("favourite", "history") if name == "bookmarks" else ("stations",)
                if any(key in value and not isinstance(value[key], list) for key in row_keys):
                    self._protected_files.add(p)
                    self._record_write_error(
                        f"{name}.json has an invalid saved-list format; "
                        "the original file was preserved and not loaded.", failed_name=name
                    )
                    return None
                rows = tuple(value.get(key, []) for key in row_keys)
                if any(isinstance(group, list) and len(group) > MAX_ROWS for group in rows):
                    self._protected_files.add(p)
                    self._record_write_error(
                        f"{name}.json contains more than {MAX_ROWS:,} rows in a saved list; "
                        "the original file was preserved and not loaded.", failed_name=name
                    )
                    return None
            if name in ("bookmarks", "local"):
                value, changed = self._migrate_legacy_urls(value)
                if changed:
                    try:
                        self.save(name, value)
                    except (OSError, ValueError) as exc:
                        self._protected_files.add(p)
                        self._record_write_error(
                            f"{name}.json migration failed; the original file was preserved: "
                            f"{type(exc).__name__}: {exc}", failed_name=name
                        )
                        return None
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

    def _record_write_error(self, message: str, *, failed_name: str | None = None) -> None:
        secret_cache = getattr(getattr(self, "secrets", None), "_cache", {})
        secrets = tuple(secret_cache.copy().values())
        message = redact_text(message, secrets)
        with self._write_condition:
            message = message[:2048]
            self._write_errors.append(message)
            if failed_name:
                self._failed_writes[failed_name] = message

    def _write_json(self, p: Path, data: Any, indent: int | None = None,
                    max_bytes: int | None = None) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        limit = self._store_max_bytes if max_bytes is None else int(max_bytes)
        if limit <= 0:
            raise ValueError("A positive JSON file limit is required")
        fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        written = 0
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                encoder = json.JSONEncoder(indent=indent, ensure_ascii=False, allow_nan=False)
                for chunk in encoder.iterencode(data):
                    written += len(chunk.encode("utf-8"))
                    if written > limit:
                        raise ValueError(f"JSON file exceeds the supported {limit}-byte limit")
                    f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
            with self._write_lock:
                if p.is_symlink():
                    raise OSError("Refusing to overwrite a symlinked data file")
                if p in self._protected_files:
                    raise OSError("Refusing to overwrite an unreadable preserved data file")
                if p in self._invalid_files and p.exists():
                    backup = p.with_name(f"{p.stem}.invalid-{time.time_ns()}.json")
                    p.rename(backup)
                    backup.chmod(0o600)
                    self._invalid_files.discard(p)
                os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def save(self, name: str, data: Any) -> bool:
        path = self._path(name)
        if name in ("bookmarks", "local") and isinstance(data, dict):
            groups = (data.get("favourite", []), data.get("history", [])) if name == "bookmarks" else (data.get("stations", []),)
            if any(isinstance(rows, list) and len(rows) > MAX_ROWS for rows in groups):
                message = f"{name} supports at most {MAX_ROWS:,} rows per saved list"
                self._record_write_error(message, failed_name=name)
                raise ValueError(message)
        with self._write_condition:
            if path in self._protected_files:
                message = f"{name}.json is unreadable and was preserved; refusing to replace it."
                self._write_errors.append(message)
                self._failed_writes[name] = message
                raise OSError(message)
            asynchronous = self._async_writes
        if not asynchronous:
            try:
                self._write_json(path, data, indent=2)
            except Exception as exc:
                self._record_write_error(
                    f"{name}.json save failed: {type(exc).__name__}: {exc}", failed_name=name
                )
                raise
            with self._write_condition:
                self._failed_writes.pop(name, None)
            return True
        # Detach from mutable caller-owned lists/dicts before returning. The
        # writer may coalesce this snapshot with a newer save of the same file.
        try:
            snapshot = copy.deepcopy(data)
        except Exception as exc:
            self._record_write_error(
                f"{name}.json snapshot failed: {type(exc).__name__}: {exc}", failed_name=name
            )
            raise
        self._enqueue_snapshot(name, snapshot)
        return True

    def save_station_snapshot(self, name: str, data: dict) -> bool:
        """Save Favorites/Local copy-on-write rows without deep-copying every row.

        These channel stores replace lists and row dicts on mutation; they never
        modify a row already handed to this API. Tuple roots keep queued lists
        stable while sharing the immutable normalized row dictionaries.
        """
        if name not in ("bookmarks", "local") or not isinstance(data, dict):
            return self.save(name, data)
        path = self._path(name)
        groups = (data.get("favourite", []), data.get("history", [])) if name == "bookmarks" else (data.get("stations", []),)
        if any(isinstance(rows, list) and len(rows) > MAX_ROWS for rows in groups):
            message = f"{name} supports at most {MAX_ROWS:,} rows per saved list"
            self._record_write_error(message, failed_name=name)
            raise ValueError(message)
        with self._write_condition:
            if path in self._protected_files:
                message = f"{name}.json is unreadable and was preserved; refusing to replace it."
                self._write_errors.append(message)
                self._failed_writes[name] = message
                raise OSError(message)
            asynchronous = self._async_writes
        if not asynchronous:
            try:
                self._write_json(path, data, indent=2)
            except Exception as exc:
                self._record_write_error(
                    f"{name}.json save failed: {type(exc).__name__}: {exc}", failed_name=name
                )
                raise
            with self._write_condition:
                self._failed_writes.pop(name, None)
            return True
        snapshot = {
            key: tuple(value) if isinstance(value, list) else copy.deepcopy(value)
            for key, value in data.items()
        }
        self._enqueue_snapshot(name, snapshot)
        return True

    def _enqueue_snapshot(self, name: str, snapshot: Any) -> None:
        with self._write_condition:
            self._write_sequence += 1
            item = (self._write_sequence, snapshot)
            self._pending_writes[name] = item
            self._pending_writes.move_to_end(name)
            self._latest_snapshots[name] = item
            if self._write_thread is None:
                self._write_thread = threading.Thread(
                    target=self._write_loop, name="config-writer", daemon=True
                )
                self._write_thread.start()
            self._write_condition.notify_all()

    @property
    def store_max_bytes(self) -> int:
        return self._store_max_bytes

    def assert_store_writable(self, name: str) -> None:
        path = self._path(name)
        with self._write_condition:
            if path in self._protected_files:
                message = f"{name}.json is oversized and was preserved; refusing to replace it."
                self._write_errors.append(message)
                self._failed_writes[name] = message
                raise OSError(message)

    @staticmethod
    def row_size_budget(row: dict) -> int:
        """Conservative pretty-JSON size for one normalized station row."""
        compact = json.dumps(row, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
        # The station schema contains only scalar fields. 512 bytes covers
        # indentation, separators, and row placement in the supported format.
        return len(compact) + 512

    def preflight_station_lists(self, name: str, lists: dict[str, list]) -> int:
        """Check cumulative rows and a conservative file-size bound before mutation."""
        self.assert_store_writable(name)
        estimate = self.estimate_station_lists(name, lists)
        if estimate > self._store_max_bytes:
            raise ValueError(
                f"{name} would exceed the supported {self._store_max_bytes:,}-byte storage limit"
            )
        return estimate

    def estimate_station_lists(self, name: str, lists: dict[str, list]) -> int:
        """Conservative byte budget without asserting the existing file is writable."""
        estimate = 4096
        for rows in lists.values():
            if not isinstance(rows, list) or len(rows) > MAX_ROWS:
                raise ValueError(f"{name} supports at most {MAX_ROWS:,} rows per list")
            estimate += sum(self.row_size_budget(row) for row in rows)
        return estimate

    def enable_async_writes(self) -> None:
        """Route subsequent durable JSON saves through one ordered writer."""
        with self._write_condition:
            self._async_writes = True

    def _write_loop(self) -> None:
        while True:
            with self._write_condition:
                self._write_condition.wait_for(lambda: bool(self._pending_writes))
                name, (sequence, snapshot) = self._pending_writes.popitem(last=False)
                self._write_active = True
            try:
                self._write_json(self._path(name), snapshot, indent=2)
            except Exception as exc:
                message = f"{name}.json save failed: {type(exc).__name__}: {exc}"
                with self._write_condition:
                    secret_cache = getattr(getattr(self, "secrets", None), "_cache", {})
                    secrets = tuple(secret_cache.copy().values())
                    message = redact_text(message, secrets)[:2048]
                    self._write_errors.append(message)
                    self._failed_writes[name] = message
            else:
                with self._write_condition:
                    if self._latest_snapshots.get(name, (None,))[0] == sequence:
                        self._latest_snapshots.pop(name, None)
                    self._failed_writes.pop(name, None)
            finally:
                with self._write_condition:
                    self._write_active = False
                    self._write_condition.notify_all()

    @property
    def has_pending_writes(self) -> bool:
        with self._write_condition:
            return bool(self._pending_writes or self._write_active)

    @property
    def has_write_failures(self) -> bool:
        with self._write_condition:
            return bool(self._failed_writes)

    @property
    def has_unsaved_writes(self) -> bool:
        """True only for queued work or a failed latest snapshot still held in memory."""
        with self._write_condition:
            failed_snapshots = any(name in self._latest_snapshots for name in self._failed_writes)
            return bool(self._pending_writes or self._write_active or failed_snapshots)

    def take_write_errors(self) -> list[str]:
        """Return and clear new storage errors; failure state remains until a retry succeeds."""
        with self._write_condition:
            errors, self._write_errors = self._write_errors, []
            return errors

    def flush_writes(self, timeout: float = 5.0) -> bool:
        """Wait for queued saves; preserved unreadable originals do not block shutdown."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._write_condition:
            complete = self._write_condition.wait_for(
                lambda: not self._pending_writes and not self._write_active,
                max(0.0, deadline - time.monotonic()),
            )
            failed_snapshots = any(name in self._latest_snapshots for name in self._failed_writes)
            return bool(complete and not failed_snapshots)

    def retry_failed_writes(self) -> int:
        """Requeue failed latest snapshots, excluding preserved unreadable originals."""
        queued = 0
        with self._write_condition:
            for name, message in list(self._failed_writes.items()):
                path = self._path(name)
                snapshot = self._latest_snapshots.get(name)
                if snapshot is None or path in self._protected_files:
                    continue
                self._pending_writes[name] = snapshot
                self._pending_writes.move_to_end(name)
                queued += 1
            if queued:
                if self._write_thread is None:
                    self._write_thread = threading.Thread(
                        target=self._write_loop, name="config-writer", daemon=True
                    )
                    self._write_thread.start()
                self._write_condition.notify_all()
        return queued

    def save_settings(self) -> None:
        with self._write_lock:
            snapshot = copy.deepcopy(self.settings)
        self.save("settings", snapshot)

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

    def _cache_files(self, cid: str | None = None) -> list[Path]:
        files = []
        try:
            for path in self.cache_dir.glob("*.json"):
                if path.is_symlink() or not path.is_file():
                    continue
                stem, sep, digest = path.stem.rpartition("__")
                if not sep or not valid_id(stem) or not re.fullmatch(r"[a-f0-9]{16}", digest):
                    continue
                if cid is None or stem == cid:
                    files.append(path)
        except OSError:
            pass
        return files

    def _trim_cache_disk(self, protect: Path | None = None) -> None:
        """Bound only files matching this app's generated channel-cache names."""
        files = self._cache_files()
        sized = []
        total = 0
        for path in files:
            try:
                size = path.stat().st_size
                modified = path.stat().st_mtime_ns
            except OSError:
                continue
            total += size
            sized.append((modified, path, size))
        count = len(sized)
        for _modified, path, size in sorted(sized):
            if total <= self._cache_disk_max_bytes and count <= self._cache_max_files:
                break
            if path == protect:
                continue
            try:
                path.unlink()
                total -= size
                count -= 1
            except OSError as exc:
                self._record_write_error(f"Cache cleanup failed: {type(exc).__name__}: {exc}")

    def cache_save(self, cid: str, cat: str, rows: list) -> bool:
        try:
            path = self._cache_path(cid, cat)
            self._write_json(path,
                             {"cid": cid, "cat": cat, "ts": time.time(), "rows": self._cache_rows(cat, rows)},
                             max_bytes=self._cache_max_bytes)
            self._trim_cache_disk(protect=path)
            return True
        except (OSError, TypeError, ValueError) as exc:
            self._record_write_error(f"Cache save for {cid} failed: {type(exc).__name__}: {exc}")
            return False

    def cache_load(self, cid: str, cat: str):
        """Return (rows, age_seconds) from disk, or None if missing/unreadable."""
        try:
            path = self._cache_path(cid, cat)
            if path.is_symlink() or path.stat().st_size > self._cache_max_bytes:
                return None
            with path.open(encoding="utf-8") as f:
                d = json.load(f, parse_constant=_invalid_constant)
            if not isinstance(d, dict):
                return None
            d, changed = self._migrate_legacy_urls(d)
            if changed:
                self._write_json(path, d, max_bytes=self._cache_max_bytes)
            rows = self._cache_rows(cat, d.get("rows"))
            # Earlier versions stored category names as station rows, losing every name.
            # Treat those empty files as cache misses so existing profiles recover.
            if cat == "__categories__" and not rows:
                return None
            return rows, max(0.0, time.time() - float(d.get("ts", 0)))
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
            return None

    def cache_clear_disk(self) -> int:
        n = 0
        for p in self._cache_files():
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
        for p in self._cache_files(cid=cid):
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
        """Count the profile in one traversal; safe to call from a worker."""
        cache = icons = other = total = cache_files = 0
        try:
            for path in self.dir.rglob("*"):
                try:
                    if not path.is_file():
                        continue
                    size = path.stat().st_size
                except OSError:
                    continue
                total += size
                try:
                    relative = path.relative_to(self.dir)
                except ValueError:
                    other += size
                    continue
                if relative.parts and relative.parts[0] == "cache":
                    cache += size
                    if len(relative.parts) == 2 and path.suffix == ".json":
                        cache_files += 1
                elif relative.parts and relative.parts[0] == "icons":
                    icons += size
                else:
                    other += size
        except OSError:
            pass
        return {"cache": cache, "icons": icons, "other": other,
                "total": total, "cache_files": cache_files}

    def clear_icons(self) -> int:
        with self._icon_lock:
            self._icon_generation += 1
            n = 0
            for p in self.icon_dir.glob("*"):
                if p.is_file() and p.name != ".nobackup":
                    try:
                        p.unlink()
                        n += 1
                    except OSError:
                        pass
            return n

    @property
    def icon_generation(self) -> int:
        with self._icon_lock:
            return self._icon_generation

    def invalidate_icons(self) -> int:
        """Fence off icon requests already in flight before an asynchronous clear."""
        with self._icon_lock:
            self._icon_generation += 1
            return self._icon_generation

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

        # Snapshot mutable state under its short-lived lock; never hold it while
        # loading profile files or scrubbing/serializing the export.
        with self._write_lock:
            settings = copy.deepcopy(self.settings)
        secret_values = tuple(self.secrets._cache.copy().values())
        bookmarks = self.load("bookmarks")
        local = self.load("local")
        return scrub_data({"app": "streamtuner-ng", "version": __version__, "exported": int(time.time()),
                           "settings": settings, "bookmarks": bookmarks,
                           "local": local}, secrets=secret_values)

    def redact(self, text: object) -> str:
        secrets = tuple(self.secrets._cache.copy().values())
        return redact_text(text, secrets)

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
        with self._write_lock:
            for key, typ in allowed.items():
                value = settings.get(key)
                if type(value) is typ and (typ is not str or len(value) <= 4096):
                    self.settings[key] = value
        columns = settings.get("visible_columns")
        allowed_columns = {"title", "genre", "bitrate", "format", "listeners", "votes", "country", "description", "playing", "url"}
        if isinstance(columns, list) and len(columns) <= len(allowed_columns):
            with self._write_lock:
                self.settings["visible_columns"] = [name for name in columns if isinstance(name, str) and name in allowed_columns]
        # Backup imports must not silently enable outbound presence sharing,
        # execute user plugins, select local paths or change stored credentials.
        self.save_settings()

    # ---- typed option access (per-plugin options live here too) ----
    def register_secret(self, key: str) -> None:
        if not valid_id(key):
            raise ValueError("Invalid declared secret key")
        with self._write_lock:
            self._declared_secrets.add(key)
            legacy = self.settings.pop(key, None)
        if isinstance(legacy, str):
            self.secrets.set(key, legacy)
            self.save_settings()

    def get(self, key: str, default: Any = None) -> Any:
        with self._write_lock:
            secret = is_secret_key(key) or key in self._declared_secrets
            value = self.settings.get(key, default)
        if secret:
            return self.secrets.get(key, default)
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
        with self._write_lock:
            secret = is_secret_key(key) or key in self._declared_secrets
            if not secret:
                self.settings[key] = copy.deepcopy(value)
        if secret:
            self.secrets.set(key, value)

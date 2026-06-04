"""The plugin host — the airlock.

The app NEVER calls a plugin method directly. It goes through `PluginHost.call`,
which runs the method on a daemon worker thread with a wall-clock timeout, catches
*everything*, and returns a Result instead of ever raising. Health is tracked per
plugin; three consecutive failures auto-disable it (the "crash budget").

This module is Qt-free: the same airlock backs both the headless selftest and the
GUI (the GUI just calls it from a QThread and reads results on the UI thread).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from .base import Channel
from .result import Result, Status

DEFAULT_TIMEOUT = 12.0
AUTO_DISABLE_THRESHOLD = 3      # consecutive errors before we trip a plugin off
LOG_KEEP = 25


@dataclass
class Health:
    """Live status of one plugin, surfaced as the colored dot + error log."""

    plugin_id: str
    status: Status = Status.DISABLED
    message: str = ""
    consecutive_errors: int = 0
    error_count: int = 0
    ok_count: int = 0
    auto_disabled: bool = False
    log: list[str] = field(default_factory=list)


class PluginHost:
    def __init__(self, config: Any = None):
        self.config = config
        self.channels: dict[str, Channel] = {}
        self.health: dict[str, Health] = {}
        # plugin_state[id] = {"enabled": bool, "visible": bool} — the single
        # source of truth, persisted to settings.json (DESIGN §6).
        self.state: dict[str, dict] = {}
        if config is not None:
            self.state = config.get("plugin_state", {}) or {}

    # ---- registration ----
    def register(self, channel: Channel) -> None:
        cid = channel.id
        self.channels[cid] = channel
        self.health[cid] = Health(cid, status=Status.DISABLED)
        st = self.state.setdefault(cid, {})
        st.setdefault("enabled", channel.default_enabled())
        st.setdefault("visible", True)

    def _persist_state(self) -> None:
        if self.config is not None:
            self.config.set("plugin_state", self.state)
            self.config.save_settings()

    # ---- enable / visible (the checkbox + menu write here) ----
    def is_enabled(self, cid: str) -> bool:
        return bool(self.state.get(cid, {}).get("enabled", False))

    def is_visible(self, cid: str) -> bool:
        return bool(self.state.get(cid, {}).get("visible", True))

    def set_enabled(self, cid: str, on: bool) -> None:
        self.state.setdefault(cid, {})["enabled"] = on
        if on:  # re-arming clears the auto-disable trip
            h = self.health.get(cid)
            if h:
                h.auto_disabled = False
                h.consecutive_errors = 0
                h.status = Status.DISABLED
        else:
            if cid in self.health:
                self.health[cid].status = Status.DISABLED
        self._persist_state()

    def set_visible(self, cid: str, on: bool) -> None:
        self.state.setdefault(cid, {})["visible"] = on
        self._persist_state()

    def shown_channels(self) -> list[Channel]:
        """Channels that should appear in the sidebar: enabled AND visible AND
        not test-only."""
        return [
            c for cid, c in self.channels.items()
            if self.is_enabled(cid) and self.is_visible(cid) and not c.test_only
        ]

    # ---- THE AIRLOCK ----
    def call(self, cid: str, method: str, *args, timeout: float = DEFAULT_TIMEOUT) -> Result:
        """Run channel.<method>(*args) behind the guard. Never raises."""
        channel = self.channels.get(cid)
        if channel is None:
            return Result(Status.ERROR, message=f"no such plugin: {cid}")
        fn = getattr(channel, method, None)
        if not callable(fn):
            return self._record(cid, Result(Status.ERROR, message=f"no method: {method}"))

        box: dict[str, Any] = {}
        done = threading.Event()

        def runner() -> None:
            try:
                box["data"] = fn(*args)
            except BaseException as exc:  # noqa: BLE001 — the whole point is to catch all
                box["exc"] = exc
            finally:
                done.set()

        # daemon=True so a hung plugin thread can be abandoned without blocking exit
        threading.Thread(target=runner, name=f"plugin:{cid}:{method}", daemon=True).start()

        if not done.wait(timeout):
            return self._record(cid, Result(Status.TIMEOUT, message=f"timed out after {timeout:.0f}s"))
        if "exc" in box:
            exc = box["exc"]
            return self._record(cid, Result(Status.ERROR, message=f"{type(exc).__name__}: {exc}"))

        data = box.get("data")
        if not data:
            # reachable but empty/None/garbage-falsey -> degraded, not an error
            return self._record(cid, Result(Status.EMPTY, data=data if data else [], message="no results"))
        return self._record(cid, Result(Status.OK, data=data))

    # convenience wrappers
    def categories(self, cid: str, **kw) -> Result:
        return self.call(cid, "update_categories", **kw)

    def streams(self, cid: str, category: str, search: str | None = None, **kw) -> Result:
        return self.call(cid, "update_streams", category, search, **kw)

    # ---- health bookkeeping + crash budget ----
    def _record(self, cid: str, result: Result) -> Result:
        h = self.health[cid]
        h.status = result.status
        h.message = result.message
        if result.status in (Status.ERROR, Status.TIMEOUT):
            h.consecutive_errors += 1
            h.error_count += 1
            h.log.append(result.message)
            del h.log[:-LOG_KEEP]
            if h.consecutive_errors >= AUTO_DISABLE_THRESHOLD and not h.auto_disabled:
                h.auto_disabled = True
                self.state.setdefault(cid, {})["enabled"] = False
                self._persist_state()
                h.message = (
                    f"auto-disabled after {h.consecutive_errors} errors "
                    f"(last: {result.message})"
                )
        else:
            h.consecutive_errors = 0
            h.ok_count += 1
        return result

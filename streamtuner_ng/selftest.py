"""Headless self-check. Proves the airlock and the first-wave channels without a
display — runs over SSH / in CI / on this WSL box.

    python -m streamtuner_ng --selftest

It exercises the REAL dispatch path (PluginHost.call), not the plugin methods
directly, exactly as DECISIONS D11 requires.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from .config import Config
from .plugins.channels import BUILTINS
from .plugins.channels._selftest import BrokenGarbage, BrokenHang, BrokenThrow
from .plugins.host import PluginHost
from .plugins.result import Status

# ---- tiny test harness ----
_G, _R, _Y, _0 = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
_results: list[bool] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    mark = f"{_G}PASS{_0}" if ok else f"{_R}FAIL{_0}"
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail else ""))


def _warn(name: str, detail: str = "") -> None:
    print(f"  [{_Y}WARN{_0}] {name}" + (f"  -- {detail}" if detail else ""))


def _section(title: str) -> None:
    print(f"\n{title}")


def run_selftest() -> int:
    print("StreamTuner-ng selftest")
    cfg = Config(base=Path(tempfile.mkdtemp(prefix="st-ng-selftest-")))
    host = PluginHost(cfg)
    for cls in BUILTINS + [BrokenThrow, BrokenHang, BrokenGarbage]:
        host.register(cls(cfg))

    # --- 1. THE AIRLOCK: broken plugins must not crash the app ---
    _section("1. Airlock — a broken plugin cannot take down the app")

    r = host.call("_broken_throw", "update_streams", "x", None, timeout=3)
    _check("plugin that throws -> ERROR (caught, not raised)",
           r.status is Status.ERROR, r.message)

    r = host.call("_broken_hang", "update_streams", "x", None, timeout=2)
    _check("plugin that hangs -> TIMEOUT (abandoned, UI never freezes)",
           r.status is Status.TIMEOUT, r.message)

    r = host.call("_broken_garbage", "update_streams", "x", None, timeout=3)
    _check("plugin that returns garbage -> EMPTY (degraded, not a crash)",
           r.status is Status.EMPTY, "returned None")

    # --- 2. CRASH BUDGET: auto-disable after 3 consecutive errors ---
    _section("2. Crash budget — auto-disable after repeated failures")
    host.set_enabled("_broken_throw", True)
    for _ in range(3):
        host.call("_broken_throw", "update_streams", "x", None, timeout=3)
    h = host.health["_broken_throw"]
    _check("3 errors -> auto-disabled", h.auto_disabled, h.message)
    _check("auto-disabled plugin is now disabled in state",
           not host.is_enabled("_broken_throw"))
    _check("its error log captured the reason", len(h.log) >= 3,
           f"{len(h.log)} entries")

    # --- 3. HEALTHY plugins are unaffected by their broken neighbours ---
    _section("3. Isolation — healthy plugins still work alongside broken ones")
    _check("bookmarks (offline plugin) responds OK",
           host.call("bookmarks", "update_categories").status is Status.OK)

    # --- 4. REAL DATA: first-wave network channels (best-effort) ---
    _section("4. Live channels — real fetch (needs network)")
    net_ok = True

    r = host.call("radiobrowser", "update_categories", timeout=20)
    if r.status is Status.OK and r.data:
        _check("RadioBrowser categories fetched", True, f"{len(r.data)} tags, e.g. {r.data[:3]}")
        rs = host.call("radiobrowser", "update_streams", "jazz", None, timeout=20)
        good = rs.status is Status.OK and rs.data and all(x["title"] and x["url"] for x in rs.data)
        _check("RadioBrowser 'jazz' stations have title+url", bool(good),
               f"{len(rs.data or [])} stations; first: {rs.data[0]['title'] if rs.data else '-'}")
    else:
        net_ok = False
        _warn("RadioBrowser unreachable (offline?) — skipping", r.message)

    r = host.call("somafm", "update_categories", timeout=20)
    if r.status is Status.OK and r.data:
        rs = host.call("somafm", "update_streams", "All", None, timeout=20)
        good = rs.status is Status.OK and rs.data and all(x["url"] for x in rs.data)
        _check("SomaFM channels.json parsed; streams have URLs", bool(good),
               f"{len(rs.data or [])} channels; first: {rs.data[0]['title'] if rs.data else '-'}")
    else:
        net_ok = False
        _warn("SomaFM unreachable (offline?) — skipping", r.message)

    if not net_ok:
        _warn("network checks were skipped; airlock checks above are what matter")

    # ---- summary ----
    passed = sum(_results)
    total = len(_results)
    color = _G if passed == total else _R
    print(f"\n{color}{passed}/{total} checks passed{_0}")
    return 0 if passed == total else 1


if __name__ == "__main__":   # pragma: no cover
    sys.exit(run_selftest())

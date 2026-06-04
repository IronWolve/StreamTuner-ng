"""Deliberately-broken plugins — used ONLY by --selftest to prove the airlock.

One throws, one hangs forever, one returns garbage. The selftest registers these
and asserts the app survives all three with the right health status, and that a
plugin auto-disables after repeated failures. (st2 had no such protection.)
"""

from __future__ import annotations

import time

from ..base import Channel


class BrokenThrow(Channel):
    id = "_broken_throw"
    title = "Broken: throws"
    test_only = True

    def update_categories(self) -> list[str]:
        return ["x"]

    def update_streams(self, category, search=None):
        raise RuntimeError("intentional explosion")


class BrokenHang(Channel):
    id = "_broken_hang"
    title = "Broken: hangs"
    test_only = True

    def update_categories(self) -> list[str]:
        return ["x"]

    def update_streams(self, category, search=None):
        time.sleep(999)          # never returns -> must hit the airlock timeout
        return []


class BrokenGarbage(Channel):
    id = "_broken_garbage"
    title = "Broken: garbage"
    test_only = True

    def update_categories(self) -> list[str]:
        return ["x"]

    def update_streams(self, category, search=None):
        return None              # falsey/garbage -> EMPTY, not a crash


BROKEN_PLUGINS = [BrokenThrow, BrokenHang, BrokenGarbage]

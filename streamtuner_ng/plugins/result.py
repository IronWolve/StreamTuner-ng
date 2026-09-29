"""Result + health status types, with the Wavmaster palette dot colors.

Every plugin call returns a Result (never raises into the app). Health status
drives the colored dot shown next to a channel in the sidebar / Options.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Status(str, Enum):
    LOADING = "loading"    # fetch in progress        -> blue
    OK = "ok"              # last fetch succeeded      -> green
    EMPTY = "empty"        # reachable but no results  -> amber (degraded)
    ERROR = "error"        # threw                     -> red
    TIMEOUT = "timeout"    # hung past the deadline    -> red
    DISABLED = "disabled"  # switched off              -> grey


# RGB dot colors, straight from the Wavmaster palette (see DECISIONS D9/D13).
DOT_COLOR: dict[Status, tuple[int, int, int]] = {
    Status.LOADING: (90, 170, 255),   # blue
    Status.OK: (80, 230, 150),        # green
    Status.EMPTY: (210, 150, 60),     # amber
    Status.ERROR: (205, 50, 45),      # red
    Status.TIMEOUT: (205, 50, 45),    # red
    Status.DISABLED: (120, 120, 120), # grey
}


@dataclass
class Result:
    """What every airlocked plugin call returns. Never an exception."""

    status: Status
    data: Any = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (Status.OK, Status.EMPTY)

"""Optional Discord Rich Presence — show the now-playing station/track on your Discord profile.

Degrades silently to a no-op when pypresence isn't installed, Discord isn't running, or no
Application ID is configured. Everything is wrapped so a flaky Discord IPC can never disrupt
playback. Needs a free Discord *Application ID* (developer portal) since Discord identifies the
app by it; the user pastes theirs in Options.
"""

from __future__ import annotations

import time

# StreamTuner-ng's own Discord application (a public client id — not a secret). Rich Presence shows
# "Listening to <this app's name>"; users just toggle it on in Options.
DISCORD_APP_ID = "1511972697310105722"


class DiscordPresence:
    def __init__(self, client_id: str = ""):
        self.client_id = (client_id or "").strip()
        self._rpc = None
        self._connected = False
        self._start: int | None = None
        self._last: tuple | None = None      # de-dupe identical updates

    # ---- connection (lazy; re-tries if Discord starts later) ----
    def _ensure(self) -> bool:
        if self._connected and self._rpc is not None:
            return True
        if not self.client_id:
            return False
        try:
            from pypresence import Presence
            self._rpc = Presence(self.client_id)
            self._rpc.connect()
            self._connected = True
        except Exception:  # noqa: BLE001 — pypresence missing / Discord down / bad id
            self._rpc = None
            self._connected = False
        return self._connected

    @staticmethod
    def _fit(s: str) -> str | None:
        """Discord wants 2..128 chars; trim and drop too-short fields."""
        s = (s or "").strip()
        if len(s) < 2:
            return None
        return s[:128]

    # ---- presence ----
    def update(self, details: str, state: str, reset_elapsed: bool = False) -> None:
        """Set the presence. `reset_elapsed` restarts the 'listening for…' timer (new station)."""
        if not self._ensure():
            return
        if reset_elapsed or self._start is None:
            self._start = int(time.time())
        payload = (self._fit(details), self._fit(state), self._start)
        if payload == self._last:
            return                            # nothing changed — don't spam the IPC
        try:
            self._rpc.update(details=payload[0], state=payload[1], start=payload[2])
            self._last = payload
        except Exception:  # noqa: BLE001 — drop the connection; we'll reconnect next time
            self._connected = False
            self._last = None

    def clear(self) -> None:
        self._start = None
        self._last = None
        if self._rpc is not None and self._connected:
            try:
                self._rpc.clear()
            except Exception:  # noqa: BLE001
                self._connected = False

    def close(self) -> None:
        self.clear()
        if self._rpc is not None:
            try:
                self._rpc.close()
            except Exception:  # noqa: BLE001
                pass
        self._rpc = None
        self._connected = False

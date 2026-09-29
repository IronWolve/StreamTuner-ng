"""Opt-in Discord presence; bounded IPC stays on a dedicated daemon thread."""
from __future__ import annotations

import asyncio
import queue
import threading
import time

DISCORD_APP_ID = "1511972697310105722"  # public application ID, not a credential


class DiscordPresence:
    def __init__(self, client_id: str = ""):
        self.client_id = (client_id or "").strip()
        self._queue = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._thread = None
        self._closed = False

    @staticmethod
    def _fit(value: str) -> str | None:
        text = (value or "").strip()
        return text[:128] if len(text) >= 2 else None

    def _submit(self, item):
        with self._lock:
            if self._closed or not self.client_id:
                return
            self._replace_pending(item)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='discord-presence', daemon=True)
                self._thread.start()

    def _replace_pending(self, item):
        try:
            self._queue.get_nowait()
        except queue.Empty:
            pass
        self._queue.put_nowait(item)

    def update(self, details: str, state: str, reset_elapsed: bool = False) -> None:
        self._submit(('UPDATE', self._fit(details), self._fit(state), reset_elapsed))

    def clear(self) -> None:
        self._submit(('CLEAR',))

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._thread is not None:
                self._replace_pending(('CLOSE',))

    def _run(self):
        rpc = loop = None
        started = last = None
        while True:
            item = self._queue.get()
            try:
                if item[0] in ('CLEAR', 'CLOSE'):
                    started = last = None
                    if rpc is not None and loop is not None:
                        loop.run_until_complete(asyncio.wait_for(rpc.clear(), timeout=5))
                else:
                    if rpc is None:
                        from pypresence import AioPresence
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        rpc = AioPresence(self.client_id, loop=loop, connection_timeout=5)
                        loop.run_until_complete(asyncio.wait_for(rpc.connect(), timeout=5))
                    if item[3] or started is None:
                        started = int(time.time())
                    payload = (item[1], item[2], started)
                    if payload != last:
                        loop.run_until_complete(asyncio.wait_for(
                            rpc.update(details=payload[0], state=payload[1], start=payload[2]), timeout=5))
                        last = payload
            except Exception:
                last = None
                if rpc is not None:
                    try:
                        rpc.close()
                    except Exception:
                        pass
                rpc = None
                if loop is not None and not loop.is_closed():
                    loop.close()
                loop = None
            finally:
                self._queue.task_done()
            if item[0] == 'CLOSE':
                if rpc is not None:
                    try:
                        rpc.close()
                    except Exception:
                        pass
                if loop is not None and not loop.is_closed():
                    loop.close()
                return

"""Run an airlocked plugin call off the UI thread, deliver the Result on the UI
thread. Keeps the window responsive while the host's own timeout guards the call.

Note: QThreadPool auto-deletes the QRunnable as soon as run() returns. If the
signal *sender* lived on the runnable, it could be destroyed before the queued
cross-thread signal is delivered, dropping the callback. So we keep the sender
(a standalone QObject) alive in a module-level set until delivery completes.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

_alive: set = set()   # keeps senders alive until their result is delivered


class _Sig(QObject):
    done = Signal(object)


_io_pool = None   # lazy: a small pool for best-effort favicon/art network (see run_io)


def run_async(fn: Callable, on_done: Callable, pool: QThreadPool | None = None) -> None:
    """fn() runs on a pool thread; on_done(result) runs on the UI thread. `pool` defaults to the
    global pool, which serves the time-critical work (playback resolves, channel/category loads)."""
    sig = _Sig()                 # created on the UI thread -> queued delivery
    _alive.add(sig)

    def deliver(result):
        try:
            on_done(result)
        finally:
            _alive.discard(sig)

    sig.done.connect(deliver)

    class _Task(QRunnable):
        def run(self) -> None:
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001 — host shouldn't raise, but be safe
                result = exc
            try:
                sig.done.emit(result)
            except RuntimeError:
                pass   # app tearing down (e.g. window closed mid-fetch) — receiver gone

    (pool or QThreadPool.globalInstance()).start(_Task())


def run_io(fn: Callable, on_done: Callable) -> None:
    """Like run_async, but on a small DEDICATED pool for best-effort network (favicons / artwork).
    Kept apart from the global pool so a slow/laggy station's icon fetch can never saturate it and
    starve playback resolves or channel loads (the freeze we hit hammering laggy stations)."""
    global _io_pool
    if _io_pool is None:
        _io_pool = QThreadPool()
        _io_pool.setMaxThreadCount(4)
    run_async(fn, on_done, pool=_io_pool)

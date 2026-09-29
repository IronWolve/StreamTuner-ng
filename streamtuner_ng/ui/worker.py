"""Bounded daemon workers with results delivered on the Qt UI thread."""
from __future__ import annotations

import queue
import sys
import threading
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot

_alive: set = set()


class _Delivery(QObject):
    done = Signal(object)

    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.done.connect(self.deliver, Qt.QueuedConnection)
        _alive.add(self)

    @Slot(object)
    def deliver(self, result):
        try:
            owner = getattr(self.callback, '__self__', None)
            if owner is None or not getattr(owner, '_closing', False):
                self.callback(result)
        except RuntimeError:
            pass  # a window can be destroyed before a queued result arrives
        except Exception as exc:
            # Result payloads can contain credentials; do not dump them to logs.
            if sys.stderr is not None:
                sys.stderr.write(f'Background result handler failed: {type(exc).__name__}\n')
        finally:
            _alive.discard(self)
            self.deleteLater()


class _Pool:
    def __init__(self, workers: int):
        self._limit = workers
        self._active = 0
        self._condition = threading.Condition()
        self._queue = queue.Queue(maxsize=128)
        threading.Thread(target=self._dispatch, daemon=True, name='work-dispatch').start()

    def setMaxThreadCount(self, count):
        with self._condition:
            self._limit = max(1, min(32, int(count)))
            self._condition.notify_all()

    def submit(self, fn, delivery):
        try:
            self._queue.put_nowait((fn, delivery))
        except queue.Full:
            delivery.done.emit(TimeoutError('Background queue is full; try again shortly'))

    def _dispatch(self):
        while True:
            fn, delivery = self._queue.get()
            with self._condition:
                self._condition.wait_for(lambda: self._active < self._limit)
                self._active += 1
            threading.Thread(target=self._execute, args=(fn, delivery), daemon=True, name='work-task').start()

    def _execute(self, fn, delivery):
        try:
            try:
                result = fn()
            except BaseException as exc:
                result = exc
            try:
                delivery.done.emit(result)
            except RuntimeError:
                pass  # QApplication has already shut down
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()
            self._queue.task_done()


_default_pool = None
_io_pool = None
_cache_pool = None
_io_threads = 4


def run_async(fn: Callable, on_done: Callable, pool=None) -> None:
    global _default_pool
    if pool is None:
        if _default_pool is None:
            _default_pool = _Pool(8)
        pool = _default_pool
    pool.submit(fn, _Delivery(on_done))


def run_io(fn: Callable, on_done: Callable) -> None:
    global _io_pool
    if _io_pool is None:
        _io_pool = _Pool(_io_threads)
    run_async(fn, on_done, pool=_io_pool)


def run_cache(fn: Callable, on_done: Callable) -> None:
    """Serialize disk cache work independently of slow network artwork requests."""
    global _cache_pool
    if _cache_pool is None:
        _cache_pool = _Pool(1)
    run_async(fn, on_done, pool=_cache_pool)


def drain_cache(timeout: float = 5.0) -> bool:
    """Finish queued disk work after the UI closes, without waiting on network jobs."""
    if _cache_pool is None:
        return True
    pending = _cache_pool._queue
    with pending.all_tasks_done:
        return pending.all_tasks_done.wait_for(lambda: pending.unfinished_tasks == 0, timeout)


def set_io_threads(n: int) -> None:
    global _io_threads
    _io_threads = max(1, min(32, int(n)))
    if _io_pool is not None:
        _io_pool.setMaxThreadCount(_io_threads)

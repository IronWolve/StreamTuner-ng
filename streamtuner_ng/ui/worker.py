"""Bounded daemon workers with results delivered on the Qt UI thread."""
from __future__ import annotations

import queue
import sys
import threading
import itertools
from concurrent.futures import CancelledError
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
    def __init__(self, workers: int, name: str = 'work'):
        self._limit = workers
        self._active = 0
        self._condition = threading.Condition()
        self._queue = queue.PriorityQueue(maxsize=128)
        self._sequence = itertools.count()
        self._threads: list[threading.Thread] = []
        self._name = name
        self._pending_keys = {}
        self._key_lock = threading.Lock()

    def setMaxThreadCount(self, count):
        with self._condition:
            self._limit = max(1, min(32, int(count)))
            self._condition.notify_all()
        self._ensure_workers()

    def _ensure_workers(self):
        with self._condition:
            target = min(self._limit, self._active + self._queue.qsize())
            live = sum(thread.is_alive() for thread in self._threads)
            while live < target:
                live += 1
                thread = threading.Thread(target=self._run, daemon=True,
                                          name=f'{self._name}-{live}')
                self._threads.append(thread)
                thread.start()

    def submit(self, fn, delivery, priority=0, cancelled=None, key=None):
        external_cancelled = cancelled
        superseded = threading.Event()
        if key is not None:
            with self._key_lock:
                previous = self._pending_keys.get(key)
                if previous is not None:
                    previous.set()             # obsolete queued work is skipped when dequeued
                self._pending_keys[key] = superseded
        try:
            self._queue.put_nowait((int(priority), next(self._sequence), fn, delivery,
                                    superseded, external_cancelled, key))
        except queue.Full:
            delivery.done.emit(TimeoutError('Background queue is full; try again shortly'))
            if key is not None:
                with self._key_lock:
                    if self._pending_keys.get(key) is superseded:
                        self._pending_keys.pop(key, None)
            return
        self._ensure_workers()

    def _run(self):
        while True:
            priority, sequence, fn, delivery, superseded, external_cancelled, key = self._queue.get()
            with self._condition:
                self._condition.wait_for(lambda: self._active < self._limit)
                self._active += 1
            try:
                if superseded.is_set() or (external_cancelled is not None and external_cancelled.is_set()):
                    result = CancelledError()
                else:
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
                if key is not None:
                    with self._key_lock:
                        if self._pending_keys.get(key) is superseded:
                            self._pending_keys.pop(key, None)
                self._queue.task_done()


_default_pool = None
_playback_pool = None
_io_pool = None
_cache_pool = None
_io_threads = 4


def run_async(fn: Callable, on_done: Callable, pool=None, priority: int = 0,
              cancelled: threading.Event | None = None) -> None:
    """Queue work; lower numeric priority is dispatched first."""
    global _default_pool
    if pool is None:
        if _default_pool is None:
            _default_pool = _Pool(8, 'work')
        pool = _default_pool
    pool.submit(fn, _Delivery(on_done), priority=priority, cancelled=cancelled)


def run_playback(fn: Callable, on_done: Callable, cancelled: threading.Event | None = None) -> None:
    """Run latency-sensitive resolve/play work in its own bounded pool."""
    global _playback_pool
    if _playback_pool is None:
        _playback_pool = _Pool(2, 'playback')
    _playback_pool.submit(fn, _Delivery(on_done), priority=0, cancelled=cancelled)


def run_io(fn: Callable, on_done: Callable, cancelled: threading.Event | None = None) -> None:
    global _io_pool
    if _io_pool is None:
        _io_pool = _Pool(_io_threads, 'io')
    run_async(fn, on_done, pool=_io_pool, cancelled=cancelled)


def run_cache(fn: Callable, on_done: Callable, key=None,
              cancelled: threading.Event | None = None) -> None:
    """Serialize disk cache work independently of slow network artwork requests."""
    global _cache_pool
    if _cache_pool is None:
        _cache_pool = _Pool(1, 'cache')
    _cache_pool.submit(fn, _Delivery(on_done), priority=0, cancelled=cancelled, key=key)


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

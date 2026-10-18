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
import queue
import time
from dataclasses import dataclass, field
from typing import Any

from .base import Channel
from .result import Page, Result, Status
from ..security import MAX_ROWS, redact_text, station_rows, valid_id, valid_url

DEFAULT_TIMEOUT = 12.0
AUTO_DISABLE_THRESHOLD = 3      # consecutive errors before we trip a plugin off
LOG_KEEP = 25


@dataclass
class _CallJob:
    host: "PluginHost"
    cid: str
    method: str
    fn: Any
    args: tuple
    deadline: float
    cancel_event: threading.Event
    external_cancel_event: threading.Event | None
    playback: bool
    box: dict[str, Any]
    done: threading.Event


class _PluginWorkers:
    """Persistent bounded daemon workers; a hung plugin cannot block process exit."""

    def __init__(self, workers: int = 16):
        self.limit = workers
        self.regular_limit = max(1, workers - 2)
        self.max_pending = 128
        self.lock = threading.Lock()
        self.threads: list[threading.Thread] = []
        self.queues: list[queue.Queue[_CallJob]] = []
        self.lane_load: list[int] = []
        self.provider_lanes: dict[tuple[object, str], list[int]] = {}
        self.playback_lanes: dict[tuple[object, str], int] = {}
        self.regular_lane_ids: list[int] = []
        self.pending = 0

    def submit(self, job: _CallJob) -> bool:
        with self.lock:
            if self.pending >= self.max_pending:
                return False
            lane_key = (job.host._session_scope, job.cid)
            lanes = self.provider_lanes.setdefault(lane_key, [])
            if job.playback:
                lane = self.playback_lanes.get(lane_key)
                if lane is None:
                    available = set(self.playback_lanes.values())
                    if (len(available) < self.limit - self.regular_limit
                            and len(self.threads) < self.limit):
                        lane = self._new_lane(lane_key, provider_lane=False)
                    elif available:
                        lane = min(available, key=lambda item: self.lane_load[item])
                    else:
                        return False
                    self.playback_lanes[lane_key] = lane
            else:
                lane = min(lanes, key=lambda item: self.lane_load[item]) if lanes else None
                # Reuse the provider's idle worker for connection pooling. A
                # second concurrent browse can borrow an existing idle worker
                # even after all regular worker threads have been created.
                if lane is None or (len(lanes) < 2 and self.lane_load[lane] > 0):
                    if (len(self.regular_lane_ids) < self.regular_limit
                            and len(self.threads) < self.limit):
                        lane = self._new_lane(lane_key)
                    else:
                        available = [item for item in self.regular_lane_ids if item not in lanes]
                        if available:
                            candidate = min(available, key=lambda item: self.lane_load[item])
                            if lane is None or self.lane_load[candidate] < self.lane_load[lane]:
                                lane = candidate
                                lanes.append(lane)
                if lane is None:
                    return False
            try:
                self.queues[lane].put_nowait(job)
            except queue.Full:
                return False
            self.lane_load[lane] += 1
            self.pending += 1
        return True

    def _new_lane(self, lane_key: tuple[object, str], provider_lane: bool = True) -> int:
        lane = len(self.threads)
        jobs: queue.Queue[_CallJob] = queue.Queue(maxsize=self.max_pending)
        self.queues.append(jobs)
        self.lane_load.append(0)
        thread = threading.Thread(target=self._run, args=(lane,), daemon=True,
                                  name=f"plugin-worker-{lane + 1}")
        self.threads.append(thread)
        if provider_lane:
            self.provider_lanes.setdefault(lane_key, []).append(lane)
            self.regular_lane_ids.append(lane)
        thread.start()
        return lane

    def _run(self, lane: int) -> None:
        jobs = self.queues[lane]
        while True:
            job = jobs.get()
            try:
                if job.cancel_event.is_set() or (job.external_cancel_event and job.external_cancel_event.is_set()):
                    job.box["cancelled"] = True
                    continue
                if time.monotonic() >= job.deadline:
                    job.box["deadline"] = True
                    continue
                try:
                    from ..net.http import request_context, session_scope
                    cancellation = (job.cancel_event, job.external_cancel_event)
                    with request_context(job.deadline, cancellation), session_scope((job.host._session_scope, job.cid)):
                        job.box["data"] = job.fn(*job.args)
                except BaseException as exc:  # the airlock contains arbitrary plugin failures
                    job.box["exc"] = exc
                if job.external_cancel_event and job.external_cancel_event.is_set():
                    job.box["cancelled"] = True
                elif job.cancel_event.is_set():
                    job.box["deadline"] = True
                elif type(job.box.get("exc")).__name__ == "RequestCancelled":
                    if job.external_cancel_event and job.external_cancel_event.is_set():
                        job.box["cancelled"] = True
                    else:
                        job.box["deadline"] = True
                elif type(job.box.get("exc")).__name__ == "RequestDeadlineExceeded":
                    job.box["deadline"] = True
            finally:
                with job.host._lock:
                    job.host._release_call_slot(job.cid, job.playback)
                with self.lock:
                    self.lane_load[lane] -= 1
                    self.pending -= 1
                job.done.set()
                jobs.task_done()


_plugin_workers = _PluginWorkers()


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
        self._lock = threading.RLock()
        self._session_scope = object()
        self._active: dict[str, int] = {}
        self._active_regular: dict[str, int] = {}
        self._active_playback: dict[str, int] = {}
        self._slots = threading.BoundedSemaphore(16)
        self._regular_slots = threading.BoundedSemaphore(14)
        self._playback_slots = threading.BoundedSemaphore(2)
        self.channels: dict[str, Channel] = {}
        self.health: dict[str, Health] = {}
        # plugin_state[id] = {"enabled": bool, "visible": bool} — the single
        # source of truth, persisted to settings.json (DESIGN §6).
        self.state: dict[str, dict] = {}
        if config is not None:
            saved = config.get("plugin_state", {}) or {}
            self.state = {cid: {k: v for k, v in state.items() if k in ("enabled", "visible") and type(v) is bool}
                          for cid, state in saved.items() if valid_id(cid) and isinstance(state, dict)}

    # ---- registration ----
    def register(self, channel: Channel) -> None:
        cid = channel.id
        if not valid_id(cid) or cid in self.channels:
            raise ValueError("Plugin ID is invalid or already registered")
        if self.config is not None:
            for option in channel.options_spec:
                if isinstance(option, dict) and option.get("type") == "secret":
                    self.config.register_secret(option.get("key"))
        self.channels[cid] = channel
        self.health[cid] = Health(cid, status=Status.DISABLED)
        st = self.state.setdefault(cid, {})
        st.setdefault("enabled", channel.default_enabled())
        st.setdefault("visible", True)
        if cid in ("bookmarks", "local"):
            st["enabled"] = True

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
    def call(self, cid: str, method: str, *args, timeout: float = DEFAULT_TIMEOUT,
             cancel_event: threading.Event | None = None, playback: bool = False) -> Result:
        """Run channel.<method>(*args) behind the guard. Never raises."""
        channel = self.channels.get(cid)
        if channel is None:
            return Result(Status.ERROR, message=f"no such plugin: {cid}")
        if not channel.test_only and not self.is_enabled(cid):
            return Result(Status.DISABLED, message="plugin is disabled")
        if method not in ("update_categories", "refresh_categories", "update_streams", "refresh_streams", "update_streams_page", "resolve_url"):
            return Result(Status.ERROR, message="unsupported plugin method")
        fn = getattr(channel, method, None)
        if not callable(fn):
            return self._record(cid, Result(Status.ERROR, message=f"no method: {method}"))

        with self._lock:
            active = self._active_playback if playback else self._active_regular
            per_provider_limit = 1 if playback else 2
            reserved_slots = self._playback_slots if playback else self._regular_slots
            if active.get(cid, 0) >= per_provider_limit or not reserved_slots.acquire(blocking=False):
                return Result(Status.TIMEOUT, message="previous plugin requests are still running")
            if not self._slots.acquire(blocking=False):
                reserved_slots.release()
                return Result(Status.TIMEOUT, message="plugin request capacity is busy")
            active[cid] = active.get(cid, 0) + 1
            self._active[cid] = self._active.get(cid, 0) + 1

        box: dict[str, Any] = {}
        done = threading.Event()
        external_cancel_event = cancel_event
        cancel_event = threading.Event()       # timeout for this provider cannot cancel sibling calls
        deadline = time.monotonic() + max(0.0, timeout)
        job = _CallJob(self, cid, method, fn, args, deadline, cancel_event,
                       external_cancel_event, playback, box, done)
        if not _plugin_workers.submit(job):
            with self._lock:
                self._release_call_slot(cid, playback)
            # Queue pressure is host capacity, not evidence that this provider
            # is unhealthy; keep its crash budget and health state untouched.
            return Result(Status.BUSY, message="plugin worker queue is full")

        # Wake promptly when a view is superseded. The worker remains bounded and
        # cooperative network code observes the same event through request_context.
        while not done.is_set():
            if external_cancel_event is not None and external_cancel_event.is_set():
                cancel_event.set()
                return self._record(cid, Result(Status.CANCELLED, message="request cancelled"))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                cancel_event.set()
                return self._record(cid, Result(Status.TIMEOUT, message=f"timed out after {timeout:.0f}s"))
            done.wait(min(0.05, remaining))
        if time.monotonic() >= deadline:
            cancel_event.set()
            return self._record(cid, Result(Status.TIMEOUT, message=f"timed out after {timeout:.0f}s"))
        if external_cancel_event is not None and external_cancel_event.is_set():
            cancel_event.set()
            return self._record(cid, Result(Status.CANCELLED, message="request cancelled"))
        if box.get("cancelled"):
            return self._record(cid, Result(Status.CANCELLED, message="request cancelled"))
        if box.get("deadline"):
            return self._record(cid, Result(Status.TIMEOUT, message=f"timed out after {timeout:.0f}s"))
        if "exc" in box:
            exc = box["exc"]
            message = f"{type(exc).__name__}: {exc}"
            message = self.config.redact(message) if self.config is not None else redact_text(message)
            return self._record(cid, Result(Status.ERROR, message=message[:2048]))

        data = box.get("data")
        if method in ("update_categories", "refresh_categories"):
            data = [x[:256] for x in data[:MAX_ROWS] if isinstance(x, str)] if isinstance(data, list) else []
        elif method in ("update_streams", "refresh_streams", "update_streams_page"):
            page = data if isinstance(data, Page) else None
            raw_rows = page.rows if page is not None else data
            if page is not None:
                offset = args[1] if (method == "update_streams_page" and len(args) > 1
                                     and type(args[1]) is int and args[1] >= 0) else 0
                has_more, next_offset = page.has_more, page.next_offset
                complete, errors = page.complete, page.errors
                metadata_valid = (
                    isinstance(raw_rows, list)
                    and type(has_more) is bool
                    and type(complete) is bool
                    and (next_offset is None or
                         (type(next_offset) is int and next_offset > offset))
                    and isinstance(errors, (tuple, list))
                    and all(isinstance(error, str) for error in errors[:50])
                )
                if not metadata_valid:
                    message = "Plugin returned invalid page metadata"
                    message = self.config.redact(message) if self.config is not None else redact_text(message)
                    return self._record(cid, Result(Status.ERROR, message=message))
                if has_more and next_offset is None:
                    # Explicit continuation without a cursor advances by the
                    # requested batch, then the provider's declared page size,
                    # matching the UI fallback contract.
                    limit = args[2] if method == "update_streams_page" and len(args) > 2 else None
                    step = limit if type(limit) is int and limit > 0 else None
                    if step is None:
                        step = getattr(channel, "page_batch_size", None) or getattr(channel, "page_size", None)
                    if type(step) is not int or step <= 0:
                        step = 1
                    next_offset = offset + step
            else:
                has_more, next_offset, complete, errors = None, None, True, ()
                if method == "update_streams_page" and isinstance(raw_rows, list):
                    offset = args[1] if len(args) > 1 and isinstance(args[1], int) else 0
                    limit = args[2] if len(args) > 2 else None
                    custom_page = getattr(fn, "__func__", None) is not Channel.update_streams_page
                    if custom_page:
                        has_more = bool(raw_rows)  # preserve legacy providers with internally shortened batches
                        if has_more and isinstance(limit, int) and limit > 0:
                            next_offset = offset + limit
                    else:
                        # The base adapter returns one complete update_streams list.
                        has_more = False
            data = station_rows(raw_rows)
            safe_errors = tuple(
                (self.config.redact(error[:2048]) if self.config is not None else redact_text(error[:2048]))[:512]
                for error in errors[:50]
            )
            message = ""
            if safe_errors or not complete:
                message = f"partial results ({len(safe_errors)} provider request error(s))"
            if safe_errors or not complete:
                return self._record(cid, Result(Status.PARTIAL, data=data, message=message,
                                                has_more=has_more, next_offset=next_offset,
                                                complete=False, errors=safe_errors))
            if not data:
                return self._record(cid, Result(Status.EMPTY, data=[], message="no results",
                                                has_more=has_more, next_offset=next_offset))
            return self._record(cid, Result(Status.OK, data=data, has_more=has_more,
                                            next_offset=next_offset))
        elif method == "resolve_url":
            if not isinstance(data, dict) or not valid_url(data.get("url")):
                return self._record(cid, Result(Status.ERROR, message="Plugin returned an invalid playback URL"))
        if not data:
            # reachable but empty/None/garbage-falsey -> degraded, not an error
            return self._record(cid, Result(Status.EMPTY, data=data if data else [], message="no results"))
        return self._record(cid, Result(Status.OK, data=data))

    def _release_call_slot(self, cid: str, playback: bool) -> None:
        self._active[cid] -= 1
        active = self._active_playback if playback else self._active_regular
        active[cid] -= 1
        (self._playback_slots if playback else self._regular_slots).release()
        self._slots.release()

    # convenience wrappers
    def categories(self, cid: str, **kw) -> Result:
        return self.call(cid, "update_categories", **kw)

    def streams(self, cid: str, category: str, search: str | None = None, **kw) -> Result:
        return self.call(cid, "update_streams", category, search, **kw)

    # ---- health bookkeeping + crash budget ----
    def _record(self, cid: str, result: Result) -> Result:
        with self._lock:
            return self._record_locked(cid, result)

    def _record_locked(self, cid: str, result: Result) -> Result:
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

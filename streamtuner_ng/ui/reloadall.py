"""View -> Reload all: fetch every saved list of every enabled feed again, in the background.

The open list is reloaded the way F5 does it; then each enabled feed's category list and each of its
saved lists are fetched, two at a time (one at a time, with a short pause, while music plays), and
the feeds take turns: two lists of one feed are never fetched together. Lists a feed cannot give keep
their saved copy. The status line counts the progress and the connections under way, and ends with a
summary once the open list has loaded.
"""

from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QTimer

from ..net.http import connections, reset_connection_peak
from .worker import run_async, run_cache

PARALLEL = 2
PAUSE_WHILE_PLAYING_MS = 1000


class ReloadAll:
    def __init__(self, window) -> None:
        self.w = window
        self.running = False
        self.cancelled = False
        self.queue: deque = deque()
        self.active = 0
        self.active_feeds: set[str] = set()
        self.total = self.queued = 0
        self.has_open = False
        self.done = 0
        self.refreshed = 0
        self._wait = QTimer(window)
        self._wait.setInterval(400)
        self._wait.timeout.connect(self._maybe_finish)

    # ---- plan ----
    def lists(self) -> list[tuple[str, str]]:
        """[(feed id, list name)] to fetch: per enabled feed its category list, then each saved list
        (or its first category when nothing is saved yet). Favorites, History and Local are not fetched."""
        w = self.w
        enabled = [c.id for c in w.host.shown_channels()
                   if c.id not in w.INTERNAL_CHANNELS and not w.host.health[c.id].auto_disabled]
        saved: dict[str, list[str]] = {}
        for cid, cat, _age in w.config.cache_index():
            if cat != "__categories__":
                saved.setdefault(cid, []).append(cat)
        jobs: list[tuple[str, str]] = []
        for cid in enabled:
            jobs.append((cid, "__categories__"))
            lists = sorted(saved.get(cid, []))
            if not lists:
                cats = w._cat_cache.get(cid) or []
                lists = [cats[0]] if cats else []
            jobs.extend((cid, cat) for cat in lists)
        open_key = (w.current_channel, w.current_category)
        return self._in_turns([job for job in jobs if job != open_key])

    @staticmethod
    def _in_turns(jobs: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """One list of each feed in turn (A1 B1 C1 A2 B2 ...), keeping each feed's own order."""
        per_feed: dict[str, list[tuple[str, str]]] = {}
        for job in jobs:
            per_feed.setdefault(job[0], []).append(job)
        turns: list[tuple[str, str]] = []
        while per_feed:
            for cid in list(per_feed):
                turns.append(per_feed[cid].pop(0))
                if not per_feed[cid]:
                    del per_feed[cid]
        return turns

    # ---- run ----
    def start(self) -> None:
        w = self.w
        if self.running:
            return
        self.queue = deque(self.lists())
        self.queued = len(self.queue)
        self.has_open = bool(w.current_channel)
        self.total = self.queued + (1 if self.has_open else 0)   # the open list counts too
        self.done = self.refreshed = self.active = 0
        self.active_feeds.clear()
        self.cancelled = False
        self.running = True
        reset_connection_peak()
        if self.has_open:
            w._reload()                                         # the open list, as F5 does it
        w._cancel_button.setText("Cancel reload all")
        w._cancel_button.show()
        self._progress()
        self._pump()
        self._wait.start()

    def cancel(self) -> None:
        if not self.running:
            return
        self.cancelled = True
        self.queue.clear()
        self.w.statusBar().showMessage("Reload all: cancelling…")

    def _limit(self) -> int:
        return 1 if self.w._music_playing() else PARALLEL

    def _next(self):
        """The first waiting list whose feed has no list under way (feeds take turns), or None."""
        for index, (cid, _cat) in enumerate(self.queue):
            if cid not in self.active_feeds:
                job = self.queue[index]
                del self.queue[index]
                return job
        return None

    def _pump(self) -> None:
        while not self.cancelled and self.queue and self.active < self._limit():
            job = self._next()
            if job is None:
                return                                          # every waiting feed is busy; a finish pumps again
            cid, cat = job
            self.active += 1
            self.active_feeds.add(cid)
            if cat == "__categories__":
                self._categories(cid)
            else:
                self.w._bg_refresh(cid, cat, lambda ok, c=cid: self._finished(ok, c))

    def _finished(self, ok: bool, cid: str | None = None) -> None:
        self.active = max(0, self.active - 1)
        if cid is not None:
            self.active_feeds.discard(cid)                          # the feed may take its next turn
        self.done += 1
        self.refreshed += 1 if ok else 0
        if not self.cancelled:
            self._progress()
        if self.w._music_playing() and self.queue and not self.cancelled:
            QTimer.singleShot(PAUSE_WHILE_PLAYING_MS, self._pump)  # a breath between lists while music plays
        else:
            self._pump()

    def _categories(self, cid: str) -> None:
        w = self.w
        cancel = w._cache_event(cid)

        def fetch():
            return w.host.call(cid, "refresh_categories", timeout=45, cancel_event=cancel, background=True)

        def landed(result):
            cats = list(result.data) if getattr(result, "ok", False) and result.data else None
            if cats and not w._closing:
                w._cat_cache[cid] = cats
                w._cat_ages[cid] = time.monotonic()
                run_cache(lambda: w.config.cache_save(cid, "__categories__", cats), lambda *_: None,
                          key=(cid, "__categories__"))
            self._finished(bool(cats), cid)
        run_async(fetch, landed, priority=10, cancelled=cancel)

    # ---- progress and end ----
    def _progress(self) -> None:
        now, peak = connections()
        busy = f"  ·  {now} connection{'s' if now != 1 else ''} now, most {peak}" if peak else ""
        self.w.statusBar().showMessage(f"Reload all: {self.done} of {self.total} lists done…{busy}")
        if self.running and (self.queue or self.active):
            self.w._cancel_button.setText("Cancel reload all")
            self.w._cancel_button.show()                         # a finished open list hides its own Cancel button

    def _open_list_loaded(self) -> bool:
        w = self.w
        return w._loading_key is None and not w._refreshing

    def _maybe_finish(self) -> None:
        if not self.running:
            self._wait.stop()
            return
        if self.active or self.queue and not self.cancelled:
            return
        if not self._open_list_loaded():
            return                                              # the summary is the last word: wait for the open list
        self._wait.stop()
        self.running = False
        w = self.w
        w._cancel_button.setText("Cancel loading")
        w._cancel_button.hide()
        failed = max(0, self.queued - self.refreshed)            # the open list shows its own result
        if self.cancelled:
            w.statusBar().showMessage(f"Reload all cancelled — {self.refreshed} lists refreshed", 20000)
        elif failed:
            w.statusBar().showMessage(f"Reload all finished — {self.total - failed} of {self.total} lists refreshed, "
                                      f"{failed} could not be (saved copies kept)", 20000)
        else:
            w.statusBar().showMessage(f"Reload all finished — all {self.total} lists refreshed", 20000)

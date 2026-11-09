"""View -> Reload all: fetch every saved list of every enabled feed again, in the background.

The open list is reloaded the way F5 does it; then each enabled feed's category list and each of its
saved lists are fetched, two at a time. Lists a feed cannot give keep their saved copy. The status
line counts the progress and ends with a summary once the open list has loaded.
"""

from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QTimer

from .worker import run_async, run_cache

PARALLEL = 2


class ReloadAll:
    def __init__(self, window) -> None:
        self.w = window
        self.running = False
        self.cancelled = False
        self.queue: deque = deque()
        self.active = 0
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
        return [job for job in jobs if job != open_key]

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
        self.cancelled = False
        self.running = True
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

    def _pump(self) -> None:
        while not self.cancelled and self.queue and self.active < PARALLEL:
            cid, cat = self.queue.popleft()
            self.active += 1
            if cat == "__categories__":
                self._categories(cid)
            else:
                self.w._bg_refresh(cid, cat, lambda ok: self._finished(ok))

    def _finished(self, ok: bool) -> None:
        self.active = max(0, self.active - 1)
        self.done += 1
        self.refreshed += 1 if ok else 0
        if not self.cancelled:
            self._progress()
        self._pump()

    def _categories(self, cid: str) -> None:
        w = self.w
        cancel = w._cache_event(cid)

        def fetch():
            return w.host.call(cid, "refresh_categories", timeout=45, cancel_event=cancel)

        def landed(result):
            cats = list(result.data) if getattr(result, "ok", False) and result.data else None
            if cats and not w._closing:
                w._cat_cache[cid] = cats
                w._cat_ages[cid] = time.monotonic()
                run_cache(lambda: w.config.cache_save(cid, "__categories__", cats), lambda *_: None,
                          key=(cid, "__categories__"))
            self._finished(bool(cats))
        run_async(fetch, landed, priority=10, cancelled=cancel)

    # ---- progress and end ----
    def _progress(self) -> None:
        self.w.statusBar().showMessage(f"Reload all: {self.done} of {self.total} lists done…")
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

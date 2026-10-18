"""Bookmarks — favourites + recently-played history.

Persists to ~/.config/streamtuner-ng/bookmarks.json:
    {"favourite": [row, ...], "history": [row, ...]}
The star button calls add()/remove(); is_favourite() drives the lit-state.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..base import Channel
from ...security import MAX_ROWS, station_row, station_rows, valid_url

HISTORY_MAX = 60


@dataclass(frozen=True)
class BookmarkImportPlan:
    baseline_favourite: list[dict]
    baseline_history: list[dict]
    candidate_favourite: list[dict]
    candidate_history: list[dict]
    favourite_urls: frozenset[str]
    estimated_bytes: int
    favorites_added: int
    history_added: int
    changed: bool


class Bookmarks(Channel):
    id = "bookmarks"
    title = "Favorites"
    description = "Your starred stations and play history."
    priority = "core"          # always on
    category_pins = ("Favorites", "History")
    icon_emoji = "⭐"          # Favorites — a star in the sidebar

    def __init__(self, config=None):
        super().__init__(config)
        data = (config.load("bookmarks") if config else None) or {}
        data = data if isinstance(data, dict) else {}
        self.favourite: list[dict] = station_rows(data.get("favourite"))
        self.history: list[dict] = station_rows(data.get("history"))[:HISTORY_MAX]
        self._fav_urls = {r.get("url") for r in self.favourite}
        self._store_estimate = (
            config.estimate_station_lists("bookmarks", {"favourite": self.favourite, "history": self.history})
            if config is not None else 0
        )

    def _row_cost(self, row: dict) -> int:
        return self.config.row_size_budget(row) if self.config is not None else 0

    def _estimate(self, favourite, history) -> int:
        if self.config is not None:
            return self.config.preflight_station_lists(
                "bookmarks", {"favourite": favourite, "history": history}
            )
        if len(favourite) > MAX_ROWS or len(history) > MAX_ROWS:
            raise ValueError(f"Bookmarks supports at most {MAX_ROWS:,} rows per list")
        return 0

    def _check_estimate(self, estimate: int) -> None:
        if self.config is not None:
            self.config.assert_store_writable("bookmarks")
            if estimate > self.config.store_max_bytes:
                raise ValueError(
                    f"Favorites and History would exceed the supported "
                    f"{self.config.store_max_bytes:,}-byte storage limit"
                )

    def _save(self) -> None:
        if self.config is not None:
            self.config.save_station_snapshot(
                "bookmarks", {"favourite": self.favourite, "history": self.history}
            )

    # ---- favourites ----
    def is_favourite(self, url: str) -> bool:
        return url in self._fav_urls

    def add(self, row: dict) -> None:
        row = station_row(row)
        if row is None:
            return
        url = row.get("url")
        if not url or url in self._fav_urls:
            return
        if len(self.favourite) >= MAX_ROWS:
            raise ValueError(f"Favorites supports at most {MAX_ROWS:,} stations")
        estimate = self._store_estimate + self._row_cost(row)
        self._check_estimate(estimate)
        old_favourite, old_urls, old_estimate = self.favourite, self._fav_urls, self._store_estimate
        self.favourite = [*self.favourite, dict(row)]
        self._fav_urls = {*self._fav_urls, url}
        self._store_estimate = estimate
        try:
            self._save()
        except Exception:
            self.favourite, self._fav_urls, self._store_estimate = old_favourite, old_urls, old_estimate
            raise

    def add_many(self, rows: list[dict]) -> int:
        """Merge several rows in at once — skips any URL already saved (no clobber),
        saves once at the end. Returns how many were actually added."""
        if isinstance(rows, list) and len(rows) > MAX_ROWS:
            raise ValueError(f"Favorites import exceeds {MAX_ROWS:,} rows")
        additions = []
        have = set(self._fav_urls)
        for row in station_rows(rows):
            url = (row.get("url") or "").strip()
            if not url or url in have:
                continue
            additions.append(dict(row))
            have.add(url)
        if not additions:
            return 0
        if len(self.favourite) + len(additions) > MAX_ROWS:
            raise ValueError(
                f"Favorites import would exceed {MAX_ROWS:,} stations; "
                f"{len(self.favourite):,} are already saved"
            )
        estimate = self._store_estimate + sum(self._row_cost(row) for row in additions)
        self._check_estimate(estimate)
        old_favourite, old_urls, old_estimate = self.favourite, self._fav_urls, self._store_estimate
        self.favourite = [*self.favourite, *additions]
        self._fav_urls = have
        self._store_estimate = estimate
        try:
            self._save()
        except Exception:
            self.favourite, self._fav_urls, self._store_estimate = old_favourite, old_urls, old_estimate
            raise
        return len(additions)

    def import_data(self, favourites=None, history=None) -> int:
        """Restore a backup: merge favourites (no-clobber) + history (dedup, capped).
        Returns how many NEW favourites were added."""
        plan = self.prepare_import(favourites, history)
        if not self.apply_import_plan(plan):
            raise RuntimeError("Favorites changed while the backup was being prepared; retry the import")
        return plan.favorites_added

    def preflight_import(self, favourites=None, history=None) -> tuple[int, int]:
        """Validate a complete backup merge without changing Favorites or History."""
        plan = self.prepare_import(favourites, history)
        return plan.favorites_added, plan.history_added

    def prepare_import(self, favourites=None, history=None) -> BookmarkImportPlan:
        """Normalize and budget a backup on a worker, without mutating saved lists."""
        baseline_favourite = self.favourite
        baseline_history = self.history
        have_fav = {r.get("url") for r in baseline_favourite}
        baseline_estimate = (
            self.config.estimate_station_lists(
                "bookmarks", {"favourite": baseline_favourite, "history": baseline_history}
            ) if self.config is not None else 0
        )
        if isinstance(favourites, list) and len(favourites) > MAX_ROWS:
            raise ValueError(f"Favorites import exceeds {MAX_ROWS:,} rows")
        if isinstance(history, list) and len(history) > MAX_ROWS:
            raise ValueError(f"History import exceeds {MAX_ROWS:,} rows")
        additions = []
        for row in station_rows(favourites or []):
            url = (row.get("url") or "").strip()
            if url and url not in have_fav:
                additions.append(dict(row))
                have_fav.add(url)
        if len(baseline_favourite) + len(additions) > MAX_ROWS:
            raise ValueError(
                f"Favorites import would exceed {MAX_ROWS:,} stations; "
                f"{len(baseline_favourite):,} are already saved"
            )

        have_history = {r.get("url") for r in baseline_history}
        fresh = [dict(r) for r in station_rows(history)
                 if r.get("url") and r.get("url") not in have_history]
        candidate_history = (fresh + baseline_history)[:HISTORY_MAX] if fresh else baseline_history
        candidate_favourites = baseline_favourite + additions if additions else baseline_favourite
        changed = bool(additions or fresh)
        estimate = (self._estimate(candidate_favourites, candidate_history)
                    if changed else baseline_estimate)
        return BookmarkImportPlan(
            baseline_favourite, baseline_history, candidate_favourites, candidate_history,
            frozenset(have_fav), estimate, len(additions), len(fresh), changed,
        )

    def plan_is_current(self, plan: BookmarkImportPlan) -> bool:
        return (isinstance(plan, BookmarkImportPlan)
                and self.favourite is plan.baseline_favourite
                and self.history is plan.baseline_history)

    def apply_import_plan(self, plan: BookmarkImportPlan) -> bool:
        """Apply a worker-prepared merge only if its captured lists are still current."""
        if not self.plan_is_current(plan):
            return False
        if not plan.changed:
            return True
        if self.config is not None:
            self.config.assert_store_writable("bookmarks")
        old_favourite, old_history, old_urls, old_estimate = (
            self.favourite, self.history, self._fav_urls, self._store_estimate
        )
        self.favourite = plan.candidate_favourite
        self.history = plan.candidate_history
        self._fav_urls = plan.favourite_urls
        self._store_estimate = plan.estimated_bytes
        try:
            self._save()
        except Exception:
            self.favourite, self.history, self._fav_urls, self._store_estimate = (
                old_favourite, old_history, old_urls, old_estimate
            )
            raise
        return True

    def remove(self, url: str) -> None:
        removed = [r for r in self.favourite if r.get("url") == url]
        if not removed:
            return
        if self.config is not None:
            self.config.assert_store_writable("bookmarks")
        old_favourite, old_urls, old_estimate = self.favourite, self._fav_urls, self._store_estimate
        self.favourite = [r for r in self.favourite if r.get("url") != url]
        self._fav_urls = set(self._fav_urls)
        self._fav_urls.discard(url)
        self._store_estimate -= sum(self._row_cost(r) for r in removed)
        try:
            self._save()
        except Exception:
            self.favourite, self._fav_urls, self._store_estimate = old_favourite, old_urls, old_estimate
            raise

    def toggle(self, row: dict) -> bool:
        url = row.get("url", "")
        if self.is_favourite(url):
            self.remove(url)
            return False
        self.add(row)
        return True

    def set_favicon(self, url: str, favicon_url: str) -> None:
        """Stamp a derived favicon onto a saved favourite that had none (icon-less stations
        get one in the background when you add them)."""
        if not valid_url(favicon_url, web=True):
            return
        updated = []
        delta = 0
        for row in self.favourite:
            if row.get("url") == url and not row.get("favicon"):
                replacement = dict(row, favicon=favicon_url)
                delta += self._row_cost(replacement) - self._row_cost(row)
                updated.append(replacement)
            else:
                updated.append(row)
        if delta or updated != self.favourite:
            estimate = self._store_estimate + delta
            self._check_estimate(estimate)
            old_favourite, old_estimate = self.favourite, self._store_estimate
            self.favourite = updated
            self._store_estimate = estimate
            try:
                self._save()
            except Exception:
                self.favourite, self._store_estimate = old_favourite, old_estimate
                raise

    # ---- history ----
    def add_history(self, row: dict) -> None:
        row = station_row(row)
        if row is None:
            return
        url = row.get("url")
        history = [r for r in self.history if r.get("url") != url]
        history.insert(0, dict(row))
        dropped = history[HISTORY_MAX:]
        del history[HISTORY_MAX:]
        removed = [r for r in self.history if r.get("url") == url]
        estimate = (self._store_estimate - sum(self._row_cost(r) for r in removed)
                    + self._row_cost(row) - sum(self._row_cost(r) for r in dropped))
        self._check_estimate(estimate)
        old_history, old_estimate = self.history, self._store_estimate
        self.history = history
        self._store_estimate = estimate
        try:
            self._save()
        except Exception:
            self.history, self._store_estimate = old_history, old_estimate
            raise

    # ---- channel contract ----
    def update_categories(self) -> list[str]:
        return ["Favorites", "History"]

    def update_streams(self, category: str, search: str | None = None) -> list[dict]:
        rows = self.history if category == "History" else self.favourite
        if search:
            s = search.lower()
            rows = [r for r in rows if s in r.get("title", "").lower()]
        # "Now Playing" is a LIVE value — clear any stale stored snapshot for display
        out = []
        for r in rows:
            c = dict(r)
            c["playing"] = ""
            out.append(c)
        return out

"""Local — your own hand-added streams (and, later, local media files).

Persists to ~/.config/streamtuner-ng/local.json. No network; never fails.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..base import Channel, make_row
from ...security import MAX_ROWS, station_row, station_rows


@dataclass(frozen=True)
class LocalImportPlan:
    baseline_rows: list[dict]
    candidate_rows: list[dict]
    estimated_bytes: int
    stations_added: int


class Local(Channel):
    id = "local"
    title = "Local"
    description = "Your own added streams."
    priority = "core"          # always on
    icon_emoji = "📁"          # your own added streams

    def __init__(self, config=None):
        super().__init__(config)
        self._rows: list[dict] = []
        if config is not None:
            data = config.load("local")
            self._rows = station_rows(data.get("stations")) if isinstance(data, dict) else []
        if config is not None:
            self._store_estimate = config.estimate_station_lists("local", {"stations": self._rows})
        else:
            self._store_estimate = 0

    def _save(self) -> None:
        if self.config is not None:
            self.config.save_station_snapshot("local", {"stations": self._rows})

    def add(self, title: str, url: str, **kw) -> dict:
        row = station_row(make_row(title, url, **kw))
        if row is None:
            raise ValueError("A supported network stream URL is required")
        previous = next((r for r in self._rows if r.get("url") == row["url"]), None)
        if previous is None and len(self._rows) >= MAX_ROWS:
            raise ValueError(f"Local supports at most {MAX_ROWS:,} stations")
        estimate = self._store_estimate
        if previous is not None and self.config is not None:
            estimate -= self.config.row_size_budget(previous)
        if self.config is not None:
            self.config.assert_store_writable("local")
            estimate += self.config.row_size_budget(row)
            if estimate > self.config.store_max_bytes:
                raise ValueError("Local stations would exceed the supported storage limit")
        old_rows, old_estimate = self._rows, self._store_estimate
        self._rows = [*filter(lambda r: r.get("url") != row["url"], self._rows), row]
        self._store_estimate = estimate
        try:
            self._save()
        except Exception:
            self._rows, self._store_estimate = old_rows, old_estimate
            raise
        return dict(row)

    def add_many(self, rows) -> int:
        """Merge rows in, skipping URLs already present (no clobber). Returns how many added."""
        plan = self.prepare_many(rows)
        if not self.apply_many_plan(plan):
            raise RuntimeError("Local stations changed while the import was being prepared; retry the import")
        return plan.stations_added

    def preflight_many(self, rows) -> int:
        """Validate a merged Local list without mutating or writing it."""
        return self.prepare_many(rows).stations_added

    def prepare_many(self, rows) -> LocalImportPlan:
        """Normalize and budget station rows on a worker, without changing Local."""
        baseline_rows = self._rows
        baseline_estimate = (
            self.config.estimate_station_lists("local", {"stations": baseline_rows})
            if self.config is not None else 0
        )
        if isinstance(rows, list) and len(rows) > MAX_ROWS:
            raise ValueError(f"Local import exceeds {MAX_ROWS:,} rows")
        have = {r.get("url") for r in baseline_rows}
        additions = []
        for r in station_rows(rows):
            u = (r.get("url") or "").strip()
            if not u or u in have:
                continue
            additions.append(dict(r))
            have.add(u)
        if not additions:
            return LocalImportPlan(baseline_rows, baseline_rows, baseline_estimate, 0)
        if len(baseline_rows) + len(additions) > MAX_ROWS:
            raise ValueError(
                f"Local import would exceed {MAX_ROWS:,} stations; "
                f"{len(baseline_rows):,} are already saved"
            )
        estimate = baseline_estimate
        if self.config is not None:
            self.config.assert_store_writable("local")
            estimate += sum(self.config.row_size_budget(r) for r in additions)
            if estimate > self.config.store_max_bytes:
                raise ValueError("Local import would exceed the supported storage limit")
        return LocalImportPlan(baseline_rows, [*baseline_rows, *additions], estimate, len(additions))

    def plan_is_current(self, plan: LocalImportPlan) -> bool:
        return isinstance(plan, LocalImportPlan) and self._rows is plan.baseline_rows

    def apply_many_plan(self, plan: LocalImportPlan) -> bool:
        """Apply a worker-prepared merge only if the captured Local list is current."""
        if not self.plan_is_current(plan):
            return False
        if not plan.stations_added:
            return True
        if self.config is not None:
            self.config.assert_store_writable("local")
        old_rows, old_estimate = self._rows, self._store_estimate
        self._rows = plan.candidate_rows
        self._store_estimate = plan.estimated_bytes
        try:
            self._save()
        except Exception:
            self._rows, self._store_estimate = old_rows, old_estimate
            raise
        return True

    def remove(self, url: str) -> None:
        removed = [r for r in self._rows if r.get("url") == url]
        if not removed:
            return
        if self.config is not None:
            self.config.assert_store_writable("local")
        old_rows, old_estimate = self._rows, self._store_estimate
        self._rows = [r for r in self._rows if r.get("url") != url]
        if self.config is not None:
            self._store_estimate -= sum(self.config.row_size_budget(r) for r in removed)
        try:
            self._save()
        except Exception:
            self._rows, self._store_estimate = old_rows, old_estimate
            raise

    def update_categories(self) -> list[str]:
        return ["My Stations"]

    def update_streams(self, category: str, search: str | None = None) -> list[dict]:
        if search:
            s = search.lower()
            return [dict(r) for r in self._rows if s in r.get("title", "").lower()]
        return [dict(r) for r in self._rows]

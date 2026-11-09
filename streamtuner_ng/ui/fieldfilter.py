"""Per-field filters for the station table: text contains / picked values, number from-to, date since.

Filters belong to a feed, last for the session, combine with each other and with the quick filter box,
and narrow the stations loaded so far. A filter spec is a small dict:
``{"contains": str}`` and/or ``{"values": [str]}`` (text), ``{"min": n, "max": n}`` (number), ``{"since": "YYYY-MM-DD"}`` (date).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from PySide6.QtCore import QSortFilterProxyModel

from ..timefmt import parse_utc

NUMBER_FIELDS = {"bitrate", "votes", "listeners"}
MAX_PICK = 300


def field_kind(model, field: str) -> str:
    """'number', 'date' or 'text' for a table field."""
    typed = model.column_type(field)
    if typed in ("int", "float"):
        return "number"
    if typed == "date":
        return "date"
    return "number" if field in NUMBER_FIELDS else "text"


def parse_number(text: str) -> float | None:
    try:
        return float(str(text).replace(",", "").strip())
    except ValueError:
        return None


def parse_day(text: str) -> datetime | None:
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d")
    except ValueError:
        return None


def clean_spec(kind: str, spec: dict | None) -> dict | None:
    """Drop empty parts; None when nothing is left to filter by."""
    if not spec:
        return None
    out: dict = {}
    if kind == "text":
        if str(spec.get("contains", "")).strip():
            out["contains"] = str(spec["contains"]).strip()
        values = [str(v) for v in spec.get("values", ()) if str(v)]
        if values:
            out["values"] = values
    elif kind == "number":
        for key in ("min", "max"):
            if isinstance(spec.get(key), (int, float)) and not isinstance(spec.get(key), bool):
                out[key] = float(spec[key])
    elif kind == "date":
        if parse_day(spec.get("since", "")) is not None:
            out["since"] = str(spec["since"]).strip()
    return out or None


def matches(model, src_row: int, field: str, kind: str, spec: dict) -> bool:
    row = model.row_at(src_row)
    if row is None:
        return False
    raw = model.filter_value(row, field)
    if kind == "text":
        text = str(raw if raw is not None else "").casefold()
        if "contains" in spec and spec["contains"].casefold() not in text:
            return False
        if "values" in spec and text not in {v.casefold() for v in spec["values"]}:
            return False
        return True
    if kind == "number":
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return False                                  # no value can't be inside a range
        if "min" in spec and number < spec["min"]:
            return False
        return not ("max" in spec and number > spec["max"])
    if kind == "date":
        moment = parse_utc(raw)
        since = parse_day(spec.get("since", ""))
        return moment is not None and since is not None and moment >= since
    return True


def value_counts(model, field: str) -> list[tuple[str, int]]:
    """The distinct values of a text field among the loaded stations, most common first."""
    counts: Counter = Counter()
    for r in range(model.rowCount()):
        row = model.row_at(r)
        value = str(model.filter_value(row, field) or "").strip()
        if value:
            counts[value] += 1
    return counts.most_common(MAX_PICK)


class StationProxy(QSortFilterProxyModel):
    """The table's sort/filter proxy: the quick filter box plus the active per-field filters."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.field_filters: dict[str, dict] = {}
        self._kinds: dict[str, str] = {}

    def set_field_filters(self, filters: dict[str, dict]) -> None:
        model = self.sourceModel()
        wanted = {f: s for f, s in filters.items() if s}
        kinds = {f: field_kind(model, f) for f in wanted} if model is not None else {}
        if wanted == self.field_filters and kinds == self._kinds:
            return
        self.field_filters, self._kinds = wanted, kinds
        self.invalidateFilter()

    def filterAcceptsRow(self, row: int, parent) -> bool:
        if not super().filterAcceptsRow(row, parent):
            return False
        model = self.sourceModel()
        return all(matches(model, row, field, self._kinds.get(field, "text"), spec)
                   for field, spec in self.field_filters.items())

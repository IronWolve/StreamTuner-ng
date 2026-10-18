"""Options dialog — the Plugins tab with the green-checkmark enable/disable that
writes straight to the single source of truth (host.state) and refreshes the UI.
Uses the app's standard styled dialog (bordered card + drop shadow).
"""

from __future__ import annotations

import threading
from concurrent.futures import CancelledError
from pathlib import Path

from ..security import MAX_IMPORT_BYTES, backup_bundle

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFontComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
    QSizePolicy,
)

from . import theme
from .dialogs import StyledDialog, info
from .worker import run_cache, run_io


class OptionsDialog(StyledDialog):
    changed = Signal()           # emitted when a plugin is enabled/disabled
    settings_changed = Signal()  # emitted when a general setting (e.g. favicons) changes
    theme_changed = Signal(str)  # emitted when a theme is picked/applied (theme id)
    visualization_changed = Signal(str)
    volume_changed = Signal(int)
    album_art_changed = Signal(bool)
    themes_reloaded = Signal()   # emitted when a theme is imported (the registry changed)
    cache_cleared = Signal()     # emitted when the disk cache is cleared (window drops its in-memory cache)
    cache_clearing = Signal()    # invalidate pending cache refreshes before the disk clear is queued
    icons_clearing = Signal()    # invalidate pending icon requests before the icon files are removed
    icons_cleared = Signal()     # emitted after icon files have been removed
    data_imported = Signal()     # emitted after a JSON backup is imported (window refreshes favourites/local)
    wallpaper_changed = Signal() # emitted when the wallpaper image / dim / on-off changes
    wallpaper_dim_changed = Signal(int) # opacity-only update; must not reload or rescale the image
    font_changed = Signal()      # emitted when the app font family/size changes

    def __init__(self, host, config=None, parent=None, mode: str = "dark"):
        super().__init__(parent, "Options", mode)
        self.host = host
        self.config = config
        if self.config is not None:
            self.config.enable_async_writes()
        self.muted = "#9aa3ad" if theme.is_dark(mode) else "#566070"   # secondary text, readable on dark/light
        self._closed = False
        self._operation_generation = 0
        self._usage_generation = 0
        self._data_cancel = None
        self.finished.connect(self._mark_closed)
        self.resize(740, 600)
        tabs = QTabWidget()
        tabs.addTab(self._scroll(self._plugins_tab()), "Plugins")
        tabs.addTab(self._scroll(self._themes_tab()), "Themes")
        tabs.addTab(self._scroll(self._general_tab()), "General")
        tabs.addTab(self._scroll(self._tray_tab()), "Tray")
        tabs.addTab(self._scroll(self._data_tab()), "Cache && Data")   # && -> literal "&" (not a mnemonic)
        self.body.addWidget(tabs)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        self.add_buttons(close)

    def _mark_closed(self, *_args) -> None:
        if self._closed:
            return
        self._closed = True
        self._operation_generation += 1
        if self._data_cancel is not None:
            self._data_cancel.set()

    def closeEvent(self, event) -> None:
        # QDialog.finished covers accept/reject/done; a window-manager close can
        # reach closeEvent directly, so cancel outstanding disk work there too.
        self._mark_closed()
        super().closeEvent(event)

    def _next_operation(self) -> int:
        self._operation_generation += 1
        return self._operation_generation

    @staticmethod
    def _error_text(result) -> str:
        return f"{type(result).__name__}: {result}" if isinstance(result, BaseException) else ""

    def _parent_is_closing(self) -> bool:
        return bool(getattr(self.parent(), "_closing", False))

    def _scroll(self, w: QWidget) -> QWidget:
        """Wrap a tab page so it scrolls when its content is taller than the dialog — otherwise a
        tall tab (e.g. on Windows, where the larger UI font overflows) clips its bottom rows, and
        the dialog would be forced past the screen height. Decouples content height from dialog size."""
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        sa.viewport().setAutoFillBackground(False)        # let the dialog card show through
        sa.setWidget(w)
        return sa

    def _general_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<b>Notifications</b>"))
        notify = QCheckBox("Show desktop notifications (now playing)")
        notify.setChecked(bool(self.config.get("notifications", True)) if self.config else True)
        notify.toggled.connect(self._toggle_notifications)
        v.addWidget(notify)
        nhint = QLabel("A desktop pop-up when you switch stations and when the track changes. "
                       "Uses the system tray, so it needs an OS notification service.")
        nhint.setStyleSheet(f"color: {self.muted};")
        nhint.setWordWrap(True)
        v.addWidget(nhint)

        v.addSpacing(8)
        v.addWidget(QLabel("<b>Discord</b>"))
        disc = QCheckBox("Discord rich presence (show what you're listening to on your profile)")
        disc.setChecked(bool(self.config.get("discord_rpc", False)) if self.config else False)
        disc.toggled.connect(self._toggle_discord)
        v.addWidget(disc)
        dhint = QLabel("Shows your now-playing station &amp; track on your Discord profile. "
                       "The Discord desktop app just needs to be running.")
        dhint.setStyleSheet(f"color: {self.muted};")
        dhint.setWordWrap(True)
        v.addWidget(dhint)

        v.addSpacing(8)
        v.addWidget(QLabel("<b>AudioAddict (DI.FM family)</b>"))
        aakey = QLineEdit((self.config.get("audioaddict_listen_key", "") if self.config else "") or "")
        aakey.setPlaceholderText("your DI.FM / AudioAddict listen key")
        aakey.setEchoMode(QLineEdit.Password)        # it's a credential — mask by default
        aakey.editingFinished.connect(lambda: self._set_aa_key(aakey.text()))
        reveal = QPushButton("Show")
        reveal.setCheckable(True)                    # lit = revealed
        reveal.setMaximumWidth(64)
        reveal.toggled.connect(lambda on: self._reveal_aa_key(aakey, reveal, on))
        krow = QHBoxLayout()
        krow.addWidget(aakey, 1)
        krow.addWidget(reveal)
        v.addLayout(krow)
        ahint = QLabel("<b>AudioAddict is subscriber-only.</b> Its old free 64k stream cluster was "
                       "discontinued, so these networks browse without a key but will not play. Paste your "
                       "subscriber listen key to play DI.FM / RadioTunes / JazzRadio / RockRadio / "
                       "ClassicalRadio / ZenRadio; right-click the channel for 320 kbps MP3, 128 kbps AAC, "
                       "or 64 kbps AAC. Get the key at di.fm -> your account -> <b>Listen Keys</b>.")
        ahint.setStyleSheet(f"color: {self.muted};")
        ahint.setWordWrap(True)
        v.addWidget(ahint)
        secret_hint = QLabel(self.config.secrets.status if self.config else "")
        secret_hint.setTextFormat(Qt.PlainText)
        secret_hint.setWordWrap(True)
        v.addWidget(secret_hint)
        transport_hint = QLabel("AudioAddict playback uses the provider's required HTTP transport.")
        transport_hint.setTextFormat(Qt.PlainText)
        transport_hint.setWordWrap(True)
        v.addWidget(transport_hint)
        v.addStretch(1)
        return w

    def _toggle_notifications(self, on: bool) -> None:
        if self.config is not None:
            self.config.set("notifications", on)
            self.config.save_settings()
        self.settings_changed.emit()

    def _toggle_discord(self, on: bool) -> None:
        if self.config is not None:
            self.config.set("discord_rpc", on)
            self.config.save_settings()
        self.settings_changed.emit()

    def _set_aa_key(self, text: str) -> None:
        if self.config is not None:
            self.config.set("audioaddict_listen_key", text.strip())
            self.config.save_settings()
        self.settings_changed.emit()

    def _reveal_aa_key(self, field: QLineEdit, btn: QPushButton, on: bool) -> None:
        field.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        btn.setText("Hide" if on else "Show")

    # ---------- Cache & Data tab ----------
    def _data_tab(self) -> QWidget:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        w = QWidget()
        v = QVBoxLayout(w)
        path = str(self.config.dir) if self.config else "(no config)"

        v.addWidget(QLabel("<b>Your data folder</b>"))
        pl = QLabel(path)
        pl.setTextFormat(Qt.PlainText)
        pl.setWordWrap(True)
        pl.setStyleSheet(f"color:{self.muted};")
        v.addWidget(pl)
        openb = QPushButton("Open folder")
        openb.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
        h0 = QHBoxLayout(); h0.addWidget(openb); h0.addStretch(1); v.addLayout(h0)

        v.addSpacing(10)
        v.addWidget(QLabel("<b>Disk usage</b>"))
        self._usage_lbl = QLabel()
        self._usage_lbl.setStyleSheet(f"color:{self.muted};")
        self._usage_lbl.setText("Calculating disk usage…")
        v.addWidget(self._usage_lbl)
        self._refresh_usage()

        v.addSpacing(10)
        v.addWidget(QLabel("<b>Station icons</b>"))
        from PySide6.QtWidgets import QButtonGroup
        cur = self.config.icon_mode() if self.config else "small"
        self._icon_mode_group = QButtonGroup(w)
        self._icon_mode_group.setExclusive(True)
        hmode = QHBoxLayout()
        for key, label in (("off", "Off"), ("full", "On"), ("small", "Downscaled")):
            b = QPushButton(label)
            b.setCheckable(True)                     # lit = active (theme highlight color)
            b.setChecked(key == cur)
            b.clicked.connect(lambda _=False, k=key: self._set_icon_mode(k))
            self._icon_mode_group.addButton(b)
            hmode.addWidget(b)
        hmode.addStretch(1)
        v.addLayout(hmode)
        ih = QLabel("<b>Off</b> — use each service's own logo (no station-icon fetching). "
                    "<b>On</b> — cache a larger PNG icon (up to 1024 pixels). "
                    "<b>Downscaled</b> — cache a 64-pixel thumbnail (keeps the art at a fraction of the "
                    "disk; a little one-time CPU per icon). Applies to newly-fetched icons — use "
                    "<i>Clear icon cache</i> below to re-fetch existing ones at the new size.")
        ih.setWordWrap(True); ih.setStyleSheet(f"color:{self.muted};")
        v.addWidget(ih)

        v.addSpacing(10)
        v.addWidget(QLabel("<b>Icon fetch threads</b>"))
        trow = QHBoxLayout()
        self._threads = QSlider(Qt.Horizontal)
        self._threads.setRange(4, 20)
        self._threads.setValue(int(self.config.get("io_threads", 4)) if self.config else 4)
        self._threads.setFixedWidth(180)
        self._threads_lbl = QLabel(str(self._threads.value()))
        self._threads.valueChanged.connect(self._set_io_threads)
        trow.addWidget(self._threads)
        trow.addWidget(self._threads_lbl)
        trow.addStretch(1)
        v.addLayout(trow)
        th = QLabel("How many station icons download at once (4–20). Higher fills icons in faster but "
                    "uses more network at once; lower is gentler on slow links. Default 4.")
        th.setWordWrap(True)
        th.setStyleSheet(f"color:{self.muted};")
        v.addWidget(th)

        v.addSpacing(10)
        v.addWidget(QLabel("<b>Maintenance</b>"))
        h1 = QHBoxLayout()
        self._clear_cache_button = QPushButton("Clear station cache")
        self._clear_cache_button.clicked.connect(self._clear_cache_clicked)
        self._clear_icons_button = QPushButton("Clear icon cache")
        self._clear_icons_button.clicked.connect(self._clear_icons_clicked)
        h1.addWidget(self._clear_cache_button); h1.addWidget(self._clear_icons_button); h1.addStretch(1)
        v.addLayout(h1)
        mh = QLabel("Cached station lists make relaunch instant; clearing just re-fetches them.")
        mh.setWordWrap(True); mh.setStyleSheet(f"color:{self.muted};")
        v.addWidget(mh)

        v.addSpacing(10)
        v.addWidget(QLabel("<b>Backup &amp; restore</b>"))
        h2 = QHBoxLayout()
        self._export_data_button = QPushButton("Export… (JSON)")
        self._export_data_button.clicked.connect(self._export_data_clicked)
        self._import_data_button = QPushButton("Import…")
        self._import_data_button.clicked.connect(self._import_data_clicked)
        h2.addWidget(self._export_data_button); h2.addWidget(self._import_data_button); h2.addStretch(1)
        v.addLayout(h2)
        bh = QLabel("Export saves your settings, favorites, history and local stations to one JSON "
                    "file (listen keys excluded). Import merges a backup back in — nothing is overwritten.")
        bh.setWordWrap(True); bh.setStyleSheet(f"color:{self.muted};")
        v.addWidget(bh)
        self._data_msg = QLabel("")
        self._data_msg.setWordWrap(True)
        self._data_msg.setStyleSheet(f"color:{self.muted};")
        v.addWidget(self._data_msg)

        v.addStretch(1)
        return w

    def _refresh_usage(self) -> None:
        if not self.config:
            return
        self._usage_generation += 1
        generation = self._usage_generation
        self._usage_lbl.setText("Calculating disk usage…")
        run_cache(self.config.data_usage,
                  lambda result, g=generation: self._usage_finished(g, result))

    def _usage_finished(self, generation: int, result) -> None:
        if self._closed or generation != self._usage_generation:
            return
        error = self._error_text(result)
        if error:
            self._usage_lbl.setText("Disk usage unavailable: " + self.config.redact(error))
            return

        def mb(n):
            return f"{n / 1048576:.1f} MB"
        self._usage_lbl.setText(f"Station cache: {mb(result['cache'])}  ({result['cache_files']} files)\n"
                                f"Icon cache: {mb(result['icons'])}\n"
                                f"Total: {mb(result['total'])}")

    def _clear_cache_clicked(self) -> None:
        if not self.config:
            return
        self._clear_cache_button.setEnabled(False)
        self._data_msg.setText("Clearing station cache…")
        self.cache_clearing.emit()
        run_cache(self.config.cache_clear_disk, self._cache_clear_finished)

    def _cache_clear_finished(self, result) -> None:
        error = self._error_text(result)
        if error:
            if not self._closed:
                self._clear_cache_button.setEnabled(True)
                self._data_msg.setText("Could not clear station cache: " + self.config.redact(error))
            return
        # MainWindow must invalidate every pending refresh before the operation starts, then
        # reload from the disk only after the serialized clear has actually completed.
        if not self._parent_is_closing():
            self.cache_cleared.emit()
        if not self._closed:
            self._clear_cache_button.setEnabled(True)
            self._data_msg.setText(f"Cleared {int(result):,} station-cache file(s).")
            self._refresh_usage()

    def _clear_icons_clicked(self) -> None:
        if not self.config:
            return
        self._clear_icons_button.setEnabled(False)
        self._data_msg.setText("Clearing icon cache…")
        self.icons_clearing.emit()
        run_cache(self.config.clear_icons, self._icons_clear_finished)

    def _icons_clear_finished(self, result) -> None:
        error = self._error_text(result)
        if error:
            if not self._closed:
                self._clear_icons_button.setEnabled(True)
                self._data_msg.setText("Could not clear icon cache: " + self.config.redact(error))
            return
        if not self._parent_is_closing():
            self.icons_cleared.emit()
        if not self._closed:
            self._clear_icons_button.setEnabled(True)
            self._data_msg.setText(f"Cleared {int(result):,} icon-cache file(s).")
            self._refresh_usage()

    def _set_icon_mode(self, mode: str) -> None:
        if self.config is not None:
            self.config.set("station_icon_mode", mode)
            self.config.save_settings()
        self.settings_changed.emit()     # window re-reads icon_mode; off<->on flips icons live

    def _set_io_threads(self, n: int) -> None:
        self._threads_lbl.setText(str(int(n)))
        if self.config is not None:
            self.config.set("io_threads", int(n))
            self.config.save_settings()
        from .worker import set_io_threads
        set_io_threads(int(n))           # resize the IO pool live

    def _export_data_clicked(self) -> None:
        import json
        import os
        import tempfile
        from PySide6.QtWidgets import QFileDialog
        if not self.config:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export settings & data",
                                              "streamtuner-ng-backup.json", "JSON (*.json)")
        if not path:
            return
        destination = Path(path)
        generation = self._begin_data_operation("Exporting backup…")
        cancelled = self._data_cancel

        def export() -> str:
            if cancelled.is_set():
                raise CancelledError()
            payload = self.config.export_bundle()
            if cancelled.is_set():
                raise CancelledError()
            fd, temporary = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp",
                                             dir=str(destination.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    encoder = json.JSONEncoder(indent=2, ensure_ascii=False)
                    for chunk in encoder.iterencode(payload):
                        if cancelled.is_set():
                            raise CancelledError()
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                if cancelled.is_set():
                    raise CancelledError()
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return str(destination)

        run_io(export, lambda result, g=generation: self._export_finished(g, result), cancelled=cancelled)

    def _begin_data_operation(self, message: str) -> int:
        generation = self._next_operation()
        self._data_cancel = threading.Event()
        self._export_data_button.setEnabled(False)
        self._import_data_button.setEnabled(False)
        self._data_msg.setText(message)
        return generation

    def _finish_data_operation(self, generation: int, message: str) -> bool:
        if self._closed or generation != self._operation_generation:
            return False
        self._export_data_button.setEnabled(True)
        self._import_data_button.setEnabled(True)
        self._data_msg.setText(message)
        return True

    def _export_finished(self, generation: int, result) -> None:
        error = self._error_text(result)
        if error:
            self._finish_data_operation(generation, "Export failed: " + self.config.redact(error))
        else:
            self._finish_data_operation(generation, f"Exported to {result}")

    def _import_data_clicked(self) -> None:
        import json
        from PySide6.QtWidgets import QFileDialog
        if not self.config:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import settings & data", "", "JSON (*.json)")
        if not path:
            return
        self._begin_data_operation("Reading and validating backup…")
        generation = self._operation_generation
        cancelled = self._data_cancel

        def parse_and_prepare():
            if cancelled.is_set():
                raise CancelledError()
            if Path(path).stat().st_size > MAX_IMPORT_BYTES:
                raise ValueError("Backup exceeds the import size limit")
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            if cancelled.is_set():
                raise CancelledError()
            data = backup_bundle(raw)
            if cancelled.is_set():
                raise CancelledError()
            return self._prepare_import_plans(data, cancelled)

        run_io(parse_and_prepare,
               lambda result, g=generation: self._import_prepared(g, result, cancelled),
               cancelled=cancelled)

    def _prepare_import_plans(self, data, cancelled):
        """Normalize and validate both potentially large station lists off the UI thread."""
        if cancelled.is_set():
            raise CancelledError()
        bm = self.host.channels.get("bookmarks") if self.host else None
        loc = self.host.channels.get("local") if self.host else None
        bookmark_plan = (bm.prepare_import(data["favourite"], data["history"])
                         if bm is not None else None)
        if cancelled.is_set():
            raise CancelledError()
        local_plan = loc.prepare_many(data["stations"]) if loc is not None else None
        if cancelled.is_set():
            raise CancelledError()
        return data, bookmark_plan, local_plan

    def _queue_import_replan(self, generation: int, data, cancelled, retries: int) -> None:
        if cancelled.is_set() or self._closed or generation != self._operation_generation:
            return
        self._data_msg.setText("Saved data changed during import; rechecking backup…")
        run_io(lambda: self._prepare_import_plans(data, cancelled),
               lambda result, g=generation, d=data, c=cancelled, n=retries:
                   self._import_prepared(g, result, c, n),
               cancelled=cancelled)

    def _import_prepared(self, generation: int, prepared, cancelled, retries: int = 0) -> None:
        error = self._error_text(prepared)
        if error:
            self._finish_data_operation(generation, "Import failed: " + self.config.redact(error))
            return
        if self._closed or generation != self._operation_generation:
            return
        data, bookmark_plan, local_plan = prepared
        self._data_msg.setText("Merging backup into saved data…")
        bm = self.host.channels.get("bookmarks") if self.host else None
        loc = self.host.channels.get("local") if self.host else None
        # Both snapshots must still match before either channel is changed. Preparation and
        # normalization run on the worker; this check and the small state swaps stay on UI.
        current = ((bm is None or bm.plan_is_current(bookmark_plan))
                   and (loc is None or loc.plan_is_current(local_plan)))
        if not current:
            if retries >= 4:
                self._finish_data_operation(
                    generation, "Import paused because Favorites or Local kept changing; please try again."
                )
            else:
                self._queue_import_replan(generation, data, cancelled, retries + 1)
            return
        try:
            nf = 0
            nl = 0
            bookmarks_current = bm is None or bm.apply_import_plan(bookmark_plan)
            local_current = loc is None or loc.apply_many_plan(local_plan)
            if not (bookmarks_current and local_current):
                if retries >= 4:
                    self._finish_data_operation(
                        generation, "Import paused because Favorites or Local kept changing; please try again."
                    )
                else:
                    self._queue_import_replan(generation, data, cancelled, retries + 1)
                return
            nf = bookmark_plan.favorites_added if bookmark_plan is not None else 0
            nl = local_plan.stations_added if local_plan is not None else 0
            # Save settings only after both list merges have passed their preflight and commit.
            self.config.import_settings(data["settings"])
        except Exception as exc:  # noqa: BLE001 — report storage/limit failures in the dialog
            self._finish_data_operation(generation, "Import failed: " + self.config.redact(exc))
            return
        self.theme_changed.emit(self.config.get("theme", "dark"))
        self.visualization_changed.emit(self.config.get("visualization", "off"))
        self.volume_changed.emit(self.config.get("volume", 70))
        self.album_art_changed.emit(bool(self.config.get("show_album_art", True)))
        self.settings_changed.emit()    # apply remaining general settings
        self.data_imported.emit()       # window drops cache + reloads so favourites/local show
        self._refresh_usage()
        self._data_msg.setText("Import merged; saving settings, Favorites and Local…")
        run_io(lambda: self.config.flush_writes(timeout=5),
               lambda result, g=generation, f=nf, l=nl: self._import_saved(g, f, l, result))

    def _import_saved(self, generation: int, favourites: int, stations: int, result) -> None:
        if self._closed or generation != self._operation_generation:
            return
        errors = self.config.take_write_errors()
        if errors:
            detail = "; ".join(errors[:2])
            self._finish_data_operation(
                generation, f"Import merged (+{favourites} favorites, +{stations} local), but saving failed: {detail}"
            )
            return
        if isinstance(result, BaseException) or not result:
            self._finish_data_operation(
                generation, f"Import merged (+{favourites} favorites, +{stations} local); save is not confirmed yet."
            )
            return
        self._finish_data_operation(
            generation, f"Imported +{favourites} favorites and +{stations} local stations; settings saved."
        )

    def _plugins_tab(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.addWidget(QLabel("<b>Channel plugins</b>  (checked = on · <i>icons</i> = load per-station stream icons)"),
                    0, 0, 1, 5)
        internal = ("bookmarks", "local")
        chans = self.host.channels
        ordered = [chans[c] for c in internal if c in chans]            # Favorites, Local pinned first
        ordered += sorted((ch for cid, ch in chans.items()
                           if cid not in internal and not ch.test_only),
                          key=lambda c: c.title.lower())                # then the services, A–Z
        self._plugin_visibility_checks = {}
        row = 1
        for ch in ordered:
            cid = ch.id
            is_internal = cid in internal
            dot = QLabel()
            if not is_internal:                  # internal channels are always on — no health dot
                dot.setPixmap(theme.status_icon(self.host.health[cid].status).pixmap(14, 14))
            cb = QCheckBox(ch.title)
            cb.setObjectName(f"pluginEnabled_{cid}")
            if is_internal:
                cb.setChecked(True)
                cb.setEnabled(False)             # locked on (built-in feature, not a toggleable plugin)
                cb.setToolTip("Always on")
            else:
                cb.setChecked(self.host.is_enabled(cid))
                cb.toggled.connect(lambda on, c=cid: self._toggle(c, on))
            cb.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            desc = QLabel(ch.description)
            desc.setStyleSheet(f"color: {self.muted};")
            desc.setWordWrap(True)
            desc.setMinimumWidth(0)
            desc.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            g.addWidget(dot, row, 0)
            g.addWidget(cb, row, 1)
            if not is_internal:
                ic = QCheckBox("icons")
                ic.setChecked(bool(self.config.get(f"stream_icons_{cid}", True)) if self.config else True)
                ic.setToolTip("Load each station's icon from its stream when available (if possible). "
                              "Off = just this service's logo for every station — handy on huge directories.")
                ic.toggled.connect(lambda on, c=cid: self._toggle_stream_icons(c, on))
                g.addWidget(ic, row, 2)
                visible = QCheckBox("Show in sidebar")
                visible.setObjectName(f"pluginVisible_{cid}")
                visible.setChecked(self.host.is_visible(cid))
                visible.setEnabled(self.host.is_enabled(cid))
                visible.toggled.connect(lambda on, c=cid: self._toggle_visibility(c, on))
                g.addWidget(visible, row, 3)
                self._plugin_visibility_checks[cid] = visible
                log = QPushButton("View Log")
                log.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                log.clicked.connect(lambda _=False, c=cid: self._view_log(c))
                g.addWidget(log, row, 4)
            g.addWidget(desc, row + 1, 1, 1, 4)
            row += 2
        g.setColumnStretch(1, 1)
        g.setColumnStretch(2, 0)
        g.setColumnStretch(3, 0)
        g.setColumnStretch(4, 0)
        g.setRowStretch(row, 1)
        return w

    def _toggle(self, cid: str, on: bool) -> None:
        self.host.set_enabled(cid, on)
        visibility = self._plugin_visibility_checks.get(cid)
        if visibility is not None:
            visibility.setEnabled(on)
        self.changed.emit()

    def _toggle_visibility(self, cid: str, on: bool) -> None:
        self.host.set_visible(cid, on)
        self.changed.emit()

    def _toggle_stream_icons(self, cid: str, on: bool) -> None:
        if self.config is not None:
            self.config.set(f"stream_icons_{cid}", bool(on))
            self.config.save_settings()
        self.settings_changed.emit()       # window re-evaluates the derive gate + repaints icons

    def _view_log(self, cid: str) -> None:
        h = self.host.health[cid]
        text = "\n".join(h.log) if h.log else "(no errors logged)"
        info(self, self.mode, f"{self.host.channels[cid].title} — error log", text)

    # ---- Themes tab ----
    def _themes_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<b>Theme</b>"))
        self._theme_combo = QComboBox()
        self._fill_theme_combo()
        self._theme_combo.currentIndexChanged.connect(self._on_theme_combo)
        v.addWidget(self._theme_combo)
        roww = QWidget()
        row = QHBoxLayout(roww)
        row.setContentsMargins(0, 0, 0, 0)
        b_imp = QPushButton("Import theme…")
        b_imp.clicked.connect(self._import_theme)
        b_exp = QPushButton("Export selected…")
        b_exp.clicked.connect(self._export_theme)
        b_fold = QPushButton("Open themes folder")
        b_fold.clicked.connect(self._open_themes_folder)
        for b in (b_imp, b_exp, b_fold):
            row.addWidget(b)
        row.addStretch(1)
        v.addWidget(roww)
        hint = QLabel("Themes are small JSON files (ten colors). Export one, edit the colors in any "
                      "text editor, then Import it back — or drop a .json into the themes folder. "
                      "Built-in themes can’t be overwritten.")
        hint.setStyleSheet(f"color: {self.muted};")
        hint.setWordWrap(True)
        v.addWidget(hint)

        v.addSpacing(8)
        v.addWidget(QLabel("<b>Font</b>"))
        from PySide6.QtGui import QFont
        fam, px = theme.app_font(self.config) if self.config else ("", 14)
        frow = QHBoxLayout()
        self._font_combo = QFontComboBox()
        if fam:
            self._font_combo.setCurrentFont(QFont(fam))
        self._font_combo.currentFontChanged.connect(self._on_font_family)
        self._font_size = QSpinBox()
        self._font_size.setRange(8, 28)
        self._font_size.setSuffix(" px")
        self._font_size.setValue(px)
        self._font_size.valueChanged.connect(self._on_font_size)
        b_fdef = QPushButton("System default")
        b_fdef.clicked.connect(self._reset_font)
        frow.addWidget(self._font_combo, 1)
        frow.addWidget(self._font_size)
        frow.addWidget(b_fdef)
        v.addLayout(frow)
        fhint = QLabel("Sets the font for the menus and the whole app — pick any installed system font. "
                       "<b>System default</b> clears it back to the OS font.")
        fhint.setStyleSheet(f"color: {self.muted};")
        fhint.setWordWrap(True)
        v.addWidget(fhint)

        v.addSpacing(8)
        v.addWidget(QLabel("<b>Wallpaper</b>"))
        self._wp_enable = QCheckBox("Show a wallpaper behind the station list")
        self._wp_enable.setChecked(bool(self.config.get("wallpaper_enabled", True)) if self.config else True)
        self._wp_enable.toggled.connect(self._toggle_wallpaper)
        v.addWidget(self._wp_enable)
        wprow = QHBoxLayout()
        wprow.addWidget(QLabel("Wallpaper:"))
        from .wallpaper import BUILTIN_WALLPAPERS
        self._wp_combo = QComboBox()
        for spec, label in BUILTIN_WALLPAPERS:
            self._wp_combo.addItem(label, spec)
        self._wp_combo.addItem("Custom image…", "__custom__")
        self._sync_wp_combo()
        self._wp_combo.activated.connect(self._wp_combo_changed)   # user picks (not programmatic) -> apply
        wprow.addWidget(self._wp_combo)
        wprow.addSpacing(10)
        wprow.addWidget(QLabel("Dim:"))
        self._wp_dim = QSlider(Qt.Horizontal)
        self._wp_dim.setRange(0, 90)
        self._wp_dim.setFixedWidth(110)
        self._wp_dim.setValue(int(self.config.get("wallpaper_dim", 35)) if self.config else 35)
        self._wp_dim.valueChanged.connect(self._set_wallpaper_dim)
        wprow.addWidget(self._wp_dim)
        wprow.addStretch(1)
        v.addLayout(wprow)
        self._wp_label = QLabel()
        self._wp_label.setTextFormat(Qt.PlainText)
        self._wp_label.setStyleSheet(f"color: {self.muted};")
        self._wp_label.setWordWrap(True)
        self._refresh_wp_label()
        v.addWidget(self._wp_label)
        return w

    def _refresh_wp_label(self) -> None:
        if not self.config:
            return
        import os
        cur = (self.config.get("wallpaper_image", "") or "").strip()
        if not cur:
            txt = ("Theme default — only Synthwave ships its own; other themes show none. "
                   "Pick Vaporwave 1/2 or your own image to use a wallpaper on any theme.")
        elif os.path.isabs(cur):
            txt = f"Custom image: {cur}"
        else:
            txt = f"Built-in wallpaper: <b>{cur}</b>"
        self._wp_label.setText(txt + " &nbsp; A higher <i>Dim</i> darkens it so the list stays readable.")

    def _toggle_wallpaper(self, on: bool) -> None:
        if self.config is not None:
            self.config.set("wallpaper_enabled", bool(on))
            self.config.save_settings()
        self.wallpaper_changed.emit()

    def _sync_wp_combo(self) -> None:
        """Point the combo at the stored wallpaper (an absolute custom path shows as 'Custom image…')."""
        if not self.config:
            return
        cur = self.config.get("wallpaper_image", "") or ""
        idx = self._wp_combo.findData(cur)
        if idx < 0:
            idx = self._wp_combo.findData("__custom__")
        self._wp_combo.blockSignals(True)
        self._wp_combo.setCurrentIndex(max(0, idx))
        self._wp_combo.blockSignals(False)

    def _wp_combo_changed(self, i: int) -> None:
        spec = self._wp_combo.itemData(i)
        if spec == "__custom__":
            from PySide6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(self, "Choose wallpaper image", "",
                                                  "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)")
            if not path:
                self._sync_wp_combo()            # cancelled -> snap back to the stored choice
                return
            spec = path
        if self.config is not None:
            self.config.set("wallpaper_image", spec)
            if spec:
                self.config.set("wallpaper_enabled", True)
            self.config.save_settings()
        if spec and hasattr(self, "_wp_enable"):
            self._wp_enable.blockSignals(True)
            self._wp_enable.setChecked(True)
            self._wp_enable.blockSignals(False)
        self._refresh_wp_label()
        self.wallpaper_changed.emit()

    def _set_wallpaper_dim(self, v: int) -> None:
        if self.config is not None:
            self.config.set("wallpaper_dim", int(v))
            self.config.save_settings()
        self.wallpaper_dim_changed.emit(int(v))

    def _fill_theme_combo(self) -> None:
        self._theme_combo.blockSignals(True)
        self._theme_combo.clear()
        cur = self.config.get("theme", "dark") if self.config else "dark"
        cur_idx = 0
        for i, (mode, label) in enumerate(theme.THEME_LABELS.items()):
            custom = mode not in theme.BUILTIN_THEMES
            self._theme_combo.addItem(label + ("  (custom)" if custom else ""), mode)
            if mode == cur:
                cur_idx = i
        self._theme_combo.setCurrentIndex(cur_idx)
        self._theme_combo.blockSignals(False)

    def _on_theme_combo(self, index: int) -> None:
        mode = self._theme_combo.itemData(index)
        if mode:
            self.theme_changed.emit(mode)

    def _on_font_family(self, qfont) -> None:
        if self.config is not None:
            self.config.set("app_font_family", qfont.family())
            self.config.save_settings()
        self.font_changed.emit()

    def _on_font_size(self, px: int) -> None:
        if self.config is not None:
            self.config.set("app_font_size", int(px))
            self.config.save_settings()
        self.font_changed.emit()

    def _reset_font(self) -> None:
        if self.config is not None:
            self.config.set("app_font_family", "")
            self.config.set("app_font_size", 14)
            self.config.save_settings()
        from PySide6.QtGui import QFont
        fam, px = theme.app_font(self.config) if self.config else ("", 14)
        self._font_combo.blockSignals(True)
        if fam:
            self._font_combo.setCurrentFont(QFont(fam))
        self._font_combo.blockSignals(False)
        self._font_size.blockSignals(True)
        self._font_size.setValue(px)
        self._font_size.blockSignals(False)
        self.font_changed.emit()

    def _export_theme(self) -> None:
        import json
        from PySide6.QtWidgets import QFileDialog
        mode = self._theme_combo.currentData()
        if not mode:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export theme", f"{mode}.json", "Theme JSON (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(theme.theme_export_dict(mode), f, indent=2)
        except OSError as e:
            info(self, self.mode, "Export failed", str(e))

    def _import_theme(self) -> None:
        import json
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Import theme", "",
                                              "Theme JSON (*.json);;All files (*)")
        if not path:
            return
        try:
            if Path(path).stat().st_size > 65536:
                raise ValueError("Theme exceeds the size limit")
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            tid = Path(path).stem or "custom"
            if tid in theme.BUILTIN_THEMES:            # never shadow a built-in theme
                tid += "-custom"
            theme.register_theme(tid, raw)             # validates -> raises ValueError on bad data
        except Exception as e:  # noqa: BLE001
            info(self, self.mode, "Import failed", f"That isn’t a valid theme file:\n\n{e}")
            return
        try:                                           # persist a normalized copy into the themes folder
            dest = theme.user_theme_dir(self.config) / f"{tid}.json"
            if dest.is_symlink():
                raise OSError("Refusing to overwrite a symlinked theme")
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(theme.theme_export_dict(tid), f, indent=2)
        except OSError:
            pass
        self.themes_reloaded.emit()                    # refresh the View → Theme menu
        self.theme_changed.emit(tid)                   # apply the freshly-imported theme
        self._fill_theme_combo()                       # now reflects it as the current selection

    def _open_themes_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(theme.user_theme_dir(self.config))))

    # ---- Tray tab ----
    def _tray_tab(self) -> QWidget:
        from PySide6.QtWidgets import QComboBox

        from .tray import DEFAULT_TRAY_ICON, TRAY_ICONS
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<b>System tray</b>"))
        en = QCheckBox("Show a tray icon")
        en.setChecked(bool(self.config.get("tray_enabled", True)) if self.config else True)
        en.toggled.connect(lambda on: self._tray_set("tray_enabled", on))
        v.addWidget(en)
        clo = QCheckBox("Close to tray  (✕ keeps it playing in the tray — Quit from the tray menu)")
        clo.setChecked(bool(self.config.get("tray_close", False)) if self.config else False)
        clo.toggled.connect(lambda on: self._tray_set("tray_close", on))
        v.addWidget(clo)
        roww = QWidget()
        h = QHBoxLayout(roww)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel("Tray icon:"))
        combo = QComboBox()
        for key, (emoji, label) in TRAY_ICONS.items():
            combo.addItem(f"{emoji}   {label}", key)
        cur = self.config.get("tray_icon", DEFAULT_TRAY_ICON) if self.config else DEFAULT_TRAY_ICON
        combo.setCurrentIndex(max(0, combo.findData(cur)))
        combo.currentIndexChanged.connect(lambda i, c=combo: self._tray_set("tray_icon", c.itemData(i)))
        h.addWidget(combo)
        h.addStretch(1)
        v.addWidget(roww)
        hint = QLabel("A music note is the default — a logo doesn't read at tray size.")
        hint.setStyleSheet(f"color: {self.muted};")
        hint.setWordWrap(True)
        v.addWidget(hint)
        v.addStretch(1)
        return w

    def _tray_set(self, key: str, value) -> None:
        if self.config is not None:
            self.config.set(key, value)
            self.config.save_settings()
        self.settings_changed.emit()

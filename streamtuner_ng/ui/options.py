"""Options dialog — the Plugins tab with the green-checkmark enable/disable that
writes straight to the single source of truth (host.state) and refreshes the UI.
Uses the app's standard styled dialog (bordered card + drop shadow).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from . import theme
from .dialogs import StyledDialog, info


class OptionsDialog(StyledDialog):
    changed = Signal()           # emitted when a plugin is enabled/disabled
    settings_changed = Signal()  # emitted when a general setting (e.g. favicons) changes
    theme_changed = Signal(str)  # emitted when a theme is picked/applied (theme id)
    themes_reloaded = Signal()   # emitted when a theme is imported (the registry changed)

    def __init__(self, host, config=None, parent=None, mode: str = "dark"):
        super().__init__(parent, "Options", mode)
        self.host = host
        self.config = config
        self.muted = "#9aa3ad" if theme.is_dark(mode) else "#566070"   # secondary text, readable on dark/light
        self.resize(580, 470)
        tabs = QTabWidget()
        tabs.addTab(self._plugins_tab(), "Plugins")
        tabs.addTab(self._themes_tab(), "Themes")
        tabs.addTab(self._general_tab(), "General")
        tabs.addTab(self._tray_tab(), "Tray")
        self.body.addWidget(tabs)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        self.add_buttons(close)

    def _general_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<b>Appearance</b>"))
        fav = QCheckBox("Show per-station favicons")
        fav.setChecked(bool(self.config.get("station_favicons", True)) if self.config else True)
        fav.toggled.connect(self._toggle_station_favicons)
        v.addWidget(fav)
        hint = QLabel("Off = use each service's logo for all of its stations (no per-station fetching).")
        hint.setStyleSheet(f"color: {self.muted};")
        hint.setWordWrap(True)
        v.addWidget(hint)

        v.addSpacing(8)
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
        aakey.editingFinished.connect(lambda: self._set_aa_key(aakey.text()))
        v.addWidget(aakey)
        ahint = QLabel("Required to play DI.FM / RadioTunes / JazzRadio / RockRadio / ClassicalRadio / "
                       "ZenRadio. Get it at di.fm → your account → <b>Listen Keys</b> (or copy the "
                       "<code>?…</code> from a .pls you download there), then tick the networks on the "
                       "Plugins tab.")
        ahint.setStyleSheet(f"color: {self.muted};")
        ahint.setWordWrap(True)
        v.addWidget(ahint)
        v.addStretch(1)
        return w

    def _toggle_station_favicons(self, on: bool) -> None:
        if self.config is not None:
            self.config.set("station_favicons", on)
            self.config.save_settings()
        self.settings_changed.emit()

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

    def _plugins_tab(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.addWidget(QLabel("<b>Channel plugins</b>  (checked = on)"), 0, 0, 1, 4)
        internal = ("bookmarks", "local")
        chans = self.host.channels
        ordered = [chans[c] for c in internal if c in chans]            # Favourites, Local pinned first
        ordered += sorted((ch for cid, ch in chans.items()
                           if cid not in internal and not ch.test_only),
                          key=lambda c: c.title.lower())                # then the services, A–Z
        row = 1
        for ch in ordered:
            cid = ch.id
            is_internal = cid in internal
            dot = QLabel()
            if not is_internal:                  # internal channels are always on — no health dot
                dot.setPixmap(theme.status_icon(self.host.health[cid].status).pixmap(14, 14))
            cb = QCheckBox(ch.title)
            if is_internal:
                cb.setChecked(True)
                cb.setEnabled(False)             # locked on (built-in feature, not a toggleable plugin)
                cb.setToolTip("Always on")
            else:
                cb.setChecked(self.host.is_enabled(cid))
                cb.toggled.connect(lambda on, c=cid: self._toggle(c, on))
            desc = QLabel(ch.description)
            desc.setStyleSheet(f"color: {self.muted};")
            g.addWidget(dot, row, 0)
            g.addWidget(cb, row, 1)
            g.addWidget(desc, row, 2)
            if not is_internal:
                log = QPushButton("View Log")
                log.clicked.connect(lambda _=False, c=cid: self._view_log(c))
                g.addWidget(log, row, 3)
            row += 1
        g.setColumnStretch(2, 1)
        g.setRowStretch(row, 1)
        return w

    def _toggle(self, cid: str, on: bool) -> None:
        self.host.set_enabled(cid, on)
        self.changed.emit()

    def _view_log(self, cid: str) -> None:
        h = self.host.health[cid]
        text = "\n".join(h.log) if h.log else "(no errors logged)"
        info(self, self.mode, f"{self.host.channels[cid].title} — error log", text)

    # ---- Themes tab ----
    def _themes_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<b>Theme</b>  (click one to apply it live)"))
        self._theme_list = QListWidget()
        self._fill_theme_list()
        self._theme_list.currentItemChanged.connect(self._on_theme_pick)
        v.addWidget(self._theme_list)
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
        hint = QLabel("Themes are small JSON files (ten colours). Export one, edit the colours in any "
                      "text editor, then Import it back — or drop a .json into the themes folder. "
                      "Built-in themes can’t be overwritten.")
        hint.setStyleSheet(f"color: {self.muted};")
        hint.setWordWrap(True)
        v.addWidget(hint)
        return w

    def _fill_theme_list(self) -> None:
        self._theme_list.blockSignals(True)
        self._theme_list.clear()
        cur = self.config.get("theme", "dark") if self.config else "dark"
        for mode, label in theme.THEME_LABELS.items():
            custom = mode not in theme.BUILTIN_THEMES
            it = QListWidgetItem(label + ("   (custom)" if custom else ""))
            it.setData(Qt.UserRole, mode)
            self._theme_list.addItem(it)
            if mode == cur:
                self._theme_list.setCurrentItem(it)
        self._theme_list.blockSignals(False)

    def _on_theme_pick(self, cur, _prev) -> None:
        if cur and cur.data(Qt.UserRole):
            self.theme_changed.emit(cur.data(Qt.UserRole))

    def _export_theme(self) -> None:
        import json
        from PySide6.QtWidgets import QFileDialog
        it = self._theme_list.currentItem()
        if not it:
            return
        mode = it.data(Qt.UserRole)
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
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            tid = Path(path).stem or "custom"
            if tid in theme.BUILTIN_THEMES:            # never shadow a built-in theme
                tid += "-custom"
            theme.register_theme(tid, raw)             # validates -> raises ValueError on bad data
        except Exception as e:  # noqa: BLE001
            info(self, self.mode, "Import failed", f"That isn’t a valid theme file:\n\n{e}")
            return
        try:                                           # persist a normalised copy into the themes folder
            dest = theme.user_theme_dir(self.config) / f"{tid}.json"
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(theme.theme_export_dict(tid), f, indent=2)
        except OSError:
            pass
        self.themes_reloaded.emit()                    # refresh the View → Theme menu
        self.theme_changed.emit(tid)                   # apply the freshly-imported theme
        self._fill_theme_list()                        # now reflects it as the current selection

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

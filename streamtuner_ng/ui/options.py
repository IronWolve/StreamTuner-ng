"""Options dialog — the Plugins tab with the green-checkmark enable/disable that
writes straight to the single source of truth (host.state) and refreshes the UI.
Uses the app's standard styled dialog (bordered card + drop shadow).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QSlider, QTabWidget, QVBoxLayout, QWidget,
)

from . import theme
from .dialogs import StyledDialog, info


class OptionsDialog(StyledDialog):
    changed = Signal()           # emitted when a plugin is enabled/disabled
    settings_changed = Signal()  # emitted when a general setting (e.g. favicons) changes
    theme_changed = Signal(str)  # emitted when a theme is picked/applied (theme id)
    themes_reloaded = Signal()   # emitted when a theme is imported (the registry changed)
    cache_cleared = Signal()     # emitted when the disk cache is cleared (window drops its in-memory cache)
    data_imported = Signal()     # emitted after a JSON backup is imported (window refreshes favourites/local)
    wallpaper_changed = Signal() # emitted when the wallpaper image / dim / on-off changes

    def __init__(self, host, config=None, parent=None, mode: str = "dark"):
        super().__init__(parent, "Options", mode)
        self.host = host
        self.config = config
        self.muted = "#9aa3ad" if theme.is_dark(mode) else "#566070"   # secondary text, readable on dark/light
        self.resize(620, 600)
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
        ih = QLabel("<b>Off</b> — use each service's own logo for all its stations (no fetching). "
                    "<b>On</b> — cache each station's icon full-size. "
                    "<b>Downscaled</b> — cache a small thumbnail (keeps the art at a fraction of the "
                    "disk; a little one-time CPU per icon). Applies to newly-fetched icons — use "
                    "<i>Clear icon cache</i> below to re-fetch existing ones at the new size.")
        ih.setWordWrap(True); ih.setStyleSheet(f"color:{self.muted};")
        v.addWidget(ih)

        v.addSpacing(10)
        v.addWidget(QLabel("<b>Maintenance</b>"))
        h1 = QHBoxLayout()
        b_cache = QPushButton("Clear station cache")
        b_cache.clicked.connect(self._clear_cache_clicked)
        b_icons = QPushButton("Clear icon cache")
        b_icons.clicked.connect(self._clear_icons_clicked)
        h1.addWidget(b_cache); h1.addWidget(b_icons); h1.addStretch(1)
        v.addLayout(h1)
        mh = QLabel("Cached station lists make relaunch instant; clearing just re-fetches them.")
        mh.setWordWrap(True); mh.setStyleSheet(f"color:{self.muted};")
        v.addWidget(mh)

        v.addSpacing(10)
        v.addWidget(QLabel("<b>Backup &amp; restore</b>"))
        h2 = QHBoxLayout()
        b_exp = QPushButton("Export… (JSON)")
        b_exp.clicked.connect(self._export_data_clicked)
        b_imp = QPushButton("Import…")
        b_imp.clicked.connect(self._import_data_clicked)
        h2.addWidget(b_exp); h2.addWidget(b_imp); h2.addStretch(1)
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
        u = self.config.data_usage()

        def mb(n):
            return f"{n / 1048576:.1f} MB"
        self._usage_lbl.setText(f"Station cache: {mb(u['cache'])}  ({u['cache_files']} files)\n"
                                f"Icon cache: {mb(u['icons'])}\n"
                                f"Total: {mb(u['total'])}")

    def _clear_cache_clicked(self) -> None:
        if not self.config:
            return
        self.config.cache_clear_disk()
        self.cache_cleared.emit()           # window drops its in-memory cache + reloads
        self._refresh_usage()

    def _clear_icons_clicked(self) -> None:
        if self.config:
            self.config.clear_icons()
            self._refresh_usage()

    def _set_icon_mode(self, mode: str) -> None:
        if self.config is not None:
            self.config.set("station_icon_mode", mode)
            self.config.save_settings()
        self.settings_changed.emit()     # window re-reads icon_mode; off<->on flips icons live

    def _export_data_clicked(self) -> None:
        import json
        from PySide6.QtWidgets import QFileDialog
        if not self.config:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export settings & data",
                                              "streamtuner-ng-backup.json", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.config.export_bundle(), f, indent=2, ensure_ascii=False)
            self._data_msg.setText(f"Exported to {path}")
        except OSError as e:
            self._data_msg.setText(f"Export failed: {e}")

    def _import_data_clicked(self) -> None:
        import json
        from PySide6.QtWidgets import QFileDialog
        if not self.config:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import settings & data", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            self._data_msg.setText("Couldn't read that file.")
            return
        if not isinstance(d, dict) or d.get("app") != "streamtuner-ng":
            self._data_msg.setText("That isn't a StreamTuner-ng backup file.")
            return
        # settings: merge (keeps current secrets like the listen key, which the backup omits)
        if isinstance(d.get("settings"), dict):
            self.config.settings.update(d["settings"])
            self.config.save_settings()
        nf = 0
        bm = self.host.channels.get("bookmarks") if self.host else None
        bmk = d.get("bookmarks")
        if bm is not None and isinstance(bmk, dict):
            nf = bm.import_data(bmk.get("favourite"), bmk.get("history"))
        nl = 0
        loc = self.host.channels.get("local") if self.host else None
        lcl = d.get("local")
        if loc is not None and lcl:
            rows = lcl.get("stations") if isinstance(lcl, dict) else lcl
            nl = loc.add_many(rows or [])
        self.settings_changed.emit()    # apply merged general settings
        self.data_imported.emit()       # window drops cache + reloads so favourites/local show
        self._refresh_usage()
        self._data_msg.setText(f"Imported +{nf} favorites and +{nl} local stations; settings merged.")

    def _plugins_tab(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.addWidget(QLabel("<b>Channel plugins</b>  (checked = on)"), 0, 0, 1, 4)
        internal = ("bookmarks", "local")
        chans = self.host.channels
        ordered = [chans[c] for c in internal if c in chans]            # Favorites, Local pinned first
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
        self._theme_list.setMinimumHeight(200)   # stay usable inside the scroll area (don't collapse)
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
        hint = QLabel("Themes are small JSON files (ten colors). Export one, edit the colors in any "
                      "text editor, then Import it back — or drop a .json into the themes folder. "
                      "Built-in themes can’t be overwritten.")
        hint.setStyleSheet(f"color: {self.muted};")
        hint.setWordWrap(True)
        v.addWidget(hint)

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
            txt = ("<b>Theme default</b> — only <b>Synthwave</b> ships its own; other themes show none. "
                   "Pick <b>Vaporwave 1/2</b> or your own image to use a wallpaper on any theme.")
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
            self._wp_enable.setChecked(True)
        self._refresh_wp_label()
        self.wallpaper_changed.emit()

    def _set_wallpaper_dim(self, v: int) -> None:
        if self.config is not None:
            self.config.set("wallpaper_dim", int(v))
            self.config.save_settings()
        self.wallpaper_changed.emit()

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
        try:                                           # persist a normalized copy into the themes folder
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

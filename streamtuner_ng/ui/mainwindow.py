"""The main window — three panes (channels -> categories -> stations), toolbar,
persistent player bar, status bar, menus. Thin: it drives host/player/config and
keeps the airlock off the UI thread via worker.run_async.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QSortFilterProxyModel, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QPushButton, QSlider, QSplitter, QTableView,
    QToolBar, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from ..player.mpv_player import clean_icy_title
from ..plugins.result import Result
from . import theme
from .models import COLUMNS, FILTER_ROLE, SORT_ROLE, StationModel
from .spectrogram import Spectrogram
from .vumeter import VuMeter
from .worker import run_async, run_io


class MainWindow(QMainWindow):
    nowplaying = Signal(str)   # marshals mpv's metadata callback onto the UI thread

    # Favorites + Local are INTERNAL features, not directory plugins: always pinned
    # at the top of the sidebar, and kept out of the enable/disable toggle lists.
    INTERNAL_CHANNELS = ("bookmarks", "local")
    _CACHE_STALE_S = 6 * 3600           # disk cache older than this -> freshen in the background
    _REFRESH_EVERY_MS = 20 * 60 * 1000  # periodic background refresh of the current category

    def __init__(self, config, host, player):
        super().__init__()
        self.config, self.host, self.player = config, host, player
        self.current_channel = ""
        self.current_category = ""
        self._np_full = ""
        self._np_meta = ""            # bitrate/codec suffix shown right after the title
        self._load_gen = 0           # bumps each category change; stale pages ignored
        self.ALL_LIMIT = 100000
        self._collapsed = set(self.config.get("collapsed_groups", []) or [])
        self._cache: dict[tuple, list] = {}   # (channel, category) -> full station list
        self._cat_cache: dict[str, list] = {}  # channel -> category list (cached on first visit)
        self._aa_key = (self.config.get("audioaddict_listen_key", "") or "").strip()  # detect key changes
        self._favicons: dict[str, str] = {}   # channel id -> cached favicon path
        self._svc_icons: dict = {}            # channel id -> QIcon, generic fallback for icon-less rows
        self._station_icons: dict = {}        # station favicon URL -> QIcon (None = in-flight)
        self._station_favicons = self.config.icon_mode() != "off"   # off -> service logo for all (full/small differ only on disk)
        self._playing_row: dict | None = None     # the row currently streaming (for the album-art box)
        self._show_art = bool(self.config.get("show_album_art", True))
        self._viz = self.config.get("visualization", "")
        if self._viz not in ("off", "vu", "spectrogram"):   # migrate the old show_vu bool
            self._viz = "vu" if self.config.get("show_vu", True) else "off"
        self._notifications = bool(self.config.get("notifications", True))  # OS now-playing toasts
        self._last_notified_title = ""        # de-dupe: only notify when the track title changes
        self._normalize = bool(self.config.get("normalize", False))   # loudness normalization (dynaudnorm)
        self._discord = None                  # Discord Rich Presence (created lazily when enabled)
        self._discord_pending = None          # throttle: latest queued presence (tuple, or "CLEAR")
        self._discord_last = 0.0              # monotonic time the last presence was actually sent
        self._song_history: list[dict] = []   # session log of ICY tracks (View → Song history)
        self._playing_row_url = ""            # url of the row currently streaming
        self._pending_filter = ""
        self._search_timer = QTimer(self)     # debounce search (smooth on huge lists)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_filter)
        self._icon_refresh = QTimer(self)     # coalesce lazy station-favicon repaints into one
        self._icon_refresh.setSingleShot(True)
        self._icon_refresh.timeout.connect(self._flush_icon_refresh)
        self.setWindowTitle(f"{APP_NAME}")
        self.resize(1060, 660)

        self._build_toolbar()
        self._build_panes()
        self._build_playerbar()
        self._build_menus()
        self.statusBar().showMessage("Ready")

        # permanent status-bar items: streaming bitrate/buffer (left) + cache count (right)
        self._stream_label = QLabel()
        self._cache_label = QLabel()
        self.statusBar().addPermanentWidget(self._stream_label)
        self.statusBar().addPermanentWidget(self._cache_label)
        self._playing_bitrate = 0
        self._update_cache_label()
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._update_stream_status)
        self._stats_timer.start(1000)
        self._vu_timer = QTimer(self)            # ~30 fps level-meter poll
        self._vu_timer.timeout.connect(self._tick_viz)
        self._vu_timer.start(33)
        self._bg_inflight: set = set()           # (cid, cat) currently being background-refreshed
        self._refresh_timer = QTimer(self)       # periodic freshening of the viewed category
        self._refresh_timer.timeout.connect(self._periodic_refresh)
        self._refresh_timer.start(self._REFRESH_EVERY_MS)
        self._discord_timer = QTimer(self)       # coalesce bursts of presence updates (rate limit)
        self._discord_timer.setSingleShot(True)
        self._discord_timer.timeout.connect(self._discord_flush)

        self.player.on_nowplaying = lambda t: self.nowplaying.emit(t)
        self.nowplaying.connect(self._on_nowplaying)

        self._populate_channels()
        self._load_channel_favicons()
        self._restore_layout()           # restore saved window size + column layout
        if not self.player.available:
            why = self.player.error or "libmpv not found"
            self.statusBar().showMessage(f"No audio engine — {why}")
            self._stream_label.setToolTip(why)        # hover the ⚠ for the full reason
        self.player.set_normalize(self._normalize)   # apply the saved loudness-normalization setting
        self._setup_shortcuts()
        self._setup_media_keys()
        self._apply_appearance(self.config.get("theme", "dark"))   # apply wallpaper (if any) over the base theme

    # ---- construction ----
    def _build_toolbar(self) -> None:
        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)
        self.act_reload = QAction("Reload", self, triggered=self._reload)
        self.act_fav = QAction("★ Favorite", self, checkable=True, triggered=self._toggle_favourite)
        self.act_play = QAction("Play", self, triggered=self._play_selected)
        self.act_stop = QAction("Stop", self, triggered=self._stop)
        tb.addAction(self.act_reload)
        tb.addAction(self.act_fav)
        tb.addSeparator()                       # | between Favorite and the transport
        tb.addAction(self.act_play)
        tb.addAction(self.act_stop)
        tb.addSeparator()                       # | between transport and Search
        tb.addWidget(QLabel(" Search: "))
        self.search = QLineEdit()
        self.search.setPlaceholderText("filter…  (Enter = search the directory)")
        self.search.setClearButtonEnabled(True)   # ✕ button to clear the filter
        self.search.setMaximumWidth(240)
        self.search.textChanged.connect(self._on_search)        # type = instant client-side filter
        self.search.returnPressed.connect(self._search_enter)   # Enter = server-side directory search
        tb.addWidget(self.search)
        self.btn_search_all = QPushButton("All Sources")
        self.btn_search_all.setCheckable(True)                  # lit = Enter searches every source at once
        self.btn_search_all.setToolTip("When lit, Enter searches every search-capable source and merges results")
        tb.addWidget(self.btn_search_all)

    def _build_panes(self) -> None:
        self.channels = QListWidget()
        self.channels.currentItemChanged.connect(self._on_channel)
        self.channels.setContextMenuPolicy(Qt.CustomContextMenu)
        self.channels.customContextMenuRequested.connect(self._channel_menu)

        self.categories = QListWidget()
        self.categories.currentItemChanged.connect(self._on_category)
        self.categories.itemDoubleClicked.connect(self._reload_category)

        self.model = StationModel()
        bm = self.host.channels.get("bookmarks")
        self.model.fav_provider = bm.is_favourite if bm else None
        self.model.icon_provider = self._station_icon
        self.proxy = QSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(0)
        self.proxy.setFilterRole(FILTER_ROLE)    # search matches name + genre + country
        self.proxy.setSortRole(SORT_ROLE)        # numeric columns sort numerically
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionsMovable(True)                        # drag headers to reorder
        hdr.setSectionResizeMode(QHeaderView.Interactive)   # drag edges to resize
        hdr.setStretchLastSection(True)                     # last column fills the gap
        # Station Genre Bitrate Codec Listeners Votes Country Description NowPlaying URL
        for col, w in enumerate((270, 110, 65, 65, 80, 75, 130, 200, 200, 320)):
            self.table.setColumnWidth(col, w)
        self.table.doubleClicked.connect(self._play_selected)
        self.table.clicked.connect(self._on_station_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._station_menu)

        split = QSplitter(Qt.Horizontal)
        self._services_pane = self._titled_pane("Services", self.channels)
        split.addWidget(self._services_pane)
        # album-art / station-logo box: floats at the bottom-left of the Services pane (overlay,
        # never reflows the list or the splitter); sized to its image.
        self.art = QLabel(self._services_pane)
        self.art.setObjectName("artBox")
        self.art.setAlignment(Qt.AlignCenter)
        self.art.hide()
        self._services_pane.installEventFilter(self)
        split.addWidget(self._titled_pane("Categories", self.categories))
        split.addWidget(self.table)         # already has column headers
        split.setStretchFactor(2, 1)
        split.setSizes([180, 200, 660])

        from .wallpaper import WallpaperWidget
        central = WallpaperWidget()           # paints the optional wallpaper behind everything
        self._wallpaper_widget = central
        lay = QVBoxLayout(central)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(split, 1)
        self._central_layout = lay
        self.setCentralWidget(central)

    def _titled_pane(self, title: str, widget) -> QWidget:
        """Wrap a pane with a header label (so the Services/Categories columns are
        labelled, matching the station table's column headers)."""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        header = QLabel(title)
        header.setObjectName("paneHeader")
        v.addWidget(header)
        v.addWidget(widget)
        return box

    # ---- album-art box (floats bottom-left of the Services pane) ----
    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is getattr(self, "_services_pane", None) and ev.type() == QEvent.Resize:
            self._position_art()
        return super().eventFilter(obj, ev)

    def _position_art(self) -> None:
        pm = self.art.pixmap()
        if self.art.isHidden() or pm.isNull():
            return
        m = 6
        y = self._services_pane.height() - pm.height() - m
        self.art.setGeometry(m, max(0, y), pm.width(), pm.height())
        self.art.raise_()

    def _update_art(self) -> None:
        """Now-playing artwork in the bottom-left box. Shows the source service's logo immediately,
        then upgrades async to the station's OWN image: the directory's per-station favicon if the
        row carries one, else the broadcaster's real logo derived from the stream's ICY `icy-url` —
        so a Shoutcast station shows its own site's favicon, not the generic Shoutcast logo.
        v2 will prefer the song's real cover art (Radio Paradise / Local mp3)."""
        if not self._show_art or self._playing_row is None:
            self.art.hide()
            return
        row = self._playing_row
        token = row.get("url", "")                     # token: ignore stale async results
        self._art_token = token
        pm = self._service_pixmap(row)                 # immediate fallback (service logo; may be None)
        if pm is not None and not pm.isNull():
            self._show_art_pixmap(pm)
        else:
            self.art.hide()                            # nothing local yet — wait for the fetch
        from ..favicons import best_station_icon          # cached-derived > directory favicon > derive
        run_io(lambda r=dict(row): best_station_icon(self.config, r),
               lambda path, t=token: self._apply_art(t, path))

    def _apply_art(self, token, path) -> None:
        if not self._show_art or self._playing_row is None:
            return
        if token != getattr(self, "_art_token", ""):
            return                                     # a different stream started — stale result
        from PySide6.QtGui import QPixmap
        pm = QPixmap(path) if path else QPixmap()
        if not pm.isNull():
            self._show_art_pixmap(pm)
        elif self.art.pixmap().isNull():
            self.art.hide()

    def _show_art_pixmap(self, pm) -> None:
        floor, cap = 56, 140                           # tiny favicons get a visible floor; big art caps
        w = pm.width()
        if w > cap:
            pm = pm.scaledToWidth(cap, Qt.SmoothTransformation)
        elif 0 < w < floor:
            pm = pm.scaledToWidth(floor, Qt.SmoothTransformation)
        self.art.setPixmap(pm)
        self.art.resize(pm.size())
        self.art.show()
        self._position_art()                           # geometry + raise above the list, after show()

    def _service_pixmap(self, row):
        """Fallback art = the source service's logo (native size)."""
        from PySide6.QtGui import QPixmap
        cid = (row or {}).get("_source") or self.current_channel
        path = self._favicons.get(cid)
        if path:
            pm = QPixmap(path)
            if not pm.isNull():
                return pm
        return None

    def _toggle_art(self, on: bool) -> None:
        self._show_art = on
        self.config.set("show_album_art", on)
        self.config.save_settings()
        self._update_art()

    def _set_visualization(self, mode: str) -> None:
        self._viz = mode
        self.config.set("visualization", mode)
        self.config.save_settings()
        self.vu.setVisible(mode == "vu")
        self.spectro.setVisible(mode == "spectrogram")
        if mode == "spectrogram":
            self.spectro.reset_width()
        for m, a in self._viz_actions.items():
            a.setChecked(m == mode)

    def _build_playerbar(self) -> None:
        bar = QWidget()
        bar.setObjectName("playerBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 4, 8, 4)
        self.btn_play = QPushButton("▶")
        self.btn_play.setCheckable(True)
        self.btn_play.setFixedWidth(40)
        self.btn_play.setToolTip("Play / Stop")
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_play.toggled.connect(lambda on: self.btn_play.setText("■" if on else "▶"))
        # (no station icon here — the now-playing artwork lives in the bottom-left art box)
        self.np_label = QLabel("Stopped")
        self.np_label.setMinimumWidth(200)
        self.np_label.setContextMenuPolicy(Qt.CustomContextMenu)        # right-click the track for options
        self.np_label.customContextMenuRequested.connect(self._nowplaying_menu)
        self.np_label.setToolTip("Right-click for track options (copy, info, website)")
        self.player.set_volume(self.config.get("volume", self.player.volume))   # restore saved volume
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setFixedWidth(120)
        self.vol.setRange(0, 100)
        self.vol.setValue(self.player.volume)
        self.vol.valueChanged.connect(self.player.set_volume)
        self.vol.valueChanged.connect(lambda v: self.config.set("volume", v))   # saved on close
        self.vu = VuMeter()                          # live stereo level meter
        self.vu.setVisible(self._viz == "vu")
        self.spectro = Spectrogram()                 # alternative viz — same slot, resizable
        self.spectro.setVisible(self._viz == "spectrogram")
        self.btn_norm = QPushButton("Norm")          # lit green when on (theme QPushButton:checked)
        self.btn_norm.setCheckable(True)
        self.btn_norm.setChecked(self._normalize)
        self.btn_norm.setToolTip("Normalize loudness — even out the volume between stations")
        self.btn_norm.toggled.connect(self._toggle_normalize)
        h.addWidget(self.btn_play)
        h.addSpacing(10)                        # breathing room between ▶ and the now-playing text
        h.addWidget(self.np_label, 1)
        h.addWidget(self.vu)
        h.addWidget(self.spectro)
        h.addWidget(self._vsep())               # | between the visualization and the volume
        h.addWidget(QLabel("Vol"))
        h.addWidget(self.vol)
        h.addWidget(self.btn_norm)
        self._central_layout.insertWidget(0, bar)   # top, right under the toolbar

    def _vsep(self) -> QWidget:
        """A thin vertical divider for the player bar (the QHBoxLayout can't use
        QToolBar.addSeparator, so this matches it with a 1px grey line)."""
        from PySide6.QtWidgets import QFrame, QSizePolicy
        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(6, 3, 6, 3)
        line = QFrame()
        line.setFixedWidth(1)
        line.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        line.setStyleSheet("background: rgb(120,120,120);")
        lay.addWidget(line)
        return holder

    def _build_menus(self) -> None:
        import sys
        mb = self.menuBar()
        if sys.platform.startswith("linux"):
            # draw the menu bar in-window — skip any native/global-menu (DBus) detour.
            # macOS keeps its proper top-of-screen native bar; Windows is in-window anyway.
            mb.setNativeMenuBar(False)
        m_file = mb.addMenu("&File")
        m_file.addAction(QAction("Open Location…", self, shortcut="Ctrl+L", triggered=self._open_location))
        m_file.addAction(QAction("New Local Station…", self, triggered=self._add_local))
        m_file.addSeparator()
        m_file.addAction(QAction("Quit", self, shortcut="Ctrl+Q", triggered=self.close))

        m_view = mb.addMenu("&View")
        m_view.addAction(QAction("Reload current channel", self, shortcut="F5", triggered=self._reload))
        self._art_action = QAction("Show album art", self, checkable=True)
        self._art_action.setChecked(self._show_art)
        self._art_action.triggered.connect(self._toggle_art)
        m_view.addAction(self._art_action)
        viz_menu = m_view.addMenu("Visualization")
        self._viz_actions = {}
        for mode, label in (("off", "Off"), ("vu", "VU Meter"), ("spectrogram", "Spectrogram")):
            a = QAction(label, self, checkable=True)
            a.setChecked(self._viz == mode)
            a.triggered.connect(lambda _=False, m=mode: self._set_visualization(m))
            viz_menu.addAction(a)
            self._viz_actions[mode] = a
        m_view.addAction(QAction("Song history…", self, triggered=self._song_history_dialog))
        m_view.addSeparator()
        self._theme_menu = m_view.addMenu("Theme")
        self._rebuild_theme_menu()
        self._wallpaper_menu = m_view.addMenu("Wallpaper")
        self._rebuild_wallpaper_menu()

        self.m_channels = mb.addMenu("&Channels")
        self._rebuild_channels_menu()

        m_play = mb.addMenu("&Playback")
        m_play.addAction(self.act_play)
        m_play.addAction(self.act_stop)
        m_play.addSeparator()
        self.act_normalize = QAction("Normalize loudness", self, checkable=True)
        self.act_normalize.setChecked(self._normalize)
        self.act_normalize.toggled.connect(self._toggle_normalize)
        m_play.addAction(self.act_normalize)

        m_tools = mb.addMenu("&Tools")
        m_tools.addAction(QAction("Options…", self, shortcut="Ctrl+,", triggered=self._open_options))
        m_tools.addAction(QAction("Open Plugins Folder…", self, triggered=self._open_plugins_folder))

        m_help = mb.addMenu("&Help")
        m_help.addAction(QAction("About", self, triggered=self._about))

    # ---- channel sidebar ----
    def _populate_channels(self) -> None:
        self.channels.blockSignals(True)
        self.channels.clear()
        chans = list(self.host.shown_channels())
        # Favorites + Local are internal — always pinned at the top from the full
        # registry, regardless of their enabled/visible state (so they can never
        # vanish from a stale config). Then a divider, then the streaming services A–Z.
        top = [self.host.channels[cid] for cid in self.INTERNAL_CHANNELS
               if cid in self.host.channels]
        rest = sorted((c for c in chans if c.id not in self.INTERNAL_CHANNELS),
                      key=lambda c: c.title.lower())

        def add(ch) -> None:
            it = QListWidgetItem(ch.title)
            it.setData(Qt.UserRole, ch.id)
            it.setIcon(self._channel_icon(ch.id))
            self.channels.addItem(it)

        for ch in top:
            add(ch)
        if top and rest:
            self._add_channel_separator()
        for ch in rest:
            add(ch)
        self.channels.blockSignals(False)
        for i in range(self.channels.count()):   # select the first real (non-separator) channel
            if self.channels.item(i).data(Qt.UserRole):
                self.channels.setCurrentRow(i)
                break

    def _add_channel_separator(self) -> None:
        """A thin divider line in the Services list (separates Favorites/Local from the services)."""
        from PySide6.QtCore import QSize
        from PySide6.QtWidgets import QFrame
        sep = QListWidgetItem()
        sep.setFlags(Qt.NoItemFlags)              # non-selectable; no UserRole -> skipped on click
        sep.setSizeHint(QSize(0, 9))
        self.channels.addItem(sep)
        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(8, 4, 8, 4)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgb(120,120,120);")
        lay.addWidget(line)
        self.channels.setItemWidget(sep, holder)

    def _rebuild_channels_menu(self) -> None:
        self.m_channels.clear()
        chans = self.host.channels
        internal = [chans[c] for c in self.INTERNAL_CHANNELS if c in chans]   # Favorites, Local first
        services = sorted((ch for cid, ch in chans.items()
                           if cid not in self.INTERNAL_CHANNELS and not ch.test_only),
                          key=lambda c: c.title.lower())                       # then services A–Z

        def add(ch, locked: bool) -> None:
            a = QAction(ch.title, self, checkable=True)
            if locked:                                   # internal feature: always on, can't toggle
                a.setChecked(True)
                a.setEnabled(False)
            else:
                a.setChecked(self.host.is_visible(ch.id) and self.host.is_enabled(ch.id))
                a.toggled.connect(lambda on, c=ch.id: self._set_visible(c, on))
            self.m_channels.addAction(a)

        for ch in internal:
            add(ch, True)
        if internal and services:
            self.m_channels.addSeparator()              # divider, mirroring the sidebar
        for ch in services:
            add(ch, False)
        self.m_channels.addSeparator()
        self.m_channels.addAction(QAction("Manage Plugins…", self, triggered=self._open_options))

    def _set_visible(self, cid: str, on: bool) -> None:
        self.host.set_visible(cid, on)
        self._populate_channels()

    def _refresh_dot(self, cid: str) -> None:
        self._set_channel_icon(cid)

    # ---- channel icons (service favicon + health badge) ----
    def _channel_icon(self, cid: str):
        ch = self.host.channels.get(cid)
        return theme.channel_icon(self._favicons.get(cid), emoji=getattr(ch, "icon_emoji", ""))

    def _set_channel_icon(self, cid: str) -> None:
        for i in range(self.channels.count()):
            it = self.channels.item(i)
            if it.data(Qt.UserRole) == cid:
                it.setIcon(self._channel_icon(cid))
                break

    def _on_favicon(self, cid: str, path) -> None:
        if isinstance(path, str) and path:
            self._favicons[cid] = path
            self._svc_icons.pop(cid, None)    # rebuild the generic fallback now that the logo is in
            self._set_channel_icon(cid)
            self.model.refresh_icons()        # paint generic icons for any icon-less rows on screen

    def _load_channel_favicons(self) -> None:
        from ..favicons import ensure_favicon
        for ch in self.host.channels.values():
            if ch.test_only or not getattr(ch, "homepage", "") or ch.id in self._favicons:
                continue
            cid = ch.id
            run_io(lambda c=ch: ensure_favicon(self.config, c),
                   lambda path, cid=cid: self._on_favicon(cid, path))

    def _channel_menu(self, pos) -> None:
        item = self.channels.itemAt(pos)
        if not item or not item.data(Qt.UserRole):
            return
        cid = item.data(Qt.UserRole)
        ch = self.host.channels.get(cid)
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        home = getattr(ch, "homepage", "")
        if home:
            menu.addAction("Open Website", lambda: QDesktopServices.openUrl(QUrl(home)))
        if cid == "bookmarks":                   # Favorites: export favourites OR history; import (merge)
            menu.addAction("Export Favorites… (CSV)",
                           lambda: self._export_rows(getattr(ch, "favourite", []), "favorites.csv"))
            menu.addAction("Export History… (CSV)",
                           lambda: self._export_rows(getattr(ch, "history", []), "history.csv"))
            menu.addAction("Import to Favorites… (CSV)", self._import_favourites)
        elif cid == "local":                     # Local: export your own added streams
            menu.addAction("Export list… (CSV)",
                           lambda: self._export_rows(getattr(ch, "_rows", []), "local-stations.csv"))
        # plugin-declared right-click options (bitrate, login, …) from the channel's options_spec
        spec = getattr(ch, "options_spec", None) or []
        if spec and self.config is not None:
            menu.addSeparator()
            for opt in spec:
                self._add_channel_option(menu, opt)
        menu.addAction("Options…", self._open_options)
        menu.addAction("Refresh Icon", lambda: self._refresh_favicon(cid))
        menu.exec(self.channels.mapToGlobal(pos))

    def _add_channel_option(self, menu, opt) -> None:
        """Render one entry from a channel's options_spec into the right-click menu.
        'choice' -> an exclusive (lit-check) submenu; 'secret' -> a masked Set/Change dialog."""
        from PySide6.QtGui import QAction, QActionGroup
        typ, key, label = opt.get("type"), opt.get("key", ""), opt.get("label", "")
        if typ == "choice":
            sub = menu.addMenu(label or "Quality")
            grp = QActionGroup(sub)
            grp.setExclusive(True)
            cur = self.config.get(key, opt.get("default", ""))
            for clabel, cval in opt.get("choices", []):
                a = QAction(clabel, sub)
                a.setCheckable(True)
                a.setChecked(cval == cur)
                a.triggered.connect(lambda _=False, k=key, v=cval, lb=clabel: self._set_channel_option(k, v, lb))
                grp.addAction(a)
                sub.addAction(a)
        elif typ == "secret":
            verb = "Change" if (self.config.get(key, "") or "") else "Set"
            menu.addAction(f"{verb} {label}…",
                           lambda k=key, lb=label, ph=opt.get("placeholder", ""): self._edit_channel_secret(k, lb, ph))

    def _set_channel_option(self, key: str, value, label: str = "") -> None:
        self.config.set(key, value)
        self.config.save_settings()
        self.statusBar().showMessage(f"Selected: {label}" if label else "Saved", 3000)

    def _edit_channel_secret(self, key: str, label: str, placeholder: str = "") -> None:
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        cur = self.config.get(key, "") or ""
        text, ok = QInputDialog.getText(self, label, f"{label}:", QLineEdit.Password, cur)
        if ok:
            self.config.set(key, text.strip())
            self.config.save_settings()
            self.statusBar().showMessage(f"{label} saved", 3000)

    def _export_rows(self, rows, suggested: str) -> None:
        """Save a station list to CSV (used by the right-click Export actions)."""
        import csv
        from PySide6.QtWidgets import QFileDialog
        rows = list(rows or [])
        if not rows:
            self.statusBar().showMessage("Nothing to export — the list is empty.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export list", suggested, "CSV files (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        cols = [("title", "Title"), ("url", "URL"), ("genre", "Genre"), ("country", "Country"),
                ("bitrate", "Bitrate"), ("format", "Format"), ("homepage", "Homepage")]
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                wr = csv.writer(f)
                wr.writerow([h for _k, h in cols])
                for r in rows:
                    wr.writerow([r.get(k, "") for k, _h in cols])
            self.statusBar().showMessage(f"Exported {len(rows)} station(s) → {path}")
        except OSError as e:
            self.statusBar().showMessage(f"Export failed: {e}")

    def _import_favourites(self) -> None:
        """Right-click Favorites → Import a CSV and MERGE it in (URLs already saved are
        skipped — no clobber). Sanity-checks the file: it must have a URL column, and each
        row needs a real URL (scheme://…); malformed rows are counted and ignored."""
        import csv
        from PySide6.QtWidgets import QFileDialog

        from ..plugins.base import make_row
        path, _ = QFileDialog.getOpenFileName(self, "Import to Favorites", "",
                                              "CSV files (*.csv);;All files (*)")
        if not path:
            return
        ch = self.host.channels.get("bookmarks")
        parsed: list[dict] = []
        skipped = bad = 0
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
                if "url" not in headers:                # not one of our exports / wrong format
                    self.statusBar().showMessage(
                        "Import failed: that CSV has no 'URL' column. Export a list first to "
                        "see the format (URL required; Title, Genre, … optional).")
                    return
                for d in reader:
                    row = {(k or "").strip().lower(): (v or "").strip()
                           for k, v in d.items() if k}  # ignore ragged / extra columns
                    url = row.get("url", "")
                    if "://" not in url or len(url) < 7:  # must look like a real stream URL
                        bad += 1
                        continue
                    if ch.is_favourite(url):
                        skipped += 1
                        continue
                    br = row.get("bitrate", "")
                    parsed.append(make_row(
                        title=row.get("title") or url, url=url, genre=row.get("genre", ""),
                        country=row.get("country", ""), homepage=row.get("homepage", ""),
                        bitrate=int(br) if br.isdigit() else 0, format=row.get("format", ""),
                    ))
        except (OSError, csv.Error, UnicodeDecodeError) as e:
            self.statusBar().showMessage(f"Import failed: {e}")
            return
        added = ch.add_many(parsed)
        self._cache.pop(("bookmarks", "Favorites"), None)    # favourites changed -> drop stale cache
        if self.current_channel == "bookmarks" and self.current_category == "Favorites":
            self._on_category(self.categories.currentItem(), None)
        parts = [f"imported {added}"]
        if skipped:
            parts.append(f"skipped {skipped} already saved")
        if bad:
            parts.append(f"ignored {bad} with no/invalid URL")
        self.statusBar().showMessage("Favorites: " + ", ".join(parts) + ".")

    def _refresh_favicon(self, cid: str) -> None:
        for p in self.config.icon_dir.glob(f"chan_{cid}.*"):
            try:
                p.unlink()
            except OSError:
                pass
        self._favicons.pop(cid, None)
        self._svc_icons.pop(cid, None)
        ch = self.host.channels.get(cid)
        from ..favicons import ensure_favicon
        run_io(lambda: ensure_favicon(self.config, ch),
               lambda path: self._on_favicon(cid, path))

    # ---- station favicons (artwork in the list; small lists only) ----
    def _station_icon(self, row):
        fav = row.get("favicon")
        if fav and self._station_favicons:       # off -> always the service logo
            if fav in self._station_icons:
                ic = self._station_icons[fav]
                if ic is not None:
                    return ic                    # already loaded
            else:
                self._request_station_icon(fav)  # lazy: fetch on first paint (visible rows only)
        return self._service_icon_for(row)       # service logo until/unless artwork loads   # no station artwork -> the source service's logo

    def _service_icon_for(self, row):
        """A station with no favicon of its own falls back to its source service's icon (e.g.
        Shoutcast stations show the Shoutcast logo). Built once per service from the cached
        channel favicon — one shared QIcon, so it costs nothing per row."""
        cid = row.get("_source") or self.current_channel
        if cid in self._svc_icons:
            return self._svc_icons[cid]
        icon = None
        path = self._favicons.get(cid)
        if path:
            from PySide6.QtGui import QIcon, QPixmap
            pm = QPixmap(path)
            if not pm.isNull():
                icon = QIcon(pm)
        self._svc_icons[cid] = icon
        return icon

    def _load_station_favicons(self) -> None:
        # Small lists: pre-load every row's artwork up front. Big lists (TuneIn All ~2.7k, etc.)
        # load LAZILY as rows scroll into view (via _station_icon) — never a fetch storm.
        if not self._station_favicons:
            return
        rows = self.model.rows()
        if len(rows) > 200:
            return
        for r in rows:
            u = r.get("favicon")
            if u and u not in self._station_icons:
                self._request_station_icon(u)

    def _request_station_icon(self, url: str) -> None:
        """Fetch one station's favicon (disk-cached -> once-only) and repaint when it arrives."""
        self._station_icons[url] = None              # mark in-flight (don't refetch / retry)
        from ..favicons import station_favicon
        run_io(lambda: station_favicon(self.config, url),
               lambda path, u=url: self._on_station_favicon(u, path))

    def _on_station_favicon(self, url, path) -> None:
        if isinstance(path, str) and path:
            from PySide6.QtGui import QIcon
            ic = QIcon(path)
            if not ic.isNull():
                self._station_icons[url] = ic
                self._icon_refresh.start(150)        # debounce: one repaint per burst of loads

    def _flush_icon_refresh(self) -> None:
        self.model.refresh_icons()

    # ---- loading flow (all async through the airlock) ----
    def _on_channel(self, cur, _prev) -> None:
        if not cur or not cur.data(Qt.UserRole):   # ignore group headers
            return
        self.current_channel = cur.data(Qt.UserRole)
        self.search.clear()          # each source starts unfiltered (no stale filter)
        cid = self.current_channel
        self._refresh_dot(cid)
        cached = self._cat_cache.get(cid)
        if cached is None and cid not in self.INTERNAL_CHANNELS:   # disk cat cache -> network-free next launch
            disk = self.config.cache_load(cid, "__categories__")
            if disk is not None:
                cached = disk[0]
                self._cat_cache[cid] = cached
        if cached is not None:       # category list already known -> instant, no network
            self.categories.clear()
            self._fill_categories(cid, cached)
            return
        self.categories.clear()
        self.categories.addItem("Loading…")
        run_async(lambda: self.host.categories(cid), lambda r: self._on_categories(cid, r))

    def _on_categories(self, cid: str, result) -> None:
        if cid != self.current_channel:
            return
        self._refresh_dot(cid)
        self.categories.clear()
        if isinstance(result, Result) and result.data:
            cats = [str(c) for c in result.data]
            self._cat_cache[cid] = cats               # cache the category list for this channel
            if cid not in self.INTERNAL_CHANNELS:
                self.config.cache_save(cid, "__categories__", cats)   # + persist for a network-free launch
            self._fill_categories(cid, cats)
        else:
            msg = result.message if isinstance(result, Result) else str(result)
            self._status_error(f"{cid}: {msg}")

    def _fill_categories(self, cid: str, cats: list) -> None:
        ch = self.host.channels.get(cid)
        cats = list(cats)
        if getattr(ch, "category_sort", True):        # A–Z by default, pins kept on top
            pins = [c for c in getattr(ch, "category_pins", ()) if c in cats]
            rest = sorted((c for c in cats if c not in pins), key=str.lower)
            cats = pins + rest
        lcat = getattr(ch, "listeners_category", "")  # inject "★ With Listeners" right under ★ All
        if lcat:
            all_cat = getattr(ch, "all_category", "")
            cats = [c for c in cats if c != lcat]
            cats.insert(cats.index(all_cat) + 1 if all_cat in cats else len(cats), lcat)
        for c in cats:
            self.categories.addItem(c)
        self.categories.setCurrentRow(0)

    def _on_category(self, cur, _prev) -> None:
        if not cur or cur.text() == "Loading…":
            return
        self.search.clear()          # each category view starts on the full list
        self.current_category = cur.text()
        self._apply_default_sort()   # e.g. Shoutcast opens sorted by live listeners (active first)
        cid, cat = self.current_channel, self.current_category
        self._load_gen += 1          # invalidate any in-flight pages from a prior pick
        gen = self._load_gen
        if cat == getattr(self.host.channels.get(cid), "listeners_category", ""):
            self._show_listeners(cid, gen)   # derived view: the All pool filtered to listeners > 0
            return
        cached = self._cache.get((cid, cat))
        if cached is None and cid not in self.INTERNAL_CHANNELS:   # internal lists are live/local — never disk-cached
            disk = self.config.cache_load(cid, cat)
            if disk is not None:
                cached, age = disk
                self._cache[(cid, cat)] = cached
                self._update_cache_label()
                if age > self._CACHE_STALE_S:       # show it now, freshen it in the background
                    self._bg_refresh(cid, cat)
        if cached is not None:       # instant from the lazy / disk cache
            rows = self._union(cid) if self._is_all(cid, cat) else cached
            self.model.set_rows(rows)
            self._apply_column_visibility()
            self._load_station_favicons()
            self.statusBar().showMessage(f"Loaded {len(rows)} stations (cached)")
            return
        self.model.set_rows([])
        self.statusBar().showMessage(f"Loading {cat}…")
        ch = self.host.channels.get(cid)
        first = getattr(ch, "page_size", None) or self.ALL_LIMIT
        self._load_page(cid, cat, 0, first, gen)

    def _apply_default_sort(self) -> None:
        """Apply the current channel's default table sort (Shoutcast → by listeners, descending),
        or clear sorting to the source order for channels that don't set one. The proxy keeps the
        order as rows stream in, so a progressively-loaded list stays sorted."""
        ch = self.host.channels.get(self.current_channel)
        field = getattr(ch, "default_sort", "") if ch else ""
        if field:
            col = next((i for i, (_lbl, key) in enumerate(COLUMNS) if key == field), -1)
            if col >= 0:
                self.table.sortByColumn(col, Qt.DescendingOrder)
                return
        self.proxy.sort(-1)          # no per-channel default → natural (source/insertion) order
        self.table.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)

    def _show_listeners(self, cid: str, gen: int) -> None:
        """The '★ With Listeners' view = the channel's All pool filtered to stations with a live
        listener count (>0). Reuses the loaded All data (instant); loads All first if needed."""
        ch = self.host.channels.get(cid)
        all_cat = getattr(ch, "all_category", "")
        if self._cache.get((cid, all_cat)) is not None:    # All already loaded -> filter now
            self._render_listeners(cid)
        else:                                              # need the full list first
            self.model.set_rows([])
            self.statusBar().showMessage("Loading the full list to find stations with listeners…")
            first = getattr(ch, "page_size", None) or self.ALL_LIMIT
            self._load_page(cid, all_cat, 0, first, gen)   # _finish_load filters when it completes

    def _render_listeners(self, cid: str) -> None:
        rows = [r for r in self._union(cid) if (r.get("listeners") or 0) > 0]
        rows.sort(key=lambda r: r.get("listeners") or 0, reverse=True)
        self.model.set_rows(rows)
        self._apply_column_visibility()
        self._load_station_favicons()
        self.statusBar().showMessage(f"{len(rows):,} stations with listeners")

    def _load_page(self, cid, cat, offset, limit, gen) -> None:
        tmo = 240 if self._is_all(cid, cat) else 45    # the ALL sweep (Shoutcast 313 genres) needs longer
        run_async(
            lambda: self.host.call(cid, "update_streams_page", cat, offset, limit, None, timeout=tmo),
            lambda r: self._on_page(cid, cat, offset, gen, r),
        )

    def _on_page(self, cid, cat, offset, gen, result) -> None:
        if gen != self._load_gen:
            return                    # user moved on; ignore this stale page
        self._refresh_dot(cid)
        ch = self.host.channels.get(cid)
        if not (isinstance(result, Result) and result.ok):
            if offset == 0:
                self.model.set_rows([])
            msg = result.message if isinstance(result, Result) else str(result)
            self._status_error(f"{cid}: {msg}")
            return
        rows = result.data or []
        if offset == 0:
            self.model.set_rows(rows)        # keep the user's column widths/order
        else:
            new = rows
            if self._is_all(cid, cat):       # ALL sweeps overlap across genres -> dedup as we go
                have = {r.get("url") for r in self.model.rows()}
                new = [r for r in rows if r.get("url") not in have]
            self.model.append_rows(new)
        total = self.model.rowCount()
        self._apply_column_visibility()
        # Shoutcast ★ All Stations pages in by genre-chunk — keep going while chunks return rows
        if getattr(ch, "all_chunked", False) and self._is_all(cid, cat):
            if rows:
                self.statusBar().showMessage(f"Loaded {total:,} stations… loading more genres")
                self._load_page(cid, cat, offset + 1, self.ALL_LIMIT, gen)
            else:
                self._finish_load(cid, cat, ch)
            return
        psize = getattr(ch, "page_size", None)
        if offset == 0 and psize and len(rows) >= psize:
            # full first page -> stream in the remainder behind it
            self.statusBar().showMessage(f"Loaded {total:,} stations… loading the rest")
            self._load_page(cid, cat, psize, self.ALL_LIMIT, gen)
        else:
            self._finish_load(cid, cat, ch)

    def _finish_load(self, cid: str, cat: str, ch) -> None:
        """Loading complete -> cache the rows. For ALL, fold in any separately-browsed
        categories (the unified cache) — but only rebuild the list if that actually ADDS rows,
        so a big ALL doesn't pointlessly reset/scroll at the very end."""
        rows = self.model.rows()
        self._cache[(cid, cat)] = rows
        if cid not in self.INTERNAL_CHANNELS:        # don't persist live/local lists (Favorites, Local)
            run_io(lambda r=list(rows): self.config.cache_save(cid, cat, r), lambda *_: None)  # async
        if self._is_all(cid, cat):
            union = self._union(cid)
            if len(union) > self.model.rowCount():
                self.model.set_rows(union)
                self._apply_column_visibility()
        self._update_cache_label()
        self._load_station_favicons()
        total = self.model.rowCount()
        self.statusBar().showMessage(
            "No stations found" if total == 0 else f"Loaded {total:,} stations from {ch.title}")
        if cat == getattr(ch, "all_category", "") and \
                self.current_category == getattr(ch, "listeners_category", ""):
            self._render_listeners(cid)      # loaded All for the "★ With Listeners" view -> filter it

    def _is_all(self, cid: str, cat: str) -> bool:
        """Is `cat` this channel's 'everything' category (its view = the union pool)?"""
        ch = self.host.channels.get(cid)
        return bool(getattr(ch, "all_category", None)) and cat == ch.all_category

    def _union(self, cid: str) -> list:
        """Every station cached for this channel, deduped by URL — so the ALL view is the
        sweep PLUS every category you've browsed (the unified cache)."""
        seen: dict = {}
        for (c, _cat), rows in self._cache.items():
            if c == cid:
                for r in rows:
                    u = r.get("url")
                    if u and u not in seen:
                        seen[u] = r
        return list(seen.values())

    def _apply_column_visibility(self) -> None:
        """Hide columns the current rows never fill — show only what this service uses
        (Station always stays visible). Re-run after each load."""
        rows = self.model.rows()
        if not rows:
            return
        for col, (_label, field) in enumerate(COLUMNS):
            if field == "title":
                self.table.setColumnHidden(col, False)
                continue
            used = any(r.get(field) not in (None, "", 0) for r in rows)
            self.table.setColumnHidden(col, not used)

    # ---- playback ----
    def _selected_row(self) -> dict | None:
        idx = self.table.currentIndex()
        if not idx.isValid():
            return None
        return self.model.row_at(self.proxy.mapToSource(idx).row())

    def _on_station_clicked(self, index) -> None:
        # selecting a station shows its details (name · bitrate/codec · URL) in the status bar
        row = self.model.row_at(self.proxy.mapToSource(index).row())
        if not row:
            return
        parts = [row["title"]]
        if row.get("bitrate"):
            parts.append(f"{row['bitrate']}k {row.get('format', '').replace('audio/', '')}".strip())
        if row.get("url"):
            parts.append(row["url"])
        self.statusBar().showMessage("  ·  ".join(parts))

    def _station_menu(self, pos) -> None:
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        row = self.model.row_at(self.proxy.mapToSource(idx).row())
        if not row:
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QApplication, QMenu
        menu = QMenu(self)
        menu.addAction("Play", lambda: (self.table.setCurrentIndex(idx), self._play_selected()))
        bm = self.host.channels.get("bookmarks")
        fav = bool(bm and bm.is_favourite(row.get("url", "")))
        menu.addAction("Remove favorite" if fav else "★ Favorite",
                       lambda: (self.table.setCurrentIndex(idx), self._toggle_favourite()))
        menu.addSeparator()
        menu.addAction("Copy URL", lambda: QApplication.clipboard().setText(row.get("url", "")))
        menu.addAction("Info…", lambda: self._station_info(row))
        menu.addAction("Recache icon", lambda: self._recache_station_icon(row))
        if row.get("homepage"):
            menu.addAction("Open Website",
                           lambda: QDesktopServices.openUrl(QUrl(row["homepage"])))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _station_info(self, row: dict) -> None:
        from .dialogs import station_info
        station_info(self, self.config.get("theme", "dark"), row)

    def _nowplaying_menu(self, pos) -> None:
        """Right-click the now-playing text on the player bar: copy the track / stream URL, station
        info, open the broadcaster's site — all acting on the row that's actually streaming."""
        row = self._playing_row
        if not row:
            return                                   # nothing playing -> no menu
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QApplication, QMenu
        menu = QMenu(self)
        track = clean_icy_title(self._np_full or "")
        if track:
            menu.addAction("Copy playing track", lambda: QApplication.clipboard().setText(track))
        menu.addAction("Copy URL", lambda: QApplication.clipboard().setText(row.get("url", "")))
        menu.addSeparator()
        menu.addAction("Info…", lambda: self._station_info(row))
        if row.get("homepage"):
            menu.addAction("Open Website", lambda: QDesktopServices.openUrl(QUrl(row["homepage"])))
        menu.exec(self.np_label.mapToGlobal(pos))

    def _recache_station_icon(self, row: dict) -> None:
        """Force-refresh this station's art-box icon: clear its cached files and re-fetch the
        best logo (prefers the broadcaster's apple-touch-icon). Reports the result + pixel size."""
        title = row.get("title", "station")
        self.statusBar().showMessage(f"Re-caching icon for {title}…")
        from ..favicons import best_station_icon
        run_io(lambda r=dict(row): best_station_icon(self.config, r, refetch=True),
               lambda path, r=dict(row): self._after_recache(r, path))

    def _after_recache(self, row: dict, path) -> None:
        from PySide6.QtGui import QIcon, QPixmap
        title = row.get("title", "station")
        if isinstance(path, str) and path:
            pm = QPixmap(path)
            sz = f"{pm.width()}×{pm.height()}" if not pm.isNull() else "?"
            fav = row.get("favicon", "")
            if fav and not pm.isNull():                       # refresh the list row's icon too
                self._station_icons[fav] = QIcon(path)
                self._icon_refresh.start(50)
            if self._playing_row and self._playing_row.get("url") == row.get("url"):
                self._art_token = self._playing_row.get("url", "")
                self._apply_art(self._art_token, path)        # update the now-playing box live
            self.statusBar().showMessage(f"Re-cached icon for {title}  ({sz})")
        else:
            self.statusBar().showMessage(f"No broadcaster logo found for {title}")

    def _play_selected(self, *_a) -> None:
        row = self._selected_row()
        if not row:
            return
        src = self._resolve_source(row)
        ch = self.host.channels.get(src)
        if ch is not None and getattr(ch, "needs_resolve", False):
            # row from a channel that needs URL resolution (e.g. TuneIn) — incl. favourites
            self.statusBar().showMessage(f"Resolving {row['title']}…")
            run_async(lambda: self.host.call(src, "resolve_url", row),
                      lambda res: self._start_playback(row, res))
        else:
            self._start_playback(row, None)

    def _resolve_source(self, row: dict) -> str:
        """Which channel resolves this row's URL: its origin if known (favourites/history
        remember it), else the current channel — with a fallback for legacy TuneIn links."""
        src = row.get("_source")
        if src and src in self.host.channels:
            return src
        if "opml.radiotime.com" in row.get("url", "") and "tunein" in self.host.channels:
            return "tunein"
        return self.current_channel

    def _start_playback(self, row: dict, resolved) -> None:
        url = row["url"]
        if isinstance(resolved, Result) and resolved.ok and isinstance(resolved.data, dict):
            url = resolved.data.get("url") or url
        self._playing_bitrate = row.get("bitrate", 0)
        self._playing_row_url = row.get("url", "")
        self._np_full = row["title"]
        self._np_meta = ""                    # filled with the stream's REAL bitrate/codec once playing
        self._elide_np()
        self._playing_row = row
        self._update_art()
        self.btn_play.setChecked(True)
        self.act_play.setEnabled(True)
        ok = self.player.play(url)
        bm = self.host.channels.get("bookmarks")
        if bm:
            hist = dict(row)
            hist.setdefault("_source", self._resolve_source(row))
            bm.add_history(hist)
        self.act_fav.setChecked(bool(bm and bm.is_favourite(row["url"])))
        if ok:
            self.statusBar().showMessage(f"Playing {row['title']}")
            self._last_notified_title = ""        # so the first track on this station notifies too
            self._notify(row["title"], row.get("genre") or row.get("description") or "Now playing", row)
            self._discord_update(row["title"], row.get("genre") or "Internet radio", reset=True)
        elif not self.player.available:
            self.statusBar().showMessage(f"Selected {row['title']} (no audio engine here)")

    def _toggle_play(self) -> None:
        if self.btn_play.isChecked():
            self._play_selected()
        else:
            self._stop()

    def _toggle_normalize(self, on) -> None:
        """Loudness normalization on/off — applies the mpv filter live, persists, and keeps the
        player-bar button and the Playback menu item in sync (echo-guarded)."""
        on = bool(on)
        if on == self._normalize:
            return                               # already there (ignores the button<->menu echo)
        self._normalize = on
        self.player.set_normalize(on)
        self.config.set("normalize", on)
        self.config.save_settings()
        for wdg in (getattr(self, "btn_norm", None), getattr(self, "act_normalize", None)):
            if wdg is not None:
                wdg.blockSignals(True)
                wdg.setChecked(on)
                wdg.blockSignals(False)
        self.statusBar().showMessage("Loudness normalization " + ("on" if on else "off"))

    def _stop(self, *_a) -> None:
        self.player.stop()
        self._playing_row_url = ""
        self.btn_play.setChecked(False)
        self.np_label.setText("Stopped")
        self._update_tray_np("")
        self._np_meta = ""
        self._last_notified_title = ""
        self._discord_clear()
        self._playing_row = None
        self._update_art()
        self.statusBar().showMessage("Stopped")

    def _toggle_favourite(self, *_a) -> None:
        row = self._selected_row()
        bm = self.host.channels.get("bookmarks")
        if not row or not bm:
            return
        row = dict(row)
        row.setdefault("_source", self.current_channel)   # remember origin (for resolving on play)
        now = bm.toggle(row)
        self.act_fav.setChecked(now)
        self._cache.pop(("bookmarks", "Favorites"), None)   # the favourites list changed -> stale cache
        if self.current_channel == "bookmarks" and self.current_category == "Favorites" and not now:
            url = row.get("url", "")                          # viewing the list we just edited ->
            self.model.set_rows([r for r in self.model.rows() if r.get("url") != url])  # drop the row now
            self._apply_column_visibility()
            self.statusBar().showMessage("Removed from Favorites: " + row["title"])
        else:
            self.model.refresh_icons()                       # elsewhere: just flip the ★ marker in place
            self.statusBar().showMessage(("Favorited " if now else "Unfavorited ") + row["title"])
        if now and not row.get("favicon"):   # icon-less station -> derive its real favicon (background)
            from ..favicons import derive_station_favicon_url
            url = row.get("url", "")
            run_io(lambda: derive_station_favicon_url(row),
                   lambda favurl, u=url: self._on_derived_favicon(u, favurl))

    def _on_derived_favicon(self, station_url: str, favurl) -> None:
        bm = self.host.channels.get("bookmarks")
        if favurl and bm:
            bm.set_favicon(station_url, favurl)   # stamp it on the saved favourite (shows next view)

    def _notify(self, title: str, message: str, row: dict | None) -> None:
        """Fire an OS desktop notification via the system tray. No-op when notifications are off
        or there's no tray host that supports messages (e.g. a bare WM). Never raises."""
        if not self._notifications:
            return
        tray = getattr(self, "_tray", None)
        if tray is None or not tray.supportsMessages():
            return
        try:
            from PySide6.QtWidgets import QSystemTrayIcon
            icon = self._notify_icon(row)
            if icon is not None and not icon.isNull():
                tray.showMessage(title, message, icon, 5000)
            else:
                tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 5000)
        except Exception:  # noqa: BLE001 — a notification must never disrupt playback
            pass

    def _notify_icon(self, row):
        """Best icon for a notification: the now-playing art if it's up (per-station/broadcaster
        logo), else the source service's logo, else the window icon."""
        from PySide6.QtGui import QIcon
        if self._show_art and not self.art.isHidden():
            pm = self.art.pixmap()
            if pm is not None and not pm.isNull():
                return QIcon(pm)
        sp = self._service_pixmap(row) if row else None
        return QIcon(sp) if (sp is not None and not sp.isNull()) else self.windowIcon()

    def _on_nowplaying(self, title: str) -> None:
        if title:
            self._np_full = title
            self._elide_np()
            self.model.set_playing(self._playing_row_url, title)   # keep the table cell in sync
            self._log_song(title)
            self._update_tray_np(title)
            self._discord_update(title, (self._playing_row or {}).get("title", ""))   # track / station
            if title != self._last_notified_title:                 # track changed -> notify (de-duped)
                self._last_notified_title = title
                self._notify(title, (self._playing_row or {}).get("title", ""), self._playing_row)

    def _log_song(self, song: str) -> None:
        """Append a played track to the session song history (View → Song history)."""
        song = clean_icy_title(song or "")     # strip iHeart-style metadata blobs before storing
        if not song:
            return
        row = self._playing_row or {}
        station = row.get("title", "")
        ch = self.host.channels.get(row.get("_source") or self.current_channel)
        service = ch.title if ch else ""
        site = f"{station} ({service})" if (station and service) else (station or service)
        if self._song_history and self._song_history[-1]["song"] == song \
                and self._song_history[-1]["site"] == site:
            return                                         # de-dupe the immediately-previous entry
        self._song_history.append({"song": song, "site": site})
        del self._song_history[:-500]                      # keep the last 500

    def _song_history_dialog(self) -> None:
        import csv
        import io

        from PySide6.QtWidgets import (
            QAbstractItemView, QApplication, QHeaderView, QPushButton, QTableWidget, QTableWidgetItem,
        )

        from .dialogs import StyledDialog
        dlg = StyledDialog(self, "Song History", self.config.get("theme", "dark"))
        dlg.card.setMinimumWidth(640)
        table = QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Song", "Site"])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setMinimumSize(600, 380)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)

        def render() -> None:
            hist = list(reversed(self._song_history))    # newest first
            table.setRowCount(len(hist))
            for i, e in enumerate(hist):
                table.setItem(i, 0, QTableWidgetItem(clean_icy_title(e.get("song", ""))))
                table.setItem(i, 1, QTableWidgetItem(e.get("site", "")))

        def copy_csv() -> None:
            buf = io.StringIO()
            wr = csv.writer(buf)
            wr.writerow(["Song", "Site"])
            for e in reversed(self._song_history):
                wr.writerow([clean_icy_title(e.get("song", "")), e.get("site", "")])
            QApplication.clipboard().setText(buf.getvalue())

        render()
        dlg.body.addWidget(table)
        copy = QPushButton("Copy")
        copy.setToolTip("Copy the table as CSV")
        copy.clicked.connect(copy_csv)
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: (self._song_history.clear(), render()))
        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        dlg.add_buttons(copy, clear, close)
        dlg.exec()

    def _elide_np(self) -> None:
        fm = QFontMetrics(self.np_label.font())
        meta = f"   -   {self._np_meta}" if self._np_meta else ""
        avail = max(120, self.np_label.width())
        title = fm.elidedText(self._np_full, Qt.ElideRight, max(40, avail - fm.horizontalAdvance(meta)))
        self.np_label.setText(title + meta)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "spectro", None) is not None:
            self.spectro.reset_width()    # window resized -> spectrogram back to its normal width
        if self._np_full:
            self._elide_np()

    # ---- misc actions ----
    def _rebuild_theme_menu(self) -> None:
        """(Re)build the View → Theme submenu from the live theme registry (so user themes
        imported at runtime show up too)."""
        self._theme_menu.clear()
        self._theme_actions = {}
        current = self.config.get("theme", "dark")
        for mode, label in theme.THEME_LABELS.items():
            a = QAction(label, self, checkable=True)
            a.setChecked(current == mode)
            a.triggered.connect(lambda _=False, m=mode: self._set_theme(m))
            self._theme_menu.addAction(a)
            self._theme_actions[mode] = a

    def _set_theme(self, mode: str) -> None:
        self.config.set("theme", mode)
        self.config.save_settings()
        self._apply_appearance(mode)
        for m, a in self._theme_actions.items():
            a.setChecked(m == mode)
        self.statusBar().showMessage(f"{theme.THEME_LABELS.get(mode, mode)} theme")

    def _rebuild_wallpaper_menu(self) -> None:
        """(Re)build the View → Wallpaper submenu: Off, the built-in wallpapers, and Load image…
        The checkmark reflects the current setting (mirrors Options → Themes → Wallpaper)."""
        from .wallpaper import BUILTIN_WALLPAPERS
        self._wallpaper_menu.clear()
        on = bool(self.config.get("wallpaper_enabled", True))
        cur = self.config.get("wallpaper_image", "") or ""
        known = {spec for spec, _ in BUILTIN_WALLPAPERS}

        off = QAction("Off", self, checkable=True)
        off.setChecked(not on)
        off.triggered.connect(lambda _=False: self._set_wallpaper_choice(None))
        self._wallpaper_menu.addAction(off)
        self._wallpaper_menu.addSeparator()
        for spec, label in BUILTIN_WALLPAPERS:
            a = QAction(label, self, checkable=True)
            a.setChecked(on and cur == spec)
            a.triggered.connect(lambda _=False, s=spec: self._set_wallpaper_choice(s))
            self._wallpaper_menu.addAction(a)
        self._wallpaper_menu.addSeparator()
        load = QAction("Load image…", self, checkable=True)
        load.setChecked(on and cur != "" and cur not in known)   # an absolute custom path is active
        load.triggered.connect(lambda _=False: self._set_wallpaper_choice("__load__"))
        self._wallpaper_menu.addAction(load)

    def _set_wallpaper_choice(self, choice) -> None:
        """View → Wallpaper pick: None = off; '__load__' = file dialog; else a spec ('' = theme
        default, 'synthwave'/'vaporwave-1'/…, or an absolute image path)."""
        from .wallpaper import BUILTIN_WALLPAPERS
        if choice is None:
            self.config.set("wallpaper_enabled", False)
            msg = "Wallpaper off"
        else:
            if choice == "__load__":
                from PySide6.QtWidgets import QFileDialog
                path, _ = QFileDialog.getOpenFileName(self, "Load wallpaper image", "",
                                                      "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)")
                if not path:
                    self._rebuild_wallpaper_menu()       # cancelled -> restore the checkmarks
                    return
                choice = path
            self.config.set("wallpaper_enabled", True)
            self.config.set("wallpaper_image", choice)
            msg = f"Wallpaper: {dict(BUILTIN_WALLPAPERS).get(choice, choice)}"
        self.config.save_settings()
        self._apply_appearance(self.config.get("theme", "dark"))
        self._rebuild_wallpaper_menu()
        self.statusBar().showMessage(msg)

    def _on_wallpaper_changed(self) -> None:
        """The Options dialog changed the wallpaper -> re-apply and resync the View menu checkmarks."""
        self._apply_appearance(self.config.get("theme", "dark"))
        self._rebuild_wallpaper_menu()

    def _active_wallpaper_spec(self, mode: str) -> str:
        """Which wallpaper to show: the user's chosen image if any, else the theme's built-in one
        (only Synthwave ships one) — or '' when wallpaper is switched off."""
        if not self.config.get("wallpaper_enabled", True):
            return ""
        custom = (self.config.get("wallpaper_image", "") or "").strip()
        return custom or theme.theme_wallpaper(mode)

    def _apply_appearance(self, mode: str) -> None:
        """Apply theme + wallpaper together: palette, a translucency-aware stylesheet, and the
        wallpaper pixmap behind the central widget. Used at startup and on every theme/wallpaper change."""
        from PySide6.QtWidgets import QApplication
        spec = self._active_wallpaper_spec(mode)
        QApplication.instance().setPalette(theme.palette(mode))           # keep OS dark-mode from bleeding in
        QApplication.instance().setStyleSheet(theme.stylesheet(mode, translucent=bool(spec)))
        self._apply_wallpaper(spec)

    def _apply_wallpaper(self, spec: str) -> None:
        wd = getattr(self, "_wallpaper_widget", None)
        if wd is None:
            return
        from .wallpaper import load_wallpaper
        base = theme.user_theme_dir(self.config) if (spec and spec != "synthwave") else None
        pix = load_wallpaper(spec, base) if spec else None
        wd.set_wallpaper(pix, int(self.config.get("wallpaper_dim", 35)))

    def _reload_category(self, item) -> None:
        """Double-click a category → drop just that category's cache and re-fetch it fresh."""
        if not item or item.text() == "Loading…":
            return
        self._cache.pop((self.current_channel, item.text()), None)
        self.config.cache_delete(self.current_channel, item.text())   # force a fresh fetch (bypass disk)
        self.statusBar().showMessage(f"Reloading {item.text()}…")
        self._on_category(item, None)

    def _reload(self, *_a) -> None:
        if self.current_category:
            # Reload = force a fresh fetch (drop BOTH the memory and disk copies first)
            self._cache.pop((self.current_channel, self.current_category), None)
            self.config.cache_delete(self.current_channel, self.current_category)
            self._cat_cache.pop(self.current_channel, None)
            self.config.cache_delete(self.current_channel, "__categories__")   # refresh the genre list too
            self._on_category(self.categories.currentItem(), None)

    def _refresh_channel_cache(self, cid: str) -> None:
        """Drop one channel's cached station lists + category list, and reload the current view
        if we're looking at it. Used when something baked into its rows changes (e.g. the
        AudioAddict listen key) so the stale rows don't linger."""
        for k in [k for k in self._cache if k[0] == cid]:
            self._cache.pop(k, None)
        self._cat_cache.pop(cid, None)
        self.config.cache_clear_channel(cid)         # also wipe its on-disk cache (e.g. stale-key URLs)
        self._update_cache_label()
        if self.current_channel == cid and self.categories.currentItem():
            self._on_category(self.categories.currentItem(), None)

    def _update_cache_label(self) -> None:
        n = len(self._cache)
        rows = sum(len(v) for v in self._cache.values())
        self._cache_label.setText(f"Cache: {n} list(s) / {rows:,} stations" if n else "")

    # ---- background refresh (keeps the disk cache fresh, non-disruptively) ----
    def _bg_refresh(self, cid: str, cat: str) -> None:
        """Quietly re-fetch a category in a worker thread and update the memory + disk cache.
        Doesn't touch the visible list — the fresh data shows on the next visit. Used for a
        stale disk cache and the periodic poll."""
        if cid in self.INTERNAL_CHANNELS or not cat:
            return
        key = (cid, cat)
        if key in self._bg_inflight:
            return
        self._bg_inflight.add(key)
        run_async(lambda: self.host.streams(cid, cat),
                  lambda res, k=key: self._apply_bg_refresh(k, res))

    def _apply_bg_refresh(self, key, res) -> None:
        self._bg_inflight.discard(key)
        rows = getattr(res, "data", None)
        if not (getattr(res, "ok", False) and rows):   # don't clobber a good cache with empty/error
            return
        cid, cat = key
        self._cache[key] = rows
        run_io(lambda r=list(rows): self.config.cache_save(cid, cat, r), lambda *_: None)
        self._update_cache_label()
        if self.current_channel == cid and self.current_category == cat:
            self.statusBar().showMessage(f"{cat}: refreshed in the background ({len(rows):,})")

    def _periodic_refresh(self) -> None:
        cid, cat = self.current_channel, self.current_category
        if cid and cat and not self._is_all(cid, cat):   # skip the heavy full-directory sweep
            self._bg_refresh(cid, cat)

    def _nudge_volume(self, delta: int) -> None:
        """Bump the app's OWN (mpv) volume — bound to the Volume keys + Ctrl+Up/Down, so when the
        app is focused they control playback volume instead of the system mixer."""
        self.vol.setValue(max(0, min(100, self.vol.value() + delta)))   # moves slider -> set_volume + save
        self.statusBar().showMessage(f"Volume {self.vol.value()}%", 1500)

    def _setup_shortcuts(self) -> None:
        from PySide6.QtGui import QKeySequence, QShortcut

        def sc(seq, fn) -> None:
            QShortcut(QKeySequence(seq), self).activated.connect(fn)
        sc("Space", self._toggle_play)                       # play / pause (search box keeps its space)
        sc("Ctrl+P", self._toggle_play)
        sc(Qt.Key_MediaPlay, self._toggle_play)
        sc(Qt.Key_MediaTogglePlayPause, self._toggle_play)
        sc("Ctrl+.", self._stop)
        sc(Qt.Key_MediaStop, self._stop)
        sc(Qt.Key_VolumeUp, lambda: self._nudge_volume(5))     # app volume when focused (not system)
        sc(Qt.Key_VolumeDown, lambda: self._nudge_volume(-5))
        sc("Ctrl+Up", lambda: self._nudge_volume(5))           # reliable in-app vol (Volume keys can be OS-grabbed)
        sc("Ctrl+Down", lambda: self._nudge_volume(-5))
        sc("F5", self._reload)
        sc("Ctrl+R", self._reload)
        sc("Ctrl+F", self._focus_search)
        sc("Ctrl+L", self._focus_search)
        sc("Ctrl+D", self._toggle_favourite)                 # add/remove the selected station
        sc("Ctrl+Q", self._quit)                             # (Ctrl+, lives on the Options menu action)
        esc = QShortcut(QKeySequence(Qt.Key_Escape), self.search)   # Esc clears the focused search box
        esc.activated.connect(self.search.clear)

    def _setup_media_keys(self) -> None:
        """OS-global media keys (Play/Pause, Stop, Next, Prev) so they work even when the window is
        minimized or unfocused. Windows-only today (RegisterHotKey via ctypes); a graceful no-op
        elsewhere, where the focused-window shortcuts above still handle the keys."""
        from .mediakeys import MediaKeys
        self._media_keys = MediaKeys({
            "play_pause": self._toggle_play,
            "stop": self._stop,
            "next": lambda: self._play_relative(1),
            "prev": lambda: self._play_relative(-1),
        })
        try:
            self._media_keys.install(int(self.winId()))   # winId() realizes the native window -> HWND
        except Exception:  # noqa: BLE001 — never let a hotkey quirk block startup
            pass

    def _play_relative(self, delta: int) -> None:
        """Media Next/Prev: step the selection by one visible row and play it (live-radio
        'next/previous station')."""
        n = self.proxy.rowCount()
        if not n:
            return
        cur = self.table.currentIndex()
        r = max(0, min(n - 1, (cur.row() if cur.isValid() else -1) + delta))
        idx = self.proxy.index(r, 0)
        if idx.isValid():
            self.table.setCurrentIndex(idx)
            self.table.scrollTo(idx)
            self._play_selected()

    def _focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def _update_stream_status(self) -> None:
        try:
            if self.player.is_playing:
                title = self.player.now_playing()        # poll the live track (reliable)
                if title and title != self._np_full:
                    self._on_nowplaying(title)           # updates player bar + the table cell
                # trust the STREAM, not the directory: show mpv's actual decoded bitrate/codec
                kbps, codec = self.player.audio_format()
                codec = codec.replace("mpeg", "mp3").replace("float", "").strip()
                meta = " / ".join(p for p in (f"{kbps}k" if kbps else "", codec) if p)
                if meta and meta != self._np_meta:
                    self._np_meta = meta
                    self._elide_np()                     # reflect it after the title in the bar too
                buf = self.player.buffer_percent()
                bits = [b for b in (self._np_meta.strip(), f"buffer {buf}%" if buf is not None else "") if b]
                self._stream_label.setText("▶ " + " · ".join(bits) if bits else "▶")
            else:
                self._stream_label.setText("" if self.player.available else "⚠ no audio engine")
        except Exception:  # noqa: BLE001 — the 1 s status poll must never take down the app
            pass

    # ---- Discord Rich Presence (optional) ----
    def _discord_enabled(self) -> bool:
        return bool(self.config.get("discord_rpc", False))

    def _discord_get(self):
        """The DiscordPresence, built lazily with the app's built-in client id; None when off."""
        if not self._discord_enabled():
            return None
        if self._discord is None:
            from ..discord_presence import DISCORD_APP_ID, DiscordPresence
            self._discord = DiscordPresence(DISCORD_APP_ID)
        return self._discord

    _DISCORD_MIN = 30.0   # min seconds between presence writes (well under Discord's ~5/20s limit)

    def _discord_update(self, details: str, state: str, reset: bool = False) -> None:
        self._discord_queue((details, state, reset))

    def _discord_clear(self) -> None:
        self._discord_queue("CLEAR")

    def _discord_queue(self, action) -> None:
        """Throttle presence writes: send immediately when enough time has passed, else coalesce
        the burst and send only the LATEST after the interval — so rapid station-hopping can't spam
        Discord or trip its rate limit."""
        if self._discord_get() is None:                  # disabled / no App ID
            self._discord_pending = None
            return
        self._discord_pending = action
        elapsed = time.monotonic() - self._discord_last
        if elapsed >= self._DISCORD_MIN:
            self._discord_flush()
        elif not self._discord_timer.isActive():
            self._discord_timer.start(int((self._DISCORD_MIN - elapsed) * 1000))

    def _discord_flush(self) -> None:
        action, self._discord_pending = self._discord_pending, None
        if action is None:
            return
        self._discord_last = time.monotonic()
        d = self._discord_get()
        if d is None:
            return
        try:
            if action == "CLEAR":
                d.clear()
            else:
                d.update(action[0], action[1], reset_elapsed=action[2])
        except Exception:  # noqa: BLE001 — presence must never disrupt playback
            pass

    def _tick_viz(self) -> None:
        """Drive the active visualization from the live audio levels (~30 fps). Never crash."""
        if self._viz == "off":
            return
        try:
            levels = self.player.audio_levels() if self.player.is_playing else None
            if self._viz == "vu":
                if levels:
                    self.vu.set_db(*levels)
                else:
                    self.vu.idle()
            elif self._viz == "spectrogram":
                if levels:
                    L, R, Lpk, Rpk = levels
                    rms = (L + R) / 2.0
                    self.spectro.push(rms, max(0.0, (Lpk + Rpk) / 2.0 - rms))
                else:
                    self.spectro.idle()
        except Exception:  # noqa: BLE001 — a meter must never take down playback
            pass

    def _on_search(self, text: str) -> None:
        self._pending_filter = text
        self._search_timer.start(220)        # debounce: filter once typing pauses

    def _apply_filter(self) -> None:
        text = self._pending_filter
        self.proxy.setFilterFixedString(text)
        total = self.model.rowCount()
        if text and total:
            self.statusBar().showMessage(
                f"Showing {self.proxy.rowCount()} of {total}  (filter: {text})"
            )

    # ---- server-side directory search (Enter) ----
    def _search_enter(self) -> None:
        q = self.search.text().strip()
        if not q:
            return
        if self.btn_search_all.isChecked():
            self._search_all_sources(q)
        else:
            self._search_channel(self.current_channel, q)

    def _search_channel(self, cid: str, q: str) -> None:
        """Search ONE channel's whole directory (not just the loaded rows)."""
        ch = self.host.channels.get(cid)
        if not getattr(ch, "has_search", False):
            self.statusBar().showMessage(
                f"{ch.title if ch else 'This source'} has no directory search — filtering the loaded list")
            return                                    # the type-to-filter result already stands
        self._load_gen += 1
        gen = self._load_gen
        self.proxy.setFilterFixedString("")           # show the server results unfiltered
        self.model.set_rows([])
        self.statusBar().showMessage(f"Searching {ch.title} for “{q}”…")
        cat = self.current_category or getattr(ch, "all_category", "") or ""
        run_async(lambda: self.host.streams(cid, cat, search=q),
                  lambda res, g=gen, c=cid: self._on_search_results(g, c, q, res))

    def _on_search_results(self, gen: int, cid: str, q: str, res) -> None:
        if gen != self._load_gen:
            return                                    # the user moved on
        ch = self.host.channels.get(cid)
        rows = []
        for r in (getattr(res, "data", None) or []):
            r = dict(r)
            r.setdefault("_source", cid)              # so it plays/resolves against the right channel
            rows.append(r)
        self.model.set_rows(rows)
        self._apply_column_visibility()
        self._load_station_favicons()
        label = f"{ch.title if ch else cid}: “{q}”"
        self.statusBar().showMessage(f"{len(rows):,} result(s) — {label}" if rows else f"No results — {label}")

    def _search_all_sources(self, q: str) -> None:
        """Fan the query out to every search-capable, enabled channel; merge + de-dup by URL."""
        cands = [cid for cid, ch in self.host.channels.items()
                 if getattr(ch, "has_search", False) and self.host.is_enabled(cid)]
        if not cands:
            self.statusBar().showMessage("No search-capable sources are enabled")
            return
        self._load_gen += 1
        gen = self._load_gen
        self.proxy.setFilterFixedString("")
        self.model.set_rows([])
        self._search_acc = {}                         # url -> row, deduped across sources
        self._search_pending = set(cands)
        self.statusBar().showMessage(f"Searching {len(cands)} sources for “{q}”…")
        for cid in cands:
            ch = self.host.channels.get(cid)
            cat = getattr(ch, "all_category", "") or ""
            run_async(lambda c=cid, k=cat: self.host.streams(c, k, search=q),
                      lambda res, g=gen, c=cid: self._on_all_results(g, c, q, res))

    def _on_all_results(self, gen: int, cid: str, q: str, res) -> None:
        if gen != self._load_gen:
            return
        for r in (getattr(res, "data", None) or []):
            url = r.get("url")
            if url and url not in self._search_acc:
                r = dict(r)
                r.setdefault("_source", cid)
                self._search_acc[url] = r
        self._search_pending.discard(cid)
        merged = list(self._search_acc.values())
        self.model.set_rows(merged)                   # progressive: grows as each source answers
        self._apply_column_visibility()
        self._load_station_favicons()
        tail = "" if not self._search_pending else " (searching…)"
        self.statusBar().showMessage(f"{len(merged):,} result(s) across all sources — “{q}”{tail}")

    def _open_location(self) -> None:
        from .dialogs import open_location
        url = open_location(self, self.config.get("theme", "dark"))
        if not url:
            return
        self._playing_row_url = url
        self._playing_bitrate = 0
        self._np_full = url
        self._np_meta = ""
        self._elide_np()
        self._playing_row = None
        self._update_art()
        self.btn_play.setChecked(True)
        ok = self.player.play(url)
        self.statusBar().showMessage(f"Playing {url}" if ok else f"Opened {url}")

    def _add_local(self) -> None:
        from .dialogs import new_station
        result = new_station(self, self.config.get("theme", "dark"))
        if not result:
            return
        name, url = result
        local = self.host.channels.get("local")
        if local:
            local.add(name or url, url)
            self.statusBar().showMessage(f"Added {name or url} to Local")

    def _open_plugins_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from ..plugins.loader import user_plugin_dir
        d = user_plugin_dir(self.config)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))
        self.statusBar().showMessage(f"Drop a .py plugin in: {d}")

    def _on_cache_cleared(self) -> None:
        """Disk cache cleared from Options → Cache & Data: drop the in-memory cache + reload."""
        self._cache.clear()
        self._reload()

    def _open_options(self) -> None:
        from .options import OptionsDialog
        dlg = OptionsDialog(self.host, self.config, self, self.config.get("theme", "dark"))
        dlg.changed.connect(self._on_plugins_changed)
        dlg.settings_changed.connect(self._apply_general_settings)
        dlg.themes_reloaded.connect(self._rebuild_theme_menu)   # a theme was imported
        dlg.theme_changed.connect(self._set_theme)              # a theme was picked/applied
        dlg.cache_cleared.connect(self._on_cache_cleared)       # disk cache cleared -> drop in-memory + reload
        dlg.data_imported.connect(self._on_cache_cleared)       # backup imported -> refresh favourites/local
        dlg.wallpaper_changed.connect(self._on_wallpaper_changed)
        dlg.exec()

    def _apply_general_settings(self) -> None:
        self._station_favicons = self.config.icon_mode() != "off"
        self._notifications = bool(self.config.get("notifications", True))
        self.model.refresh_icons()           # flip station icons <-> service logo immediately
        self._apply_tray()                   # tray icon / visibility may have changed
        # The AudioAddict listen key is baked into every stream URL when the rows load, so a
        # key change leaves already-loaded networks (e.g. DI.FM) holding stale keyless URLs.
        # Drop AudioAddict's cache so every network reloads with the new key.
        key = (self.config.get("audioaddict_listen_key", "") or "").strip()
        if key != self._aa_key:
            self._aa_key = key
            self._refresh_channel_cache("audioaddict")
        if self._discord_enabled():          # just turned on (or new App ID) -> show now if playing
            if self.player.is_playing and self._playing_row:
                self._discord_update(self._np_full or self._playing_row.get("title", ""),
                                     self._playing_row.get("title", ""), reset=True)
        elif self._discord is not None:      # turned off -> drop the presence + connection
            self._discord.close()
            self._discord = None

    def _on_plugins_changed(self) -> None:
        self._populate_channels()
        self._rebuild_channels_menu()

    def _about(self) -> None:
        from PySide6.QtWidgets import QPushButton

        from .. import asset_path
        from .dialogs import StyledDialog
        dlg = StyledDialog(self, "", self.config.get("theme", "dark"))
        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        _pm = QPixmap(asset_path("logo.png"))
        if not _pm.isNull():
            logo.setPixmap(_pm.scaledToWidth(120, Qt.SmoothTransformation))
        title = QLabel(
            f"<span style='font-size:20px;color:rgb(80,230,150)'><b>{APP_NAME}</b></span>"
            f"<span style='color:{dlg.text_col}'> {__version__}</span>"
        )
        body = QLabel("A modern internet-radio browser.<br>"
                      "Audio by libmpv · UI by Qt · stations from RadioBrowser.")
        body.setStyleSheet(f"color:{dlg.text_col};")
        credit = QLabel("Ported &amp; reimagined by <b>IronWolve</b><br>"
                        "<a href='https://github.com/IronWolve/StreamTuner-ng'>"
                        "github.com/IronWolve/StreamTuner-ng</a>")
        credit.setStyleSheet(f"color:{dlg.text_col};")
        credit.setOpenExternalLinks(True)        # the repo link opens in the browser
        credit.setTextFormat(Qt.RichText)
        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        dlg.body.addWidget(logo)
        dlg.body.addWidget(title)
        dlg.body.addWidget(body)
        dlg.body.addWidget(credit)
        dlg.add_buttons(close)
        dlg.exec()

    def _status_error(self, msg: str) -> None:
        self.statusBar().showMessage("⚠ " + msg)

    def _save_layout(self) -> None:
        """Persist window size/position + table column widths/order to config."""
        import base64
        try:
            self.config.set("window_geometry",
                            base64.b64encode(self.saveGeometry().data()).decode("ascii"))
            self.config.set("column_state",
                            base64.b64encode(self.table.horizontalHeader().saveState().data()).decode("ascii"))
            self.config.save_settings()
        except Exception:  # noqa: BLE001
            pass

    def _restore_layout(self) -> None:
        import base64

        from PySide6.QtCore import QByteArray
        geo = self.config.get("window_geometry")
        if geo:
            try:
                self.restoreGeometry(QByteArray(base64.b64decode(geo)))
            except Exception:  # noqa: BLE001
                pass
        col = self.config.get("column_state")
        if col:
            try:   # Qt ignores it cleanly if the column count changed (old saved state)
                self.table.horizontalHeader().restoreState(QByteArray(base64.b64decode(col)))
            except Exception:  # noqa: BLE001
                pass

    def _toggle_window(self) -> None:
        """Tray left-click / Show-Hide: restore+raise if hidden or minimised, else hide."""
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _quit(self) -> None:
        """Really exit (tray Quit / Ctrl+Q), bypassing close-to-tray."""
        self._really_quit = True
        self.close()

    def _apply_tray(self) -> None:
        """Re-apply the tray icon + visibility after an Options change."""
        tray = getattr(self, "_tray", None)
        if tray is None:
            return
        from .tray import tray_icon
        tray.setIcon(tray_icon(self.config))
        tray.setVisible(bool(self.config.get("tray_enabled", True)))

    def _update_tray_np(self, text: str) -> None:
        tray = getattr(self, "_tray", None)
        if tray is None:
            return
        label = (text or "").strip() or "Stopped"
        if len(label) > 60:
            label = label[:57] + "…"
        np = getattr(tray, "_np_action", None)
        if np is not None:
            np.setText(label)
        tray.setToolTip(f"StreamTuner-ng — {label}" if label != "Stopped" else "StreamTuner-ng")

    def closeEvent(self, e):
        tray = getattr(self, "_tray", None)
        if (not getattr(self, "_really_quit", False)
                and self.config.get("tray_close", False)
                and tray is not None and tray.isVisible()):
            e.ignore()                   # close-to-tray: hide + keep playing; Quit from the tray menu
            self.hide()
            return
        mk = getattr(self, "_media_keys", None)
        if mk is not None:
            mk.uninstall()               # release the global media-key grab
        self._save_layout()              # remember window size + column layout
        for t in (self._vu_timer, self._stats_timer, self._refresh_timer,
                  self._discord_timer, self._search_timer, self._icon_refresh):
            t.stop()                     # no stray ticks hitting a torn-down player during teardown
        # stop audio IMMEDIATELY on close so nothing keeps playing after the window's gone
        try:
            self.player.stop()
            self.player.shutdown()
        except Exception:  # noqa: BLE001
            pass
        if self._discord is not None:
            self._discord.close()
        if tray is not None:
            tray.hide()
        from PySide6.QtWidgets import QApplication
        QApplication.instance().quit()   # ensure the process actually exits
        super().closeEvent(e)

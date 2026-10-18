"""The main window — three panes (channels -> categories -> stations), toolbar,
persistent player bar, status bar, menus. Thin: it drives host/player/config and
keeps the airlock off the UI thread via worker.run_async.
"""

from __future__ import annotations

import time
import threading
from collections import OrderedDict

from PySide6.QtCore import QSortFilterProxyModel, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QPushButton, QSlider, QSplitter, QTableView,
    QToolBar, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from ..player.mpv_player import clean_icy_title
from ..plugins.result import Result, Status
from ..security import MAX_IMPORT_BYTES, MAX_ROWS, csv_cell, redact_url, station_row, valid_url
from . import theme
from .models import COLUMNS, FILTER_ROLE, SORT_ROLE, StationModel
from .spectrogram import Spectrogram
from .vumeter import VuMeter
from .worker import run_async, run_io, run_cache, run_playback
from .cache import CatalogCache


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
        self._play_gen = 0
        self._play_started_at = 0.0
        self._closing = False
        self._load_gen = 0           # bumps each category change; stale pages ignored
        self._channel_gen = 0
        self._view_search = False
        self._search_query = None
        self._cache_epoch: dict[str, int] = {}
        self._loading_key = None
        self._page_retries = set()
        self._load_cancel = threading.Event()
        self._play_cancel = threading.Event()
        self._icon_cancel = threading.Event()
        self._cache_events = {}
        self._cache_generation = 0
        self._icon_epoch = 0
        self._icon_retry_at = OrderedDict()
        self._cat_ages = {}
        self._load_complete = True
        self._load_errors = []
        self._load_started = False
        self._retain_during_load = False
        self._fresh_rows = []
        self._fresh_seen = set()
        self._refresh_view_key = None
        self._icons_clearing = False
        self._refreshing = False
        self._pending_restore = None
        self._search_failures = {}
        self.ALL_LIMIT = 100000
        self._collapsed = set(self.config.get("collapsed_groups", []) or [])
        self._cache = CatalogCache()   # (channel, category) -> full station list
        self._cat_cache: dict[str, list] = {}  # channel -> category list (cached on first visit)
        self._aa_key = (self.config.get("audioaddict_listen_key", "") or "").strip()  # detect key changes
        self._favicons: dict[str, str] = {}   # channel id -> cached favicon path
        self._svc_icons: dict = {}            # channel id -> QIcon, generic fallback for icon-less rows
        self._station_icons = OrderedDict()        # station favicon URL -> QIcon (None = in-flight)
        self._derived_icons = OrderedDict()        # station URL -> QIcon for favicon-less rows (SHOUTcast/TuneIn); None=in-flight, "miss"=none
        self._station_favicons = self.config.icon_mode() != "off"   # off -> service logo for all (full/small differ only on disk)
        self._pending_play = False
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
        self.setWindowTitle(f"{APP_NAME} {__version__}")
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
        # Visual polling starts only for visible, active playback.
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
        tb.addWidget(QLabel(" Search: "))
        self.search = QLineEdit()
        self.search.setPlaceholderText("filter…  (Enter = search the directory)")
        self.search.setClearButtonEnabled(True)   # ✕ button to clear the filter
        self.search.setMinimumWidth(180)
        self.search.textChanged.connect(self._on_search)        # type = instant client-side filter
        self.search.returnPressed.connect(self._search_enter)   # Enter = server-side directory search
        tb.addWidget(self.search)
        self.search_scope = QComboBox()
        self.search_scope.addItems(["Loaded list", "This source", "All sources"])
        self.search_scope.currentIndexChanged.connect(self._scope_changed)
        tb.addWidget(self.search_scope)
        self.search_submit = QPushButton("Search")
        self.search_submit.clicked.connect(self._search_enter)
        self._search_submit_action = tb.addWidget(self.search_submit)
        self._scope_changed()

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
        for col, w in enumerate((230, 100, 60, 60, 80, 65, 95, 200, 200, 320)):
            self.table.setColumnWidth(col, w)
        self.table.doubleClicked.connect(self._play_selected)
        self.table.clicked.connect(self._on_station_clicked)
        self.table.selectionModel().currentRowChanged.connect(lambda *_: self._sync_selection_actions())
        self.table.verticalScrollBar().sliderPressed.connect(lambda: setattr(self, "_pending_restore", None))
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
        station_pane = QWidget()
        station_layout = QVBoxLayout(station_pane)
        station_layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self._list_notice = QLabel("Choose a service to browse stations")
        self._list_notice.setTextFormat(Qt.PlainText)
        self._list_notice.setWordWrap(True)
        row.addWidget(self._list_notice, 1)
        self._back_button = QPushButton("Back to category")
        self._back_button.clicked.connect(self._back_to_category)
        self._back_button.hide()
        row.addWidget(self._back_button)
        self._retry_button = QPushButton("Retry")
        self._retry_button.clicked.connect(self._retry_failed_search)
        self._retry_button.hide()
        row.addWidget(self._retry_button)
        self._cancel_button = QPushButton("Cancel loading")
        self._cancel_button.clicked.connect(self._cancel_load)
        self._cancel_button.hide()
        row.addWidget(self._cancel_button)
        station_layout.addLayout(row)
        station_layout.addWidget(self.table)
        split.addWidget(station_pane)
        self._split = split
        split.setStretchFactor(2, 1)
        split.setSizes([160, 170, 730])

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
        if self._closing:
            return
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
        self._sync_visualizer()

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
        self.np_label.setTextFormat(Qt.PlainText)
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
        m_file.addAction(QAction("Quit", self, shortcut="Ctrl+Q", triggered=self._quit))

        m_view = mb.addMenu("&View")
        m_view.addAction(QAction("Reload current channel", self, shortcut="F5", triggered=self._reload))
        columns_menu = m_view.addMenu("Columns")
        self._column_actions = {}
        saved_columns = self.config.get("visible_columns", [])
        for label, field in COLUMNS:
            action = QAction(label, self, checkable=True)
            action.setChecked(field == "title" or (field in saved_columns if saved_columns else field != "url"))
            action.setEnabled(field != "title")
            action.setToolTip("Shown when this service supplies the field")
            action.toggled.connect(lambda on, name=field: self._set_column_visible(name, on))
            columns_menu.addAction(action)
            self._column_actions[field] = action
        self._art_action = QAction("Show album art", self, checkable=True)
        self._art_action.setChecked(self._show_art)
        self._art_action.triggered.connect(self._toggle_art)
        m_view.addAction(self._art_action)
        viz_menu = m_view.addMenu("Visualization")
        self._viz_actions = {}
        for mode, label in (("off", "Off"), ("vu", "VU Meter"), ("spectrogram", "Level history")):
            a = QAction(label, self, checkable=True)
            a.setChecked(self._viz == mode)
            a.triggered.connect(lambda _=False, m=mode: self._set_visualization(m))
            viz_menu.addAction(a)
            self._viz_actions[mode] = a
        m_view.addAction(QAction("Song history…", self, triggered=self._song_history_dialog))
        m_view.addSeparator()
        self._colors_menu = m_view.addMenu("Colors")
        self._build_colors_menu()
        self._theme_menu = m_view.addMenu("Theme")
        self._rebuild_theme_menu()
        self._wallpaper_menu = m_view.addMenu("Wallpaper")
        self._rebuild_wallpaper_menu()

        self.m_channels = mb.addMenu("&Services")
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
        previous = self.current_channel
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
        chosen = next((i for i in range(self.channels.count())
                       if self.channels.item(i).data(Qt.UserRole) == previous), None)
        same = chosen is not None
        if chosen is None:
            chosen = next((i for i in range(self.channels.count())
                           if self.channels.item(i).data(Qt.UserRole)), None)
        if chosen is not None:
            self.channels.setCurrentRow(chosen)
        self.channels.blockSignals(False)
        if chosen is not None and not same:
            self._on_channel(self.channels.currentItem(), None)

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
                enabled = self.host.is_enabled(ch.id)
                a.setChecked(self.host.is_visible(ch.id) and enabled)
                a.setEnabled(enabled)
                if not enabled:
                    a.setText(ch.title + " (disabled)")
                    a.setToolTip("Enable this service in Options → Plugins")
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
        if self._closing:
            return
        if isinstance(path, str) and path:
            self._favicons[cid] = path
            self._svc_icons.pop(cid, None)    # rebuild the generic fallback now that the logo is in
            self._set_channel_icon(cid)
            self.model.refresh_icons()        # paint generic icons for any icon-less rows on screen

    def _load_channel_favicons(self) -> None:
        from ..favicons import ensure_favicon
        for ch in self.host.channels.values():
            if ch.test_only or not self.host.is_enabled(ch.id) or not getattr(ch, "homepage", "") or ch.id in self._favicons:
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
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        home = getattr(ch, "homepage", "")
        if home:
            menu.addAction("Open Website", lambda: self._open_website(home))
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
        """Save a stable station-list snapshot to CSV without blocking the UI."""
        import csv
        import os
        import tempfile
        from concurrent.futures import CancelledError
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog

        snapshot = tuple(rows or ())
        if not snapshot:
            self.statusBar().showMessage("Nothing to export — the list is empty.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export list", suggested, "CSV files (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        destination = Path(path)
        cols = [("title", "Title"), ("url", "URL"), ("genre", "Genre"), ("country", "Country"),
                ("bitrate", "Bitrate"), ("format", "Format"), ("homepage", "Homepage")]
        # Favorites/Local replace rows on mutation; they do not edit them in place.
        # Capturing the tuple keeps the exact list version selected by the user.
        secret_snapshot = tuple(self.config.secrets._cache.copy().values())
        cancel = threading.Event()
        if not hasattr(self, "_export_cancels"):
            self._export_cancels = set()
        self._export_cancels.add(cancel)
        self.statusBar().showMessage(f"Exporting {len(snapshot):,} stations…")

        def write_csv():
            fd = temporary = None
            try:
                fd, temporary = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp",
                                                 dir=str(destination.parent))
                with os.fdopen(fd, "w", newline="", encoding="utf-8") as stream:
                    fd = None
                    writer = csv.writer(stream)
                    writer.writerow([heading for _key, heading in cols])
                    for row in snapshot:
                        if cancel.is_set():
                            raise CancelledError("CSV export cancelled")
                        writer.writerow([csv_cell(row.get(key, ""), secret_snapshot) for key, _heading in cols])
                    stream.flush()
                    os.fsync(stream.fileno())
                if cancel.is_set():
                    raise CancelledError("CSV export cancelled")
                os.replace(temporary, destination)
                temporary = None
                return len(snapshot)
            finally:
                if fd is not None:
                    os.close(fd)
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)

        def finished(result):
            self._export_cancels.discard(cancel)
            if self._closing:
                return
            if isinstance(result, BaseException):
                if not isinstance(result, CancelledError):
                    self._status_error(f"Export failed: {self.config.redact(result)}")
                return
            self.statusBar().showMessage(f"Exported {int(result):,} station(s) → {destination}")

        run_io(write_csv, finished, cancelled=cancel)

    def _import_favourites(self) -> None:
        import csv
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        from ..plugins.base import make_row
        path, _ = QFileDialog.getOpenFileName(self, "Import to Favorites", "", "CSV files (*.csv);;All files (*)")
        if not path:
            return
        if hasattr(self, "_import_cancel"):
            self._import_cancel.set()
        cancel = threading.Event()
        self._import_cancel = cancel
        bm = self.host.channels["bookmarks"]
        self._list_message("Reading station file…")
        def read():
            if Path(path).stat().st_size > MAX_IMPORT_BYTES:
                raise ValueError("CSV exceeds the import size limit")
            parsed, bad = [], 0
            with open(path, newline="", encoding="utf-8-sig") as stream:
                reader = csv.DictReader(stream)
                if "url" not in {(h or "").strip().lower() for h in reader.fieldnames or []}:
                    raise ValueError("The CSV needs a URL column")
                for index, values in enumerate(reader):
                    if cancel.is_set():
                        return None
                    if index >= MAX_ROWS:
                        raise ValueError("CSV has too many rows")
                    row = {(key or "").strip().lower(): (value or "").strip()
                           for key, value in values.items() if key and isinstance(value, str)}
                    if not valid_url(row.get("url"), token=True):
                        bad += 1
                        continue
                    bitrate = row.get("bitrate", "")
                    parsed.append(make_row(title=row.get("title") or row["url"], url=row["url"],
                                           genre=row.get("genre", ""), country=row.get("country", ""),
                                           homepage=row.get("homepage", ""),
                                           bitrate=int(bitrate) if bitrate.isdigit() else 0,
                                           format=row.get("format", "")))
            return parsed, bad, bm.prepare_import(parsed, [])
        def done(result, attempts=0):
            if self._closing or cancel.is_set():
                return
            if isinstance(result, BaseException):
                self._status_error(self.config.redact(result))
                return
            if result is None:
                return
            rows, bad, plan = result
            if not bm.plan_is_current(plan):
                if attempts >= 4:
                    self._status_error("Favorites changed during import; retry when editing is finished")
                    return
                run_io(lambda: (rows, bad, bm.prepare_import(rows, [])),
                       lambda outcome: done(outcome, attempts + 1), cancelled=cancel)
                return
            try:
                if not bm.apply_import_plan(plan):
                    self._status_error("Favorites changed during import; retry")
                    return
                added = plan.favorites_added
            except (ValueError, OSError) as exc:
                self._status_error(self.config.redact(exc))
                return
            self._cache.pop(("bookmarks", "Favorites"), None)
            if (self.current_channel, self.current_category) == ("bookmarks", "Favorites"):
                self._on_category(self.categories.currentItem(), None)
            self._list_message(f"Imported {added:,} favorites; {bad:,} invalid rows skipped")
        run_io(read, done, cancelled=cancel)


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
        if self._icons_clearing or not self._station_favicons:  # no new icon work during a clear
            return self._service_icon_for(row)
        from PySide6.QtGui import QIcon
        fav = row.get("favicon")
        if fav:
            if fav in self._station_icons:
                ic = self._station_icons[fav]
                if isinstance(ic, QIcon):
                    self._station_icons.move_to_end(fav)
                    return ic
                if time.monotonic() >= self._icon_retry_at.get(("station", fav), float("inf")):
                    self._station_icons.pop(fav, None)
                    self._request_station_icon(fav)
            else:
                self._request_station_icon(fav)      # lazy: fetch on first paint (visible rows only)
        else:                                        # no directory favicon -> derive from the stream (if enabled)
            url = row.get("url")
            if url and self._stream_icons_on(row):
                if url in self._derived_icons:
                    ic = self._derived_icons[url]
                    if isinstance(ic, QIcon):
                        self._derived_icons.move_to_end(url)
                        return ic
                    if time.monotonic() >= self._icon_retry_at.get(("derived", url), float("inf")):
                        self._derived_icons.pop(url, None)
                        self._request_derived_icon(row)
                else:
                    self._request_derived_icon(row)
        return self._service_icon_for(row)           # service logo until a per-station icon arrives

    def _stream_icons_on(self, row) -> bool:
        """Whether to derive a per-station stream icon for this row's channel — Options -> Plugins ->
        'icons' (per service, default on). Off -> the channel just shows its service logo."""
        cid = row.get("_source") or self.current_channel
        return bool(self.config.get(f"stream_icons_{cid}", True))

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
        # Paint requests icons only for visible rows; never enqueue an entire category.
        self.table.viewport().update()

    def _request_station_icon(self, url: str) -> None:
        """Fetch one station's favicon (disk-cached -> once-only) and repaint when it arrives."""
        self._station_icons[url] = None              # mark in-flight (don't refetch / retry)
        from ..favicons import station_favicon
        self._trim_icons(self._station_icons)
        epoch = self._icon_epoch
        run_io(lambda: station_favicon(self.config, url),
               lambda path: self._on_station_favicon(url, path, epoch), cancelled=self._icon_cancel)

    def _on_station_favicon(self, url, path, epoch=None) -> None:
        if self._closing or epoch is not None and epoch != self._icon_epoch:
            return
        if isinstance(path, str) and path:
            from PySide6.QtGui import QIcon
            ic = QIcon(path)
            if not ic.isNull():
                self._station_icons[url] = ic
                self._trim_icons(self._station_icons)
                self._icon_retry_at.pop(("station", url), None)
                self._icon_refresh.start(150)        # debounce: one repaint per burst of loads

                return
        self._station_icons.pop(url, None)
        self._station_icons[url] = "retry"
        self._icon_failed("station", url, path)

    def _request_derived_icon(self, row) -> None:
        """For a station with no directory favicon (SHOUTcast/TuneIn), derive its broadcaster logo
        from the stream — resolve the .pls -> the ICY 'icy-url' homepage -> that site's favicon.
        Disk-cached + miss-marked so the slow round-trip runs once per station; lazy (visible rows)."""
        url = row.get("url")
        self._derived_icons[url] = None              # in-flight (don't re-request)
        from ..favicons import best_station_icon
        self._trim_icons(self._derived_icons)
        epoch = self._icon_epoch
        run_io(lambda r=dict(row): best_station_icon(self.config, r),
               lambda path: self._on_derived_icon(url, path, epoch), cancelled=self._icon_cancel)

    def _on_derived_icon(self, url, path, epoch=None) -> None:
        if self._closing or epoch is not None and epoch != self._icon_epoch:
            return
        if isinstance(path, str) and path:
            from PySide6.QtGui import QIcon
            ic = QIcon(path)
            if not ic.isNull():
                self._derived_icons[url] = ic
                self._trim_icons(self._derived_icons)
                self._icon_retry_at.pop(("derived", url), None)
                self._icon_refresh.start(150)        # debounce: one repaint per burst
                return
        self._derived_icons[url] = "miss"
        self._icon_failed("derived", url, path)

    def _flush_icon_refresh(self) -> None:
        self.model.refresh_icons()

    # ---- loading flow (all async through the airlock) ----
    def _on_channel(self, cur, _prev) -> None:
        if not cur or not cur.data(Qt.UserRole):
            return
        self._reset_load()
        self._cancel_pending_icons()
        self._channel_gen += 1
        self._load_gen += 1
        self._loading_key = None
        self.current_channel = cur.data(Qt.UserRole)
        self.current_category = ""
        self._view_search = False
        self._search_query = None
        self.search.clear()
        self.model.set_rows([])
        self.categories.clear()
        cid, generation = self.current_channel, self._channel_gen
        self._refresh_dot(cid)
        cached = self._cat_cache.get(cid)
        if cached:
            self._fill_categories(cid, cached)
            if time.monotonic() - self._cat_ages.get(cid, 0) > self._CACHE_STALE_S:
                self._fetch_categories(cid, generation, self.current_category)
        elif cid in self.INTERNAL_CHANNELS:
            self._fetch_categories(cid, generation)
        else:
            self.categories.addItem("Loading…")
            run_cache(lambda: self.config.cache_load(cid, "__categories__"),
                      lambda result: self._on_cached_categories(cid, generation, result))

    def _on_cached_categories(self, cid, generation, cached) -> None:
        if self._closing or cid != self.current_channel or generation != self._channel_gen:
            return
        if isinstance(cached, tuple) and cached[0]:
            self._cat_cache[cid] = cached[0]
            self._cat_ages[cid] = time.monotonic() - cached[1]
            self._fill_categories(cid, cached[0])
            if cached[1] > self._CACHE_STALE_S:
                self._fetch_categories(cid, generation, self.current_category)
        else:
            self._fetch_categories(cid, generation)

    def _fetch_categories(self, cid, generation, preferred=None, fresh=False) -> None:
        operation = "refresh_categories" if fresh else "update_categories"
        cancel = self._load_cancel
        run_async(lambda: self.host.call(cid, operation, timeout=45, cancel_event=cancel),
                  lambda result: self._on_categories(cid, result, generation, preferred, fresh),
                  cancelled=cancel)


    def _on_categories(self, cid: str, result, generation=None, preferred=None, fresh=False) -> None:
        if self._closing or cid != self.current_channel:
            return
        if generation is not None and generation != self._channel_gen:
            return
        self._refresh_dot(cid)
        if isinstance(result, Result) and result.ok and result.data:
            cats = list(result.data)
            unchanged = cats == self._cat_cache.get(cid) and self.categories.count() > 0
            self._cat_cache[cid] = cats
            self._cat_ages[cid] = time.monotonic()
            if cid not in self.INTERNAL_CHANNELS:
                cancel = self._cache_event(cid)
                run_cache(lambda: self.config.cache_save(cid, "__categories__", cats), lambda *_: None,
                          key=(cid, "__categories__"), cancelled=cancel)
            if not unchanged or fresh:
                self._fill_categories(cid, cats, preferred=preferred, fresh=fresh)
        else:
            self._refreshing = False
            msg = result.message if isinstance(result, Result) else str(result)
            self._list_message(f"Showing saved stations — {cid}: {msg or 'No categories returned'}"
                               if self.model.rowCount() else f"Unable to load {cid}: {msg or 'No categories returned'}")
            self._retry_button.show()
            self._cancel_button.hide()

    def _fill_categories(self, cid: str, cats: list, preferred=None, fresh=False) -> None:
        ch = self.host.channels.get(cid)
        cats = list(cats)
        if getattr(ch, "category_sort", True):
            pins = [c for c in getattr(ch, "category_pins", ()) if c in cats]
            cats = pins + sorted((c for c in cats if c not in pins), key=str.lower)
        lcat = getattr(ch, "listeners_category", "")
        if lcat:
            all_cat = getattr(ch, "all_category", "")
            cats = [c for c in cats if c != lcat]
            cats.insert(cats.index(all_cat) + 1 if all_cat in cats else len(cats), lcat)
        self.categories.blockSignals(True)
        try:
            self.categories.clear()
            self.categories.addItems(cats)
            self.categories.setCurrentRow(cats.index(preferred) if preferred in cats else 0)
        finally:
            self.categories.blockSignals(False)
        self._on_category(self.categories.currentItem(), None, fresh=fresh)

    def _on_category(self, cur, _prev, fresh=False) -> None:
        if self._closing or not cur or cur.text() == "Loading…":
            return
        self._reset_load()
        self._view_search = False
        self._back_button.hide()
        self._search_query = None
        self.search.clear()
        self.current_category = cur.text()
        self._apply_default_sort()
        cid, cat = self.current_channel, self.current_category
        self._load_gen += 1
        gen = self._load_gen
        self._loading_key = None
        if cat == getattr(self.host.channels.get(cid), "listeners_category", ""):
            self._show_listeners(cid, gen)
            return
        cached = None if fresh or cid in self.INTERNAL_CHANNELS else self._cache.get((cid, cat))
        if cached is not None:
            self._show_cached_rows(cid, cat, cached)
            if self._cache.age((cid, cat)) > self._CACHE_STALE_S:
                self._bg_refresh(cid, cat)
        elif fresh or cid in self.INTERNAL_CHANNELS:
            self._retain_during_load = bool(fresh and self._refresh_view_key == (cid, cat) and self.model.rowCount())
            self._begin_load(cid, cat, gen)
        else:
            self.model.set_rows([])
            self.statusBar().showMessage(f"Loading {cat}…")
            run_cache(lambda: self.config.cache_load(cid, cat),
                      lambda disk: self._on_cached_rows(cid, cat, gen, disk))

    def _on_cached_rows(self, cid, cat, gen, disk) -> None:
        if self._closing or gen != self._load_gen or cid != self.current_channel:
            return
        if isinstance(disk, tuple):
            cached, age = disk
            self._cache.put((cid, cat), cached, age=age)
            self._update_cache_label()
            self._show_cached_rows(cid, cat, cached)
            if age > self._CACHE_STALE_S:
                self._bg_refresh(cid, cat)
        else:
            self._begin_load(cid, cat, gen)

    def _show_cached_rows(self, cid, cat, cached) -> None:
        rows = self._union(cid) if self._is_all(cid, cat) else cached
        self.model.set_rows(rows, validated=True)
        self._apply_column_visibility()
        self._load_station_favicons()
        self._list_message(f"Loaded {len(rows):,} saved stations")

    def _begin_load(self, cid, cat, gen) -> None:
        self._load_started = False
        self._load_complete = True
        self._load_errors = []
        self._fresh_rows = []
        self._fresh_seen = set()
        self._pending_restore = self._view_position() if self._retain_during_load else None
        if not self._retain_during_load:
            self.model.set_rows([])
        self._list_message(f"Refreshing {cat} — showing saved stations" if self._retain_during_load
                           else f"Loading {cat}…")
        first = getattr(self.host.channels.get(cid), "page_size", None) or self.ALL_LIMIT
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
        self._loading_key = (cid, cat)
        self._cancel_button.show()
        search, cancel = self._search_query, self._load_cancel
        run_async(
            lambda: self.host.call(cid, "update_streams_page", cat, offset, limit, search,
                                   timeout=45, cancel_event=cancel),
            lambda result: self._on_page(cid, cat, offset, gen, result), cancelled=cancel)


    def _on_page(self, cid, cat, offset, gen, result) -> None:
        if self._closing or gen != self._load_gen or cid != self.current_channel:
            return
        self._refresh_dot(cid)
        ch = self.host.channels.get(cid)
        if not (isinstance(result, Result) and result.ok):
            retry = (gen, offset)
            if offset and retry not in self._page_retries and self.host.is_enabled(cid):
                self._page_retries.add(retry)
                self._list_message(f"Loaded {self.model.rowCount():,} stations; retrying next batch…")
                def again():
                    if not self._closing and gen == self._load_gen and not self._load_cancel.is_set():
                        self._load_page(cid, cat, offset,
                                        getattr(ch, "page_batch_size", None) or self.ALL_LIMIT, gen)
                QTimer.singleShot(1000, again)
                return
            self._loading_key = None
            self._cancel_button.hide()
            self._refreshing = False
            self._load_complete = False
            message = getattr(result, "message", str(result)) or "Request failed"
            self._list_message(f"Refresh failed; saved stations retained. {self.config.redact(message)}"
                               if self._retain_during_load else f"Incomplete list: {self.config.redact(message)}")
            self._retry_button.show()
            return
        rows = result.data or []
        self._load_complete &= bool(getattr(result, "complete", True))
        self._load_errors.extend(getattr(result, "errors", ()) or ())
        if rows:
            if self._retain_during_load:
                for row in rows:
                    if row['url'] not in self._fresh_seen and len(self._fresh_rows) < self.ALL_LIMIT:
                        self._fresh_seen.add(row['url'])
                        self._fresh_rows.append(row)
                self._load_started = True
            elif offset == 0:
                self.model.set_rows(rows, validated=True)
                self._load_started = True
            else:
                remaining = self.ALL_LIMIT - self.model.rowCount()
                self.model.append_rows(rows[:max(0, remaining)], validated=True)
                if len(rows) > remaining:
                    self._load_complete = False
                    self._load_errors.append("Display limit reached; narrow the category or search")
                self._load_started = True
            self._apply_column_visibility()
            self._restore_view_position(self._pending_restore)
        more, next_offset = self._page_continuation(ch, cat, offset, result)
        loaded_count = len(self._fresh_rows) if self._retain_during_load else self.model.rowCount()
        if more and loaded_count < self.ALL_LIMIT:
            self._list_message(f"Refreshing: {loaded_count:,} stations received; showing saved stations"
                               if self._retain_during_load else f"Loaded {loaded_count:,} stations… loading more")
            self._load_page(cid, cat, next_offset,
                            getattr(ch, "page_batch_size", None) or getattr(ch, "page_size", None) or self.ALL_LIMIT, gen)
        else:
            if more:
                self._load_complete = False
            if not self._load_started and self._retain_during_load and self._load_complete:
                self.model.set_rows([])
            self._finish_load(cid, cat, ch)


    def _finish_load(self, cid, cat, ch) -> None:
        self._loading_key = None
        self._cancel_button.hide()
        self._refreshing = False
        complete = self._load_complete and not self._load_errors
        rows = list(self._fresh_rows) if self._retain_during_load else self.model.rows()
        if self._retain_during_load and complete:
            self._replace_rows(rows, validated=True)
        if self._view_search:
            self._list_message(f"{len(rows):,} results — {ch.title}: “{self._search_query}”"
                               + (" (partial; retry available)" if not complete else ""))
        elif complete:
            if cid not in self.INTERNAL_CHANNELS:
                self._cache[(cid, cat)] = rows
                self._save_catalog(cid, cat, rows)
            self._list_message(f"Loaded {len(rows):,} stations from {ch.title}" if rows else self._empty_message(cid, cat))
        else:
            detail = self.config.redact("; ".join(self._load_errors[:3]))
            self._list_message(f"Partial list: {len(rows):,} stations. Saved complete list retained. {detail}")
        self._retry_button.setVisible(not complete)
        self._retain_during_load = False
        self._fresh_rows = []
        self._fresh_seen = set()
        self._pending_restore = None
        self._update_cache_label()
        self._load_station_favicons()
        if cat == getattr(ch, "all_category", "") and self.current_category == getattr(ch, "listeners_category", ""):
            self._render_listeners(cid)


    def _is_all(self, cid: str, cat: str) -> bool:
        """Is `cat` this channel's 'everything' category (its view = the union pool)?"""
        ch = self.host.channels.get(cid)
        return bool(getattr(ch, "all_category", None)) and cat == ch.all_category

    def _union(self, cid):
        """A fresh complete All snapshot supersedes older category snapshots."""
        channel = self.host.channels.get(cid)
        all_key = (cid, getattr(channel, "all_category", None))
        cutoff = self._cache.timestamp(all_key)
        snapshots = [(self._cache.timestamp(key), values) for key, values in self._cache.items()
                     if key[0] == cid and self._cache.timestamp(key) >= cutoff]
        seen = {}
        for _stamp, values in sorted(snapshots, key=lambda item: item[0]):
            for row in values:
                if row.get("url"):
                    seen[row["url"]] = row
        return list(seen.values())

    def _apply_column_visibility(self) -> None:
        if not self.model.rowCount():
            return
        for col, (_label, field) in enumerate(COLUMNS):
            preferred = self.config.get("visible_columns", [])
            visible = self.model.has_field(field)
            if field == "url" and not preferred:
                visible = False
            if preferred:
                visible = visible and (field == "title" or field in preferred)
            self.table.setColumnHidden(col, not visible)


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
        from PySide6.QtWidgets import QApplication, QMenu
        menu = QMenu(self)
        menu.addAction("Play", lambda: (self.table.setCurrentIndex(idx), self._play_selected()))
        bm = self.host.channels.get("bookmarks")
        fav = bool(bm and bm.is_favourite(row.get("url", "")))
        menu.addAction("Remove favorite" if fav else "★ Favorite",
                       lambda: (self.table.setCurrentIndex(idx), self._toggle_favourite()))
        menu.addSeparator()
        menu.addAction("Copy URL", lambda: QApplication.clipboard().setText(redact_url(row.get("url", ""))))
        menu.addAction("Info…", lambda: self._station_info(row))
        menu.addAction("Recache icon", lambda: self._recache_station_icon(row))
        if row.get("homepage"):
            menu.addAction("Open Website",
                           lambda: self._open_website(row["homepage"]))
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
        from PySide6.QtWidgets import QApplication, QMenu
        menu = QMenu(self)
        track = clean_icy_title(self._np_full or "")
        if track:
            menu.addAction("Copy playing track", lambda: QApplication.clipboard().setText(track))
        menu.addAction("Copy URL", lambda: QApplication.clipboard().setText(redact_url(row.get("url", ""))))
        menu.addSeparator()
        menu.addAction("Info…", lambda: self._station_info(row))
        if row.get("homepage"):
            menu.addAction("Open Website", lambda: self._open_website(row["homepage"]))
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
        if self._closing:
            return
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
        if row:
            self._play_row(row)

    def _play_row(self, row) -> None:
        row = station_row(row)
        if row is None:
            self._status_error("Unsupported station URL")
            return
        self._play_cancel.set()
        self._play_cancel = threading.Event()
        cancel = self._play_cancel
        self._play_gen += 1
        self._pending_play = True
        self.btn_play.setChecked(True)
        self.act_stop.setEnabled(True)
        generation = self._play_gen
        src = self._resolve_source(row)
        row.setdefault("_source", src)
        ch = self.host.channels.get(src)
        if ch is not None and getattr(ch, "needs_resolve", False):
            self.np_label.setText(f"Resolving {row['title']}…")
            run_playback(lambda: self.host.call(src, "resolve_url", row, cancel_event=cancel, playback=True),
                         lambda res: self._start_playback(row, res, generation), cancelled=cancel)
        else:
            self._start_playback(row, None, generation)


    def _resolve_source(self, row: dict) -> str:
        """Which channel resolves this row's URL: its origin if known (favourites/history
        remember it), else the current channel — with a fallback for legacy TuneIn links."""
        if row.get("url", "").startswith("audioaddict://"):
            return "audioaddict"
        src = row.get("_source")
        if src and src in self.host.channels:
            return src
        if "opml.radiotime.com" in row.get("url", "") and "tunein" in self.host.channels:
            return "tunein"
        return self.current_channel

    def _start_playback(self, row: dict, resolved, generation=None) -> None:
        if self._closing or (generation is not None and generation != self._play_gen):
            return
        self._pending_play = False
        row = station_row(row)
        if row is None:
            self._status_error("Unsupported or invalid station URL")
            return
        if resolved is not None:
            if not isinstance(resolved, Result) or not resolved.ok or not isinstance(resolved.data, dict):
                self._status_error(self.config.redact(getattr(resolved, "message", "Unable to resolve station")))
                self.btn_play.setChecked(bool(self.player.is_playing))
                self.np_label.setText(self._np_full if self.player.is_playing else "Connection failed")
                return
            url = resolved.data.get("url")
        else:
            url = row["url"]
        if not valid_url(url):
            self._status_error("This station did not resolve to a supported network stream")
            return
        if not self.player.play(url):
            self._status_error(self.config.redact(self.player.error or "Audio playback is unavailable"))
            self.btn_play.setChecked(bool(self.player.is_playing))
            return
        self._play_started_at = time.monotonic()
        self._playing_bitrate = row.get("bitrate", 0)
        self._playing_row_url = row["url"]
        self._np_full = row["title"]
        self._np_meta = ""
        self._elide_np()
        self._playing_row = row
        self._update_art()
        self.btn_play.setChecked(True)
        self.act_play.setEnabled(True)
        bm = self.host.channels.get("bookmarks")
        if bm:
            hist = dict(row)
            hist.setdefault("_source", self._resolve_source(row))
            try:
                bm.add_history(hist)
            except (ValueError, OSError) as exc:
                self._status_error(self.config.redact(exc))
            self._cache.pop(("bookmarks", "History"), None)
            if self.current_channel == "bookmarks" and self.current_category == "History":
                self._replace_rows(bm.update_streams("History"))
        self.act_fav.setChecked(bool(bm and bm.is_favourite(row["url"])))
        self.np_label.setText(f"Connecting — {row['title']}")
        self.statusBar().showMessage(f"Connecting to {row['title']}")
        self._sync_visualizer()
        self._last_notified_title = ""
        self._notify(row["title"], row.get("genre") or row.get("description") or "Now playing", row)
        self._discord_update(row["title"], row.get("genre") or "Internet radio", reset=True)

    def _toggle_play(self, checked=None) -> None:
        play = not self.btn_play.isChecked() if checked is None else bool(checked)
        if play:
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
        self._play_gen += 1
        self._play_cancel.set()
        self._pending_play = False
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
        self._sync_visualizer()

    def _toggle_favourite(self, *_a) -> None:
        row = self._selected_row()
        bm = self.host.channels.get("bookmarks")
        if not row or not bm:
            return
        row = dict(row)
        row.setdefault("_source", self.current_channel)   # remember origin (for resolving on play)
        try:
            now = bm.toggle(row)
        except (OSError, ValueError) as exc:
            self._status_error(self.config.redact(exc))
            return
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
        if self._closing:
            return
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
        if self._closing or not self._playing_row or not self.player.is_playing:
            return
        title = self.config.redact(title)
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
        self.np_label.setTextFormat(Qt.PlainText)
        self.np_label.setText(title + meta)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "spectro", None) is not None:
            self.spectro.reset_width()    # window resized -> spectrogram back to its normal width
        if self._np_full:
            self._elide_np()

    # ---- misc actions ----
    def _build_colors_menu(self) -> None:
        from PySide6.QtGui import QColor, QPainter, QIcon
        from PySide6.QtWidgets import QToolTip
        from .color_palettes import COLOR_PALETTES
        self._color_actions = {}
        groups = {name: self._colors_menu.addMenu(name) for name in ("Bold", "Soft", "Classic")}
        for colors in COLOR_PALETTES:
            menu = groups[colors['group']]
            swatch = QPixmap(60, 18)
            painter = QPainter(swatch)
            for index, name in enumerate(('bg', 'panel', 'fg')):
                painter.fillRect(index * 20, 0, 20, 18, QColor(colors[name]))
            painter.end()
            action = QAction(QIcon(swatch), colors['name'], self, checkable=True)
            action.setChecked(self.config.get('theme', 'dark') == colors['id'])
            action.triggered.connect(lambda _checked=False, mode=colors['id']: self._set_theme(mode))
            action.hovered.connect(lambda palette=colors, item=action, parent=menu:
                                   self._preview_colors(palette, item, parent))
            menu.addAction(action)
            self._color_actions[colors['id']] = action
        for menu in groups.values():
            menu.aboutToHide.connect(QToolTip.hideText)

    def _preview_colors(self, colors, action, menu) -> None:
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QToolTip
        # Preview only this card. Hover never changes the app palette or saved settings.
        preview = (f"<table cellpadding='10' bgcolor='{colors['bg']}'><tr><td>"
                   f"<font color='{colors['fg']}'><b>{colors['name']}</b><br>Station · Genre</font>"
                   f"</td></tr><tr><td bgcolor='{colors['panel']}'>"
                   f"<font color='{colors['fg']}'>Click to apply these colors</font></td></tr></table>")
        QToolTip.showText(menu.mapToGlobal(menu.actionGeometry(action).topRight()) + QPoint(12, 0),
                          preview, menu)

    def _rebuild_theme_menu(self) -> None:
        """(Re)build the View → Theme submenu from the live theme registry (so user themes
        imported at runtime show up too)."""
        self._theme_menu.clear()
        self._theme_actions = {}
        current = self.config.get("theme", "dark")
        for mode, label in theme.THEME_LABELS.items():
            if mode.startswith('bs-'):
                continue  # the grouped Colors menu presents these palettes
            a = QAction(label, self, checkable=True)
            a.setChecked(current == mode)
            a.triggered.connect(lambda _=False, m=mode: self._set_theme(m))
            self._theme_menu.addAction(a)
            self._theme_actions[mode] = a

    def _set_theme(self, mode: str) -> None:
        self.config.set("theme", mode)
        self.config.save_settings()
        self._apply_appearance(mode)
        for m, a in {**self._theme_actions, **self._color_actions}.items():
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
        """Apply theme + wallpaper + font together: palette, a translucency-aware stylesheet, the app
        font, and the wallpaper pixmap. Used at startup and on every theme/wallpaper/font change."""
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        spec = self._active_wallpaper_spec(mode)
        fam, px = theme.app_font(self.config)
        f = app.font()
        if fam:
            f.setFamily(fam)
        f.setPixelSize(px)
        app.setFont(f)
        app.setPalette(theme.palette(mode))                              # keep OS dark-mode from bleeding in
        app.setStyleSheet(theme.stylesheet(mode, translucent=bool(spec), font_family=fam, font_px=px))
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
        if item and item.text() != "Loading…":
            self._reload(preferred=item.text())

    def _clear_channel_cache(self, cid) -> None:
        self._invalidate_source(cid)
        for key in list(self._cache):
            if key[0] == cid:
                self._cache.pop(key, None)
        self._cat_cache.pop(cid, None)
        run_cache(lambda: self.config.cache_clear_channel(cid), lambda *_: None)
        self._update_cache_label()


    def _reload(self, *_a, preferred=None) -> None:
        cid = self.current_channel
        if not cid:
            return
        if self.host.health[cid].auto_disabled:
            self.host.set_enabled(cid, True)
        selected = preferred or self.current_category
        self._refresh_view_key = (cid, selected)
        self._reset_load()
        self._invalidate_source(cid)
        self._channel_gen += 1
        self._load_gen += 1
        self._refreshing = True
        self._view_search = False
        self._search_query = None
        self._cache.mark_stale(cid)
        self._list_message(f"Refreshing {self.host.channels[cid].title} — saved stations remain available")
        self._fetch_categories(cid, self._channel_gen, selected, fresh=True)


    def _refresh_channel_cache(self, cid) -> None:
        if self.current_channel == cid:
            self._reload()
        else:
            self._invalidate_source(cid)
            self._cache.mark_stale(cid)
            self._cat_ages[cid] = 0


    def _update_cache_label(self) -> None:
        self._cache_label.setText("")
        self._cache_label.setToolTip(f"{len(self._cache)} cached lists; {self._cache.row_count:,} rows")


    def _bg_refresh(self, cid, cat) -> None:
        if self._closing or cid in self.INTERNAL_CHANNELS or not cat:
            return
        key = (cid, cat)
        if key in self._bg_inflight or key == self._loading_key:
            return
        self._bg_inflight.add(key)
        epoch, generation = self._cache_epoch.get(cid, 0), self._cache_generation
        cancel = self._cache_event(cid)
        ch = self.host.channels[cid]
        def refresh():
            categories = self.host.call(cid, "refresh_categories", timeout=45, cancel_event=cancel)
            if not categories.ok:
                return categories
            rows, seen, errors = [], set(), []
            offset, complete = 0, True
            while not cancel.is_set():
                limit = getattr(ch, "page_size", None) if offset == 0 else getattr(ch, "page_batch_size", None)
                result = self.host.call(cid, "update_streams_page", cat, offset, limit or self.ALL_LIMIT, None,
                                        timeout=45, cancel_event=cancel)
                if not result.ok:
                    return result
                complete &= getattr(result, "complete", True)
                errors.extend(getattr(result, "errors", ()) or ())
                for row in result.data or []:
                    if row["url"] not in seen:
                        seen.add(row["url"])
                        rows.append(row)
                more, next_offset = self._page_continuation(ch, cat, offset, result)
                if not more:
                    return Result(Status.OK, data=rows, complete=complete, errors=tuple(errors))
                if len(rows) >= self.ALL_LIMIT:
                    return Result(Status.OK, data=rows[:self.ALL_LIMIT], complete=False,
                                  errors=("Catalog limit reached",))
                offset = next_offset
            return Result(Status.ERROR, message="Refresh cancelled")
        run_async(refresh, lambda result: self._apply_bg_refresh(key, result, epoch, generation),
                  priority=10, cancelled=cancel)


    def _apply_bg_refresh(self, key, res, epoch=None, generation=None) -> None:
        self._bg_inflight.discard(key)
        cid, cat = key
        if (self._closing or epoch is not None and epoch != self._cache_epoch.get(cid, 0)
                or generation is not None and generation != self._cache_generation):
            return
        if not (getattr(res, "ok", False) and getattr(res, "complete", True)
                and not getattr(res, "errors", ())):
            if (self.current_channel, self.current_category) == key:
                self._list_message("Showing saved stations — refresh incomplete; use Retry")
                self._retry_button.show()
            return
        rows = res.data or []
        self._cache[key] = rows
        self._save_catalog(cid, cat, rows)
        self._update_cache_label()
        if (self.current_channel, self.current_category) == key and key != self._loading_key and not self._view_search:
            self._replace_rows(rows, validated=True)
            self._list_message(f"{cat}: refreshed ({len(rows):,} stations)")


    def _periodic_refresh(self) -> None:
        cid, cat = self.current_channel, self.current_category
        derived = cat == getattr(self.host.channels.get(cid), "listeners_category", "")
        if cid and cat and not self._view_search and not derived:
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
        sc("Ctrl+R", self._reload)
        sc("Ctrl+F", self._focus_search)
        sc("Ctrl+D", self._toggle_favourite)                 # add/remove the selected station
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
        self._sync_visualizer()
        self._sync_selection_actions()
        errors = self.config.take_write_errors() if hasattr(self.config, "take_write_errors") else []
        if errors:
            self._status_error("Data could not be saved: " + self.config.redact("; ".join(errors)))
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
                if self._playing_row:
                    if isinstance(buf, (int, float)) and buf < 100:
                        self.np_label.setText(f"Buffering — {self._np_full}")
                    elif codec:
                        self._elide_np()
                    else:
                        self.np_label.setText(f"Connecting — {self._np_full}")
                bits = [b for b in (self._np_meta.strip(), f"buffer {buf}%" if buf is not None else "") if b]
                self._stream_label.setText("▶ " + " · ".join(bits) if bits else "▶")
            else:
                self._stream_label.setText("" if self.player.available else "⚠ no audio engine")
                if self.btn_play.isChecked() and self._playing_row and time.monotonic() - self._play_started_at > 1:
                    failure = getattr(self.player, 'playback_error', '')
                    self._stop()
                    if isinstance(failure, str) and failure:
                        self._status_error("Stream unavailable: " + self.config.redact(failure))
                    else:
                        self.statusBar().showMessage("Playback ended")
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
        if action == "CLEAR":
            self._discord_pending = None
            self._discord_timer.stop()
            self._discord.clear()
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
        if not self._visualizer_active():
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

    def _on_search(self, text) -> None:
        self._pending_filter = text
        if self.search_scope.currentIndex() == 0:
            self._search_timer.start(220)
        elif not text and self._view_search:
            QTimer.singleShot(0, self._back_to_category)


    def _apply_filter(self) -> None:
        text = self._pending_filter
        self.proxy.setFilterFixedString(text)
        total = self.model.rowCount()
        if text:
            count = self.proxy.rowCount()
            self._list_message(f"{count:,} of {total:,} stations" if count else "No matches in this list — clear the filter or search this source")


    def _search_enter(self) -> None:
        query = self.search.text().strip()
        if not query:
            return
        scope = self.search_scope.currentIndex()
        if scope == 2:
            self._search_all_sources(query)
        elif scope == 1:
            self._search_channel(self.current_channel, query)
        else:
            self._apply_filter()


    def _search_channel(self, cid, query) -> None:
        channel = self.host.channels.get(cid)
        if not getattr(channel, "has_search", False):
            self._list_message("This service supports filtering its loaded list; choose Loaded list")
            return
        self._reset_load()
        self._load_gen += 1
        self._view_search = True
        self._search_query = query
        self._back_button.show()
        self._search_timer.stop()
        self.proxy.setFilterFixedString("")
        self.model.set_rows([])
        self._list_message(f"Searching {channel.title} for “{query}”…")
        cat = self.current_category or getattr(channel, "all_category", "") or ""
        self._load_page(cid, cat, 0, getattr(channel, "page_size", None) or self.ALL_LIMIT, self._load_gen)


    def _search_all_sources(self, query) -> None:
        candidates = [cid for cid, ch in self.host.channels.items()
                      if getattr(ch, "has_search", False) and self.host.is_enabled(cid)]
        if not candidates:
            self._list_message("No searchable services are enabled")
            return
        self._reset_load()
        self._load_gen += 1
        self._view_search = True
        self._search_query = query
        self._search_timer.stop()
        self.proxy.setFilterFixedString("")
        self.model.set_rows([])
        self._search_acc = {}
        self._search_pending = set(candidates)
        self._search_failures = {}
        self._search_done = set()
        self._back_button.show()
        self._cancel_button.show()
        self._list_message(f"Searching {len(candidates)} services…")
        for cid in candidates:
            self._search_source_page(self._load_gen, cid, query, 0)


    def _on_all_results(self, gen, cid, query, result) -> None:
        self._on_all_page(gen, cid, query, 0, result)


    def _open_website(self, url: str) -> None:
        if not valid_url(url, web=True):
            self._status_error("Only HTTP(S) website links can be opened")
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))

    def _open_location(self) -> None:
        from .dialogs import open_location
        url = open_location(self, self.config.get("theme", "dark"))
        if not url:
            return
        self._play_gen += 1
        self._start_playback({"title": redact_url(url), "url": url}, None, self._play_gen)

    def _add_local(self) -> None:
        from .dialogs import new_station
        result = new_station(self, self.config.get("theme", "dark"))
        if not result:
            return
        name, url = result
        local = self.host.channels.get("local")
        if local:
            try:
                local.add(name or redact_url(url), url)
                self.statusBar().showMessage(f"Added {name or redact_url(url)} to Local")
            except ValueError:
                self._status_error("A supported network stream URL is required")

    def _open_plugins_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from ..plugins.loader import user_plugin_dir
        d = user_plugin_dir(self.config)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))
        self.statusBar().showMessage(f"Drop a .py plugin in: {d}")

    def _on_cache_cleared(self) -> None:
        if self._closing:
            return
        self._cache.clear()
        self._cat_cache.clear()
        self._reload()


    def _open_options(self) -> None:
        from .options import OptionsDialog
        dlg = OptionsDialog(self.host, self.config, self, self.config.get("theme", "dark"))
        dlg.changed.connect(self._on_plugins_changed)
        dlg.visualization_changed.connect(self._set_visualization)
        dlg.volume_changed.connect(self.vol.setValue)
        dlg.album_art_changed.connect(self._toggle_art)
        dlg.settings_changed.connect(self._apply_general_settings)
        dlg.themes_reloaded.connect(self._rebuild_theme_menu)   # a theme was imported
        dlg.theme_changed.connect(self._set_theme)              # a theme was picked/applied
        dlg.cache_clearing.connect(self._before_cache_clear)
        dlg.icons_clearing.connect(self._before_icons_clear)
        dlg.icons_cleared.connect(self._after_icons_clear)
        dlg.cache_cleared.connect(self._on_cache_cleared)       # disk cache cleared -> drop in-memory + reload
        dlg.data_imported.connect(self._on_cache_cleared)       # backup imported -> refresh favourites/local
        dlg.wallpaper_dim_changed.connect(self._wallpaper_widget.set_dim)
        dlg.wallpaper_changed.connect(self._on_wallpaper_changed)
        dlg.font_changed.connect(lambda: self._apply_appearance(self.config.get("theme", "dark")))
        dlg.exec()

    def _apply_general_settings(self) -> None:
        self._toggle_normalize(bool(self.config.get("normalize", False)))
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
        self.statusBar().showMessage("⚠ " + self.config.redact(msg))

    def _save_layout(self) -> None:
        """Persist window size/position + table column widths/order to config."""
        import base64
        try:
            self.config.set("window_geometry",
                            base64.b64encode(self.saveGeometry().data()).decode("ascii"))
            self.config.set("pane_sizes", self._split.sizes())
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
        sizes = self.config.get("pane_sizes", [])
        if isinstance(sizes, list) and len(sizes) == 3 and all(type(x) is int and x >= 0 for x in sizes):
            self._split.setSizes(sizes)
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
            self._sync_visualizer()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()
            self._sync_visualizer()

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

    def _list_message(self, message):
        self._list_notice.setText(self.config.redact(message))
        self.statusBar().showMessage(self.config.redact(message))

    def _empty_message(self, cid, cat):
        if cid == "bookmarks" and cat == "Favorites":
            return "No favorites yet — choose a station and click Favorite"
        if cid == "bookmarks":
            return "Your recently played stations will appear here"
        if cid == "local":
            return "Add a station with File → New Local Station"
        return "No stations returned — try another category or Retry"

    def _scope_changed(self, *_):
        scope = self.search_scope.currentIndex()
        self.search.setPlaceholderText("Filter loaded stations" if scope == 0 else "Search stations…")
        self._search_submit_action.setVisible(scope != 0)
        self.search_submit.setVisible(scope != 0)
        if scope != 0:
            self._search_timer.stop()
            if hasattr(self, "proxy"):
                self.proxy.setFilterFixedString("")
        elif hasattr(self, "proxy"):
            self._on_search(self.search.text())

    def _back_to_category(self, *_):
        if not self._view_search:
            return
        self._view_search = False
        self._search_query = None
        self._back_button.hide()
        self._search_failures.clear()
        self._on_category(self.categories.currentItem(), None)

    def _search_source_page(self, gen, cid, query, offset):
        ch = self.host.channels[cid]
        category = getattr(ch, "all_category", "") or ""
        limit = (getattr(ch, "page_size", None) if not offset else getattr(ch, "page_batch_size", None)) or self.ALL_LIMIT
        cancel = self._load_cancel
        run_async(lambda: self.host.call(cid, "update_streams_page", category, offset, limit, query,
                                         timeout=45, cancel_event=cancel),
                  lambda result: self._on_all_page(gen, cid, query, offset, result), cancelled=cancel)

    def _on_all_page(self, gen, cid, query, offset, result):
        if self._closing or gen != self._load_gen or self._load_cancel.is_set():
            return
        ch = self.host.channels[cid]
        if not isinstance(result, Result) or not result.ok:
            self._search_failures[cid] = self.config.redact(getattr(result, "message", str(result)))
            self._search_pending.discard(cid)
        else:
            new = []
            for row in result.data or []:
                if len(self._search_acc) >= self.ALL_LIMIT:
                    self._search_failures[cid] = "Result limit reached; refine the search"
                    break
                if row["url"] not in self._search_acc:
                    row = dict(row)
                    row.setdefault("_source", cid)
                    self._search_acc[row["url"]] = row
                    new.append(row)
            self.model.append_rows(new, validated=True)
            self._apply_column_visibility()
            errors = getattr(result, "errors", ()) or ()
            if errors or not getattr(result, "complete", True):
                self._search_failures[cid] = self.config.redact("; ".join(errors) or "Partial results")
            more, next_offset = self._page_continuation(ch, getattr(ch, "all_category", "") or "", offset, result)
            if more and len(self._search_acc) < self.ALL_LIMIT:
                self._search_source_page(gen, cid, query, next_offset)
            else:
                self._search_pending.discard(cid)
                if more:
                    self._search_failures[cid] = "Result limit reached; refine the search"
        pending, failed = len(self._search_pending), len(self._search_failures)
        text = f"{self.model.rowCount():,} results"
        if pending:
            text += f"; {pending} services searching…"
        if failed:
            names = ", ".join(self.host.channels[c].title for c in self._search_failures)
            text += f"; {failed} services unavailable or partial: {names}"
        elif not pending and not self.model.rowCount():
            text = "No matching stations — try a different search"
        self._list_message(text)
        self._list_notice.setToolTip("\n".join(f"{self.host.channels[c].title}: {why}"
                                              for c, why in self._search_failures.items()))
        self._cancel_button.setVisible(bool(pending))
        self._retry_button.setVisible(bool(failed) and not pending)

    def _retry_failed_search(self, *_):
        if self._view_search and getattr(self, "_search_pending", set()):
            return
        if self._view_search and self._search_failures:
            failed = tuple(self._search_failures)
            self._search_failures.clear()
            self._search_pending = set(failed)
            self._retry_button.hide()
            self._cancel_button.show()
            self._list_message(f"Retrying {len(failed)} services; keeping previous results…")
            for cid in failed:
                if self.host.health[cid].auto_disabled:
                    self.host.set_enabled(cid, True)
                self._search_source_page(self._load_gen, cid, self._search_query, 0)
        elif self._view_search:
            self._search_channel(self.current_channel, self._search_query)
        else:
            self._reload()

    def _reset_load(self):
        self._load_cancel.set()
        self._load_cancel = threading.Event()
        self._loading_key = None
        self._page_retries.clear()
        self._load_started = False
        self._load_complete = True
        self._load_errors = []
        self._retain_during_load = False
        self._fresh_rows = []
        self._fresh_seen = set()
        self._pending_restore = None
        if hasattr(self, "_cancel_button"):
            self._cancel_button.hide()
            self._retry_button.hide()

    def _cancel_load(self, *_):
        self._load_cancel.set()
        self._load_gen += 1
        self._channel_gen += 1
        self._loading_key = None
        self._refreshing = False
        self._cancel_button.hide()
        self._list_message("Loading cancelled — displayed stations remain usable")

    @staticmethod
    def _page_continuation(channel, category, offset, result):
        explicit = getattr(result, "has_more", None)
        if explicit is not None:
            next_offset = getattr(result, "next_offset", None)
            if next_offset is None:
                next_offset = offset + (getattr(channel, "page_batch_size", None)
                                        or getattr(channel, "page_size", None) or 1)
            return bool(explicit and next_offset > offset), next_offset
        rows = result.data or []
        if getattr(channel, "all_chunked", False) and category == getattr(channel, "all_category", None):
            return bool(rows), offset + 1
        size = getattr(channel, "page_size", None) if offset == 0 else (getattr(channel, "page_batch_size", None) or getattr(channel, "page_size", None))
        return bool(rows and size and offset + size < MAX_ROWS), offset + (size or 1)

    def _cache_event(self, cid):
        return self._cache_events.setdefault(cid, threading.Event())

    def _invalidate_source(self, cid):
        self._cache_epoch[cid] = self._cache_epoch.get(cid, 0) + 1
        self._cache_event(cid).set()
        self._cache_events[cid] = threading.Event()
        self._bg_inflight = {key for key in self._bg_inflight if key[0] != cid}

    def _save_catalog(self, cid, cat, rows):
        cancel = self._cache_event(cid)
        run_cache(lambda snapshot=list(rows): self.config.cache_save(cid, cat, snapshot),
                  lambda *_: None, key=(cid, cat), cancelled=cancel)

    def _before_cache_clear(self):
        if self._closing:
            return
        self._cache_generation += 1
        self._reset_load()
        self._load_gen += 1
        self._channel_gen += 1
        for cid in self.host.channels:
            self._invalidate_source(cid)
        self._cache.clear()
        self._cat_cache.clear()

    def _trim_icons(self, cache):
        while len(cache) > 512:
            cache.popitem(last=False)
        while len(self._icon_retry_at) > 1024:
            self._icon_retry_at.popitem(last=False)

    def _icon_failed(self, kind, url, result):
        delay = 2 if isinstance(result, BaseException) else 60
        self._icon_retry_at[(kind, url)] = time.monotonic() + delay
        self._trim_icons(self._station_icons)
        self._trim_icons(self._derived_icons)
        QTimer.singleShot(delay * 1000, self._retry_visible_icons)

    def _retry_visible_icons(self):
        if not self._closing:
            self.table.viewport().update()

    def _cancel_pending_icons(self):
        self._icon_cancel.set()
        self._icon_cancel = threading.Event()
        self._icon_epoch += 1
        for cache in (self._station_icons, self._derived_icons):
            for key in [key for key, value in cache.items() if value is None]:
                cache.pop(key, None)

    def _before_icons_clear(self):
        if self._closing:
            return
        self._icons_clearing = True
        self._icon_cancel.set()
        self._icon_cancel = threading.Event()
        self._icon_epoch += 1
        if hasattr(self.config, "invalidate_icons"):
            self.config.invalidate_icons()
        self._station_icons.clear()
        self._derived_icons.clear()
        self._icon_retry_at.clear()
        self._favicons.clear()
        self._svc_icons.clear()

    def _after_icons_clear(self):
        if self._closing:
            return
        self._icons_clearing = False
        self._icon_epoch += 1
        self._station_icons.clear()
        self._derived_icons.clear()
        self._load_channel_favicons()
        self._update_art()
        self.table.viewport().update()

    def _set_column_visible(self, field, enabled):
        current = self.config.get("visible_columns", [])
        valid = {name for _label, name in COLUMNS}
        chosen = {name for name in current if isinstance(name, str) and name in valid} if current else valid - {"url"}
        if enabled:
            chosen.add(field)
        else:
            chosen.discard(field)
        chosen.add("title")
        self.config.set("visible_columns", [name for _label, name in COLUMNS if name in chosen])
        self.config.save_settings()
        self._apply_column_visibility()

    def _sync_selection_actions(self):
        row = self._selected_row()
        bm = self.host.channels.get("bookmarks")
        self.act_fav.setEnabled(bool(row and bm))
        self.act_fav.setChecked(bool(row and bm and bm.is_favourite(row["url"])))
        self.act_play.setEnabled(bool(row))
        self.act_stop.setEnabled(bool(self._pending_play or self.player.is_playing))

    def _view_position(self):
        row = self._selected_row()
        return (row.get("url") if row else None, self.table.verticalScrollBar().value())

    def _restore_view_position(self, position):
        if not position:
            return
        url, scroll = position
        index = self.model._positions.get(url)
        if index is not None:
            self.table.setCurrentIndex(self.proxy.mapFromSource(self.model.index(index, 0)))
        self.table.verticalScrollBar().setValue(scroll)

    def _replace_rows(self, rows, *, validated=False):
        position = self._view_position()
        self.model.set_rows(rows, validated=validated)
        self._apply_column_visibility()
        self._restore_view_position(position)

    def _visualizer_active(self):
        return (self._viz != "off" and self.isVisible() and not self.isMinimized()
                and bool(self.player.is_playing) and not self._closing)

    def _sync_visualizer(self):
        active = self._visualizer_active()
        if active and not self._vu_timer.isActive():
            self._vu_timer.start(33)
        elif not active and self._vu_timer.isActive():
            self._vu_timer.stop()
            self.vu.idle()
            self.spectro.idle()
        if hasattr(self.player, "enable_meter") and getattr(self, "_meter_active", None) != active:
            self.player.enable_meter(active)
            self._meter_active = active

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "_vu_timer"):
            self._sync_visualizer()

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, "_vu_timer"):
            self._sync_visualizer()

    def changeEvent(self, event):
        super().changeEvent(event)
        if hasattr(self, "_vu_timer"):
            self._sync_visualizer()

    def closeEvent(self, e):
        tray = getattr(self, "_tray", None)
        if (not getattr(self, "_really_quit", False)
                and self.config.get("tray_close", False)
                and tray is not None and tray.isVisible()):
            e.ignore()                   # close-to-tray: hide + keep playing; Quit from the tray menu
            self.hide()
            return
        self._save_layout()
        if hasattr(self.config, "retry_failed_writes"):
            self.config.retry_failed_writes()
        if hasattr(self.config, "flush_writes"):
            drained = self.config.flush_writes(timeout=5)
            if not drained and getattr(self.config, "has_unsaved_writes", True):
                e.ignore()
                self._status_error("Changes are not saved yet. Keep this window open and retry closing after storage is available.")
                return
        self._closing = True
        if hasattr(self, "_import_cancel"):
            self._import_cancel.set()
        for cancel in tuple(getattr(self, "_export_cancels", ())):
            cancel.set()
        self._load_cancel.set()
        self._play_cancel.set()
        self._icon_cancel.set()
        for event in self._cache_events.values():
            event.set()
        self._play_gen += 1
        self._load_gen += 1
        mk = getattr(self, "_media_keys", None)
        if mk is not None:
            mk.uninstall()               # release the global media-key grab
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

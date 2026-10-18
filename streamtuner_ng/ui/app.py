"""Launch the GUI: build the shared Config / PluginHost / Player, register the
built-in channels, apply the theme, show the window (+ tray)."""

from __future__ import annotations

import sys
import time

from .. import APP_NAME, asset_path
from ..config import Config
from ..player import Player
from ..plugins.channels import discover_builtin_channels
from ..plugins.channels.bookmarks import Bookmarks
from ..plugins.channels.local import Local
from ..plugins.host import PluginHost


def run_app() -> int:
    from PySide6.QtWidgets import QApplication

    from PySide6.QtCore import Qt, qInstallMessageHandler

    def _quiet(_mode, _ctx, msg):
        # drop a benign Wayland popup-grab notice; forward everything else to stderr
        if "grabbing the mouse only for popup" in msg:
            return
        if sys.stderr is not None:
            sys.stderr.write(msg + "\n")

    qInstallMessageHandler(_quiet)

    app = QApplication.instance() or QApplication(sys.argv)
    # Force Fusion on every platform so our stylesheet renders identically. Linux already defaults
    # to Fusion; Windows defaults to its native style, which only partially honours the QSS (e.g. it
    # won't fill the row-selection color across the table) — Fusion fixes that.
    app.setStyle("Fusion")
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    from PySide6.QtGui import QIcon
    app.setWindowIcon(QIcon(asset_path("logo.png")))   # taskbar / title-bar / alt-tab icon
    app.setQuitOnLastWindowClosed(True)
    # menus snap open instantly (no fade/animate delay — feels much faster)
    app.setEffectEnabled(Qt.UIEffect.UI_AnimateMenu, False)
    app.setEffectEnabled(Qt.UIEffect.UI_FadeMenu, False)

    started = time.monotonic()
    config = Config()
    config.enable_async_writes()
    host = PluginHost(config)
    host.register(Bookmarks(config))
    host.register(Local(config))

    player = Player()
    # belt-and-braces: stop audio whenever the app is quitting, however it quits
    app.aboutToQuit.connect(player.stop)
    app.aboutToQuit.connect(player.shutdown)

    from .theme import app_font, load_user_themes, palette, stylesheet
    load_user_themes(config)               # register any user themes/*.json before we apply one
    _mode = config.get("theme", "dark")
    _ffam, _fpx = app_font(config)         # app-wide font (Segoe UI default on Windows; user-settable)
    _font = app.font()
    if _ffam:
        _font.setFamily(_ffam)
    _font.setPixelSize(_fpx)
    app.setFont(_font)
    app.setPalette(palette(_mode))         # so the OS palette (e.g. Windows dark mode) can't bleed in
    app.setStyleSheet(stylesheet(_mode, font_family=_ffam, font_px=_fpx))

    from .worker import set_io_threads
    set_io_threads(int(config.get("io_threads", 4)))   # apply the saved icon-fetch concurrency

    from .mainwindow import MainWindow
    win = MainWindow(config, host, player)

    from .tray import build_tray
    win._tray = build_tray(win, host, player)   # keep a ref so it isn't GC'd

    win.show()
    win._startup_timings = {"shell_seconds": time.monotonic() - started}
    win._list_message("Loading services…")
    from .worker import run_io
    from PySide6.QtCore import QTimer
    import threading
    boot_cancel = threading.Event()
    app.aboutToQuit.connect(boot_cancel.set)

    def discover():
        classes, errors = discover_builtin_channels()
        staged = PluginHost(config)
        for cls in classes:
            if cls.id in ("bookmarks", "local"):
                continue
            try:
                staged.register(cls(config))
            except BaseException as exc:
                errors.append((getattr(cls, "id", cls.__name__), config.redact(str(exc))))
        from ..plugins.loader import register_user_plugins
        _count, legacy_errors = register_user_plugins(staged, config)
        return list(staged.channels.values()), list(errors) + legacy_errors

    def ready(result):
        if win._closing or boot_cancel.is_set():
            return
        if isinstance(result, BaseException):
            win._list_message("Service discovery failed: " + config.redact(result))
            return
        channels, errors = result
        for channel in channels:
            try:
                host.register(channel)
            except (ValueError, TypeError) as exc:
                errors.append((channel.id, config.redact(exc)))
        host.load_errors = errors
        win._populate_channels()
        win._rebuild_channels_menu()
        win._load_channel_favicons()
        win._startup_timings["services_seconds"] = time.monotonic() - started
        if errors:
            win._list_message(f"{len(channels)} services loaded; {len(errors)} plugin(s) could not load")
            win._list_notice.setToolTip("\n".join(f"{name}: {error}" for name, error in errors))
        elif not win.model.rowCount():
            win._list_message(win._empty_message(win.current_channel, win.current_category))
        # Legacy icon conversion is optional maintenance after first paint.
        from ..favicons import migrate_ico_to_png
        run_io(lambda: migrate_ico_to_png(config), lambda *_: win.table.viewport().update(), cancelled=boot_cancel)

    QTimer.singleShot(0, lambda: run_io(discover, ready, cancelled=boot_cancel))
    rc = app.exec()
    from .worker import drain_cache
    drain_cache()
    config.flush_writes()
    player.shutdown()
    return rc

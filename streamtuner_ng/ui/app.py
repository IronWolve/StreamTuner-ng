"""Launch the GUI: build the shared Config / PluginHost / Player, register the
built-in channels, apply the theme, show the window (+ tray)."""

from __future__ import annotations

import sys

from .. import APP_NAME, asset_path
from ..config import Config
from ..player import Player
from ..plugins.channels import BUILTINS, PLUGIN_ERRORS
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

    config = Config()
    host = PluginHost(config)
    plugin_errors = list(PLUGIN_ERRORS)
    for cls in BUILTINS:
        try:
            host.register(cls(config))
        except BaseException as exc:
            plugin_errors.append((getattr(cls, 'id', cls.__name__), config.redact(str(exc))))

    # user plugins: anyone can drop a .py in ~/.config/streamtuner-ng/plugins/
    from ..plugins.loader import register_user_plugins
    n_user, legacy_errors = register_user_plugins(host, config)
    plugin_errors.extend(legacy_errors)

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

    from ..favicons import migrate_ico_to_png
    migrate_ico_to_png(config)             # cached .ico logos -> PNG (Qt renders ICO unreliably; e.g. SHOUTcast)

    from .worker import set_io_threads
    set_io_threads(int(config.get("io_threads", 4)))   # apply the saved icon-fetch concurrency

    from .mainwindow import MainWindow
    win = MainWindow(config, host, player)

    from .tray import build_tray
    win._tray = build_tray(win, host, player)   # keep a ref so it isn't GC'd

    win.show()
    if n_user or plugin_errors:
        win.statusBar().showMessage(
            f"Loaded {n_user} user plugin(s)"
            + (f" — {len(plugin_errors)} failed (see Options)" if plugin_errors else "")
        )
    rc = app.exec()
    from .worker import drain_cache
    drain_cache()
    player.shutdown()
    return rc

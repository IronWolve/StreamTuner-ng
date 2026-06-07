"""Launch the GUI: build the shared Config / PluginHost / Player, register the
built-in channels, apply the theme, show the window (+ tray)."""

from __future__ import annotations

import sys

from .. import APP_NAME, asset_path
from ..config import Config
from ..player import Player
from ..plugins.channels import BUILTINS
from ..plugins.host import PluginHost


def run_app() -> int:
    from PySide6.QtWidgets import QApplication

    from PySide6.QtCore import Qt, qInstallMessageHandler

    def _quiet(_mode, _ctx, msg):
        # drop a benign Wayland popup-grab notice; forward everything else to stderr
        if "grabbing the mouse only for popup" in msg:
            return
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
    if sys.platform.startswith("win"):       # Qt's default Win font (MS Shell Dlg) looks dated;
        f = app.font()                       # Segoe UI is the native, clean Windows UI font

        f.setFamily("Segoe UI")
        app.setFont(f)
    # menus snap open instantly (no fade/animate delay — feels much faster)
    app.setEffectEnabled(Qt.UIEffect.UI_AnimateMenu, False)
    app.setEffectEnabled(Qt.UIEffect.UI_FadeMenu, False)

    config = Config()
    host = PluginHost(config)
    for cls in BUILTINS:
        host.register(cls(config))

    # user plugins: anyone can drop a .py in ~/.config/streamtuner-ng/plugins/
    from ..plugins.loader import register_user_plugins
    n_user, plugin_errors = register_user_plugins(host, config)

    player = Player()
    # belt-and-braces: stop audio whenever the app is quitting, however it quits
    app.aboutToQuit.connect(player.stop)
    app.aboutToQuit.connect(player.shutdown)

    from .theme import load_user_themes, palette, stylesheet
    load_user_themes(config)               # register any user themes/*.json before we apply one
    _mode = config.get("theme", "dark")
    app.setPalette(palette(_mode))         # so the OS palette (e.g. Windows dark mode) can't bleed in
    app.setStyleSheet(stylesheet(_mode))

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
    player.shutdown()
    return rc

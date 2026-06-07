"""StreamTuner-ng — a modern, cross-platform internet-radio browser.

Re-interpretation of the Public-Domain StreamTuner2 idea (channels -> genres ->
stations -> play) on a PySide6 + libmpv stack, with crash-isolated plugins.

This package is layered so the core (config, plugins, net) never imports the UI,
which keeps it headless-testable -- see `python -m streamtuner_ng --selftest`.
"""

# Single source of truth for the version (read everywhere; never duplicated).
__version__ = "1.2.0"

APP_NAME = "StreamTuner-ng"   # human-facing display name
APP_ID = "streamtuner-ng"     # filesystem / config-dir name (lowercase)


def asset_path(name: str) -> str:
    """Absolute path to a bundled asset (streamtuner_ng/assets/<name>), working both from source
    and from a PyInstaller build (where data files land under sys._MEIPASS)."""
    import os
    import sys
    if hasattr(sys, "_MEIPASS"):
        p = os.path.join(sys._MEIPASS, "streamtuner_ng", "assets", name)
        if os.path.exists(p):
            return p
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", name)

#!/usr/bin/env bash
# Convenience launcher: runs the app from the project's venv without activating it.
#   ./run.sh            launch the GUI
#   ./run.sh --selftest headless self-check
cd "$(dirname "$0")" || exit 1

# Wayland-only sessions (e.g. WSLg on Ubuntu 26.04 set WAYLAND_DISPLAY but no
# DISPLAY): tell Qt to use the wayland platform instead of defaulting to xcb/X11.
if [ -z "$DISPLAY" ] && [ -n "$WAYLAND_DISPLAY" ] && [ -z "$QT_QPA_PLATFORM" ]; then
    export QT_QPA_PLATFORM=wayland
fi

exec .venv/bin/python run.py "$@"

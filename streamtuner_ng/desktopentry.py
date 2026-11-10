"""Give an AppImage its own launcher entry and icon, so the dock and the menu show the app's icon.

A bare AppImage installs nothing. GNOME's dock (on Wayland) takes an app's icon from an installed
.desktop file whose name matches the app, never from the window itself, so a downloaded AppImage
shows the generic gear. When the app runs from an AppImage it therefore writes, for this user only,
``applications/<id>.desktop`` and the icon files under ``icons/hicolor`` in the XDG data folder.

The entry carries a marker: one that is not ours is never touched, and ours is rewritten only when
the AppImage moved or the icon changed. Turn it off with the setting ``desktop_integration`` = false.

The file manager draws its own generic icon for an AppImage file (it does not read the icon inside),
so the app also asks GNOME Files to show its icon on the AppImage file itself (``gio set`` of
``metadata::custom-icon``); a custom icon the user chose is never replaced.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

MARKER = "X-StreamTuner-Managed=true"
SIZES = (512, 256, 128, 64, 48)
SETTING = "desktop_integration"


def data_home() -> Path:
    env = os.environ.get("XDG_DATA_HOME", "")
    return Path(env) if env and os.path.isabs(env) else Path.home() / ".local" / "share"


def _quoted(path: str) -> str | None:
    """The AppImage path as one quoted Exec argument, or None for a path too unusual to quote safely."""
    if any(ch in path for ch in '"`$\\') or any(ord(ch) < 32 for ch in path):
        return None
    return '"' + path.replace("%", "%%") + '"'


def entry_text(appimage: str, name: str, app_id: str, icon_stamp: str = "") -> str | None:
    quoted = _quoted(appimage)
    if quoted is None:
        return None
    lines = ["[Desktop Entry]", "Type=Application", f"Name={name}", "GenericName=Internet Radio",
             "Comment=Browse, search and play internet radio stations", f"Exec={quoted} %U",
             f"TryExec={appimage}", f"Icon={app_id}", "Terminal=false",
             "Categories=AudioVideo;Audio;Player;Network;", "Keywords=radio;stream;shoutcast;icecast;music;",
             MARKER]
    if icon_stamp:
        lines.append(f"X-StreamTuner-Icon={icon_stamp}")
    return "\n".join(lines) + "\n"


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + ".tmp")
    temp.write_bytes(data)
    os.chmod(temp, 0o644)
    os.replace(temp, path)


def _icons(home: Path, source: Path, app_id: str, force: bool) -> int:
    """Write the icon at each size that is missing (all of them when ``force``); returns how many."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QImage
    image = QImage(str(source))
    if image.isNull():
        raise OSError(f"icon unreadable: {source}")
    written = 0
    for size in SIZES:
        target = home / "icons" / "hicolor" / f"{size}x{size}" / "apps" / f"{app_id}.png"
        if not force and target.is_file() and target.stat().st_size > 0:
            continue
        scaled = image.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        data, buffer = QByteArray(), QBuffer()
        buffer.setBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        scaled.save(buffer, "PNG")
        buffer.close()
        _write(target, bytes(data))
        written += 1
    return written


def _bundle_dirs(env: dict) -> list[str]:
    """Folders that belong to this app's own bundle (an AppImage mount, the frozen app folder)."""
    found = [env.get("APPDIR"), getattr(sys, "_MEIPASS", None)]
    if getattr(sys, "frozen", False):
        found.append(os.path.dirname(sys.executable))
    return [os.path.realpath(p).rstrip("/") for p in found if p]


def _system_env() -> dict:
    """The environment for a system tool: the bundle's library folders removed from LD_LIBRARY_PATH.

    The AppImage points LD_LIBRARY_PATH at its own libraries (and the frozen app remembers the value it
    found in LD_LIBRARY_PATH_ORIG). A system tool such as ``gio`` loaded against those old libraries dies
    with a symbol error on a newer system (glib 2.88 on Ubuntu 26.04), so only folders outside the bundle stay."""
    env = dict(os.environ)
    bundle = _bundle_dirs(env)
    value = env.pop("LD_LIBRARY_PATH_ORIG", env.get("LD_LIBRARY_PATH", ""))
    kept = [p for p in value.split(":") if p and not any(
        os.path.realpath(p).rstrip("/") == b or os.path.realpath(p).startswith(b + "/") for b in bundle)]
    if kept:
        env["LD_LIBRARY_PATH"] = ":".join(kept)
    else:
        env.pop("LD_LIBRARY_PATH", None)
    env.pop("LD_PRELOAD", None)
    return env


def mark_file(appimage: str, icon: Path, app_id: str) -> str:
    """Show the app icon on the AppImage file in the file manager; returns what happened, as a short phrase."""
    gio = shutil.which("gio")
    if not gio:
        return "no gio"
    uri = icon.resolve().as_uri()
    env = _system_env()
    try:
        info = subprocess.run([gio, "info", "-a", "metadata::custom-icon", appimage],
                              capture_output=True, text=True, timeout=8, env=env)
        current = ""
        for line in info.stdout.splitlines():
            if "metadata::custom-icon:" in line:
                current = line.split("metadata::custom-icon:", 1)[1].strip()
        if current == uri:
            return "already set"
        if current and not current.endswith(f"/apps/{app_id}.png"):
            return "kept: another icon is set"
        done = subprocess.run([gio, "set", appimage, "metadata::custom-icon", uri],
                              capture_output=True, text=True, timeout=8, env=env)
        return "set" if done.returncode == 0 else "failed: " + done.stderr.strip()[:80]
    except (OSError, subprocess.SubprocessError) as exc:
        return f"failed: {exc}"


def integrate(appimage: str | None, icon_source: str | os.PathLike, *, name: str, app_id: str,
              home: str | os.PathLike | None = None, enabled: bool = True, mark: bool = False) -> str:
    """Install or refresh the launcher entry and icons; returns what happened, as a short phrase.
    With ``mark`` the AppImage file also gets the app icon in the file manager."""
    if not enabled:
        return "disabled"
    if not appimage or not os.path.isabs(appimage) or not os.path.isfile(appimage):
        return "not an AppImage"
    source = Path(icon_source)
    try:
        stamp = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    except OSError as exc:
        return f"failed: {exc}"
    text = entry_text(appimage, name, app_id, stamp)
    if text is None:
        return "skipped: unusual AppImage path"
    base = Path(home) if home else data_home()
    entry = base / "applications" / f"{app_id}.desktop"
    try:
        if entry.is_file():
            current = entry.read_text(encoding="utf-8", errors="replace")
            if MARKER not in current:
                return "kept: an entry of your own exists"
            status = "unchanged" if current == text else "updated"
        else:
            status = "created"
        icons = _icons(base, source, app_id, force=status != "unchanged")
        if status != "unchanged":
            _write(entry, text.encode("utf-8"))
        elif icons:
            status = "updated"
        if mark:
            mark_file(appimage, base / "icons" / "hicolor" / "512x512" / "apps" / f"{app_id}.png", app_id)
    except OSError as exc:
        return f"failed: {exc}"
    return status


def integrate_appimage(config, icon_source: str | os.PathLike) -> str:
    """Called at start-up: does nothing unless the app is running from an AppImage and the setting allows it."""
    from . import APP_ID, APP_NAME
    return integrate(os.environ.get("APPIMAGE"), icon_source, name=APP_NAME, app_id=APP_ID,
                     enabled=bool(config.get(SETTING, True)), mark=True)

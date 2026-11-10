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
import struct
import subprocess
import sys
from pathlib import Path

MARKER = "X-StreamTuner-Managed=true"
SIZES = (512, 256, 128, 64, 48)
SETTING = "desktop_integration"

# The AppImage build appends a small PNG of the app icon to the end of the file, followed by its length
# (4 bytes, big-endian) and this tag. A thumbnailer can then read the icon with nothing but python3, never
# running the AppImage, and so works inside GNOME's thumbnail sandbox, which cannot reach the home folder.
ICON_MAGIC = b"STNGICN1"
ICON_FOOTER = 12                      # 4 bytes of length + the 8-byte tag
THUMB_SIZE = 256
THUMBNAILER_NAME = "{app_id}-appimage.thumbnailer"
THUMBNAILER_NOTE = "# Managed by StreamTuner-ng: shows its icon on its AppImage file (desktop_integration)."
# Reads argv[1] (the AppImage) and writes the appended icon to argv[2]; exits 1 for any AppImage without it.
THUMBNAILER_PY = ("import sys,struct;f=open(sys.argv[1],'rb');f.seek(-12,2);t=f.read(12);"
                  "n=struct.unpack('>I',t[:4])[0] if t[4:]==b'STNGICN1' else 0;(0<n<8388608) or sys.exit(1);"
                  "f.seek(-12-n,2);d=f.read(n);d[:4]==bytes([137])+b'PNG' or sys.exit(1);"
                  "open(sys.argv[2],'wb').write(d)")


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


def scaled_png(source: str | os.PathLike, size: int) -> bytes:
    """The picture at ``source`` scaled to size x size, as PNG bytes."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QImage
    image = QImage(str(source))
    if image.isNull():
        raise OSError(f"icon unreadable: {source}")
    scaled = image.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    data, buffer = QByteArray(), QBuffer()
    buffer.setBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    scaled.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def icon_footer(png: bytes) -> bytes:
    """What the build appends to an AppImage: the icon, its length, the tag."""
    return png + struct.pack(">I", len(png)) + ICON_MAGIC


def read_icon_footer(path: str | os.PathLike) -> bytes | None:
    """The icon appended to the file at ``path``, or None when it has none."""
    try:
        with open(path, "rb") as handle:
            handle.seek(-ICON_FOOTER, os.SEEK_END)
            tail = handle.read(ICON_FOOTER)
            if tail[4:] != ICON_MAGIC:
                return None
            length = struct.unpack(">I", tail[:4])[0]
            if not 0 < length < 8 * 1024 * 1024:
                return None
            handle.seek(-ICON_FOOTER - length, os.SEEK_END)
            png = handle.read(length)
    except OSError:
        return None
    return png if png.startswith(b"\x89PNG") else None


def thumbnailer_text() -> str:
    return ("[Thumbnailer Entry]\n" + THUMBNAILER_NOTE + "\nTryExec=/usr/bin/python3\n"
            f'Exec=/usr/bin/python3 -c "{THUMBNAILER_PY}" %i %o\nMimeType=application/vnd.appimage;\n')


def _forget_failed_thumbnails(appimage: str) -> None:
    """A thumbnail that failed before is not retried; drop any such record for this file."""
    cache = os.environ.get("XDG_CACHE_HOME", "")
    root = Path(cache) if cache and os.path.isabs(cache) else Path.home() / ".cache"
    name = hashlib.md5(Path(appimage).as_uri().encode()).hexdigest() + ".png"
    for record in (root / "thumbnails" / "fail").glob(f"*/{name}"):
        try:
            record.unlink()
        except OSError:
            pass


def install_thumbnailer(appimage: str, home: Path, app_id: str) -> str:
    """Install the user-level thumbnailer that draws the icon appended to this AppImage."""
    if read_icon_footer(appimage) is None:
        return "no icon appended to this AppImage"
    target = home / "thumbnailers" / THUMBNAILER_NAME.format(app_id=app_id)
    text = thumbnailer_text()
    try:
        if target.is_file():
            current = target.read_text(encoding="utf-8", errors="replace")
            if THUMBNAILER_NOTE not in current:
                return "kept: a thumbnailer of your own exists"
            if current == text:
                return "unchanged"
            status = "updated"
        else:
            status = "created"
        _write(target, text.encode("utf-8"))
        _forget_failed_thumbnails(appimage)
    except OSError as exc:
        return f"failed: {exc}"
    return status


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + ".tmp")
    temp.write_bytes(data)
    os.chmod(temp, 0o644)
    os.replace(temp, path)


def _icons(home: Path, source: Path, app_id: str, force: bool) -> int:
    """Write the icon at each size that is missing (all of them when ``force``); returns how many."""
    written = 0
    for size in SIZES:
        target = home / "icons" / "hicolor" / f"{size}x{size}" / "apps" / f"{app_id}.png"
        if not force and target.is_file() and target.stat().st_size > 0:
            continue
        _write(target, scaled_png(source, size))
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
              home: str | os.PathLike | None = None, enabled: bool = True, mark: bool = False,
              thumbs: bool = False) -> str:
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
        if thumbs:
            install_thumbnailer(appimage, base, app_id)
    except (OSError, ValueError) as exc:           # ValueError: a path that cannot be encoded
        return f"failed: {exc}"
    return status


def trusted_appimage() -> str | None:
    """The AppImage this process is really running from, or None.

    ``APPIMAGE`` is inherited by every child of an AppImage (a terminal or editor that is itself one), so it
    counts only when this executable lies inside the mount (``APPDIR``) that the same runtime set."""
    image, mount = os.environ.get("APPIMAGE"), os.environ.get("APPDIR")
    if not (image and mount and os.path.isabs(image) and os.path.isabs(mount) and os.path.isfile(image)):
        return None
    try:
        inside = Path(sys.executable).resolve().is_relative_to(Path(mount).resolve())
    except (OSError, RuntimeError, ValueError):
        return None
    return image if inside else None


def _entry_target(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("TryExec="):
            return line[len("TryExec="):].strip()
    return ""


def tidy(app_id: str, *, appimage: str | None, enabled: bool, home: str | os.PathLike | None = None) -> str:
    """Take back what this app installed when it would now be wrong; never touches anything of the user's own.

    Removes our launcher entry, thumbnailer and icons when the setting is off, or when the app is not running
    from an AppImage and the AppImage our entry points to is gone (a stale entry has the same name as a package's
    launcher and would hide it)."""
    base = Path(home) if home else data_home()
    entry = base / "applications" / f"{app_id}.desktop"
    try:
        current = entry.read_text(encoding="utf-8", errors="replace") if entry.is_file() else ""
    except OSError:
        return "unchanged"
    if MARKER not in current:
        return "unchanged"
    target = _entry_target(current)
    stale = not enabled or (appimage is None and bool(target) and not os.path.exists(target))
    if not stale:
        return "unchanged"
    removed = []
    thumbnailer = base / "thumbnailers" / THUMBNAILER_NAME.format(app_id=app_id)
    paths = [entry, thumbnailer] + [base / "icons" / "hicolor" / f"{s}x{s}" / "apps" / f"{app_id}.png" for s in SIZES]
    for path in paths:
        try:
            if path == thumbnailer and THUMBNAILER_NOTE not in path.read_text(encoding="utf-8", errors="replace"):
                continue                                     # a thumbnailer of the user's own
            path.unlink()
            removed.append(path.name)
        except OSError:
            continue
    return f"removed {len(removed)} file(s)" if removed else "unchanged"


def integrate_appimage(config, icon_source: str | os.PathLike) -> str:
    """Called at start-up: integrates only when really running from an AppImage and the setting allows it;
    otherwise removes what an earlier run installed if that is now stale or switched off."""
    from . import APP_ID, APP_NAME
    enabled = bool(config.get(SETTING, True))
    image = trusted_appimage()
    try:
        status = integrate(image, icon_source, name=APP_NAME, app_id=APP_ID, enabled=enabled, mark=True, thumbs=True)
        if not enabled or image is None:
            cleaned = tidy(APP_ID, appimage=image, enabled=enabled)
            if cleaned != "unchanged":
                status = f"{status}; {cleaned}"
    except Exception as exc:  # noqa: BLE001 - this must never reach start-up
        return f"failed: {type(exc).__name__}: {exc}"
    return status

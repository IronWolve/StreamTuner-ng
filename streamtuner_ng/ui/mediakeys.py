"""OS-global media keys, so Play/Pause / Stop / Next / Prev work even when the window is
minimized or unfocused.

On **Windows** this grabs the dedicated media virtual-keys with `RegisterHotKey` (dependency-free,
via `ctypes`) and routes the resulting `WM_HOTKEY` messages through a Qt native event filter. On
every other platform `install()` is a graceful no-op and returns False — the focused-window
`QShortcut`s in the main window still handle the keys while the app is focused (Linux desktops
route media keys to MPRIS, which is a possible future addition).

No extra dependencies: only `ctypes` (stdlib) and Qt, both already required.
"""

from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QAbstractNativeEventFilter

# Windows virtual-key codes for the dedicated media keys.
_VK_MEDIA = [
    ("play_pause", 0xB3),   # VK_MEDIA_PLAY_PAUSE
    ("stop",       0xB2),   # VK_MEDIA_STOP
    ("next",       0xB0),   # VK_MEDIA_NEXT_TRACK
    ("prev",       0xB1),   # VK_MEDIA_PREV_TRACK
]
_WM_HOTKEY = 0x0312
_MOD_NOREPEAT = 0x4000      # don't fire repeatedly while the key is held
_HOTKEY_BASE = 0xB100       # arbitrary id base for our RegisterHotKey ids


class _MSG(ctypes.Structure):
    """Minimal Win32 MSG layout (so we don't need ctypes.wintypes, which is Windows-only).
    ctypes fills in the x86/x64 padding from the field types."""
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_void_p),
        ("lParam", ctypes.c_void_p),
        ("time", ctypes.c_uint),
        ("pt_x", ctypes.c_long),
        ("pt_y", ctypes.c_long),
    ]


class MediaKeys(QAbstractNativeEventFilter):
    """Register OS-global media keys and dispatch them to callbacks.

    `handlers` maps action names ("play_pause", "stop", "next", "prev") to zero-arg callables.
    Windows-only today; elsewhere `install()` returns False and nothing is hooked.
    """

    def __init__(self, handlers: dict):
        super().__init__()
        self._handlers = handlers
        self._ids: dict[int, str] = {}      # RegisterHotKey id -> action name
        self._hwnd = 0
        self._installed = False

    def install(self, hwnd: int) -> bool:
        """Grab the media keys for window `hwnd` (the app's HWND). Returns True if at least one
        key was registered. Safe to call on any platform — a non-Windows host just returns False."""
        if sys.platform != "win32" or not hwnd:
            return False
        try:
            user32 = ctypes.windll.user32
            # Pin argtypes: HWND is a 64-bit pointer — without c_void_p, ctypes' default c_int
            # truncates it on x64 and the registration silently targets the wrong window.
            user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
            user32.RegisterHotKey.restype = ctypes.c_int
            user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.UnregisterHotKey.restype = ctypes.c_int
            self._hwnd = int(hwnd)
            for i, (name, vk) in enumerate(_VK_MEDIA):
                if name not in self._handlers:
                    continue
                hid = _HOTKEY_BASE + i
                if user32.RegisterHotKey(self._hwnd, hid, _MOD_NOREPEAT, vk):
                    self._ids[hid] = name
            if self._ids:
                from PySide6.QtWidgets import QApplication
                QApplication.instance().installNativeEventFilter(self)
                self._installed = True
        except Exception:  # noqa: BLE001 — registration is best-effort; never block startup
            return False
        return self._installed

    def uninstall(self) -> None:
        """Release the global media-key grab (call on app quit). No-op if not installed."""
        if not self._installed:
            return
        try:
            user32 = ctypes.windll.user32
            for hid in self._ids:
                user32.UnregisterHotKey(self._hwnd, hid)
            from PySide6.QtWidgets import QApplication
            inst = QApplication.instance()
            if inst is not None:
                inst.removeNativeEventFilter(self)
        except Exception:  # noqa: BLE001
            pass
        self._ids.clear()
        self._installed = False

    def nativeEventFilter(self, eventType, message):
        # PySide6 contract: return (handled: bool, result: int).
        if self._ids:
            try:
                et = bytes(eventType)
            except Exception:  # noqa: BLE001
                et = b""
            if et == b"windows_generic_MSG":
                try:
                    msg = _MSG.from_address(int(message))
                except (TypeError, ValueError):
                    return False, 0
                if msg.message == _WM_HOTKEY:
                    name = self._ids.get(int(msg.wParam or 0))
                    fn = self._handlers.get(name) if name else None
                    if fn is not None:
                        fn()
                        return True, 0
        return False, 0

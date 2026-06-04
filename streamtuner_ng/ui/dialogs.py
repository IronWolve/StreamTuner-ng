"""The app's standard popup look: a frameless **card with a grey border and a drop
shadow** (DESIGN — "standard dialog style"). All our dialogs use this so popups are
consistent. Add content to `self.body` (a QVBoxLayout). Draggable by the card.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

BORDER = "rgb(120,120,120)"   # the standard grey border for boxes


def _card_colors(mode: str) -> tuple[str, str]:
    from . import theme
    return theme.dialog_colors(mode)


class StyledDialog(QDialog):
    """Frameless dialog rendered as a bordered card with a drop shadow."""

    def __init__(self, parent=None, title: str = "", mode: str = "dark"):
        super().__init__(parent)
        self.mode = mode
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        bg, self.text_col = _card_colors(mode)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 30, 30, 30)          # room for the shadow
        self.card = QFrame()
        self.card.setObjectName("dialogCard")
        self.card.setAttribute(Qt.WA_StyledBackground, True)   # ensure the stylesheet bg paints
        self.card.setStyleSheet(
            f"#dialogCard {{ background: {bg}; border: 1px solid {BORDER}; border-radius: 12px; }}"
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 200))
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)

        self.body = QVBoxLayout(self.card)
        self.body.setContentsMargins(26, 22, 26, 20)
        self.body.setSpacing(12)
        if title:
            t = QLabel(f"<b>{title}</b>")
            t.setStyleSheet(f"font-size:16px; color:{self.text_col};")
            self.body.addWidget(t)
        self._drag = None

    def add_buttons(self, *buttons: QPushButton) -> None:
        row = QHBoxLayout()
        row.addStretch(1)
        for b in buttons:
            row.addWidget(b)
        self.body.addLayout(row)

    # frameless windows can't be moved by a title bar -> let the card drag the dialog
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag)


def new_station(parent, mode: str):
    """Styled 'New Local Station' dialog. Returns (name, url) or None."""
    dlg = StyledDialog(parent, "New Local Station", mode)
    name = QLineEdit()
    name.setPlaceholderText("Name")
    url = QLineEdit()
    url.setPlaceholderText("Stream URL  (http://… .mp3 / .pls / .m3u)")
    dlg.body.addWidget(name)
    dlg.body.addWidget(url)
    ok, cancel = QPushButton("Add"), QPushButton("Cancel")
    ok.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    url.returnPressed.connect(dlg.accept)
    dlg.add_buttons(cancel, ok)
    if dlg.exec() and url.text().strip():
        return name.text().strip(), url.text().strip()
    return None


def info(parent, mode: str, title: str, text: str) -> None:
    """Styled information popup (replaces QMessageBox.information)."""
    dlg = StyledDialog(parent, title, mode)
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color:{dlg.text_col};")
    dlg.body.addWidget(lbl)
    close = QPushButton("Close")
    close.clicked.connect(dlg.accept)
    dlg.add_buttons(close)
    dlg.exec()


def confirm(parent, mode: str, title: str, text: str) -> bool:
    """Styled Yes / Cancel confirmation. Returns True only if confirmed."""
    dlg = StyledDialog(parent, title, mode)
    dlg.card.setMinimumWidth(360)
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color:{dlg.text_col};")
    dlg.body.addWidget(lbl)
    cancel, yes = QPushButton("Cancel"), QPushButton("Yes")
    cancel.clicked.connect(dlg.reject)
    yes.clicked.connect(dlg.accept)
    dlg.add_buttons(cancel, yes)
    return bool(dlg.exec())


def station_info(parent, mode: str, row: dict) -> None:
    """Styled per-station info card: Name / Genre / Bitrate·Codec / Country / URL, one
    per line (selectable), with a Copy URL button."""
    dlg = StyledDialog(parent, row.get("title", "Station"), mode)
    dlg.card.setMinimumWidth(500)          # a wide rectangle — let long URLs breathe
    codec = (row.get("format", "") or "").replace("audio/", "").replace("mpeg", "mp3")
    bitcodec = " · ".join(p for p in (f"{row['bitrate']}k" if row.get("bitrate") else "", codec) if p)

    def _num(v):
        try:
            return f"{int(v):,}"
        except (TypeError, ValueError):
            return str(v or "")

    fields = [
        ("Name", row.get("title", "")),
        ("Description", row.get("description", "")),
        ("Now Playing", row.get("playing", "")),
        ("Genre", row.get("genre", "")),
        ("Bitrate / Codec", bitcodec),
        ("Country", row.get("country", "")),
        ("Listeners", _num(row.get("listeners")) if row.get("listeners") else ""),
        ("Votes", _num(row.get("votes")) if row.get("votes") else ""),
        ("Homepage", row.get("homepage", "")),
        ("URL", row.get("url", "")),
    ]
    grid = QGridLayout()
    grid.setHorizontalSpacing(14)
    r = 0
    for key, val in fields:
        if not str(val).strip():
            continue
        k = QLabel(f"<b>{key}</b>")
        k.setStyleSheet(f"color:{dlg.text_col};")
        v = QLabel(str(val))
        v.setStyleSheet(f"color:{dlg.text_col};")
        v.setWordWrap(True)
        v.setTextInteractionFlags(Qt.TextSelectableByMouse)
        grid.addWidget(k, r, 0, Qt.AlignTop)
        grid.addWidget(v, r, 1)
        r += 1
    grid.setColumnStretch(1, 1)
    dlg.body.addLayout(grid)
    copy = QPushButton("Copy URL")
    copy.clicked.connect(lambda: QApplication.clipboard().setText(row.get("url", "")))
    close = QPushButton("Close")
    close.clicked.connect(dlg.accept)
    dlg.add_buttons(copy, close)
    dlg.exec()


def open_location(parent, mode: str):
    """Styled 'Open Location' dialog — enter a stream URL to play. Returns url or None."""
    dlg = StyledDialog(parent, "Open Location", mode)
    lbl = QLabel("Enter a stream URL to play:")
    lbl.setStyleSheet(f"color:{dlg.text_col};")
    url = QLineEdit()
    url.setPlaceholderText("http://…  (mp3 / aac / ogg / pls / m3u)")
    url.setMinimumWidth(400)
    dlg.body.addWidget(lbl)
    dlg.body.addWidget(url)
    play, cancel = QPushButton("Play"), QPushButton("Cancel")
    play.clicked.connect(dlg.accept)
    cancel.clicked.connect(dlg.reject)
    url.returnPressed.connect(dlg.accept)
    dlg.add_buttons(cancel, play)
    if dlg.exec() and url.text().strip():
        return url.text().strip()
    return None

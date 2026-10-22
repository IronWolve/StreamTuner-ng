"""The app's standard popup look: a frameless **card with a grey border and a drop
shadow** (DESIGN — "standard dialog style"). All our dialogs use this so popups are
consistent. Add content to `self.body` (a QVBoxLayout). Draggable by the card.
"""

from __future__ import annotations

from html import escape

from ..security import redact_url, valid_url

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout,
)

BORDER = "rgb(120,120,120)"   # the standard grey border for boxes
MAX_LOCAL_BATCH_URLS = 1000


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
            t = QLabel(f"<b>{escape(title)}</b>")
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
    """Collect one named stream or a newline-separated batch for Local."""
    dlg = StyledDialog(parent, "Add Stations to Local", mode)
    dlg.card.setMinimumWidth(480)
    name = QLineEdit()
    name.setObjectName("localStationName")
    name.setPlaceholderText("Name")
    name_label = QLabel("Name (optional)")
    name_label.setBuddy(name)
    url = QLineEdit()
    url.setObjectName("localStationUrl")
    url.setAccessibleName("Direct stream URL")
    url.setPlaceholderText("https://radio.example/live.mp3")
    url_label = QLabel("Stream URL")
    url_label.setBuddy(url)
    batch_toggle = QCheckBox("Add multiple URLs")
    batch_toggle.setObjectName("localStationBatchToggle")
    batch_toggle.setToolTip("Paste one direct stream URL per line")
    batch_label = QLabel("Enter the actual stream URL, not the station or website page.")
    batch_label.setWordWrap(True)
    batch_urls = QPlainTextEdit()
    batch_urls.setObjectName("localStationBatchUrls")
    batch_urls.setAccessibleName("Direct stream URLs, one per line")
    batch_label.setBuddy(batch_urls)
    batch_urls.setPlaceholderText("https://radio.example/live.mp3\nhttps://another.example/stream")
    batch_urls.setMinimumHeight(190)
    # Keep the optional single-station name above its field, while leaving the
    # bulk editor room for pasted lists.
    dlg.body.addWidget(name_label)
    dlg.body.addWidget(name)
    dlg.body.addWidget(url_label)
    dlg.body.addWidget(url)
    dlg.body.addWidget(batch_toggle)
    dlg.body.addWidget(batch_label)
    dlg.body.addWidget(batch_urls)
    for label in (name_label, url_label, batch_label):
        label.setStyleSheet(f"color:{dlg.text_col};")
    batch_urls.hide()
    ok, cancel = QPushButton("Add"), QPushButton("Cancel")
    ok.setObjectName("localStationAdd")
    cancel.setObjectName("localStationCancel")
    ok.setDefault(True)
    problem = QLabel()
    problem.setTextFormat(Qt.PlainText)
    problem.setWordWrap(True)
    problem.setStyleSheet(f"color:{dlg.text_col};")
    dlg.body.addWidget(problem)

    def set_mode(is_batch: bool) -> None:
        name_label.setVisible(not is_batch)
        name.setVisible(not is_batch)
        url_label.setVisible(not is_batch)
        url.setVisible(not is_batch)
        batch_label.setText("Paste actual stream URLs, one per line (not station or website pages)."
                            if is_batch else "Enter the actual stream URL, not the station or website page.")
        batch_urls.setVisible(is_batch)

    batch_toggle.toggled.connect(set_mode)

    def submit():
        problem.clear()
        if batch_toggle.isChecked():
            lines = [(number, line.strip()) for number, line in enumerate(batch_urls.toPlainText().splitlines(), 1)
                     if line.strip()]
            if not lines:
                problem.setText("Paste at least one direct stream URL.")
                batch_urls.setFocus()
                return
            if len(lines) > MAX_LOCAL_BATCH_URLS:
                problem.setText(f"You can add up to {MAX_LOCAL_BATCH_URLS:,} stream URLs at once.")
                batch_urls.setFocus()
                return
            invalid = next((number for number, value in lines if not valid_url(value, token=True)), None)
            if invalid is not None:
                problem.setText(f"Line {invalid} is not a supported stream URL. Invalid lists are not saved.")
                batch_urls.setFocus()
                return
            dlg.station_input = {"bulk": True, "name": "", "urls": [value for _number, value in lines]}
        else:
            value = url.text().strip()
            if not valid_url(value, token=True):
                problem.setText("Enter a direct HTTP(S), ICY or MMSH stream URL, not a station website page.")
                url.setFocus()
                return
            dlg.station_input = {"bulk": False, "name": name.text().strip(), "urls": [value]}
        dlg.accept()

    ok.clicked.connect(submit)
    cancel.clicked.connect(dlg.reject)
    url.returnPressed.connect(submit)
    batch_urls.setTabChangesFocus(True)
    dlg.add_buttons(cancel, ok)
    if dlg.exec():
        return getattr(dlg, "station_input", None)
    return None


def info(parent, mode: str, title: str, text: str) -> None:
    """Styled information popup (replaces QMessageBox.information)."""
    dlg = StyledDialog(parent, title, mode)
    lbl = QLabel(text)
    lbl.setTextFormat(Qt.PlainText)
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
    lbl.setTextFormat(Qt.PlainText)
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
        v = QLabel(redact_url(str(val)) if key in ("URL", "Homepage") else str(val))
        v.setTextFormat(Qt.PlainText)
        v.setStyleSheet(f"color:{dlg.text_col};")
        v.setWordWrap(True)
        v.setTextInteractionFlags(Qt.TextSelectableByMouse)
        grid.addWidget(k, r, 0, Qt.AlignTop)
        grid.addWidget(v, r, 1)
        r += 1
    grid.setColumnStretch(1, 1)
    dlg.body.addLayout(grid)
    copy = QPushButton("Copy URL")
    copy.clicked.connect(lambda: QApplication.clipboard().setText(redact_url(row.get("url", ""))))
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
    problem = QLabel()
    problem.setTextFormat(Qt.PlainText)
    dlg.body.addWidget(problem)

    def submit():
        if valid_url(url.text().strip(), token=False):
            dlg.accept()
        else:
            problem.setText("Enter a supported HTTP(S), ICY or MMSH network stream URL.")

    play.clicked.connect(submit)
    cancel.clicked.connect(dlg.reject)
    url.returnPressed.connect(submit)
    dlg.add_buttons(cancel, play)
    if dlg.exec() and url.text().strip():
        return url.text().strip()
    return None

# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: the splash screen while it starts, and a small note on the screen while it works.

The note says what Lens is doing when it takes a moment: loading or downloading the translator,
or reading and translating the screen. Both windows are left out of screenshots, so Lens never
reads them back, and the note never takes the mouse or the keyboard.
"""
import ctypes
import sys

from PySide6.QtCore import QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import APP_NAME, __version__, config

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WDA_EXCLUDEFROMCAPTURE = 0x11
CARD = QColor(15, 23, 42, 235)
ACCENT = QColor(79, 195, 247)
TEXT = QColor(241, 245, 249)
MUTED = QColor(148, 163, 184)
LINGER_MS = 900          # the note stays this long after the work is done: quick work is seen, not a flicker
NOTICE_MS = 2500         # how long a note about something done (a translator switched off) stays
SPLASH_MIN_MS = 1200     # long enough to be read


def _keep_out_of_screenshots(widget, click_through=False):
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    hwnd = int(widget.winId())
    if click_through:
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)


class _Spinning(QWidget):
    """A frameless, see-through window on top that draws a turning arc."""

    def __init__(self, click_through):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle(APP_NAME)
        self._click_through = click_through
        self._angle = 0
        self._spin = QTimer(self)
        self._spin.setInterval(30)
        self._spin.timeout.connect(self._turn)

    def _turn(self):
        self._angle = (self._angle + 12) % 360
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        _keep_out_of_screenshots(self, self._click_through)
        self._spin.start()

    def hideEvent(self, event):
        self._spin.stop()
        super().hideEvent(event)

    def draw_spinner(self, p, rect, width=3):
        p.setPen(QPen(QColor(255, 255, 255, 40), width))
        p.drawEllipse(rect)
        pen = QPen(ACCENT, width)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, -self._angle * 16, 100 * 16)


class SplashScreen(_Spinning):
    """Shown in the middle of the main screen while Lens starts; `finish()` closes it."""

    def __init__(self):
        super().__init__(click_through=False)
        self.message = "Starting…"
        self.setFixedSize(380, 210)
        self.icon = QIcon(f"{config.ASSETS_DIR}/echosub.ico").pixmap(72, 72)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())
        self._shown_at = None
        self._closing = False

    def show_message(self, text):
        self.message = text
        self.update()
        QGuiApplication.processEvents()

    def start(self):
        from PySide6.QtCore import QElapsedTimer

        self._shown_at = QElapsedTimer()
        self._shown_at.start()
        self.show()
        self.raise_()
        QGuiApplication.processEvents()

    def finish(self):
        """Close once it has been seen for a moment."""
        if self._closing:
            return
        self._closing = True
        waited = self._shown_at.elapsed() if self._shown_at else SPLASH_MIN_MS
        QTimer.singleShot(max(0, SPLASH_MIN_MS - waited), self.close)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(CARD)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 16, 16)
        p.setPen(QPen(QColor(79, 195, 247, 90), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 16, 16)
        p.drawPixmap(28, 32, self.icon)
        p.setPen(TEXT)
        p.setFont(QFont("Segoe UI", 20, QFont.Bold))
        p.drawText(QRect(116, 30, 250, 40), Qt.AlignLeft | Qt.AlignVCenter, APP_NAME)
        p.setPen(MUTED)
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(QRect(116, 68, 250, 40), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                   "Translate the text on your screen, in place")
        p.drawText(QRect(116, 104, 250, 20), Qt.AlignLeft | Qt.AlignVCenter, f"Version {__version__}")
        self.draw_spinner(p, QRect(30, 160, 18, 18))
        p.setPen(TEXT)
        p.setFont(QFont("Segoe UI", 10))
        p.drawText(QRect(60, 156, 300, 26), Qt.AlignLeft | Qt.AlignVCenter, self.message)
        p.end()


class Activity(QObject):
    """Says what Lens is doing, from any thread: `say(source, text, percent)`; an empty text means it is done.

    `percent` is how far it got (0-100), or -1 when that is not known.
    """
    changed = Signal(str, str, int)
    told = Signal(str)

    def say(self, source, text="", percent=-1):
        self.changed.emit(source, text, percent)

    def tell(self, text):
        """Something was done (a translator switched off, translation stopped): a note for a moment."""
        self.told.emit(text)


class BusyIndicator(_Spinning):
    """A small note with a progress bar in the corner of a screen while Lens works on something that takes
    a moment."""

    def __init__(self):
        super().__init__(click_through=True)
        self.texts = {}          # source -> (text, percent); the newest one is shown
        self.screen_of = QGuiApplication.primaryScreen
        self._shown = ("", -1)
        self._hide = QTimer(self)
        self._hide.setSingleShot(True)
        self._hide.setInterval(LINGER_MS)
        self._hide.timeout.connect(self._disappear)
        self._notice_end = QTimer(self)
        self._notice_end.setSingleShot(True)
        self._notice_end.timeout.connect(lambda: self.set("notice", ""))
        self.setFont(QFont("Segoe UI", 10))

    @property
    def text(self):
        return self._shown[0]

    @property
    def percent(self):
        return self._shown[1]

    def set(self, source, text, percent=-1):
        """Show `text` for `source` (or stop showing it, when empty), with how far it got (-1: not known)."""
        self.texts.pop(source, None)
        if text:
            self.texts[source] = (text, max(-1, min(100, percent)))
        if self.texts:
            self._hide.stop()
            self._shown = next(reversed(self.texts.values()))
            self._place()
            self.show()
            self.update()
        elif self.isVisible():
            self._shown = (self._shown[0], 100)  # done: the last words stay a moment, the bar full
            self.update()
            self._hide.start()
        else:
            self._shown = ("", -1)

    def notice(self, text, ms=NOTICE_MS):
        """Say that something was done; it goes away by itself."""
        self.set("notice", text, 100)
        self._notice_end.start(ms)

    def clear(self):
        self.texts.clear()
        self._notice_end.stop()
        self._hide.stop()
        self._disappear()

    def _disappear(self):
        if not self.texts:
            self._shown = ("", -1)
            self.hide()

    def _place(self):
        width = min(520, self.fontMetrics().horizontalAdvance(self.text) + 64)
        self.resize(width, 50)
        screen = (self.screen_of() or QGuiApplication.primaryScreen()).availableGeometry()
        self.move(screen.right() - width - 16, screen.bottom() - self.height() - 16)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(CARD)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 12, 12)
        self.draw_spinner(p, QRect(14, 11, 18, 18), 2.5)
        p.setPen(TEXT)
        p.drawText(QRect(42, 6, self.width() - 56, 26), Qt.AlignLeft | Qt.AlignVCenter, self.text)
        track = QRect(42, 35, self.width() - 56, 5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(track, 2.5, 2.5)
        p.setBrush(ACCENT)
        if self.percent >= 0:
            filled = QRect(track.x(), track.y(), max(5, track.width() * self.percent // 100), track.height())
        else:  # not known: a piece that slides along the track
            piece = track.width() // 4
            filled = QRect(track.x() + (track.width() - piece) * self._angle // 360, track.y(), piece,
                           track.height())
        p.drawRoundedRect(filled, 2.5, 2.5)
        p.end()

# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: the translation window — a small window above all others with the translated text in a box.

It shows what Lens translated from the screen, in reading order, ready to read, select and copy. It shows
in screenshots like any window; Lens leaves the part of the screen under it alone, so it never reads its
own text back.
"""
from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QSizeGrip, QSlider, QVBoxLayout, QWidget)

from . import languages

MIN_FONT_PT, MAX_FONT_PT = 8, 40
MIN_OPACITY = 0                  # % of the background left when it is transparent (0: only the words)
DIRECTIONS = (("auto", "Direction: automatic"), ("rtl", "Right to left"), ("ltr", "Left to right"))
GLASS = "#141414"                # the colour of a transparent window's background


def reading_order(blocks):
    """Top to bottom; blocks side by side on about the same line, left to right."""
    def key(block):
        box = block.box
        return round(box.y / max(block.line_height, 1) / 1.5), box.x
    return sorted(blocks, key=key)


def window_text(blocks, with_original=False):
    parts = []
    for block in reading_order(blocks):
        if not block.translation:
            continue
        parts.append(f"{block.translation}\n    ({block.text})" if with_original else block.translation)
    return "\n\n".join(parts) if with_original else "\n".join(parts)


class TextWindow(QWidget):
    """Always on top. `show_blocks()` puts the translations in the box; closing it says `closed`."""
    closed = Signal()
    changed = Signal(dict)   # its settings: geometry, font size, with_original, overlay

    def __init__(self, settings=None, overlay_visible=True):
        super().__init__(None, Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Translation — EchoSub Lens")
        settings = settings or {}
        self.font_pt = int(settings.get("font_pt", 14))
        self.direction = settings.get("direction", "auto")
        if self.direction not in dict(DIRECTIONS):
            self.direction = "auto"
        self._blocks, self._target = [], None
        self._glass = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.box = QPlainTextEdit()
        self.box.setReadOnly(True)
        self.box.setPlaceholderText("The translated text of the screen appears here.")
        layout.addWidget(self.box, 1)

        # Transparency and direction
        look = QHBoxLayout()
        self.transparent = QCheckBox("Transparent")
        self.transparent.setToolTip("See the screen through the window; the words stay clear. "
                                    "Drag an empty spot to move it, the corner to resize it.")
        self.transparent.setChecked(bool(settings.get("transparent", False)))
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(MIN_OPACITY, 100)
        self.opacity.setValue(int(settings.get("opacity", 40)))
        self.opacity.setFixedWidth(140)
        self.opacity.setToolTip("How much of the background is left: 0 % shows only the words")
        self.opacity_label = QLabel()
        self.opacity_label.setMinimumWidth(110)
        self.direction_box = QComboBox()
        for value, text in DIRECTIONS:
            self.direction_box.addItem(text, value)
        self.direction_box.setCurrentIndex(self.direction_box.findData(self.direction))
        self.direction_box.setToolTip("Automatic: right to left for Arabic, Hebrew, Persian and Urdu")
        self.close_button = QPushButton("✕")
        self.close_button.setFixedWidth(36)
        self.close_button.setToolTip("Close the translation window")
        self.close_button.clicked.connect(self.close)
        for widget in (self.transparent, self.opacity, self.opacity_label):
            look.addWidget(widget)
        look.addStretch(1)
        look.addWidget(self.direction_box)
        look.addWidget(self.close_button)
        layout.addLayout(look)

        bar = QHBoxLayout()
        self.with_original = QCheckBox("Original text too")
        self.with_original.setChecked(bool(settings.get("with_original", False)))
        self.with_original.toggled.connect(lambda _: (self._fill(), self._changed()))
        self.only_here = QCheckBox("Show translations only in this window")
        self.only_here.setToolTip("Not over the text on the screen. Closing this window puts them back there.")
        self.only_here.setChecked(not overlay_visible)
        self.only_here.toggled.connect(lambda _: self._changed())
        smaller, bigger = QPushButton("A−"), QPushButton("A+")
        for button in (smaller, bigger):
            button.setFixedWidth(36)
        smaller.clicked.connect(lambda: self.set_font_pt(self.font_pt - 2))
        bigger.clicked.connect(lambda: self.set_font_pt(self.font_pt + 2))
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.box.toPlainText()))
        for widget in (self.only_here, self.with_original):
            bar.addWidget(widget)
        bar.addStretch(1)
        for widget in (smaller, bigger, copy):
            bar.addWidget(widget)
        self.grip = QSizeGrip(self)
        bar.addWidget(self.grip, 0, Qt.AlignBottom | Qt.AlignRight)
        layout.addLayout(bar)

        self.set_font_pt(self.font_pt, save=False)
        geometry = settings.get("geometry")
        if geometry and QGuiApplication.screenAt(QRect(*geometry).center()) is not None:
            self.setGeometry(QRect(*geometry))
        else:
            screen = QGuiApplication.primaryScreen().availableGeometry()
            self.setGeometry(screen.right() - 600, screen.top() + 80, 560, 380)
        self.transparent.toggled.connect(lambda _: (self._apply_look(), self._changed()))
        self.opacity.valueChanged.connect(lambda _: (self._apply_look(), self._changed()))
        self.direction_box.activated.connect(lambda _: self.set_direction(self.direction_box.currentData()))
        self._apply_look()

    # ---- transparency ----------------------------------------------------------
    def _apply_look(self):
        """A see-through window has no title bar: its own background, as transparent as chosen, and words that
        stay readable over anything (a shadow around them)."""
        glass = self.transparent.isChecked()
        self.opacity.setEnabled(glass)
        self.opacity_label.setText(f"Background {self.opacity.value()} %" if glass else "")
        self.close_button.setVisible(glass)
        self.grip.setVisible(glass)
        if glass != self._glass:
            self._glass = glass
            visible, geometry = self.isVisible(), self.geometry()
            flags = Qt.Window | Qt.WindowStaysOnTopHint
            if glass:
                flags |= Qt.FramelessWindowHint
            self.setAttribute(Qt.WA_TranslucentBackground, glass)
            self.setWindowFlags(flags)
            self.setGeometry(geometry)
            if visible:
                self.show()
        if glass:
            self.box.setStyleSheet("QPlainTextEdit { background: transparent; border: none; color: white; }")
            self.setStyleSheet("QCheckBox, QLabel { color: white; }")
            shadow = QGraphicsDropShadowEffect(self.box)
            shadow.setBlurRadius(6)
            shadow.setOffset(1, 1)
            shadow.setColor(QColor(0, 0, 0, 255))
            self.box.setGraphicsEffect(shadow)
        else:
            self.box.setStyleSheet("")
            self.setStyleSheet("")
            self.box.setGraphicsEffect(None)
        self.update()

    def paintEvent(self, event):
        if not self._glass:
            return super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = QColor(GLASS)
        color.setAlpha(max(1, round(255 * self.opacity.value() / 100)))  # never 0: it would let clicks through
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(self.rect(), 10, 10)
        p.end()

    def mousePressEvent(self, event):
        if self._glass and event.button() == Qt.LeftButton and self.windowHandle() is not None:
            self.windowHandle().startSystemMove()  # no title bar: an empty spot moves the window
            return
        super().mousePressEvent(event)

    # ---- direction -------------------------------------------------------------
    def set_direction(self, direction):
        self.direction = direction if direction in dict(DIRECTIONS) else "auto"
        self.direction_box.setCurrentIndex(self.direction_box.findData(self.direction))
        self._fill(force=True)
        self._changed()

    def right_to_left(self):
        if self.direction == "auto":
            return self._target in languages.RTL
        return self.direction == "rtl"

    # ---- content ---------------------------------------------------------------
    def show_blocks(self, blocks, target):
        self._blocks, self._target = list(blocks), target
        self._fill()

    def _fill(self, force=False):
        text = window_text(self._blocks, self.with_original.isChecked())
        if text == self.box.toPlainText() and not force:
            return  # the same text: keep the reader's place and selection
        rtl = self.right_to_left()
        self.box.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        option = self.box.document().defaultTextOption()
        option.setTextDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        option.setAlignment(Qt.AlignRight if rtl else Qt.AlignLeft)
        self.box.document().setDefaultTextOption(option)
        self.box.setPlainText(text)

    def set_font_pt(self, pt, save=True):
        self.font_pt = max(MIN_FONT_PT, min(MAX_FONT_PT, int(pt)))
        self.box.setFont(QFont("Segoe UI", self.font_pt))
        if save:
            self._changed()

    # ---- settings ----------------------------------------------------------------
    def settings(self):
        g = self.geometry()
        return {"geometry": [g.x(), g.y(), g.width(), g.height()], "font_pt": self.font_pt,
                "with_original": self.with_original.isChecked(), "overlay": not self.only_here.isChecked(),
                "transparent": self.transparent.isChecked(), "opacity": self.opacity.value(),
                "direction": self.direction}

    def _changed(self):
        self.changed.emit(self.settings())

    # ---- window ----------------------------------------------------------------------
    def moveEvent(self, event):
        super().moveEvent(event)
        if self.isVisible():
            self._changed()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.isVisible():
            self._changed()

    def closeEvent(self, event):
        self._changed()
        super().closeEvent(event)
        self.closed.emit()

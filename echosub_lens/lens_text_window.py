# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: the translation window — a small window above all others with the translated text in a box.

It shows what Lens translated from the screen, in reading order, ready to read, select and copy. It shows
in screenshots like any window; Lens leaves the part of the screen under it alone, so it never reads its
own text back.
"""
from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from . import languages

MIN_FONT_PT, MAX_FONT_PT = 8, 40


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
        self._blocks, self._target = [], None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.box = QPlainTextEdit()
        self.box.setReadOnly(True)
        self.box.setPlaceholderText("The translated text of the screen appears here.")
        layout.addWidget(self.box, 1)

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
        layout.addLayout(bar)

        self.set_font_pt(self.font_pt, save=False)
        geometry = settings.get("geometry")
        if geometry and QGuiApplication.screenAt(QRect(*geometry).center()) is not None:
            self.setGeometry(QRect(*geometry))
        else:
            screen = QGuiApplication.primaryScreen().availableGeometry()
            self.setGeometry(screen.right() - 600, screen.top() + 80, 560, 380)

    # ---- content ---------------------------------------------------------------
    def show_blocks(self, blocks, target):
        self._blocks, self._target = list(blocks), target
        self._fill()

    def _fill(self):
        text = window_text(self._blocks, self.with_original.isChecked())
        if text == self.box.toPlainText():
            return  # the same text: keep the reader's place and selection
        rtl = self._target in languages.RTL
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
                "with_original": self.with_original.isChecked(), "overlay": not self.only_here.isChecked()}

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

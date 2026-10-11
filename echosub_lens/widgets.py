# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""Small widgets for the Settings window."""
from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QPushButton


class ColorButton(QPushButton):
    changed = Signal()

    def __init__(self, color, text=True):
        super().__init__()
        self.show_text = text
        self.setMinimumWidth(36)
        self.setMaximumWidth(220 if text else 44)
        self.setToolTip("Click to pick a colour")
        self.set_color(color)
        self.clicked.connect(self._pick)

    def set_color(self, color):
        self.color = color
        self.setText(color.upper() if self.show_text else "")
        fg = "#000" if QColor(color).lightness() > 128 else "#fff"
        self.setStyleSheet(f"background:{color}; color:{fg}; border:1px solid #666; padding:4px;")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color), self)
        if c.isValid():
            self.set_color(c.name())
            self.changed.emit()

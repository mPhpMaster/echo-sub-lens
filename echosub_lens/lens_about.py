# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: the About page — the author, the version, links and the legal documents (as in EchoSub)."""
import os
import sys

from PySide6.QtCore import QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import (APP_NAME, AUTHOR, AUTHOR_EMAIL, COPYRIGHT, DESCRIPTION, DONATE_URL, LICENSE_NAME, LINKS, REPO_URL,
               __version__, config)

AVATAR_NAMES = ("author.png", "author.jpg", "author.jpeg")
ACCENT = "#14B8A6"
DOCUMENTS = (("License", "LICENSE"), ("Changelog", "CHANGELOG.md"), ("Source code", REPO_URL))


def find_document(name):
    """A document shipped with Lens: the project folder from source, the install folder when installed."""
    folders = [os.path.dirname(config.PACKAGE_DIR), os.path.dirname(sys.executable), getattr(sys, "_MEIPASS", "")]
    for folder in folders:
        path = os.path.join(folder, name) if folder else ""
        if path and os.path.exists(path):
            return path
    return None


def open_target(target):
    """A web page, or a document of Lens (its copy on GitHub when it is not on this PC)."""
    if target.startswith("http") or target.startswith("mailto:"):
        url = QUrl(target)
    else:
        path = find_document(target)
        url = QUrl.fromLocalFile(path) if path else QUrl(f"{REPO_URL}/blob/main/{target}")
    QDesktopServices.openUrl(url)
    return url


def avatar_pixmap(size):
    """The author's photo in a circle (assets/author.jpg); his initials when it is missing."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addEllipse(QRectF(0, 0, size, size))
    p.setClipPath(clip)
    photo = next((QPixmap(os.path.join(config.ASSETS_DIR, n)) for n in AVATAR_NAMES
                  if os.path.exists(os.path.join(config.ASSETS_DIR, n))), None)
    if photo is not None and not photo.isNull():
        scaled = photo.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        p.drawPixmap((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    else:
        p.fillRect(0, 0, size, size, QColor("#1F2937"))
        p.setPen(QColor(ACCENT))
        p.setFont(QFont("Segoe UI", int(size * 0.32), QFont.Bold))
        initials = "".join(part[0] for part in AUTHOR.replace("-", " ").split()[:2]).upper()
        p.drawText(pm.rect(), Qt.AlignCenter, initials)
    p.setClipping(False)
    p.setPen(QColor(255, 255, 255, 60))
    p.drawEllipse(QRectF(0.5, 0.5, size - 1, size - 1))
    p.end()
    return pm


class AboutPage(QWidget):
    """Who made Lens and how to reach him. `on_support` opens the donation page (Lens says thank you)."""

    def __init__(self, on_support=None, parent=None):
        super().__init__(parent)
        self.setObjectName("about")
        self.setStyleSheet(f"""
            QPushButton#link {{ background: transparent; color: #E5E7EB; border: 1px solid #2B3432;
                               border-radius: 8px; padding: 9px 16px; font-weight: 600; }}
            QPushButton#link:hover {{ border-color: {ACCENT}; color: white; }}
            QPushButton#small {{ background: transparent; color: #9CA3AF; border: none; padding: 2px 6px; }}
            QPushButton#small:hover {{ color: {ACCENT}; }}
            QPushButton#support {{ background: transparent; color: #FDA4AF; border: 1px solid #7F1D3A;
                                  border-radius: 8px; padding: 9px 18px; font-weight: 700; }}
            QPushButton#support:hover {{ border-color: #FB7185; color: white; background: #2A1219; }}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 8, 24, 8)
        root.setSpacing(10)

        images = QHBoxLayout()
        images.setSpacing(14)
        images.addStretch(1)
        avatar = QLabel()
        avatar.setPixmap(avatar_pixmap(96))
        avatar.setToolTip(AUTHOR)
        icon = QLabel()
        icon.setPixmap(QIcon(os.path.join(config.ASSETS_DIR, "echosub.ico")).pixmap(QSize(72, 72)))
        images.addWidget(avatar)
        images.addWidget(icon)
        images.addStretch(1)
        root.addLayout(images)

        self.name = QLabel(f"<b style='font-size:16pt'>{APP_NAME}</b> "
                           f"<span style='color:#9CA3AF; font-size:10pt'>v{__version__}</span>")
        self.name.setAlignment(Qt.AlignCenter)
        root.addWidget(self.name)

        desc = QLabel(DESCRIPTION)
        desc.setObjectName("muted")
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        root.addWidget(desc)

        by = QLabel(f"Made by <b>{AUTHOR}</b><br><span style='color:#9CA3AF'>{AUTHOR_EMAIL}</span>")
        by.setAlignment(Qt.AlignCenter)
        by.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(by)

        links = QHBoxLayout()
        links.setSpacing(10)
        links.addStretch(1)
        self.link_buttons = {}
        for label, url in list(LINKS.items()) + [("Email", f"mailto:{AUTHOR_EMAIL}?subject={APP_NAME}")]:
            self.link_buttons[label] = self._button(label, url, "link")
            links.addWidget(self.link_buttons[label])
        links.addStretch(1)
        root.addSpacing(4)
        root.addLayout(links)

        thanks = QLabel(f"{APP_NAME} is free. If it helps you, you can support its development.")
        thanks.setObjectName("muted")
        thanks.setAlignment(Qt.AlignCenter)
        thanks.setWordWrap(True)
        root.addSpacing(6)
        root.addWidget(thanks)
        support = QHBoxLayout()
        support.addStretch(1)
        self.support_button = self._button(f"♥  Support {APP_NAME}", DONATE_URL, "support", on_support)
        support.addWidget(self.support_button)
        support.addStretch(1)
        root.addLayout(support)

        docs = QHBoxLayout()
        docs.addStretch(1)
        for i, (label, target) in enumerate(DOCUMENTS):
            if i:
                dot = QLabel("·")
                dot.setObjectName("muted")
                docs.addWidget(dot)
            docs.addWidget(self._button(label, target, "small"))
        docs.addStretch(1)
        root.addLayout(docs)

        self.legal = QLabel(f"{COPYRIGHT}<br>Licensed under the {LICENSE_NAME}.<br>"
                            "This program comes with ABSOLUTELY NO WARRANTY.")
        self.legal.setObjectName("muted")
        self.legal.setAlignment(Qt.AlignCenter)
        self.legal.setStyleSheet("font-size: 8.5pt;")
        root.addWidget(self.legal)
        root.addStretch(1)

    @staticmethod
    def _button(label, target, style, on_click=None):
        b = QPushButton(label)
        b.setObjectName(style)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(target if target.startswith("http") or target.startswith("mailto:") else "")
        b.clicked.connect(on_click or (lambda: open_target(target)))
        return b

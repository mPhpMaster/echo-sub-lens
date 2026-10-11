# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""Takes the README's pictures of the Lens window, the translation window and a note on the screen.

    .venv\\Scripts\\python.exe scripts\\screenshots.py

They are saved in docs/images. Nothing is translated and the settings on this PC are not touched.
"""
import os
import sys
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

from echosub_lens import app_windows, lens_app, lens_scanner, lens_ui  # noqa: E402
from echosub_lens.lens_ocr import Block, Box, Line  # noqa: E402

OUT = os.path.join(ROOT, "docs", "images")
SAMPLE_WINDOWS = [app_windows.AppWindow(1, "Movie.mkv - VLC media player", "vlc"),
                  app_windows.AppWindow(2, "Game", "game")]  # not the windows open on this PC


def settle(app, ms=400):
    from PySide6.QtCore import QElapsedTimer

    timer = QElapsedTimer()
    timer.start()
    while timer.elapsed() < ms:
        app.processEvents()


def block(text, translation, y):
    return Block([Line([(word, Box(10 + 60 * i, y, 55, 20)) for i, word in enumerate(text.split())])],
                 translation=lens_scanner.keep_dash(text, translation))


def main():
    os.makedirs(OUT, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    with mock.patch.object(lens_ui.hotkeys.GlobalHotkeys, "enable_keys", return_value=[]), \
            mock.patch.object(lens_ui.LensScanner, "start", lambda scanner: scanner._timer.start()), \
            mock.patch.object(lens_ui.LensScanner, "tick", lambda scanner: None), \
            mock.patch.object(app_windows, "list_windows", return_value=SAMPLE_WINDOWS):
        cfg = dict(lens_app.DEFAULTS, mode="area", area=[200, 600, 1200, 160], target_lang="ar")
        lens = lens_ui.LensApp(app, cfg, lambda c: None, base_cfg={"target_lang": "ar", "translator": "google"})
        lens.activity.tell = lambda text: None
        lens.scanner._timer.start()
        lens._update_state()
        for page in ("translate", "languages", "translator", "appearance", "shortcuts", "about"):
            lens.open_window(page)
            window = lens.main_window
            window.resize(900, 680)
            settle(app)
            for area in window.findChildren(QScrollArea):
                area.verticalScrollBar().setValue(0)
            settle(app, 150)
            window.grab().save(os.path.join(OUT, f"window-{page}.png"))
        window.hide()

        lens.menu.adjustSize()
        lens.menu.grab().save(os.path.join(OUT, "tray-menu.png"))

        lens.show_text_window(True)
        blocks = [block("- Where are you going?", "- إلى أين أنت ذاهب؟", 10),
                  block("- Home.", "- إلى البيت.", 40),
                  block("Updates, man. Bet it's not compatible.", "التحديثات يا رجل. أراهن أنه غير متوافق.", 70)]
        lens.text_window.show_blocks(blocks, "ar")
        lens.text_window.resize(560, 300)
        settle(app)
        lens.text_window.grab().save(os.path.join(OUT, "translation-window.png"))
        lens.text_window.opacity.setValue(30)
        lens.text_window.transparent.setChecked(True)
        settle(app)
        glass = lens.text_window.grab()
        picture = QPixmap(glass.size())  # over a colourful picture, to show what it looks like
        p = QPainter(picture)
        gradient = QLinearGradient(0, 0, picture.width(), picture.height())
        gradient.setColorAt(0, QColor("#0E7490"))
        gradient.setColorAt(0.5, QColor("#7C3AED"))
        gradient.setColorAt(1, QColor("#F59E0B"))
        p.fillRect(picture.rect(), gradient)
        p.drawPixmap(0, 0, glass)
        p.end()
        picture.save(os.path.join(OUT, "translation-window-transparent.png"))
        lens.text_window.transparent.setChecked(False)
        lens.show_text_window(False)

        lens.busy.set("screen", "Translating… 2 of 3", 66)
        settle(app)
        lens.busy.grab().save(os.path.join(OUT, "progress-note.png"))
        lens.busy.clear()
        lens.stop()
        lens.tray.hide()
        lens.overlay.close()
    print("Saved in", OUT)


if __name__ == "__main__":
    main()

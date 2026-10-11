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

from PySide6.QtWidgets import QApplication  # noqa: E402

from echosub_lens import lens_app, lens_scanner, lens_ui  # noqa: E402
from echosub_lens.lens_ocr import Block, Box, Line  # noqa: E402

OUT = os.path.join(ROOT, "docs", "images")


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
            mock.patch.object(lens_ui.LensScanner, "tick", lambda scanner: None):
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
            window.grab().save(os.path.join(OUT, f"window-{page}.png"))
        window.hide()

        lens.show_text_window(True)
        blocks = [block("- Where are you going?", "- إلى أين أنت ذاهب؟", 10),
                  block("- Home.", "- إلى البيت.", 40),
                  block("Updates, man. Bet it's not compatible.", "التحديثات يا رجل. أراهن أنه غير متوافق.", 70)]
        lens.text_window.show_blocks(blocks, "ar")
        lens.text_window.resize(560, 300)
        settle(app)
        lens.text_window.grab().save(os.path.join(OUT, "translation-window.png"))
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

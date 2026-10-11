# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: the file window translates into any language, with any translator, chosen in the window."""
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ECHOSUB_LENS_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_data"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from echosub_lens import lens_app, lens_style  # noqa: E402
from echosub_lens.lens_file_window import FileWindow  # noqa: E402

app = QApplication.instance() or QApplication([])

SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello there\n\n2\n00:00:03,000 --> 00:00:04,000\nGood night\n"


class Tagger:
    """Writes "[key:target] text": shows which translator made a line, and into which language."""

    def __init__(self, key, gate=None):
        self.key, self.gate, self.calls = key, gate, []

    def translate_many(self, texts, target, progress=None):
        self.calls.append(target)
        if self.gate is not None:
            self.gate.wait(10)
        return [f"[{self.key}:{target}] {t}" for t in texts]


def wait_for(window, condition=lambda w: w.finished):
    deadline = time.monotonic() + 10
    while not condition(window) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    return condition(window)


class FileChoicesTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.folder.name, "movie.srt")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(SRT)
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.close()
        app.processEvents()
        self.folder.cleanup()

    def window(self, **kwargs):
        window = FileWindow(self.path, kwargs.pop("target", "fr"), kwargs.pop("translator", Tagger("google")),
                            "auto", lens_style.merged(None), **kwargs)
        self.windows.append(window)
        return window

    def test_changing_the_language_translates_again(self):
        notes = []
        window = self.window(target_choices=[("", "Default (Arabic)"), ("fr", "French"), (None, None),
                                             ("de", "German"), ("ar", "Arabic")], notify=notes.append)
        self.assertEqual(window.target_combo.findData(""), -1)  # Lens's "default" means nothing for a file
        self.assertEqual(window.target_combo.currentData(), "fr")
        self.assertFalse(window.translator_combo.isEnabled())  # no way to get translators: fixed
        self.assertTrue(wait_for(window), window.status.text())
        self.assertIn("[google:fr] Hello there", window.text_view.toPlainText())
        self.assertTrue(window.suggested_name().endswith("movie.fr.srt"))

        window.target_combo.setCurrentIndex(window.target_combo.findData("de"))
        self.assertFalse(window.finished)
        self.assertFalse(window.save_button.isEnabled())
        self.assertTrue(wait_for(window), window.status.text())
        text = window.text_view.toPlainText()
        self.assertIn("[google:de] Hello there", text)
        self.assertIn("00:00:01,000 --> 00:00:02,000", text)
        self.assertNotIn(":fr]", text)
        self.assertIn("German", window.windowTitle())
        self.assertTrue(window.suggested_name().endswith("movie.de.srt"))
        self.assertEqual(window.text_view.layoutDirection(), Qt.LeftToRight)
        self.assertIn("German", notes[-1])

        window.target_combo.setCurrentIndex(window.target_combo.findData("ar"))
        self.assertTrue(wait_for(window))
        self.assertEqual(window.text_view.layoutDirection(), Qt.RightToLeft)

    def test_changing_the_translator_drops_the_old_run(self):
        gate = threading.Event()
        shared, made, released = Tagger("same", gate), {}, []

        def choose(choice):
            made[choice] = Tagger(choice)
            return made[choice]

        window = self.window(translator=shared, translator_choice="same", choose_translator=choose,
                             release_translator=released.append)
        self.assertTrue(window.translator_combo.isEnabled())
        self.assertEqual(window.translator_combo.currentData(), "same")
        self.assertTrue(window.translator_combo.itemData(window.translator_combo.findData("google"), Qt.ToolTipRole))
        self.assertTrue(wait_for(window, lambda w: shared.calls))  # the first run is stuck in its translator

        window.translator_combo.setCurrentIndex(window.translator_combo.findData("google"))
        self.assertIs(window.translator, made["google"])
        self.assertEqual(released, [shared])  # handed back (Lens keeps its own shared one)
        self.assertTrue(wait_for(window), window.status.text())
        gate.set()  # the old run finishes now: what it sends must be ignored
        end = time.monotonic() + 0.3
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)
        text = window.text_view.toPlainText()
        self.assertIn("[google:fr] Hello there", text)
        self.assertNotIn("[same:", text)

        window.translator_combo.setCurrentIndex(window.translator_combo.findData("nllb-600m"))
        self.assertEqual(released, [shared, made["google"]])
        self.assertTrue(wait_for(window))
        self.assertIn("[nllb-600m:fr] Hello there", window.text_view.toPlainText())

    def test_open_file_passes_the_choices_and_releases_the_last_translator(self):
        from echosub_lens import lens_ui

        with mock.patch.object(lens_ui.hotkeys.GlobalHotkeys, "enable_keys", return_value=[]), \
                mock.patch("echosub_lens.lens_menu.lens_ocr.available_languages", return_value=[]), \
                mock.patch.object(lens_ui.LensScanner, "start"):
            lens = lens_ui.LensApp(app, dict(lens_app.DEFAULTS, style=None), lambda cfg: None,
                                   base_cfg={"target_lang": "ar", "translator": "google"})
        try:
            made, released = [], []
            lens.translator_for = lambda choice=None: made.append(choice) or Tagger(choice or "lens")
            lens.release_translator = released.append
            window = lens.open_file(self.path, target="de", translator="nllb-600m")
            self.assertEqual(made, ["nllb-600m"])
            self.assertEqual(window.target, "de")
            self.assertEqual(window.translator_combo.currentData(), "nllb-600m")
            self.assertTrue(window.translator_combo.isEnabled())
            self.assertGreater(window.target_combo.count(), 50)  # every language
            self.assertTrue(wait_for(window))
            first = window.translator
            window.translator_combo.setCurrentIndex(window.translator_combo.findData("google"))
            self.assertEqual(made, ["nllb-600m", "google"])
            self.assertEqual(released, [first])
            last = window.translator
            window.close()
            app.processEvents()
            from PySide6.QtCore import QCoreApplication, QEvent

            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
            self.assertEqual(released, [first, last])
            self.assertNotIn(window, lens.file_windows)
        finally:
            lens.stop()
            lens.tray.hide()
            lens.overlay.close()


if __name__ == "__main__":
    unittest.main()

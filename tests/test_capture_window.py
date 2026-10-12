# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: the capture window reads a part of the screen, translates it, copies it and saves it."""
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

from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from echosub_lens import lens_capture_window, lens_ocr, lens_style  # noqa: E402
from echosub_lens.lens_capture_window import NO_TEXT, ORIGINAL, TRANSLATED, CaptureWindow  # noqa: E402
from echosub_lens.lens_ocr import Box, Line  # noqa: E402

app = QApplication.instance() or QApplication([])

RLM = "\u200f"


def line(text, x, y, h=24):
    """As Windows reads it: words left to right on the screen (an Arabic line's last word first)."""
    words, left = [], x
    order = text.split()
    if any("؀" <= ch <= "ۿ" for ch in text):
        order.reverse()
    for word in order:
        words.append((word, Box(left, y, 14 * len(word), h)))
        left += 14 * len(word) + 8
    return Line(words)


ENGLISH = [line("Save the file before closing", 10, 10), line("Open the settings", 10, 120)]
ARABIC = [line("مرحبا بكم في البرنامج", 10, 60)]


class FakeReader:
    made, closed = [], []
    lines = ENGLISH

    def __init__(self, language="auto"):
        self.language = language
        self.thread = threading.current_thread()
        FakeReader.made.append(self)

    def read(self, image):
        return list(FakeReader.lines)

    def close(self):
        FakeReader.closed.append(self)


class Tagger:
    """Writes "[key:target] text" — shows which translator made a line, and into which language."""

    def __init__(self, key, gate=None, mark=""):
        self.key, self.gate, self.mark, self.calls = key, gate, mark, []

    def translate_many(self, texts, target, progress=None):
        self.calls.append((list(texts), target))
        if self.gate is not None:
            self.gate.wait(10)
        return [f"{self.mark}[{self.key}:{target}] {t}" for t in texts]


class FakeOverlay:
    style = lens_style.merged(None)


class FakeLens:
    def __init__(self, target="ar", translator="google", mark=""):
        self.target = target
        self.cfg = {"translator": translator, "ocr_language": "auto", "style": None, "last_folder": ""}
        self.overlay = FakeOverlay()
        self.made, self.released, self.notes = [], [], []
        self.mark = mark
        self.gates = {}

    def translator_for(self, choice=None):
        translator = Tagger(choice, self.gates.get(choice), self.mark)
        self.made.append(translator)
        return translator

    def release_translator(self, translator):
        self.released.append(translator)

    def _target_choices(self):
        return [("", "Default (Arabic)"), ("ar", "Arabic"), ("fr", "French"), (None, None), ("de", "German")]

    def _notify(self, text):
        self.notes.append(text)

    def _set(self, **values):
        self.cfg.update(values)


def picture(width=600, height=200):
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor("white"))
    return image


def wait_for(condition, seconds=10):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    return condition()


def settle(seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


class CaptureWindowTest(unittest.TestCase):
    def setUp(self):
        FakeReader.made, FakeReader.closed, FakeReader.lines = [], [], ENGLISH
        patcher = mock.patch.object(lens_ocr, "Reader", FakeReader)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            try:
                window.close()
            except RuntimeError:
                pass  # already deleted
        app.processEvents()

    def window(self, lens=None, image=None):
        lens = lens or FakeLens()
        window = CaptureWindow(image or picture(), lens)
        self.windows.append(window)
        return window, lens

    def test_reads_and_translates(self):
        FakeReader.lines = ENGLISH + ARABIC
        window, lens = self.window()
        self.assertEqual(window.windowTitle(), "Capture — EchoSub Lens")
        self.assertTrue(window.windowFlags() & Qt.WindowStaysOnTopHint)
        self.assertTrue(window.testAttribute(Qt.WA_DeleteOnClose))
        self.assertEqual(window.target_combo.findData(""), -1)  # Lens's "default" is left out
        self.assertEqual(window.target_combo.currentData(), "ar")
        self.assertEqual(window.translator_combo.currentData(), "google")
        self.assertTrue(wait_for(lambda: window.finished), window.status.text())
        self.assertEqual(len(FakeReader.made), 1)
        self.assertIsNot(FakeReader.made[0].thread, threading.main_thread())  # read in the worker thread
        self.assertEqual(FakeReader.closed, FakeReader.made)
        # Only the English text was sent; the Arabic text is already in the target language.
        self.assertEqual(lens.made[0].calls, [(["Save the file before closing", "Open the settings"], "ar")])
        self.assertEqual(window.view, TRANSLATED)
        self.assertTrue(window.translated_button.isChecked())
        self.assertEqual(window.text_box.toPlainText().split("\n"),
                         ["[google:ar] Save the file before closing", "مرحبا بكم في البرنامج",
                          "[google:ar] Open the settings"])
        self.assertEqual(window.text_box.layoutDirection(), Qt.RightToLeft)
        self.assertIsNotNone(window.rendered)
        self.assertIn("Arabic", window.status.text())

    def test_view_switch_changes_picture_and_text(self):
        window, _ = self.window()
        self.assertTrue(wait_for(lambda: window.finished))
        translated_picture = window.current_image()
        window.original_button.click()
        self.assertEqual(window.view, ORIGINAL)
        self.assertEqual(window.text_box.toPlainText(), "Save the file before closing\nOpen the settings")
        self.assertEqual(window.text_box.layoutDirection(), Qt.LeftToRight)
        original_picture = window.current_image()
        self.assertNotEqual(original_picture, translated_picture)
        self.assertEqual(original_picture.size(), translated_picture.size())
        self.assertTrue(window.suggested_name().startswith("Capture "))
        self.assertNotIn("(Arabic)", window.suggested_name())
        window.translated_button.click()
        self.assertEqual(window.view, TRANSLATED)
        self.assertIn("[google:ar]", window.text_box.toPlainText())
        self.assertTrue(window.suggested_name().endswith(" (Arabic).png"))

    def test_copy_text_strips_direction_marks(self):
        window, lens = self.window(FakeLens(mark=RLM))
        self.assertTrue(wait_for(lambda: window.finished))
        self.assertIn(RLM, window.text_box.toPlainText())
        window.copy_text_button.click()
        copied = QGuiApplication.clipboard().text()
        self.assertNotIn(RLM, copied)
        self.assertIn("[google:ar] Save the file before closing", copied)
        # A selection copied with Ctrl+C leaves the marks out too.
        window.text_box.selectAll()
        window.text_box.copy()
        self.assertNotIn(RLM, QGuiApplication.clipboard().text())
        self.assertIn("[google:ar] Open the settings", QGuiApplication.clipboard().text())

    def test_copy_picture(self):
        window, lens = self.window()
        self.assertTrue(wait_for(lambda: window.finished))
        QGuiApplication.clipboard().clear()
        window.copy_picture_button.click()
        image = QGuiApplication.clipboard().image()
        self.assertFalse(image.isNull())
        self.assertEqual((image.width(), image.height()), (600, 200))
        self.assertIn("Picture copied", lens.notes)

    def test_save_writes_a_png(self):
        window, lens = self.window()
        self.assertTrue(wait_for(lambda: window.finished))
        out = os.path.join(self.folder.name, "shot")
        with mock.patch.object(lens_capture_window.QFileDialog, "getSaveFileName",
                               return_value=(out, "PNG picture (*.png)")) as dialog:
            saved = window.save_button.click() or window.status.text()
        self.assertIn("(Arabic).png", dialog.call_args[0][2])
        self.assertTrue(os.path.isfile(out + ".png"), saved)
        image = QImage(out + ".png")
        self.assertEqual((image.width(), image.height()), (600, 200))
        self.assertEqual(lens.cfg["last_folder"], self.folder.name)
        self.assertTrue(lens.notes[-1].startswith("Picture saved"))
        self.assertIn("Picture saved", window.status.text())

        jpg = os.path.join(self.folder.name, "shot.jpg")
        with mock.patch.object(lens_capture_window.QFileDialog, "getSaveFileName",
                               return_value=(jpg, "JPEG picture (*.jpg *.jpeg)")) as dialog:
            window.save()
        self.assertTrue(dialog.call_args[0][2].startswith(self.folder.name))  # the folder is remembered
        self.assertFalse(QImage(jpg).isNull())

    def test_changing_language_and_translator_translates_again(self):
        lens = FakeLens()
        gate = threading.Event()
        lens.gates["google"] = gate  # the first run is stuck in its translator
        window, _ = self.window(lens)
        self.assertTrue(wait_for(lambda: lens.made[0].calls))
        window.target_combo.setCurrentIndex(window.target_combo.findData("fr"))
        self.assertEqual(window.target, "fr")
        lens.gates.clear()
        window.translator_combo.setCurrentIndex(window.translator_combo.findData("bing"))
        self.assertEqual(lens.released, [lens.made[0]])
        self.assertEqual(window.translator, lens.made[1])
        self.assertTrue(wait_for(lambda: window.finished), window.status.text())
        gate.set()  # the old run ends now: what it sends is dropped
        settle()
        self.assertIn("[bing:fr] Save the file before closing", window.text_box.toPlainText())
        self.assertNotIn("[google:", window.text_box.toPlainText())
        self.assertEqual(window.text_box.layoutDirection(), Qt.LeftToRight)

        window.target_combo.setCurrentIndex(window.target_combo.findData("de"))
        self.assertTrue(wait_for(lambda: window.finished))
        self.assertIn("[bing:de] Open the settings", window.text_box.toPlainText())
        self.assertEqual(len(lens.made), 2)  # the language changed, not the translator
        self.assertEqual(lens.released, [lens.made[0]])

    def test_retranslating_does_not_read_again(self):
        window, lens = self.window()
        self.assertTrue(wait_for(lambda: window.finished))
        window.target_combo.setCurrentIndex(window.target_combo.findData("fr"))
        self.assertTrue(wait_for(lambda: window.finished))
        self.assertEqual(len(FakeReader.made), 1)
        self.assertIn("[google:fr]", window.text_box.toPlainText())

    def test_cancel_and_escape_close_and_release(self):
        for close in ("cancel", "escape"):
            window, lens = self.window()
            window.show()
            self.assertTrue(wait_for(lambda: window.finished))
            translator = window.translator
            if close == "cancel":
                window.cancel_button.click()
            else:
                window.activateWindow()
                QTest.keyClick(window, Qt.Key_Escape)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
            self.assertEqual(lens.released, [translator], close)
            self.windows.remove(window)

    def test_no_text(self):
        FakeReader.lines = []
        window, lens = self.window()
        self.assertTrue(wait_for(lambda: window.finished))
        self.assertEqual(window.status.text(), NO_TEXT)
        self.assertEqual(window.view, ORIGINAL)
        self.assertEqual(window.text_box.toPlainText(), "")
        self.assertEqual(window.text_box.placeholderText(), NO_TEXT)
        self.assertEqual(lens.made[0].calls, [])  # nothing to translate
        window.copy_picture_button.click()
        self.assertFalse(QGuiApplication.clipboard().image().isNull())
        out = os.path.join(self.folder.name, "empty.png")
        with mock.patch.object(lens_capture_window.QFileDialog, "getSaveFileName", return_value=(out, "")):
            window.save()
        self.assertTrue(os.path.isfile(out))

    def test_text_already_in_the_target_language(self):
        FakeReader.lines = ARABIC
        window, lens = self.window()
        self.assertTrue(wait_for(lambda: window.finished))
        self.assertEqual(window.view, ORIGINAL)
        self.assertEqual(window.text_box.toPlainText(), "مرحبا بكم في البرنامج")
        self.assertEqual(window.text_box.layoutDirection(), Qt.RightToLeft)
        self.assertIn("already in Arabic", window.status.text())

    def test_reading_fails_in_words(self):
        with mock.patch.object(FakeReader, "read", side_effect=RuntimeError("no OCR language")):
            window, lens = self.window()
            self.assertTrue(wait_for(lambda: window.finished))
        self.assertEqual(window.status.text(), "Could not read the text: no OCR language")
        self.assertEqual(window.view, ORIGINAL)

    def test_high_dpi_capture_keeps_its_pixels(self):
        image = picture(1200, 400)
        image.setDevicePixelRatio(2.0)
        window, lens = self.window(image=image)
        self.assertTrue(wait_for(lambda: window.finished))
        copy = window.current_image()
        self.assertEqual((copy.width(), copy.height(), copy.devicePixelRatio()), (1200, 400, 2.0))
        out = os.path.join(self.folder.name, "hidpi.png")
        with mock.patch.object(lens_capture_window.QFileDialog, "getSaveFileName", return_value=(out, "")):
            window.save()
        self.assertEqual(QImage(out).width(), 1200)

    def test_with_the_real_lens_app(self):
        from echosub_lens import lens_app, lens_ui

        with mock.patch.object(lens_ui.hotkeys.GlobalHotkeys, "enable_keys", return_value=[]), \
                mock.patch("echosub_lens.lens_menu.lens_ocr.available_languages", return_value=[]), \
                mock.patch.object(lens_ui.LensScanner, "start"):
            lens = lens_ui.LensApp(app, dict(lens_app.DEFAULTS, style=None), lambda cfg: None,
                                   base_cfg={"target_lang": "ar", "translator": "google"})
        try:
            made, released = [], []
            lens.translator_for = lambda choice=None: made.append(choice) or Tagger(choice or "lens")
            lens.release_translator = released.append
            window = CaptureWindow(picture(), lens)
            self.assertEqual(made, [lens.cfg["translator"]])
            self.assertGreater(window.target_combo.count(), 50)  # every language
            self.assertTrue(wait_for(lambda: window.finished), window.status.text())
            self.assertIn(":ar] Save the file before closing", window.text_box.toPlainText())
            first = window.translator
            window.close()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
            self.assertEqual(released, [first])
        finally:
            lens.stop()
            lens.tray.hide()
            lens.overlay.close()


if __name__ == "__main__":
    unittest.main()

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
from PySide6.QtGui import QColor, QGuiApplication, QImage, QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    from echosub_lens import google_lens  # noqa: E402, F401
except ImportError:  # not written yet: the window only needs its search_picture(png), patched in the tests
    import types

    import echosub_lens

    _fake = types.ModuleType("echosub_lens.google_lens")
    _fake.search_picture = lambda png: "Google Lens is opening in your browser"
    sys.modules["echosub_lens.google_lens"] = echosub_lens.google_lens = _fake

from echosub_lens import lens_capture_window, lens_ocr, lens_style  # noqa: E402
from echosub_lens.lens_capture_window import NO_TEXT, TRANSLATING, CaptureWindow  # noqa: E402
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
        self.assertFalse(hasattr(window, "original_button"))  # no views any more: two text boxes
        self.assertEqual(window.translation_box.toPlainText().split("\n"),
                         ["[google:ar] Save the file before closing", "مرحبا بكم في البرنامج",
                          "[google:ar] Open the settings"])
        self.assertEqual(window.translation_box.layoutDirection(), Qt.RightToLeft)
        option = window.translation_box.document().defaultTextOption()
        self.assertEqual(option.alignment(), Qt.AlignRight)
        self.assertEqual(window.original_box.toPlainText().split("\n"),
                         ["Save the file before closing", "مرحبا بكم في البرنامج", "Open the settings"])
        self.assertEqual(window.original_box.layoutDirection(), Qt.LeftToRight)
        self.assertEqual(window.original_heading.text(), "Original")
        self.assertEqual(window.translation_heading.text(), "Translation (Arabic)")
        self.assertIsNotNone(window.rendered)
        self.assertIn("Arabic", window.status.text())

    def test_two_boxes_and_the_picture_stays_as_captured(self):
        lens = FakeLens()
        gate = threading.Event()
        lens.gates["google"] = gate
        window, _ = self.window(lens)
        self.assertTrue(wait_for(lambda: window.lines is not None))
        # read, not translated yet: the original is there, the translation box says it is working
        self.assertEqual(window.original_box.toPlainText(), "Save the file before closing\nOpen the settings")
        self.assertEqual(window.translation_box.toPlainText(), "")
        self.assertEqual(window.translation_box.placeholderText(), TRANSLATING)
        self.assertFalse(window.search_rendered_action.isEnabled())
        self.assertFalse(window.search_translation_action.isEnabled())
        self.assertTrue(window.search_original_action.isEnabled())
        gate.set()
        self.assertTrue(wait_for(lambda: window.finished))
        self.assertTrue(window.search_rendered_action.isEnabled())
        self.assertTrue(window.search_translation_action.isEnabled())
        # the window, its copy and its file always hold the picture as captured
        self.assertEqual(window.current_image().convertToFormat(QImage.Format_RGB32), picture())
        self.assertNotEqual(window.rendered.convertToFormat(QImage.Format_RGB32), picture())
        shown = window.picture_label.pixmap().toImage().convertToFormat(QImage.Format_RGB32)
        self.assertEqual(shown.pixelColor(shown.width() // 2, 20), QColor("white"))
        self.assertEqual(window.suggested_name(), time.strftime("Capture %Y-%m-%d %H-%M-%S.png", window.taken))
        window.target_combo.setCurrentIndex(window.target_combo.findData("fr"))
        self.assertEqual(window.translation_heading.text(), "Translation (French)")
        self.assertTrue(wait_for(lambda: window.finished))
        self.assertEqual(window.translation_box.layoutDirection(), Qt.LeftToRight)
        self.assertEqual(window.suggested_name(), time.strftime("Capture %Y-%m-%d %H-%M-%S.png", window.taken))

    def test_each_line_of_the_picture_on_its_own_line(self):
        # two lines close together are one block (translated as one text), yet two lines in the text box
        FakeReader.lines = [line("Save the file", 10, 10), line("before closing it", 10, 38),
                            line("Open the settings", 10, 120), line("Close", 10, 170)]
        window, lens = self.window()
        self.assertTrue(wait_for(lambda: window.finished), window.status.text())
        self.assertEqual(lens.made[0].calls,
                         [(["Save the file before closing it", "Open the settings", "Close"], "ar")])
        self.assertEqual(window.translation_box.toPlainText().split("\n"),
                         ["[google:ar] Save the", "file before closing it", "", "[google:ar] Open the settings",
                          "[google:ar] Close"])
        window.copy_translation_button.click()
        self.assertEqual(QGuiApplication.clipboard().text(), window.translation_box.toPlainText())
        self.assertEqual(lens.notes[-1], "Translation copied")
        self.assertEqual(window.original_box.toPlainText(),
                         "Save the file\nbefore closing it\n\nOpen the settings\nClose")
        window.copy_original_button.click()
        self.assertEqual(QGuiApplication.clipboard().text(),
                         "Save the file\nbefore closing it\n\nOpen the settings\nClose")
        self.assertEqual(lens.notes[-1], "Original text copied")

    def test_shown_text(self):
        self.assertEqual(lens_capture_window.shown_text([["a"], ["b", "c"], ["d"], ["e"]]), "a\n\nb\nc\n\nd\ne")
        self.assertEqual(lens_capture_window.shown_text([]), "")

    def test_copy_text_strips_direction_marks(self):
        window, lens = self.window(FakeLens(mark=RLM))
        self.assertTrue(wait_for(lambda: window.finished))
        self.assertIn(RLM, window.translation_box.toPlainText())
        window.copy_translation_button.click()
        copied = QGuiApplication.clipboard().text()
        self.assertNotIn(RLM, copied)
        self.assertIn("[google:ar] Save the file before closing", copied)
        # A selection copied with Ctrl+C leaves the marks out too.
        window.translation_box.selectAll()
        window.translation_box.copy()
        self.assertNotIn(RLM, QGuiApplication.clipboard().text())
        self.assertIn("[google:ar] Open the settings", QGuiApplication.clipboard().text())

    def test_copy_original_strips_direction_marks(self):
        FakeReader.lines = [line(RLM + "- مرحبا بكم", 10, 60)]
        window, lens = self.window(FakeLens(target="en"))
        self.assertTrue(wait_for(lambda: window.finished))
        self.assertIn(RLM, window.original_box.toPlainText())
        self.assertEqual(window.original_box.layoutDirection(), Qt.RightToLeft)
        self.assertEqual(window.translation_box.layoutDirection(), Qt.LeftToRight)
        window.copy_original_button.click()
        self.assertNotIn(RLM, QGuiApplication.clipboard().text())
        self.assertIn("مرحبا", QGuiApplication.clipboard().text())
        window.original_box.selectAll()
        window.original_box.copy()
        self.assertNotIn(RLM, QGuiApplication.clipboard().text())

    def test_copy_picture(self):
        window, lens = self.window()
        self.assertTrue(wait_for(lambda: window.finished))
        QGuiApplication.clipboard().clear()
        window.copy_picture_button.click()
        image = QGuiApplication.clipboard().image()
        self.assertFalse(image.isNull())
        self.assertEqual((image.width(), image.height()), (600, 200))
        self.assertEqual(image.convertToFormat(QImage.Format_RGB32), picture())  # not the translated one
        self.assertIn("Picture copied", lens.notes)

    def test_save_writes_a_png(self):
        window, lens = self.window()
        self.assertTrue(wait_for(lambda: window.finished))
        out = os.path.join(self.folder.name, "shot")
        with mock.patch.object(lens_capture_window.QFileDialog, "getSaveFileName",
                               return_value=(out, "PNG picture (*.png)")) as dialog:
            saved = window.save_button.click() or window.status.text()
        self.assertTrue(os.path.basename(dialog.call_args[0][2]).startswith("Capture "))
        self.assertNotIn("(Arabic)", dialog.call_args[0][2])
        self.assertTrue(os.path.isfile(out + ".png"), saved)
        image = QImage(out + ".png")
        self.assertEqual((image.width(), image.height()), (600, 200))
        self.assertEqual(image.convertToFormat(QImage.Format_RGB32), picture())  # the capture, as it is
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
        self.assertIn("[bing:fr] Save the file before closing", window.translation_box.toPlainText())
        self.assertNotIn("[google:", window.translation_box.toPlainText())
        self.assertEqual(window.translation_box.layoutDirection(), Qt.LeftToRight)

        window.target_combo.setCurrentIndex(window.target_combo.findData("de"))
        self.assertTrue(wait_for(lambda: window.finished))
        self.assertIn("[bing:de] Open the settings", window.translation_box.toPlainText())
        self.assertEqual(len(lens.made), 2)  # the language changed, not the translator
        self.assertEqual(lens.released, [lens.made[0]])

    def test_a_new_key_gives_a_new_translator_and_translates_again(self):
        window, lens = self.window(FakeLens(translator="deepl"))
        self.assertTrue(wait_for(lambda: window.finished))
        first = window.translator
        self.assertFalse(window.key_changed("bing"))  # another service: nothing changes
        self.assertEqual(lens.made, [first])
        self.assertTrue(window.key_changed("deepl"))
        self.assertEqual(len(lens.made), 2)
        self.assertIs(window.translator, lens.made[1])
        self.assertEqual(lens.released, [first])
        self.assertTrue(wait_for(lambda: window.finished))
        self.assertEqual(len(lens.made[1].calls), 1)  # translated again, with the new one
        self.assertEqual(len(FakeReader.made), 1)  # not read again
        window.release()
        self.assertFalse(window.key_changed("deepl"))  # closing: nothing more

    def test_retranslating_does_not_read_again(self):
        window, lens = self.window()
        self.assertTrue(wait_for(lambda: window.finished))
        window.target_combo.setCurrentIndex(window.target_combo.findData("fr"))
        self.assertTrue(wait_for(lambda: window.finished))
        self.assertEqual(len(FakeReader.made), 1)
        self.assertIn("[google:fr]", window.translation_box.toPlainText())

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
        for box in (window.original_box, window.translation_box):
            self.assertEqual(box.toPlainText(), "")
            self.assertEqual(box.placeholderText(), NO_TEXT)
        self.assertEqual(lens.made[0].calls, [])  # nothing to translate
        self.assertIsNone(window.rendered)
        for action in (window.search_rendered_action, window.search_original_action,
                       window.search_translation_action):
            self.assertFalse(action.isEnabled(), action.text())
        self.assertTrue(window.search_picture_action.isEnabled())
        window.copy_original_button.click()
        self.assertEqual(lens.notes[-1], "There is no text to copy")
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
        for box in (window.original_box, window.translation_box):
            self.assertEqual(box.toPlainText(), "مرحبا بكم في البرنامج")
            self.assertEqual(box.layoutDirection(), Qt.RightToLeft)
        self.assertIn("already in Arabic", window.status.text())
        self.assertIsNone(window.rendered)  # nothing translated: no translated picture to search
        self.assertFalse(window.search_rendered_action.isEnabled())

    def test_reading_fails_in_words(self):
        with mock.patch.object(FakeReader, "read", side_effect=RuntimeError("no OCR language")):
            window, lens = self.window()
            self.assertTrue(wait_for(lambda: window.finished))
        self.assertEqual(window.status.text(), "Could not read the text: no OCR language")
        self.assertEqual(window.original_box.placeholderText(), "Could not read the text: no OCR language")
        self.assertEqual(window.translation_box.placeholderText(), "Could not read the text: no OCR language")

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
            self.assertIn(":ar] Save the file before closing", window.translation_box.toPlainText())
            first = window.translator
            window.close()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
            self.assertEqual(released, [first])
        finally:
            lens.stop()
            lens.tray.hide()
            lens.overlay.close()


class GoogleSearchTest(CaptureWindowTest.__bases__[0]):
    """The "Search Google" button: the text (selected, or all) in Google, the picture in Google Lens."""

    def setUp(self):
        FakeReader.made, FakeReader.closed, FakeReader.lines = [], [], ENGLISH
        patcher = mock.patch.object(lens_ocr, "Reader", FakeReader)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lens = FakeLens()
        self.window = CaptureWindow(picture(), self.lens)
        self.addCleanup(self.window.close)
        self.assertTrue(wait_for(lambda: self.window.finished))
        self.opened = []
        opener = mock.patch.object(lens_capture_window.QDesktopServices, "openUrl",
                                   side_effect=lambda url: self.opened.append(url.toString()))
        opener.start()
        self.addCleanup(opener.stop)
        self.sent = []
        lens_search = mock.patch.object(lens_capture_window.google_lens, "search_picture", create=True,
                                        side_effect=self._lens_opened)
        lens_search.start()
        self.addCleanup(lens_search.stop)

    def _lens_opened(self, png):
        self.sent.append(png)
        return "Google Lens is opening in your browser"

    @staticmethod
    def select(box, start, end):
        cursor = box.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        box.setTextCursor(cursor)

    def test_exactly_four_searches(self):
        self.assertEqual([a.text() for a in self.window.search_menu.actions() if not a.isSeparator()],
                         ["Search the picture (Google Lens)", "Search the picture, translated (Google Lens)",
                          "Search the original text", "Search the translation"])
        self.window._update_actions()
        self.assertTrue(all(a.isEnabled() for a in self.window.search_menu.actions()))

    def test_the_original_text_or_its_selection_is_searched(self):
        w = self.window
        w.search_original_action.trigger()
        self.assertTrue(self.opened[-1].startswith(lens_capture_window.GOOGLE_SEARCH))
        self.assertTrue(self.opened[-1].endswith("q=Save+the+file+before+closing+Open+the+settings"))
        self.select(w.original_box, 0, 4)
        w.search_original_action.trigger()
        self.assertTrue(self.opened[-1].endswith("q=Save"))
        self.assertIn("Searching Google for the original text, in your browser", self.lens.notes)
        self.assertEqual(self.sent, [])  # no picture went anywhere
        self.assertEqual(lens_capture_window.text_search_url("‏- مرحبا\nبكم"),
                         lens_capture_window.GOOGLE_SEARCH + "-+%D9%85%D8%B1%D8%AD%D8%A8%D8%A7+%D8%A8%D9%83%D9%85")

    def test_the_translation_or_its_selection_is_searched(self):
        w = self.window
        w.search_translation_action.trigger()
        self.assertTrue(self.opened[-1].endswith(
            "q=%5Bgoogle%3Aar%5D+Save+the+file+before+closing+%5Bgoogle%3Aar%5D+Open+the+settings"))
        self.select(w.original_box, 0, 4)  # a selection in the other box does not count
        w.search_translation_action.trigger()
        self.assertIn("%5Bgoogle%3Aar%5D+Open+the+settings", self.opened[-1])
        self.select(w.translation_box, 0, 11)
        w.search_translation_action.trigger()
        self.assertTrue(self.opened[-1].endswith("q=%5Bgoogle%3Aar%5D"))

    def test_the_picture_as_captured_goes_to_google_lens(self):
        self.window.search_picture_action.trigger()
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0].startswith(b"\x89PNG"))
        self.assertEqual(QImage.fromData(self.sent[0]).convertToFormat(QImage.Format_RGB32), picture())
        self.assertEqual(self.window.status.text(), "Google Lens is opening in your browser")
        self.assertIn("Google Lens is opening in your browser", self.lens.notes)
        self.assertEqual(self.opened, [])  # the module opens the browser itself

    def test_the_translated_picture_goes_to_google_lens(self):
        self.window.search_rendered_action.trigger()
        self.assertEqual(len(self.sent), 1)
        sent = QImage.fromData(self.sent[0]).convertToFormat(QImage.Format_RGB32)
        self.assertEqual(sent, self.window.rendered.convertToFormat(QImage.Format_RGB32))
        self.assertNotEqual(sent, picture())

    def test_no_translated_picture_while_translating(self):
        lens = FakeLens()
        lens.gates["google"] = gate = threading.Event()
        window = CaptureWindow(picture(), lens)
        self.addCleanup(gate.set)       # after the window is closed: its run is cancelled
        self.addCleanup(window.close)
        self.assertTrue(wait_for(lambda: window.lines is not None))
        self.assertFalse(window.search_rendered_action.isEnabled())
        self.assertFalse(window.search_translation_action.isEnabled())
        window.search_translated_picture()
        self.assertEqual(self.sent, [])
        self.assertEqual(lens.notes[-1], "There is no translated picture yet")

    def test_when_google_lens_cannot_open_the_picture_is_copied_to_paste_there(self):
        QGuiApplication.clipboard().clear()
        with mock.patch.object(lens_capture_window.google_lens, "search_picture", create=True,
                               side_effect=OSError("no browser")):
            self.window.search_picture()
        self.assertEqual(self.window.status.text(), "Could not open Google Lens: no browser. The picture is "
                                                    "copied: paste it in lens.google.com (Ctrl+V).")
        copied = QGuiApplication.clipboard().image()
        self.assertEqual(copied.convertToFormat(QImage.Format_RGB32), picture())
        self.assertIn("Picture copied — paste it in Google Lens (Ctrl+V)", self.lens.notes)


if __name__ == "__main__":
    unittest.main()

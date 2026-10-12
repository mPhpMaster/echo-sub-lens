# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: "Capture part of the screen" — the key, the menu item, the button, the picker and the window."""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ECHOSUB_LENS_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_data"))

from PySide6.QtCore import QCoreApplication, QEvent, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from echosub_lens import lens_app, lens_picker, lens_shortcuts, lens_ui  # noqa: E402

app = QApplication.instance() or QApplication([])


class FakeCaptureWindow(QWidget):
    """Stands in for lens_capture_window.CaptureWindow(image, lens)."""
    made = []

    def __init__(self, image, lens):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.image, self.lens = image, lens
        FakeCaptureWindow.made.append(self)


class FakePicker:
    made = []

    def __init__(self, on_done, hint=lens_picker.HINT):
        self.on_done, self.hint, self.started = on_done, hint, 0
        FakePicker.made.append(self)

    def start(self):
        self.started += 1


class CaptureFlowTest(unittest.TestCase):
    def setUp(self):
        FakeCaptureWindow.made, FakePicker.made = [], []
        fake_module = types.ModuleType("echosub_lens.lens_capture_window")
        fake_module.CaptureWindow = FakeCaptureWindow
        self.grabbed = []

        def gdi(screen, rect):
            self.grabbed.append((screen, QRect(rect)))
            image = QImage(rect.width(), rect.height(), QImage.Format_ARGB32)
            image.fill(QColor("white"))
            return image

        patches = [mock.patch.object(lens_ui.hotkeys.GlobalHotkeys, "enable_keys", return_value=[]),
                   mock.patch("echosub_lens.lens_menu.lens_ocr.available_languages", return_value=["en-US"]),
                   mock.patch.object(lens_ui.LensScanner, "start", lambda scanner: scanner._timer.start()),
                   mock.patch.dict(sys.modules, {"echosub_lens.lens_capture_window": fake_module}),
                   mock.patch.object(lens_ui, "RegionPicker", FakePicker),
                   mock.patch.object(lens_ui.lens_capture, "gdi", gdi)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.lens = lens_ui.LensApp(app, dict(lens_app.DEFAULTS), lambda cfg: None,
                                    base_cfg={"target_lang": "ar", "translator": "google"})
        self.notes = []
        self.lens.activity.tell = self.notes.append
        self.addCleanup(self._close)

    def _close(self):
        for window in list(self.lens.capture_windows):
            window.close()
        if self.lens.main_window is not None:
            self.lens.main_window.hide()
        self.lens.stop()
        self.lens.tray.hide()
        self.lens.overlay.close()

    def _wait_for_photo(self):
        QTest.qWait(lens_ui.CAPTURE_SETTLE_MS + 150)

    # ---- where it is offered ---------------------------------------------------
    def test_a_key_of_its_own(self):
        self.assertEqual(lens_shortcuts.ACTIONS["snip"], ("Capture part of the screen", "Ctrl+Alt+S"))
        self.assertEqual(lens_shortcuts.problems(lens_shortcuts.defaults()), {})  # no other action has it
        self.assertEqual(self.lens.actions()["snip"], self.lens.capture_screen)

    def test_the_tray_menu_offers_it_near_the_top(self):
        texts = [a.text() for a in self.lens.menu.actions()]
        capture = next(i for i, t in enumerate(texts) if t.startswith("Capture part of the screen…"))
        self.assertTrue(texts[capture].endswith(lens_shortcuts.native("Ctrl+Alt+S")))
        self.assertLess(texts.index(next(t for t in texts if t.startswith("Translating"))), capture)
        self.assertLess(capture, texts.index("What to translate"))
        with mock.patch.object(self.lens, "capture_screen") as capture_screen:
            self.lens.refresh_menu()  # the menu item calls the action by its name
            item = next(a for a in self.lens.menu.actions() if a.text().startswith("Capture part"))
            item.trigger()
        capture_screen.assert_called_once()

    def test_the_lens_window_has_a_button(self):
        window = self.lens.open_window("translate")
        with mock.patch.object(self.lens, "capture_screen") as capture_screen:
            window.capture_button.click()
        capture_screen.assert_called_once()
        self.assertIn("Capture part of the screen", window.capture_button.text())

    # ---- the picker --------------------------------------------------------------
    def test_it_starts_the_picker_once(self):
        self.lens.capture_screen()
        self.assertEqual(len(FakePicker.made), 1)
        self.assertEqual(FakePicker.made[0].started, 1)
        self.assertEqual(FakePicker.made[0].hint, lens_picker.CAPTURE_HINT)
        self.lens.capture_screen()
        self.lens.choose_area()  # the same picker: not a second one over the first
        self.assertEqual(len(FakePicker.made), 1)

    def test_the_screen_translation_waits_while_picking_and_goes_on(self):
        self.assertTrue(self.lens.scanner.running)
        self.lens.capture_screen()
        self.assertTrue(self.lens.scanner.paused())
        FakePicker.made[0].on_done((QGuiApplication.primaryScreen(), QRect(10, 20, 640, 200)))
        self.assertTrue(self.lens.scanner.paused())  # until the photo is taken
        self._wait_for_photo()
        self.assertFalse(self.lens._capturing)
        self.assertTrue(self.lens.scanner.running)

    def test_a_chosen_area_opens_the_window_with_its_picture(self):
        screen = QGuiApplication.primaryScreen()
        with mock.patch.object(self.lens.overlay, "set_peeking") as peeking:
            self.lens.capture_screen()
            FakePicker.made[0].on_done((screen, QRect(10, 20, 640, 200)))
            self.assertEqual(FakeCaptureWindow.made, [])  # not yet: the shades leave the screen first
            self._wait_for_photo()
        self.assertEqual(self.grabbed, [(screen, QRect(10, 20, 640, 200))])
        self.assertEqual([c.args for c in peeking.call_args_list], [(True,), (False,)])  # translations left out
        self.assertEqual(len(FakeCaptureWindow.made), 1)
        window = FakeCaptureWindow.made[0]
        self.assertIsInstance(window.image, QImage)
        self.assertEqual((window.image.width(), window.image.height()), (640, 200))
        self.assertEqual(window.image.devicePixelRatio(), screen.devicePixelRatio())
        self.assertIs(window.lens, self.lens)
        self.assertTrue(window.isVisible())
        self.assertEqual(self.lens.capture_windows, [window])
        self.assertIn("Captured 640 × 200 — reading the text…", self.notes)
        self.assertIsNone(self.lens.picker)

        window.close()  # deleted on close: Lens lets go of it
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertEqual(self.lens.capture_windows, [])

    def test_escape_opens_nothing_and_says_so(self):
        self.lens.capture_screen()
        FakePicker.made[0].on_done(None)
        self._wait_for_photo()
        self.assertEqual(FakeCaptureWindow.made, [])
        self.assertEqual(self.grabbed, [])
        self.assertIn("Capture canceled", self.notes)
        self.assertIsNone(self.lens.picker)
        self.assertFalse(self.lens.scanner.paused())
        self.lens.capture_screen()  # and it can be started again
        self.assertEqual(len(FakePicker.made), 2)

    def test_a_failed_photo_opens_nothing(self):
        with mock.patch.object(lens_ui.lens_capture, "gdi", return_value=QImage()):
            self.lens.capture_screen()
            FakePicker.made[0].on_done((QGuiApplication.primaryScreen(), QRect(0, 0, 100, 100)))
            self._wait_for_photo()
        self.assertEqual(FakeCaptureWindow.made, [])
        self.assertIn("Could not capture the screen", self.notes)
        self.assertFalse(self.lens._capturing)


if __name__ == "__main__":
    unittest.main()

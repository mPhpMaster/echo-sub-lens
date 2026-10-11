# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: the window a click on the tray icon opens, and its About page."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ECHOSUB_LENS_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_data"))

from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication, QSystemTrayIcon  # noqa: E402

import echosub_lens  # noqa: E402
from echosub_lens import lens_app, lens_ui  # noqa: E402
from echosub_lens.lens_about import AboutPage  # noqa: E402

app = QApplication.instance() or QApplication([])


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.saved = []
        patches = [mock.patch.object(lens_ui.hotkeys.GlobalHotkeys, "enable_keys", return_value=[]),
                   mock.patch("echosub_lens.lens_menu.lens_ocr.available_languages", return_value=["en-US", "ar-SA"]),
                   mock.patch.object(lens_ui.LensScanner, "start", lambda scanner: scanner._timer.start())]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.lens = lens_ui.LensApp(app, dict(lens_app.DEFAULTS), lambda cfg: self.saved.append(dict(cfg)),
                                    base_cfg={"target_lang": "ar", "translator": "google"})
        self.lens.activity.tell = lambda text: None  # no notes on the screen while testing
        self.addCleanup(self._close)

    def _close(self):
        if self.lens.main_window is not None:
            self.lens.main_window.hide()
        self.lens.stop()
        self.lens.tray.hide()
        self.lens.overlay.close()

    def test_a_click_on_the_tray_icon_opens_the_window_and_does_not_stop(self):
        self.assertTrue(self.lens.scanner.running)
        self.lens._tray_activated(QSystemTrayIcon.Trigger)
        window = self.lens.main_window
        self.assertIsNotNone(window)
        self.assertTrue(window.isVisible())
        self.assertTrue(self.lens.scanner.running)  # still translating: a click no longer toggles
        self.lens._tray_activated(QSystemTrayIcon.DoubleClick)
        self.assertIs(self.lens.main_window, window)  # the same window, not a second one
        texts = [a.text() for a in self.lens.menu.actions()]
        self.assertTrue(texts[0].startswith("Open EchoSub Lens"))
        self.assertTrue(any(t.startswith("About EchoSub Lens") for t in texts))

    def test_the_big_button_starts_and_stops_and_shows_it(self):
        window = self.lens.open_window()
        self.assertEqual(window.state_title.text(), "Translating")
        self.assertIn("Stop", window.start_button.text())
        window.start_button.click()
        self.assertFalse(self.lens.scanner.running)
        self.assertEqual(window.state_title.text(), "Stopped")
        self.assertIn("Start", window.start_button.text())
        self.lens.toggle()  # a key or the menu: the window follows
        self.assertTrue(window.start_button.property("running"))
        self.assertIn("Stop", window.start_button.text())
        self.assertTrue(self.lens.act_running.isChecked())

    def test_choices_go_through_lens_and_the_menu_follows(self):
        window = self.lens.open_window("languages")
        self.assertEqual(window.current_page(), "languages")
        index = window.target_box.findData("fr")
        window.target_box.setCurrentIndex(index)
        window.target_box.activated.emit(index)
        self.assertEqual(self.lens.target, "fr")
        self.assertEqual(self.saved[-1]["target_lang"], "fr")
        menu = next(a.menu() for a in self.lens.menu.actions() if a.text() == "Translate into")
        self.assertTrue(next(a for a in menu.actions() if a.text() == "French").isChecked())

        with mock.patch.object(self.lens, "set_translator") as set_translator:
            window.translator_buttons["nllb-600m"].click()
        set_translator.assert_called_once_with("nllb-600m")

        index = window.ocr_box.findData("ar-SA")
        window.ocr_box.setCurrentIndex(index)
        window.ocr_box.activated.emit(index)
        self.assertEqual(self.lens.cfg["ocr_language"], "ar-SA")

        window.capture_box.setChecked(False)
        self.assertTrue(self.lens.overlay.capture_hidden)
        self.assertFalse(self.lens.act_capture.isChecked())

        self.lens._set_peek(10)  # from the menu: the window shows it
        self.assertEqual(window.peek_box.currentData(), 10)
        self.lens.act_select.setChecked(True)
        self.assertTrue(window.select_box.isChecked())

        window.mode_mouse.click()
        self.assertEqual(self.lens.cfg["mode"], "mouse")
        self.assertTrue(self.lens.act_mouse.isChecked())
        window.mode_screen.click()
        self.assertEqual(self.lens.cfg["mode"], "screen")

    def test_settings_open_the_window_on_their_page(self):
        window = self.lens.open_settings("appearance")
        self.assertEqual(window.current_page(), "appearance")
        window.appearance.bold.setChecked(True)  # shows at once
        self.assertTrue(self.lens.overlay.style["bold"])
        self.assertTrue(self.saved[-1]["style"]["bold"])
        self.lens.open_settings("shortcuts")
        self.assertEqual(window.current_page(), "shortcuts")

    def test_keys_are_kept_only_when_windows_gives_them(self):
        window = self.lens.open_settings("shortcuts")
        self.assertFalse(window.save_keys.isEnabled())
        window.shortcuts.editors["copy_all"].setKeySequence(QKeySequence("F7"))
        self.assertTrue(window.save_keys.isEnabled())
        with mock.patch.object(self.lens, "apply_keys", return_value=["F7"]) as apply_keys:
            self.assertFalse(window.apply_keys())
        self.assertEqual(apply_keys.call_args[0][0]["copy_all"], "F7")
        self.assertIn("F7", window.shortcuts.status.text())
        with mock.patch.object(self.lens.hotkeys, "enable_keys", return_value=[]):
            self.assertTrue(window.apply_keys())
        self.assertEqual(self.lens.keys["copy_all"], "F7")
        self.assertFalse(window.save_keys.isEnabled())

    def test_closing_only_hides_the_window_and_keeps_its_place(self):
        window = self.lens.open_window()
        window.close()
        self.assertFalse(window.isVisible())
        self.assertTrue(self.lens.scanner.running)
        self.assertEqual(len(self.saved[-1]["window_geometry"]), 4)

    def test_the_about_page(self):
        window = self.lens.open_window("about")
        self.assertEqual(window.current_page(), "about")
        about = window.about
        self.assertIn(echosub_lens.__version__, about.name.text())
        self.assertEqual(about.support_button.toolTip(), echosub_lens.DONATE_URL)
        self.assertIn("GPL", about.legal.text().replace("GNU General Public License", "GPL"))
        self.assertEqual(set(about.link_buttons), {"GitHub", "LinkedIn", "Discord", "Email"})
        supported = []
        page = AboutPage(on_support=lambda: supported.append(True))
        page.support_button.click()
        self.assertEqual(supported, [True])


if __name__ == "__main__":
    unittest.main()

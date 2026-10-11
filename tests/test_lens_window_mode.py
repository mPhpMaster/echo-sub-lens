# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: translating one window, and the translation window's transparency and direction."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ECHOSUB_LENS_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_data"))

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from echosub_lens import app_windows, lens_app, lens_ui  # noqa: E402
from echosub_lens.app_windows import AppWindow  # noqa: E402
from echosub_lens.lens_ocr import Block, Box, Line  # noqa: E402

app = QApplication.instance() or QApplication([])


class FakeScreen:
    def __init__(self, name, x, y, w, h, ratio):
        self._name, self._g, self._ratio = name, QRect(x, y, w, h), ratio

    def geometry(self):
        return self._g

    def devicePixelRatio(self):
        return self._ratio

    def name(self):
        return self._name


class WindowsTest(unittest.TestCase):
    def test_windows_pixels_become_a_screen_and_a_rectangle_on_it(self):
        left = FakeScreen("A", 0, 0, 1536, 864, 1.25)       # 1920 × 1080 at 125 %
        right = FakeScreen("B", 1920, 0, 1920, 1080, 1.0)
        screen, rect = app_windows.to_screen(125, 250, 1125, 750, [left, right])
        self.assertEqual((screen.name(), rect.getRect()), ("A", (100, 200, 800, 400)))
        screen, rect = app_windows.to_screen(2020, 100, 2620, 500, [left, right])
        self.assertEqual((screen.name(), rect.getRect()), ("B", (100, 100, 600, 400)))

    def test_the_window_chosen_before_is_found_again(self):
        windows = [AppWindow(1, "Movie.mkv - VLC", "vlc"), AppWindow(2, "Inbox", "outlook"),
                   AppWindow(3, "Other.mkv - VLC", "vlc")]
        self.assertEqual(app_windows.find("Movie.mkv - VLC", "vlc", windows).hwnd, 1)
        self.assertEqual(app_windows.find("Mail", "outlook", windows).hwnd, 2)  # its only window, new title
        self.assertIsNone(app_windows.find("Gone", "notepad", windows))

    def test_the_list_is_real_windows_on_this_pc(self):
        for window in app_windows.list_windows():
            self.assertTrue(window.title)
            self.assertNotEqual(window.title, "Program Manager")


class WindowModeTest(unittest.TestCase):
    def setUp(self):
        patches = [mock.patch.object(lens_ui.hotkeys.GlobalHotkeys, "enable_keys", return_value=[]),
                   mock.patch("echosub_lens.lens_menu.lens_ocr.available_languages", return_value=["en-US"]),
                   mock.patch.object(lens_ui.LensScanner, "start", lambda scanner: scanner._timer.start())]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.lens = lens_ui.LensApp(app, dict(lens_app.DEFAULTS), lambda cfg: None,
                                    base_cfg={"target_lang": "ar", "translator": "google"})
        self.lens.activity.tell = lambda text: None
        self.addCleanup(self._close)

    def _close(self):
        self.lens.stop()
        self.lens.tray.hide()
        self.lens.overlay.close()

    def test_only_the_chosen_window_is_translated_and_followed(self):
        screen = app.primaryScreen()
        where = {"spot": (screen, QRect(100, 100, 640, 360))}
        window = AppWindow(42, "Movie.mkv - VLC", "vlc")
        with mock.patch.object(app_windows, "alive", return_value=True), \
                mock.patch.object(app_windows, "spot", side_effect=lambda hwnd: where["spot"]), \
                mock.patch.object(app_windows, "hidden_behind", return_value=False):
            self.lens.use_window(window)
            self.assertEqual(self.lens.cfg["mode"], "window")
            self.assertEqual(self.lens.cfg["window_title"], "Movie.mkv - VLC")
            self.assertEqual(self.lens.scanner.area[1].getRect(), (100, 100, 640, 360))
            self.assertIn("Movie.mkv", self.lens._where())
            self.lens._follow_window()
            self.assertFalse(self.lens.scanner.paused())
            where["spot"] = (screen, QRect(300, 120, 640, 360))  # the window moved
            self.lens._follow_window()
            self.assertEqual(self.lens.scanner.area[1].getRect(), (300, 120, 640, 360))
            where["spot"] = None  # minimized
            self.lens._follow_window()
            self.assertTrue(self.lens.scanner.paused())
            self.assertFalse(self.lens.overlay.isVisible())

    def test_the_menu_lists_the_open_windows(self):
        with mock.patch.object(app_windows, "list_windows", return_value=[AppWindow(7, "Game", "game")]):
            self.lens._fill_windows()
        labels = [a.text() for a in self.lens.windows_menu.actions()]
        self.assertEqual(labels, ["Game  (game)"])

    def test_every_settings_page_can_be_reset(self):
        lens = self.lens
        lens.open_window("translate")
        window = lens.main_window
        lens._set_capture_visible(False)
        lens.set_select_mode(True)
        lens._set_peek(10)
        window.reset_page("translate", ask=False)
        self.assertEqual((lens.cfg["capture_visible"], lens.cfg["select_mode"], lens.cfg["peek_seconds"]),
                         (True, False, 1))
        self.assertEqual(lens.cfg["mode"], "screen")
        lens._set_and_restart(target_lang="fr", ocr_language="en-US")
        window.reset_page("languages", ask=False)
        self.assertEqual((lens.cfg["target_lang"], lens.cfg["ocr_language"]), (None, "auto"))
        with mock.patch.object(lens, "set_translator") as chosen:
            window.reset_page("translator", ask=False)
        chosen.assert_called_once_with("same")
        lens.apply_style(dict(lens.overlay.style, bold=True, direction="ltr"))
        window.reset_page("appearance", ask=False)
        self.assertEqual((lens.overlay.style["bold"], lens.overlay.style["direction"]), (False, "auto"))
        self.assertFalse(window.appearance.value()["bold"])
        from echosub_lens import lens_shortcuts

        lens.keys = dict(lens.keys, toggle="Ctrl+Alt+Y")
        window.reset_page("shortcuts", ask=False)
        self.assertEqual(lens.keys, lens_shortcuts.defaults())
        # every page but About has the button
        self.assertEqual(len([b for b in window.findChildren(type(window.reset_keys))
                              if b.text() == "Reset this page"]), 5)
        window.hide()


class TextWindowLookTest(unittest.TestCase):
    def test_transparent_with_a_level_and_a_direction_remembered(self):
        from echosub_lens.lens_text_window import TextWindow

        window = TextWindow({"transparent": True, "opacity": 25, "direction": "ltr"})
        self.assertTrue(window._glass)
        self.assertEqual(window.opacity_label.text(), "Background 25 %")
        block = Block([Line([("Hello", Box(0, 0, 50, 20))])], translation="مرحبا")
        window.show_blocks([block], "ar")
        self.assertFalse(window.right_to_left())  # left to right, though Arabic
        window.set_direction("auto")
        self.assertTrue(window.right_to_left())
        window.transparent.setChecked(False)
        self.assertFalse(window._glass)
        settings = window.settings()
        self.assertEqual((settings["transparent"], settings["opacity"], settings["direction"]), (False, 25, "auto"))
        window.close()

    def test_the_translations_on_the_screen_can_change_direction(self):
        from echosub_lens.lens_overlay import LensOverlay
        from echosub_lens.lens_style import merged

        overlay = LensOverlay(merged({"direction": "ltr"}))
        overlay.cover(QRect(0, 0, 400, 200), 1.0)
        block = Block([Line([("Hello", Box(10, 10, 60, 20))])], translation="مرحبا")
        overlay.show_blocks([block], "ar")
        self.assertEqual(overlay.labels[0].layoutDirection().name, "LeftToRight")
        overlay.set_style(merged({"direction": "auto"}))
        self.assertEqual(overlay.labels[0].layoutDirection().name, "RightToLeft")
        overlay.close()


if __name__ == "__main__":
    unittest.main()

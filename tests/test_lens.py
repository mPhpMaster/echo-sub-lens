# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: grouping read text into blocks, translating it in bulk, and laying it over the screen."""
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ECHOSUB_LENS_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_data"))

import numpy as np  # noqa: E402
from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from echosub_lens import hotkeys, lens_app, lens_ocr, lens_scanner, lens_translate, translate  # noqa: E402
from echosub_lens.lens_ocr import Block, Box, Line  # noqa: E402
from echosub_lens.lens_overlay import LensOverlay, fit_font_px, fit_line_px  # noqa: E402

app = QApplication.instance() or QApplication([])


def line(text, x, y, h=16, word_w=None, gap=6):
    words = []
    for word in text.split():
        w = word_w or len(word) * h * 0.55
        words.append((word, Box(x, y, w, h)))
        x += w + gap
    return Line(words)


class GroupingTest(unittest.TestCase):
    def test_lines_of_a_paragraph_are_one_block(self):
        blocks = lens_ocr.group_blocks([line("Your account is safe", 20, 100), line("with two steps.", 20, 120)])
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].text, "Your account is safe with two steps.")

    def test_a_heading_stays_apart_from_the_text_under_it(self):
        blocks = lens_ocr.group_blocks([line("Settings", 20, 60, h=28), line("Change your password", 20, 100)])
        self.assertEqual([b.text for b in blocks], ["Settings", "Change your password"])

    def test_far_apart_lines_are_separate(self):
        blocks = lens_ocr.group_blocks([line("First", 20, 100), line("Second", 20, 200)])
        self.assertEqual(len(blocks), 2)

    def test_columns_on_one_line_are_cut_apart(self):
        wide = Line(line("Download data", 20, 150).words + line("Delete account", 300, 150).words)
        blocks = lens_ocr.group_blocks([wide])
        self.assertEqual([b.text for b in blocks], ["Download data", "Delete account"])

    def test_right_to_left_lines_are_cut_too(self):
        words = [("مرحبا", Box(500, 10, 50, 16)), ("بكم", Box(440, 10, 40, 16)), ("حذف", Box(100, 10, 40, 16))]
        self.assertEqual([p.text for p in lens_ocr.split_columns(Line(words))], ["مرحبا بكم", "حذف"])

    def test_numbers_and_symbols_are_not_translated(self):
        self.assertFalse(lens_ocr.worth_translating("19.99 $"))
        self.assertFalse(lens_ocr.worth_translating("12:45"))
        self.assertTrue(lens_ocr.worth_translating("Price"))

    def test_box_union(self):
        box = Box(10, 10, 5, 5).united(Box(0, 12, 4, 10))
        self.assertEqual((box.x, box.y, box.right, box.bottom), (0, 10, 15, 22))


class FakeGoogle(translate.GoogleTranslator):
    def __init__(self, merge=False):  # no deep_translator, no network
        self.calls = []
        self.merge = merge

    def translate(self, text, src, tgt):
        self.calls.append(text)
        lines = [f"<{t}>" for t in text.split("\n")]
        return " ".join(lines) if self.merge and len(lines) > 1 else "\n".join(lines)


class FakeOffline:
    def __init__(self):
        self.calls = []

    def translate(self, text, src, tgt):
        self.calls.append(text)
        return text.upper()


class TranslatorTest(unittest.TestCase):
    def test_google_gets_one_request_for_many_lines(self):
        backend = FakeGoogle()
        t = lens_translate.LensTranslator("google", factory=lambda key, device: backend)
        self.assertEqual(t.translate_many(["a", "b", "a"], "ar"), ["<a>", "<b>", "<a>"])
        self.assertEqual(backend.calls, ["a\nb"])

    def test_when_google_merges_lines_each_is_asked_alone(self):
        backend = FakeGoogle(merge=True)
        t = lens_translate.LensTranslator("google", factory=lambda key, device: backend)
        self.assertEqual(t.translate_many(["a", "b"], "ar"), ["<a>", "<b>"])
        self.assertEqual(backend.calls, ["a\nb", "a", "b"])

    def test_progress_is_reported_for_new_text_only(self):
        class Batches(FakeOffline):
            def translate_many(self, texts, src, tgt):
                return [t.upper() for t in texts]

        t = lens_translate.LensTranslator("x", factory=lambda k, d: Batches())
        t.translate_many(["a"], "ar")
        steps = []
        t.translate_many(["a"] + [f"t{i}" for i in range(20)], "ar", progress=lambda d, n: steps.append((d, n)))
        self.assertEqual(steps, [(0, 20), (16, 20), (20, 20)])

    def test_text_already_translated_is_not_asked_again(self):
        backend = FakeOffline()
        t = lens_translate.LensTranslator("nllb-600m", factory=lambda key, device: backend)
        t.translate_many(["hello", "world"], "ar")
        self.assertEqual(t.translate_many(["world", "new"], "ar"), ["WORLD", "NEW"])
        self.assertEqual(backend.calls, ["hello", "world", "new"])
        t.translate_many(["hello"], "fr")  # another language is another translation
        self.assertEqual(backend.calls[-1], "hello")

    def test_the_backend_loads_only_when_needed_and_vulkan_means_cuda(self):
        created = []
        t = lens_translate.LensTranslator("nllb-600m", "vulkan",
                                          factory=lambda key, device: created.append(device) or FakeOffline())
        self.assertEqual(created, [])
        t.translate_many(["x"], "ar")
        self.assertEqual(created, ["cuda"])

    def test_google_chunks_stay_under_the_limit(self):
        chunks = list(lens_translate._chunks(["x" * 1500] * 5, 4000))
        self.assertEqual([len(c) for c in chunks], [2, 2, 1])

    def test_same_as_echosub_follows_its_translator_but_never_none(self):
        self.assertEqual(lens_translate.resolve("same", {"translator": "nllb-1.3b"}), "nllb-1.3b")
        self.assertEqual(lens_translate.resolve("same", {"translator": "none"}), "google")
        self.assertEqual(lens_translate.resolve("google", {"translator": "nllb-600m"}), "google")

    def test_text_already_in_the_target_alphabet_is_left_alone(self):
        self.assertTrue(lens_translate.already_in("الإعدادات والخصوصية", "ar"))
        self.assertFalse(lens_translate.already_in("Settings and privacy", "ar"))
        self.assertFalse(lens_translate.already_in("Settings and privacy", "fr"))  # Latin: cannot tell


class ScannerTest(unittest.TestCase):
    def test_small_changes_do_not_count(self):
        a = np.zeros((108, 192), np.int16)
        b = a.copy()
        b[0, 0] = 255  # a blinking caret
        self.assertFalse(lens_scanner.changed(a, b))
        b[:20, :] = 255  # a page scrolled
        self.assertTrue(lens_scanner.changed(a, b))
        self.assertTrue(lens_scanner.changed(a, None))

    def test_colours_follow_the_background(self):
        pixels = np.full((40, 100, 3), 20, np.uint8)
        pixels[10:14, 10:50] = 230  # light text on a dark background
        background, foreground = lens_scanner.colors_of(pixels, Box(5, 5, 60, 20))
        self.assertEqual(background, (20, 20, 20))
        self.assertEqual(foreground, (255, 255, 255))
        background, foreground = lens_scanner.colors_of(np.full((10, 10, 3), 250, np.uint8), Box(0, 0, 9, 9))
        self.assertEqual(foreground, (0, 0, 0))

    def test_process_skips_numbers_and_text_already_in_the_language(self):
        class Reader:
            def read(self, image):
                return [line("Settings", 10, 10), line("19.99", 10, 60), line("مرحبا بكم جميعا", 10, 110)]

        from PySide6.QtGui import QImage
        image = QImage(200, 160, QImage.Format_RGB32)
        image.fill(0xFFFFFF)
        t = lens_translate.LensTranslator("x", factory=lambda key, device: FakeOffline())
        blocks = lens_scanner.LensScanner.process(Reader(), t, image, "ar")
        self.assertEqual([(b.text, b.translation) for b in blocks], [("Settings", "SETTINGS")])
        self.assertEqual(blocks[0].background, (255, 255, 255))


class OverlayTest(unittest.TestCase):
    def test_fitting_shrinks_until_the_text_fits(self):
        self.assertEqual(fit_font_px("short", 400, 40, 20), 20)
        self.assertLess(fit_font_px("a much longer sentence " * 6, 120, 30, 20), 20)
        self.assertEqual(fit_font_px("x " * 500, 10, 10, 20), 9)  # never below the smallest size
        self.assertLess(fit_line_px("a long button caption", 60, 40, 20), 20)

    def test_blocks_become_selectable_labels_over_their_text(self):
        overlay = LensOverlay()
        overlay.cover(QRect(0, 0, 600, 300), 1.0)
        block = Block([line("Delete account", 100, 50)], translation="حذف الحساب")
        overlay.show_blocks([block, Block([line("Nothing", 10, 10)], translation="")], "ar")
        self.assertEqual(len(overlay.labels), 1)
        label = overlay.labels[0]
        self.assertEqual(label.text(), "حذف الحساب")
        self.assertEqual(label.toolTip(), "Delete account")  # the original, on hover
        self.assertTrue(label.textInteractionFlags())
        self.assertLessEqual(abs(label.x() - 100), 5)
        self.assertEqual(overlay.all_text(), "حذف الحساب")
        overlay.copy_all()
        self.assertEqual(QApplication.clipboard().text(), "حذف الحساب")
        overlay.clear()
        self.assertEqual(overlay.labels, [])
        overlay.close()

    def test_boxes_are_scaled_back_to_screen_pixels(self):
        overlay = LensOverlay()
        overlay.cover(QRect(0, 0, 800, 400), 2.0)  # a screen at 200 %: photo pixels are twice as many
        overlay.show_blocks([Block([line("Hello there", 400, 200, h=32)], translation="Bonjour")], "fr")
        self.assertLessEqual(abs(overlay.labels[0].x() - 200), 6)
        self.assertLessEqual(abs(overlay.labels[0].y() - 100), 6)
        overlay.close()


class PickerTest(unittest.TestCase):
    def test_dragging_chooses_an_area_and_esc_cancels(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        from echosub_lens.lens_picker import RegionPicker

        results = []
        picker = RegionPicker(results.append)
        picker.start()
        shade = picker.shades[0]
        QTest.mousePress(shade, Qt.LeftButton, Qt.NoModifier, QPoint(10, 20))
        QTest.mouseMove(shade, QPoint(210, 120))
        QTest.mouseRelease(shade, Qt.LeftButton, Qt.NoModifier, QPoint(210, 120))
        self.assertEqual(picker.shades, [])
        screen, rect = results[0]
        self.assertEqual((rect.x(), rect.y(), rect.width(), rect.height()), (10, 20, 201, 101))
        picker.start()
        QTest.keyClick(picker.shades[0], Qt.Key_Escape)
        self.assertIsNone(results[1])


class CaptureTest(unittest.TestCase):
    """Translations show in screenshots, so Lens must never read its own words back."""

    def make(self, frames, covered):
        scanner = lens_scanner.LensScanner(10_000)
        scanner.area = (app.primaryScreen(), QRect(0, 0, 192, 108))
        scanner.translator = object()
        scanner.covered = lambda: covered
        scanner.peek_every = 0
        scanner.capturer = types.SimpleNamespace(  # a late timer finds nothing
            grab=lambda screen, rect, clean=False: frames.pop(0) if frames else QImage())
        scanner._timer.start()  # running, without photographing anything by itself
        scanner.submitted = []
        scanner._jobs.put = lambda job: scanner.submitted.append(job[1])  # the photo
        return scanner

    @staticmethod
    def frame(value, box=None, box_value=0):
        from PySide6.QtGui import QColor, QImage, QPainter

        image = QImage(192, 108, QImage.Format_Grayscale8)
        image.fill(QColor(value, value, value))
        if box is not None:
            p = QPainter(image)
            p.fillRect(box, QColor(box_value, box_value, box_value))
            p.end()
        return image

    def test_masked_comparison_ignores_the_translations(self):
        a = np.zeros((108, 192), np.int16)
        b = a.copy()
        b[10:40, 10:100] = 200  # a translation drawn there
        hidden = lens_scanner.thumb_mask([QRect(10, 10, 90, 30)], 192, 108)
        self.assertTrue(lens_scanner.changed(a, b))
        self.assertFalse(lens_scanner.changed(a, b, hidden))
        b[80:100, :] = 200  # the page itself moved
        self.assertTrue(lens_scanner.changed(a, b, hidden))
        self.assertIsNone(lens_scanner.thumb_mask([], 192, 108))

    def test_a_photo_showing_translations_is_never_read(self):
        box = QRect(10, 10, 90, 30)
        clean = self.frame(0)
        scanner = self.make([self.frame(255), self.frame(255), self.frame(255, box), self.frame(0, box, 255),
                             self.frame(0, box, 255), clean], [])
        scanner.tick()  # the first photo: wait one more to be sure the screen is still
        scanner.tick()
        self.assertEqual(len(scanner.submitted), 1)  # the clean page
        scanner._busy = False
        scanner._showing = True
        scanner.covered = lambda: [box]  # translations on the screen now
        scanner.tick()  # same page, translation drawn: nothing to do
        self.assertEqual(len(scanner.submitted), 1)
        stale, looks = [], []
        scanner.stale.connect(lambda: stale.append(1))
        scanner.peeking = looks.append
        scanner.tick()  # the page changed around the translation...
        scanner.tick()  # ... and is still
        self.assertEqual(stale, [])  # the translations stay on the screen (no blinking over a video)...
        self.assertEqual(looks, [True, False])  # ... left out of one photo for a moment
        self.assertEqual(len(scanner.submitted), 2)
        self.assertIs(scanner.submitted[1], clean)  # and that photo is read, not the one with translations

    def test_a_video_behind_the_text_is_read_often(self):
        frames = [self.frame(v) for v in (10, 60, 110, 160, 210)]
        scanner = self.make(frames, [])
        scanner.tick()
        self.assertEqual(scanner.submitted, [])  # moving: wait a moment for it to settle...
        scanner._changing_since -= lens_scanner.MAX_WAIT
        scanner.tick()
        self.assertEqual(len(scanner.submitted), 1)  # ... but not long
        self.assertLessEqual(lens_scanner.MAX_WAIT, 1.0)

    def test_looking_under_the_translations_finds_a_change(self):
        box = QRect(0, 0, 192, 108)  # the translation covers everything
        scanner = self.make([self.frame(255), self.frame(255), self.frame(255, box, 0), self.frame(128)], [])
        scanner.tick()
        scanner.tick()
        scanner._busy, scanner._showing = False, True
        scanner.covered = lambda: [box]
        scanner.peek_every = 1
        scanner._last_clean = 0.0
        looks = []
        scanner.peeking = looks.append
        stale = []
        scanner.stale.connect(lambda: stale.append(1))
        scanner.tick()
        self.assertEqual(looks, [True, False])
        self.assertEqual(stale, [])  # the translations stay on the screen...
        self.assertEqual(len(scanner.submitted), 2)  # ... while the clean photo is read again

    def test_when_only_the_translations_changed_the_photo_nothing_is_read(self):
        box = QRect(10, 10, 90, 30)
        drawn = QRect(5, 5, 110, 45)  # a little wider than Lens thinks
        scanner = self.make([self.frame(255), self.frame(255), self.frame(255, drawn), self.frame(255, drawn),
                             self.frame(255), self.frame(255, drawn)], [])
        scanner.tick()
        scanner.tick()
        scanner._busy, scanner._showing = False, True
        scanner.covered = lambda: [box]
        looks = []
        scanner.peeking = looks.append
        scanner.tick()
        scanner.tick()  # looks changed: a look underneath finds the same page
        self.assertEqual(looks, [True, False])
        self.assertEqual(len(scanner.submitted), 1)
        scanner.tick()  # the same photo again: no need to look again
        self.assertEqual(looks, [True, False])
        self.assertEqual(len(scanner.submitted), 1)

    def test_nothing_changes_while_somebody_copies(self):
        scanner = self.make([], [])
        scanner.paused = lambda: True
        scanner.tick()  # would fail on the empty list of frames if it photographed
        self.assertEqual(scanner.submitted, [])


class StyleTest(unittest.TestCase):
    def test_colours_follow_the_page_unless_chosen(self):
        from echosub_lens import lens_style

        style = lens_style.merged(None)
        self.assertEqual(lens_style.colors(style, (20, 20, 20), (255, 255, 255)), ((20, 20, 20), (255, 255, 255)))
        style = lens_style.merged({"background": "#FFFF00", "nonsense": 1})
        self.assertNotIn("nonsense", style)
        self.assertEqual(lens_style.colors(style, (20, 20, 20), (255, 255, 255)), ((255, 255, 0), (0, 0, 0)))
        style["text_color"] = "#FF0000"
        self.assertEqual(lens_style.colors(style, (20, 20, 20), (255, 255, 255))[1], (255, 0, 0))

    def test_a_new_style_redraws_what_is_shown(self):
        from echosub_lens import lens_style

        overlay = LensOverlay()
        overlay.cover(QRect(0, 0, 600, 300), 1.0)
        block = Block([line("Delete account", 100, 50)], translation="حذف الحساب")
        overlay.show_blocks([block], "ar")
        small = overlay.labels[0].font().pixelSize()
        overlay.set_style(dict(lens_style.DEFAULT_STYLE, size_mode="fixed", font_px=40, bold=True,
                               background="#112233", radius=6))
        label = overlay.labels[0]
        self.assertEqual(label.font().pixelSize(), 40)
        self.assertGreater(40, small)
        self.assertTrue(label.font().bold())
        self.assertIn("rgba(17, 34, 51", label.styleSheet())
        self.assertIn("border-radius: 6px", label.styleSheet())
        overlay.close()

    def test_the_same_translations_are_not_drawn_again(self):
        overlay = LensOverlay()
        overlay.cover(QRect(0, 0, 600, 300), 1.0)
        overlay.show_blocks([Block([line("Delete account", 100, 50)], translation="x y")], "fr")
        first = overlay.labels[0]
        overlay.show_blocks([Block([line("Delete account", 101, 50)], translation="x y")], "fr")
        self.assertIs(overlay.labels[0], first)  # nothing flickers
        overlay.show_blocks([Block([line("Delete account", 100, 50)], translation="z")], "fr")
        self.assertIsNot(overlay.labels[0], first)
        overlay.close()

    def test_covered_is_empty_when_hidden_from_screenshots(self):
        overlay = LensOverlay()
        overlay.cover(QRect(0, 0, 600, 300), 1.0)
        overlay.show_blocks([Block([line("Delete account", 100, 50)], translation="x y")], "fr")
        self.assertEqual(len(overlay.covered()), 1)
        overlay.set_capture_hidden(True)
        self.assertEqual(overlay.covered(), [])
        overlay.set_capture_hidden(False)
        overlay.set_peeking(True)  # invisible for a moment: the photo is clean
        self.assertEqual(overlay.covered(), [])
        overlay.set_peeking(False)
        overlay.close()

    def test_the_appearance_page_reports_every_change(self):
        from echosub_lens import lens_style

        applied = []
        page = lens_style.AppearancePage(lens_style.DEFAULT_STYLE)
        page.changed.connect(applied.append)
        page.bold.setChecked(True)
        self.assertTrue(applied[-1]["bold"])
        page.size_mode.setCurrentIndex(1)
        self.assertEqual(applied[-1]["size_mode"], "fixed")
        self.assertFalse(page.scale.isEnabled())
        page.load(lens_style.DEFAULT_STYLE)
        without_font = {k: v for k, v in applied[-1].items() if k != "font_family"}  # tests run without Segoe UI
        self.assertEqual(without_font, {k: v for k, v in lens_style.DEFAULT_STYLE.items() if k != "font_family"})


class AppTest(unittest.TestCase):
    def test_lens_default_keys_do_not_clash_with_echosubs(self):
        from echosub_lens import lens_shortcuts

        self.assertEqual(lens_shortcuts.problems(lens_shortcuts.defaults()), {})
        keys = [(mods, vk) for _, mods, vk in hotkeys.HOTKEYS.values()]
        self.assertEqual(len(keys), len(set(keys)))

    def test_launch_command(self):
        self.assertEqual(lens_app.launch_command(True, r"C:\EchoSubLens\EchoSubLens.exe"),
                         (r"C:\EchoSubLens\EchoSubLens.exe", []))
        program, args = lens_app.launch_command(False, r"C:\nowhere\python.exe")
        self.assertEqual(args, ["-m", "echosub_lens"])

    def test_settings_keep_only_known_keys(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(lens_app, "config_path", lambda: os.path.join(folder, "lens.json")):
            from echosub_lens import lens_style
            self.assertEqual(lens_app.load_config(), dict(lens_app.DEFAULTS, style=lens_style.DEFAULT_STYLE))
            with open(lens_app.config_path(), "w", encoding="utf-8") as f:
                json.dump({"mode": "area", "area": [1, 2, 300, 200], "junk": 1}, f)
            cfg = lens_app.load_config()
            self.assertEqual(cfg["area"], [1, 2, 300, 200])
            self.assertNotIn("junk", cfg)
            lens_app.save_config(cfg)
            self.assertEqual(lens_app.load_config(), cfg)

    def test_the_app_starts_with_its_own_menu_and_settings(self):
        from echosub_lens import lens_ui

        saved = []
        with mock.patch.object(lens_ui.hotkeys.GlobalHotkeys, "enable_keys", return_value=[]), \
                mock.patch("echosub_lens.lens_menu.lens_ocr.available_languages", return_value=["en-US", "ar-SA"]), \
                mock.patch.object(lens_ui.LensScanner, "start"):
            lens = lens_ui.LensApp(app, dict(lens_app.DEFAULTS), lambda cfg: saved.append(dict(cfg)),
                                   base_cfg={"target_lang": "ar", "translator": "google"})
            texts = [a.text() for a in lens.menu.actions()]
            self.assertTrue(any(t.startswith("Translating") for t in texts))
            self.assertTrue(any(t.startswith("Choose an area") for t in texts))
            self.assertEqual(lens.target, "ar")
            self.assertEqual(lens.translator().key, "google")
            screen, rect = lens.area()
            self.assertEqual(rect.size(), screen.geometry().size())  # the whole screen by default
            lens._area_chosen((screen, QRect(10, 20, 300, 200)))
            self.assertEqual(saved[-1]["area"], [10, 20, 300, 200])
            self.assertEqual(lens.area()[1], QRect(10, 20, 300, 200))
            lens._set_and_restart(target_lang="fr")
            self.assertEqual(lens.target, "fr")
            lens.apply_style(dict(lens.overlay.style, bold=True))
            self.assertTrue(saved[-1]["style"]["bold"])
            lens._set_capture_visible(False)
            self.assertTrue(lens.overlay.capture_hidden)
            self.assertFalse(saved[-1]["capture_visible"])
            lens._set_peek(10)
            self.assertEqual(lens.scanner.peek_every, 10)
            lens.stop()
            lens.tray.hide()
            lens.overlay.close()

    def test_loading_and_downloading_the_translator_is_shown(self):
        from echosub_lens import lens_ui

        with mock.patch.object(lens_ui.hotkeys.GlobalHotkeys, "enable_keys", return_value=[]), \
                mock.patch("echosub_lens.lens_menu.lens_ocr.available_languages", return_value=["en-US"]), \
                mock.patch.object(lens_ui.LensScanner, "start"):
            lens = lens_ui.LensApp(app, dict(lens_app.DEFAULTS), lambda cfg: None,
                                   base_cfg={"target_lang": "ar", "translator": "google"})
            said = []
            lens.activity.changed.connect(lambda source, text, percent: said.append((source, text, percent)))
            lens._download_event({"state": "progress", "title": "Translation model (NLLB-600M)",
                                  "done": 300 * 1024 * 1024, "total": 600 * 1024 * 1024})
            self.assertEqual(said[-1], ("translator", "Downloading the translation model (NLLB-600M)… 50% "
                                                      "(300.0 MB of 600.0 MB)", 50))
            with mock.patch.object(lens_ui.translate, "create", return_value="backend"):
                self.assertEqual(lens._load_translator("google", "cpu", True), "backend")
            self.assertEqual(said[-2], ("translator", "Loading the translator (Google Translate)…", -1))
            self.assertEqual(said[-1], ("translator", "", -1))
            lens.scanner.working.emit(True)
            self.assertEqual(lens.busy.text, "Reading and translating the screen…")
            lens.stop()
            self.assertEqual(lens.busy.text, "")
            lens.tray.hide()
            lens.overlay.close()


class TextWindowTest(unittest.TestCase):
    def test_text_is_in_reading_order_with_the_original_on_request(self):
        from echosub_lens import lens_text_window

        blocks = [Block([line("World", 200, 10)], translation="B"), Block([line("Below", 10, 60)], translation="C"),
                  Block([line("Hello", 10, 12)], translation="A")]
        self.assertEqual(lens_text_window.window_text(blocks), "A\nB\nC")
        self.assertEqual(lens_text_window.window_text(blocks[:1], with_original=True), "B\n    (World)")

    def test_the_window_shows_translations_and_can_take_them_off_the_screen(self):
        from echosub_lens import lens_ui

        saved = []
        with mock.patch.object(lens_ui.hotkeys.GlobalHotkeys, "enable_keys", return_value=[]), \
                mock.patch("echosub_lens.lens_menu.lens_ocr.available_languages", return_value=["en-US"]), \
                mock.patch.object(lens_ui.LensScanner, "start"):
            lens = lens_ui.LensApp(app, dict(lens_app.DEFAULTS), lambda cfg: saved.append(dict(cfg)),
                                   base_cfg={"target_lang": "ar", "translator": "google"})
            lens.overlay.cover(QRect(0, 0, 600, 300), 1.0)
            lens.act_text_window.toggle()
            window = lens.text_window
            self.assertTrue(window.isVisible())
            self.assertTrue(saved[-1]["text_window"])
            lens._show([Block([line("Delete account", 100, 50)], translation="حذف الحساب")])
            self.assertEqual(window.box.toPlainText(), "حذف الحساب")
            self.assertEqual(len(lens.overlay.labels), 1)
            window.only_here.setChecked(True)  # only in the window
            self.assertFalse(saved[-1]["overlay_visible"])
            self.assertEqual(lens.overlay.labels, [])
            lens._show([Block([line("Save", 100, 50)], translation="حفظ")])
            self.assertEqual(lens.overlay.labels, [])
            self.assertEqual(window.box.toPlainText(), "حفظ")
            window.close()  # closing it puts the translations back over the screen
            self.assertIsNone(lens.text_window)
            self.assertFalse(saved[-1]["text_window"])
            self.assertTrue(saved[-1]["overlay_visible"])
            self.assertFalse(lens.act_text_window.isChecked())
            lens.stop()
            lens.tray.hide()
            lens.overlay.close()


class BusyTest(unittest.TestCase):
    def test_the_newest_work_is_shown_and_quick_work_never_appears(self):
        from echosub_lens.lens_busy import BusyIndicator

        busy = BusyIndicator()
        busy.set("screen", "Reading…")
        self.assertFalse(busy.isVisible())  # only after a moment
        busy.set("translator", "Downloading…", 40)
        self.assertEqual((busy.text, busy.percent), ("Downloading…", 40))
        busy._appear()
        self.assertTrue(busy.isVisible())
        busy.set("translator", "")
        self.assertEqual(busy.text, "Reading…")
        busy.set("screen", "")
        self.assertFalse(busy.isVisible())
        busy.set("screen", "Reading…")
        busy.set("screen", "")
        self.assertFalse(busy._due.isActive())

    def test_the_splash_shows_its_message_and_closes(self):
        from echosub_lens.lens_busy import SplashScreen

        splash = SplashScreen()
        splash.start()
        self.assertTrue(splash.isVisible())
        splash.show_message("Loading…")
        self.assertEqual(splash.message, "Loading…")
        splash._shown_at = None  # seen long enough
        splash.finish()
        app.processEvents()
        QApplicationWait.until(lambda: not splash.isVisible())
        self.assertFalse(splash.isVisible())


class QApplicationWait:
    @staticmethod
    def until(condition, ms=2000):
        from PySide6.QtCore import QElapsedTimer

        timer = QElapsedTimer()
        timer.start()
        while not condition() and timer.elapsed() < ms:
            app.processEvents()


if __name__ == "__main__":
    unittest.main()

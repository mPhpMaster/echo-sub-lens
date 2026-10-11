# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: reading and translating faster, and only what is worth it."""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ECHOSUB_LENS_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_data"))

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from echosub_lens import lens_app, lens_ocr, lens_scanner, lens_translate, translate  # noqa: E402
from echosub_lens.lens_ocr import Block, Box, Line  # noqa: E402

app = QApplication.instance() or QApplication([])


def block(text, x=10, y=10):
    words, left = [], x
    for word in text.split():
        words.append((word, Box(left, y, len(word) * 9, 16)))
        left += len(word) * 9 + 6
    return Block([Line(words)])


class Echo:
    """An offline translator that counts what it was asked."""
    device = "cuda"

    def __init__(self):
        self.asked = []

    def translate_many(self, texts, src, tgt):
        self.asked.append(list(texts))
        return [f"<{t}>" for t in texts]


class SimilarTextTest(unittest.TestCase):
    def make(self):
        backend = Echo()
        return lens_translate.LensTranslator("x", factory=lambda k, d: backend), backend

    def test_a_letter_read_differently_keeps_the_translation(self):
        t, backend = self.make()
        t.translate_many(["The quick brown fox jumps over"], "ar")
        self.assertEqual(t.translate_many(["The quick brown fox jumps 0ver"], "ar"),
                         ["<The quick brown fox jumps over>"])
        self.assertEqual(len(backend.asked), 1)

    def test_other_numbers_and_other_words_are_translated_again(self):
        t, backend = self.make()
        t.translate_many(["Showing page 12 of 40", "Notifications enabled"], "ar")
        out = t.translate_many(["Showing page 13 of 40", "Notifications disabled"], "ar")
        self.assertEqual(out, ["<Showing page 13 of 40>", "<Notifications disabled>"])

    def test_short_text_must_match_exactly(self):
        t, _ = self.make()
        t.translate_many(["Save"], "ar")
        self.assertEqual(t.translate_many(["Same"], "ar"), ["<Same>"])

    def test_a_graphics_card_does_the_whole_screen_in_one_pass(self):
        t, backend = self.make()
        t.translate_many([f"line number {i}" for i in range(40)], "ar")
        self.assertEqual(len(backend.asked), 1)
        backend.device = "cpu"
        t.translate_many([f"other line {i}" for i in range(40)], "ar")
        self.assertEqual(len(backend.asked), 1 + 3)  # batches of 16 on the processor, to show progress


class GoogleTest(unittest.TestCase):
    def test_lines_google_merged_are_asked_again_in_halves_not_one_by_one(self):
        g = translate.GoogleTranslator()
        g.MIN_INTERVAL = 0.0
        asked = []

        def ask(text, key):
            asked.append(text)
            lines = text.split("\n")
            return " ".join(lines) if len(lines) == 4 else text.upper()

        g._ask = ask
        self.assertEqual(g._ask_lines(["a", "b", "c", "d"], "ar"), ["A", "B", "C", "D"])
        self.assertEqual(len(asked), 3)

    def test_chromes_service_answers_each_text_on_its_own_in_one_request(self):
        g = translate.GoogleTranslator()
        g.MIN_INTERVAL = 0.0
        reply = mock.Mock(status_code=200)
        reply.json.return_value = [["- إلى أين؟", "en"], ["- إلى البيت.", "en"]]
        g._session = mock.Mock()
        g._session.post.return_value = reply
        self.assertEqual(g.translate_many(["- Where to?", "- Home."], "auto", "ar"), ["- إلى أين؟", "- إلى البيت."])
        self.assertEqual(g._session.post.call_count, 1)
        self.assertEqual(g._session.post.call_args.args[0], translate.MANY_URL)
        self.assertEqual(g._session.post.call_args.kwargs["data"], [("q", "- Where to?"), ("q", "- Home.")])
        reply.json.return_value = ["مرحبا", "en"]  # one text: answered without the list around it
        self.assertEqual(g.translate("Hello", "auto", "ar"), "مرحبا")

    def test_when_one_service_refuses_another_is_asked_and_a_429_everywhere_pauses(self):
        g = translate.GoogleTranslator()
        g.MIN_INTERVAL = 0.0
        g.RETRY_DELAYS = ()
        refused = mock.Mock(status_code=429)
        json_reply = mock.Mock(status_code=200)
        json_reply.json.return_value = [[["مرحبا\n", "Hello\n"], ["عالم", "world"]], None, "en"]
        g._session = mock.Mock()
        g._session.post.side_effect = lambda url, **kw: refused if url == translate.MANY_URL else json_reply
        self.assertEqual(g.translate_many(["Hello", "world"], "auto", "ar"), ["مرحبا", "عالم"])
        g._session.post.side_effect = None
        g._session.post.return_value = refused
        with self.assertRaises(translate.RateLimited):
            g.translate("Hello", "auto", "ar")
        self.assertLessEqual(translate.GoogleTranslator.COOLDOWN[0], 5.0)  # a short pause, not 20 s

    def test_a_broken_json_service_falls_back_to_the_web_page(self):
        g = translate.GoogleTranslator()
        g.MIN_INTERVAL = 0.0
        g._session = mock.Mock()
        g._session.post.side_effect = ValueError("not JSON")
        page = mock.Mock()
        page.translate.return_value = "مرحبا"
        g._cls = lambda source, target: page
        self.assertEqual(g.translate("Hello", "auto", "ar"), "مرحبا")
        self.assertGreater(g._web_only_until, time.monotonic())  # for a while, not for good


def subtitle_line(text, y):
    return Line([(word, Box(100 + 60 * i, y, 55, 30)) for i, word in enumerate(text.split())])


class SubtitleTest(unittest.TestCase):
    def test_each_speaker_of_a_dialogue_is_a_text_of_its_own(self):
        blocks = lens_ocr.group_blocks([subtitle_line("- Where are you going?", 600),
                                        subtitle_line("- Home.", 634)])
        self.assertEqual([b.text for b in blocks], ["- Where are you going?", "- Home."])
        # one sentence over two lines is still one text
        blocks = lens_ocr.group_blocks([subtitle_line("but some of us here", 600),
                                        subtitle_line("to learn to learn.", 634)])
        self.assertEqual(len(blocks), 1)

    def test_a_dialogue_line_keeps_its_dash(self):
        rtl = lens_scanner.RLM  # puts the dash on the right of an Arabic line
        self.assertEqual(lens_scanner.keep_dash("- Home.", "إلى البيت."), rtl + "- إلى البيت.")
        self.assertEqual(lens_scanner.keep_dash("- Home.", "- إلى البيت."), rtl + "- إلى البيت.")
        self.assertEqual(lens_scanner.keep_dash("- Home.", "- Maison."), "- Maison.")
        self.assertEqual(lens_scanner.keep_dash("- Home.", "Maison."), "- Maison.")
        self.assertEqual(lens_scanner.keep_dash("Home.", "البيت."), "البيت.")

    def test_two_lines_stay_two_lines(self):
        from echosub_lens.lens_overlay import split_like

        self.assertEqual(split_like("لقد جعلتها متوافقة تماماً مع النظام الجديد يا رجل", 2).count("\n"), 1)
        self.assertEqual(split_like("مرحبا", 2), "مرحبا")
        self.assertEqual(split_like("سطر\nسطر", 2), "سطر\nسطر")


class GarbageTest(unittest.TestCase):
    def test_what_is_not_text_is_not_translated(self):
        for junk in ("| / — ~ ° ¦ :", "l I i | l", "a b c d e f", "@@ #$ %^ &*", "12:45", "x"):
            self.assertFalse(lens_ocr.worth_translating(junk), junk)
        for text in ("Open the file", "I am here", "حذف الحساب", "設定を開く", "Привет мир", "OK"):
            self.assertTrue(lens_ocr.worth_translating(text), text)


class TypingTest(unittest.TestCase):
    def test_text_still_being_written_out_waits(self):
        now = [0.0]
        watch = lens_scanner.TypingWatch(clock=lambda: now[0])
        self.assertEqual(watch.growing([block("Hello th")]), set())
        now[0] = 0.3
        self.assertEqual(watch.growing([block("Hello there, trav")]), {"Hello there, trav"})
        now[0] = 0.6
        self.assertEqual(watch.growing([block("Hello there, traveller.")]), {"Hello there, traveller."})
        now[0] = 0.9
        self.assertEqual(watch.growing([block("Hello there, traveller.")]), set())  # done: translate it
        now[0] = 5.0
        self.assertEqual(watch.growing([block("Hello there, traveller. Welcome")]), set())  # long after

    def test_a_growing_log_is_translated_after_a_while(self):
        now = [0.0]
        watch = lens_scanner.TypingWatch(clock=lambda: now[0])
        text = "Log line"
        results = []
        for _ in range(lens_scanner.TYPING_ROUNDS + 2):
            now[0] += 0.2
            text += " more"
            results.append(watch.growing([block(text)]))
        self.assertIn(set(), results[1:])

    def test_the_scanner_reads_again_when_text_was_still_growing(self):
        class Reader:
            def __init__(self):
                self.texts = ["Hello th", "Hello there, traveller."]

            def read(self, image):
                return [block(self.texts.pop(0)).lines[0]] if self.texts else []

        reader, watch = Reader(), lens_scanner.TypingWatch()
        image = QImage(400, 100, QImage.Format_RGB32)
        image.fill(QColor("white"))
        translator = lens_translate.LensTranslator("x", factory=lambda k, d: Echo())
        first = lens_scanner.LensScanner.process(reader, translator, image, "ar", typing=watch)
        self.assertNotIsInstance(first, lens_scanner.Unsettled)
        second = lens_scanner.LensScanner.process(reader, translator, image, "ar", typing=watch)
        self.assertIsInstance(second, lens_scanner.Unsettled)
        self.assertEqual(second, [])


class PictureTest(unittest.TestCase):
    def test_light_text_on_dark_becomes_dark_on_white(self):
        image = QImage(200, 60, QImage.Format_RGB32)
        image.fill(QColor(20, 20, 30))
        p = QPainter(image)
        p.fillRect(QRect(20, 20, 100, 10), QColor(230, 230, 230))
        p.end()
        out = lens_scanner.binarized(image)
        self.assertEqual(QColor(out.pixel(5, 5)).red(), 255)    # the background is white
        self.assertEqual(QColor(out.pixel(50, 25)).red(), 0)    # the "text" is black
        self.assertTrue(lens_scanner.has_contrast(image))
        blank = QImage(200, 60, QImage.Format_RGB32)
        blank.fill(QColor("white"))
        self.assertFalse(lens_scanner.has_contrast(blank))

    def test_text_under_lens_own_window_is_left_alone(self):
        self.assertTrue(lens_scanner.inside(Box(10, 10, 20, 10), [(0, 0, 50, 50)]))
        self.assertFalse(lens_scanner.inside(Box(60, 10, 20, 10), [(0, 0, 50, 50)]))


class PaceTest(unittest.TestCase):
    def test_photos_come_quickly_while_the_screen_settles(self):
        scanner = lens_scanner.LensScanner(400)
        scanner.area = (app.primaryScreen(), QRect(0, 0, 192, 108))
        scanner.translator = object()
        frames = []
        for value in (255, 0, 0):
            frame = QImage(192, 108, QImage.Format_Grayscale8)
            frame.fill(QColor(value, value, value))
            frames.append(frame)
        scanner._submitted = scanner._previous = lens_scanner.thumbnail(frames.pop(0))
        scanner.grab = lambda: frames.pop(0)
        scanner._jobs.put = lambda job: None
        scanner.tick()  # changed: look again soon
        self.assertEqual(scanner._timer.interval(), lens_scanner.FAST_MS)
        scanner.tick()  # still: read it, then back to the calm pace
        self.assertEqual(scanner._timer.interval(), 400)
        self.assertTrue(scanner._busy)

    def test_the_old_default_pace_becomes_the_new_one(self):
        path = os.path.join(tempfile.mkdtemp(), "lens.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"interval_ms": 700}, f)
        with mock.patch.object(lens_app, "config_path", return_value=path):
            self.assertEqual(lens_app.load_config()["interval_ms"], 400)


class EchoSubLibrariesTest(unittest.TestCase):
    def test_echosubs_installed_folder_is_searched_for_nvidias_libraries(self):
        from echosub_lens import cuda_libs

        with mock.patch.object(cuda_libs, "echosub_install_dirs", return_value=[r"D:\Apps\EchoSub\_internal"]):
            self.assertIn(r"D:\Apps\EchoSub\_internal", cuda_libs.search_roots())


if __name__ == "__main__":
    unittest.main()

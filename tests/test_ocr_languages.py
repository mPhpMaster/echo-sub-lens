# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: reading Arabic (and other alphabets) with the right recognizer, in the right word order."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from echosub_lens import lens_ocr  # noqa: E402
from echosub_lens.lens_ocr import Box, Line, best_lines, line_quality, logical_text, word_quality  # noqa: E402


def visual(text, y=0, rtl=False):
    """A line as Windows hands it over: words left to right on the screen."""
    words, left = [], 10
    for word in text.split():
        words.append((word, Box(left, y, 12 * len(word), 24)))
        left += 12 * len(word) + 8
    return Line(words, rtl)


class WordOrderTest(unittest.TestCase):
    def test_an_arabic_line_is_turned_around(self):
        self.assertEqual(logical_text("الإغلاق قبل الملف احفظ".split(), True), "احفظ الملف قبل الإغلاق")

    def test_english_in_an_arabic_line_keeps_its_own_order(self):
        words = "Windows 11 نظام تشغيل".split()  # on the screen: "تشغيل نظام" read right to left after it
        self.assertEqual(logical_text(words, True), "تشغيل نظام Windows 11")

    def test_a_colon_an_arabic_recognizer_put_in_front_goes_back(self):
        self.assertEqual(logical_text(["Original", "/", ":Translated"], True), "Original / Translated:")
        self.assertEqual(logical_text(["Original", "/", ":Translated"], False), "Original / :Translated")

    def test_mixed_lines_as_windows_arabic_recognizer_read_them(self):
        # the words Windows' ar-SA recognizer returned (left to right on the screen) for lines drawn with Qt,
        # first in a left-to-right layout, then in a right-to-left one: both read the same
        cases = {
            "Original / Translated: الصورة مع الترجمة": (
                ["Original", "/", ":Translated", "الترجمة", "مع", "الصورة"],
                ["الترجمة", "مع", "الصورة", "Original:", "/", "Translated"]),
            "Translate into و with: يعيد الترجمة": (
                ["Translate", "into", "و", ":with", "الترجمة", "يعيد"],
                ["الترجمة", "يعيد", "with:", "و", "Translate", "into"]),
            "Save as: احفظ باسم": (["Save", ":as", "باسم", "احفظ"], ["باسم", "احفظ", "Save:", "as"]),
            "File name: اسم الملف": (["File", ":name", "الملف", "اسم"], ["الملف", "اسم", "File:", "name"]),
            "الإعدادات: Settings": (["Settings", "الإعدادات:"],),
            "الإعدادات العامة: General Settings": (["General", "Settings", "العامة:", "الإعدادات"],),
            "اللغة: English": (["English", "اللغة:"],),
            "افتح الملف في Notepad الآن.": (["الآن.", "Notepad", "في", "الملف", "افتح"],),
            "Note: see the docs في المجلد": ([":Note", "see", "the", "docs", "المجلد", "في"],),
            "هل حفظت الملف؟ نعم، حفظته.": (["حفظته.", "نعم،", "الملف؟", "حفظت", "هل"],),
        }
        for text, readings in cases.items():
            for words in readings:
                self.assertEqual(logical_text(words, True), text, words)

    def test_an_english_recognizer_sees_the_marks_where_they_are(self):
        # what Windows' en-US recognizer returned for "Original / Translated: …" in a right-to-left layout
        self.assertEqual(logical_text(["الترجمة", "مع", "الصورة", ":Original", "/", "Translated"]),
                         "Original / Translated: الصورة مع الترجمة")
        self.assertEqual(logical_text(["Original", "/", "Translated:", "الترجمة", "مع", "الصورة"]),
                         "Original / Translated: الصورة مع الترجمة")

    def test_english_read_by_an_arabic_recognizer(self):
        self.assertEqual(logical_text([",Hello", "world.", "Is", "it", "done?", "!Yes"], True),
                         "Hello, world. Is it done? Yes!")

    def test_english_alone_is_untouched(self):
        self.assertEqual(visual("Save the file before closing").text, "Save the file before closing")


class QualityTest(unittest.TestCase):
    def test_what_an_english_recognizer_makes_of_arabic_looks_wrong(self):
        for junk in ("öJPJl", "e..o", "TransIated", "0riginaI", "ua-iJl"):
            self.assertEqual(word_quality(junk), 0, junk)
        for good in ("Translated:", "احفظ", "Привет", "12:45", "don't", "(file)"):
            self.assertEqual(word_quality(good), 1, good)
        self.assertIsNone(word_quality("/"))

    def test_the_best_reading_of_each_line_is_kept(self):
        english = [visual("Original / Translated: e..o öJPJl", 0), visual("Save the file before closing", 60)]
        arabic = [visual("Original / :Translated الترجمة مع الصورة", 0, True),
                  visual("Save the file before closing", 60, True),
                  visual("الإغلاق قبل الملف احفظ", 120, True)]
        lines = best_lines([english, arabic])
        self.assertEqual([line.text for line in lines],
                         ["Original / Translated: الصورة مع الترجمة", "Save the file before closing",
                          "احفظ الملف قبل الإغلاق"])
        self.assertIs(lines[1], english[1])  # a tie: the main recognizer's reading

    def test_a_line_only_a_wrong_recognizer_saw_is_dropped(self):
        for junk in ("Ш; ьы 0riginaI", "Ш; ьы"):  # what a Russian recognizer made of an Arabic line
            self.assertEqual(best_lines([[], [visual(junk, 200)]]), [], junk)
        self.assertLess(line_quality(visual("Ш; ьы 0riginaI")), 0.8)
        self.assertEqual(len(best_lines([[], [visual("Привет, как дела?", 200)]])), 1)  # real Russian stays


def at(text, x, y, h=22):
    """A line as Windows reads it, its words left to right from `x`."""
    words, left = [], x
    for word in text.split():
        words.append((word, Box(left, y, 10 * len(word), h)))
        left += 10 * len(word) + 6
    return Line(words)


class ListTest(unittest.TestCase):
    """Windows reads a list's numbers as lines of their own; the list keeps its numbers and its items."""

    def picture(self):
        # As Windows read a real screenshot: the numbers apart, then the items, then a two-line paragraph
        return [at("1.", 78, 22, 16), at("2.", 78, 56, 16), at("3.", 78, 90, 16),
                at("Wait until app pushes 1.20.1.", 108, 21), at("Rebase my commit on top of it.", 110, 55),
                at("Publish it as 1.21.0, so it is higher.", 110, 89),
                at("I told both sessions this plan, and I will get a notice", 65, 148),
                at("publish 1.21.0, or do you want to check first?", 64, 176)]

    def test_each_number_goes_back_in_front_of_its_item(self):
        lines = lens_ocr.attach_markers(self.picture())
        self.assertEqual([line.text for line in lines][:3],
                         ["1. Wait until app pushes 1.20.1.", "2. Rebase my commit on top of it.",
                          "3. Publish it as 1.21.0, so it is higher."])

    def test_each_item_is_a_block_and_a_paragraph_stays_one(self):
        blocks = lens_ocr.group_blocks(self.picture())
        self.assertEqual([b.text for b in blocks],
                         ["1. Wait until app pushes 1.20.1.", "2. Rebase my commit on top of it.",
                          "3. Publish it as 1.21.0, so it is higher.",
                          "I told both sessions this plan, and I will get a notice publish 1.21.0, or do you want to "
                          "check first?"])

    def test_markers(self):
        for marker in ("1.", "12)", "(3)", "a.", "B)", "•", "-", "▪"):
            self.assertTrue(lens_ocr.list_marker(marker), marker)
        for text in ("1.5", "Hi", "--", "I"):
            self.assertFalse(lens_ocr.list_marker(text), text)
        self.assertTrue(lens_ocr.item_line("• Open the file"))
        self.assertFalse(lens_ocr.item_line("I told both sessions"))

    def test_a_number_alone_stays(self):
        lines = lens_ocr.attach_markers([at("1.", 10, 10, 16), at("Far away text", 600, 200)])
        self.assertEqual(sorted(line.text for line in lines), ["1.", "Far away text"])


class AlphabetTest(unittest.TestCase):
    def test_tags(self):
        self.assertEqual(lens_ocr.tag_script("ar-SA"), "arabic")
        self.assertEqual(lens_ocr.tag_script("en-US"), "latin")
        self.assertEqual(lens_ocr.tag_script("ru"), "cyrillic")
        self.assertEqual(lens_ocr.tag_script("sr-Latn-RS"), "latin")
        self.assertEqual(lens_ocr.tag_script("ar"), "arabic")  # a language code, for `skip`

    @unittest.skipUnless(sys.platform == "win32", "Windows OCR")
    def test_automatic_reads_with_every_alphabet_installed_but_the_one_translated_into(self):
        installed = lens_ocr.available_languages()
        if not any(tag.startswith("ar") for tag in installed) or lens_ocr.Reader().language.startswith("ar"):
            self.skipTest("needs Windows' Arabic OCR beside another display language")
        reader = lens_ocr.Reader()
        self.assertTrue(any(tag.startswith("ar") for tag in reader.languages))
        skipping = lens_ocr.Reader(skip=("ar",))
        self.assertFalse(any(tag.startswith("ar") for tag in skipping.languages))
        self.assertEqual(lens_ocr.Reader("en-US").languages, ["en-US"])  # a language chosen: that one only


if __name__ == "__main__":
    unittest.main()

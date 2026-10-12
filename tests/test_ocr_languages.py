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
                         ["الصورة مع الترجمة Original / Translated:", "Save the file before closing",
                          "احفظ الملف قبل الإغلاق"])
        self.assertIs(lines[1], english[1])  # a tie: the main recognizer's reading

    def test_a_line_only_a_wrong_recognizer_saw_is_dropped(self):
        for junk in ("Ш; ьы 0riginaI", "Ш; ьы"):  # what a Russian recognizer made of an Arabic line
            self.assertEqual(best_lines([[], [visual(junk, 200)]]), [], junk)
        self.assertLess(line_quality(visual("Ш; ьы 0riginaI")), 0.8)
        self.assertEqual(len(best_lines([[], [visual("Привет, как дела?", 200)]])), 1)  # real Russian stays


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

# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: a picture searched with Google Lens, sent by the user's own browser."""
import base64
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from echosub_lens import google_lens  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"picture" * 20


class GoogleLensTest(unittest.TestCase):
    def test_the_page_sends_the_picture_to_google_lens_by_itself(self):
        html = google_lens.page(PNG, now=1700000000)
        self.assertIn('action="https://lens.google.com/v3/upload?ep=gsbubb&hl=en&re=df&st=1700000000000"', html)
        self.assertIn('name="encoded_image"', html)  # the field Google Lens's own page sends
        self.assertIn('enctype="multipart/form-data"', html)
        self.assertIn(base64.b64encode(PNG).decode(), html)
        self.assertIn(".submit()", html)
        self.assertIn("lens.google.com", html.split("<noscript>")[1])  # without JavaScript: what to do by hand

    def test_search_picture_writes_the_page_and_opens_it_in_the_browser(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(google_lens.config, "DATA_DIR", folder):
            opened = []
            note = google_lens.search_picture(PNG, opener=opened.append)
            self.assertEqual(len(opened), 1)
            self.assertTrue(opened[0].startswith(folder))
            with open(opened[0], encoding="utf-8") as f:
                self.assertIn(base64.b64encode(PNG).decode(), f.read())
            self.assertIn("Google Lens", note)
            google_lens.search_picture(PNG + b"2", opener=opened.append)
            self.assertEqual(opened[0], opened[1])  # one page, written again: no pictures left behind

    def test_no_browser_is_an_error_in_words(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(google_lens.config, "DATA_DIR", folder), \
                mock.patch("PySide6.QtGui.QDesktopServices.openUrl", return_value=False):
            with self.assertRaises(OSError):
                google_lens.search_picture(PNG)


if __name__ == "__main__":
    unittest.main()

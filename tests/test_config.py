# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""What "Default" means: EchoSub's own choices when EchoSub is on this PC, else Windows' language and Google."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ECHOSUB_LENS_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_data"))

from echosub_lens import config  # noqa: E402


class DefaultsTest(unittest.TestCase):
    def test_without_echosub_windows_language_and_google(self):
        with mock.patch.object(config, "windows_language", return_value="fr"):
            base = config.load(os.path.join(tempfile.gettempdir(), "no-such-echosub", "settings.json"))
        self.assertEqual(base["target_lang"], "fr")
        self.assertEqual(base["translator"], "google")

    def test_with_echosub_its_choices(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "settings.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"target_lang": "ar", "translator": "nllb-600m", "device": "cpu", "font_size": 30}, f)
            base = config.load(path)
        self.assertEqual((base["target_lang"], base["translator"], base["device"]), ("ar", "nllb-600m", "cpu"))
        self.assertNotIn("font_size", base)  # only what Lens needs

    def test_windows_language_falls_back_to_english(self):
        from PySide6.QtCore import QLocale

        with mock.patch.object(QLocale, "system", return_value=QLocale("xx_XX")):
            self.assertEqual(config.windows_language(), "en")


if __name__ == "__main__":
    unittest.main()

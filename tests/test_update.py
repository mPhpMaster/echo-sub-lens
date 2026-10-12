# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: the update check — versions, GitHub's answer, the daily check and the About page."""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ECHOSUB_LENS_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_data"))

import requests  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from echosub_lens import __version__, lens_app, lens_ui, lens_update  # noqa: E402
from echosub_lens.lens_about import AboutPage  # noqa: E402

app = QApplication.instance() or QApplication([])
REAL_IN_BACKGROUND = lens_update.in_background

RELEASE = {
    "tag_name": "v1.9.0",
    "html_url": "https://github.com/mPhpMaster/echo-sub-lens/releases/tag/v1.9.0",
    "body": "What's new",
    "assets": [{"name": "EchoSubLens-Setup-1.9.0.exe.sha256", "browser_download_url": "https://x/sha"},
               {"name": "EchoSubLens-Setup-1.9.0.exe", "browser_download_url": "https://x/setup.exe"}],
}


class Response:
    def __init__(self, status_code, data=None, bad_json=False):
        self.status_code = status_code
        self.data = data
        self.bad_json = bad_json

    def json(self):
        if self.bad_json:
            raise ValueError("not JSON")
        return self.data


class Session:
    """Answers GET with `answer` (a Response, or an exception to raise) and remembers the call."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class VersionTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(lens_update.parse_version("v1.8.1"), (1, 8, 1))
        self.assertEqual(lens_update.parse_version("1.10"), (1, 10))
        self.assertEqual(lens_update.parse_version("V2.0.0-beta"), (2, 0, 0))
        self.assertEqual(lens_update.parse_version((1, 2)), (1, 2))
        for bad in (None, "", "latest", "vX"):
            self.assertIsNone(lens_update.parse_version(bad))

    def test_newer(self):
        self.assertTrue(lens_update.newer("1.9.0", "1.8.1"))
        self.assertTrue(lens_update.newer("v1.10.0", "1.9.9"))
        self.assertTrue(lens_update.newer("2", "1.99"))
        self.assertTrue(lens_update.newer("1.8.1.1", "1.8.1"))
        self.assertFalse(lens_update.newer("1.8.1", "1.8.1"))
        self.assertFalse(lens_update.newer("1.9", "1.9.0"))
        self.assertFalse(lens_update.newer("1.8.0", "1.8.1"))
        self.assertFalse(lens_update.newer("junk", "1.0"))
        self.assertFalse(lens_update.newer("1.0", None))

    def test_due(self):
        now = 1_000_000_000
        self.assertTrue(lens_update.due({"check_updates": True, "update_checked": 0}, now))
        self.assertFalse(lens_update.due({"check_updates": True, "update_checked": now - 3600}, now))
        self.assertTrue(lens_update.due({"check_updates": True, "update_checked": now - 25 * 3600}, now))
        self.assertTrue(lens_update.due({"check_updates": True, "update_checked": now + 3600}, now))  # clock back
        self.assertFalse(lens_update.due({"check_updates": False, "update_checked": 0}, now))


class LatestReleaseTest(unittest.TestCase):
    def test_ok_takes_the_installer(self):
        session = Session(Response(200, RELEASE))
        self.assertEqual(lens_update.latest_release(session), ("1.9.0", "https://x/setup.exe", "What's new"))
        url, kwargs = session.calls[0]
        self.assertEqual(url, lens_update.API_URL)
        self.assertEqual(kwargs["timeout"], 10)
        self.assertEqual(kwargs["headers"]["User-Agent"], f"EchoSub-Lens/{__version__}")
        self.assertNotIn("Authorization", kwargs["headers"])

    def test_without_an_installer_the_release_page(self):
        data = dict(RELEASE, assets=[], body=None)
        self.assertEqual(lens_update.latest_release(Session(Response(200, data))),
                         ("1.9.0", RELEASE["html_url"], ""))

    def test_failures_are_none(self):
        answers = (Response(404, {"message": "Not Found"}), Response(403, {}), requests.exceptions.Timeout("slow"),
                   requests.exceptions.ConnectionError("offline"), Response(200, bad_json=True),
                   Response(200, ["not", "a", "dict"]), Response(200, {"tag_name": "nightly"}),
                   Response(200, {"assets": "x"}), RuntimeError("anything"))
        for answer in answers:
            with self.subTest(answer=answer):
                self.assertIsNone(lens_update.latest_release(Session(answer)))

    def test_malformed_assets_do_not_raise(self):
        data = dict(RELEASE, assets=[None, {"name": None}, {"name": "EchoSubLens-Setup-1.9.0.exe"}])
        self.assertEqual(lens_update.latest_release(Session(Response(200, data)))[1], RELEASE["html_url"])


class LensTestBase(unittest.TestCase):
    latest = ("1.9.0", "https://x/setup.exe", "")

    def setUp(self):
        self.saved = []
        self.notes = []
        self.asked = 0
        patches = [mock.patch.object(lens_ui.hotkeys.GlobalHotkeys, "enable_keys", return_value=[]),
                   mock.patch("echosub_lens.lens_menu.lens_ocr.available_languages", return_value=["en-US"]),
                   mock.patch.object(lens_ui.LensScanner, "start", lambda scanner: None),
                   mock.patch.object(lens_update, "in_background", lambda work: work()),  # inline
                   mock.patch.object(lens_update, "__version__", "1.8.1"),
                   mock.patch("echosub_lens.lens_about.__version__", "1.8.1"),
                   mock.patch.object(lens_ui.lens_update, "latest_release", self._latest)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.version = mock.patch("echosub_lens.__version__", "1.8.1")
        self.version.start()
        self.addCleanup(self.version.stop)
        self.lens = lens_ui.LensApp(app, dict(lens_app.DEFAULTS), lambda cfg: self.saved.append(dict(cfg)),
                                    base_cfg={"target_lang": "ar", "translator": "google"})
        self.lens.activity.tell = self.notes.append
        self.addCleanup(self._close)

    def _latest(self):
        self.asked += 1
        return self.latest

    def _close(self):
        self.lens._update_timer.stop()
        if self.lens.main_window is not None:
            self.lens.main_window.hide()
        self.lens.stop()
        self.lens.tray.hide()
        self.lens.overlay.close()

    def menu_texts(self):
        return [a.text() for a in self.lens.menu.actions()]


class CheckFlowTest(LensTestBase):
    def test_no_automatic_check_in_tests(self):
        self.assertFalse(self.lens._update_timer.isActive())  # offscreen: never on its own, no network

    def test_newer_version_says_so_and_adds_the_menu_item(self):
        self.assertTrue(self.lens.check_for_updates())
        self.assertEqual(self.asked, 1)
        self.assertEqual(self.lens.new_release[0], "1.9.0")
        self.assertEqual(self.notes, ["EchoSub Lens 1.9.0 is out — open the About page to get it"])
        self.assertEqual(self.menu_texts()[0], "⬆ Update to 1.9.0…")
        self.assertGreater(self.lens.cfg["update_checked"], time.time() - 60)
        with mock.patch("PySide6.QtGui.QDesktopServices.openUrl") as opened:
            self.lens.menu.actions()[0].trigger()
        self.assertEqual(opened.call_args[0][0].toString(), lens_update.release_page("1.9.0"))

    def test_a_second_check_within_a_day_does_nothing(self):
        self.lens.check_for_updates()
        self.assertFalse(self.lens.check_for_updates())
        self.assertEqual(self.asked, 1)
        self.assertEqual(len(self.notes), 1)

    def test_checked_less_than_a_day_ago_across_restarts(self):
        self.lens.cfg["update_checked"] = int(time.time()) - 23 * 3600
        self.assertFalse(self.lens.check_for_updates())
        self.assertEqual(self.asked, 0)
        self.lens.cfg["update_checked"] = int(time.time()) - 25 * 3600
        self.assertTrue(self.lens.check_for_updates())
        self.assertEqual(self.asked, 1)

    def test_turned_off(self):
        self.lens.set_check_updates(False)
        self.assertFalse(self.saved[-1]["check_updates"])
        self.assertFalse(self.lens.check_for_updates())
        self.assertEqual(self.asked, 0)

    def test_same_or_older_or_skipped_or_no_answer_says_nothing(self):
        for latest, skipped in ((("1.8.1", "u", ""), ""), (("1.7.0", "u", ""), ""), (("1.9.0", "u", ""), "1.9.0"),
                                (None, "")):
            with self.subTest(latest=latest, skipped=skipped):
                self.latest = latest
                self.lens.cfg.update(update_checked=0, update_skipped=skipped)
                self.assertTrue(self.lens.check_for_updates())
                self.assertIsNone(self.lens.new_release)
                self.assertEqual(self.notes, [])
                self.assertTrue(self.menu_texts()[0].startswith("Open EchoSub Lens"))

    def test_skip_removes_the_item(self):
        self.lens.check_for_updates()
        self.lens.skip_update("1.9.0")
        self.assertEqual(self.lens.cfg["update_skipped"], "1.9.0")
        self.assertIsNone(self.lens.new_release)
        self.assertTrue(self.menu_texts()[0].startswith("Open EchoSub Lens"))

    def test_a_real_thread_answers_in_the_qt_thread(self):
        import threading

        seen = []
        with mock.patch.object(lens_update, "in_background", REAL_IN_BACKGROUND):
            self.lens.check_for_updates(done=lambda release: seen.append((release, threading.current_thread())))
            deadline = time.time() + 5
            while not seen and time.time() < deadline:
                app.processEvents()
                time.sleep(0.01)
        self.assertEqual(seen[0][0][0], "1.9.0")
        self.assertIs(seen[0][1], threading.main_thread())
        self.assertEqual(self.menu_texts()[0], "⬆ Update to 1.9.0…")

    def test_the_timer_checks_once_a_day(self):
        self.lens._update_tick()
        self.assertEqual(self.lens._update_timer.interval(), lens_update.TICK_MS)
        self.lens._update_tick()  # an hour later: not a day yet
        self.assertEqual(self.asked, 1)


class AboutPageTest(LensTestBase):
    def page(self):
        page = AboutPage(lens=self.lens)
        self.addCleanup(page.deleteLater)
        return page

    def test_a_newer_version(self):
        page = self.page()
        self.assertFalse(page.download_button.isVisibleTo(page))
        page.check_button.click()
        self.assertTrue(page.check_button.isEnabled())
        self.assertIn("1.9.0", page.update_status.text())
        self.assertIn("is out", page.update_status.text())
        self.assertTrue(page.download_button.isVisibleTo(page))
        self.assertTrue(page.skip_button.isVisibleTo(page))
        self.assertEqual(self.notes, [])  # the page says it, not a note
        with mock.patch("echosub_lens.lens_about.QDesktopServices.openUrl") as opened:
            page.download_button.click()
        self.assertEqual(opened.call_args[0][0].toString(), "https://x/setup.exe")
        page.skip_button.click()
        self.assertEqual(self.lens.cfg["update_skipped"], "1.9.0")
        self.assertFalse(page.download_button.isVisibleTo(page))
        self.assertIn("skipped", page.update_status.text())

    def test_latest_and_no_internet(self):
        page = self.page()
        self.latest = ("1.8.1", "u", "")
        page.check_button.click()
        self.assertEqual(page.update_status.text(), "You have the latest version (1.8.1)")
        self.assertFalse(page.download_button.isVisibleTo(page))
        self.latest = None
        page.check_button.click()  # the button asks even within a day, and even when automatic checks are off
        self.assertEqual(page.update_status.text(), "Could not check: no internet")
        self.assertEqual(self.asked, 2)

    def test_checking_does_not_block(self):
        page = self.page()
        with mock.patch.object(lens_update.threading, "Thread") as thread, \
                mock.patch.object(lens_update, "in_background", REAL_IN_BACKGROUND):
            page.check_button.click()
        self.assertTrue(thread.call_args.kwargs["daemon"])
        thread.return_value.start.assert_called_once()
        self.assertEqual(self.asked, 0)  # not asked in this thread
        self.assertFalse(page.check_button.isEnabled())
        self.assertEqual(page.update_status.text(), "Checking…")

    def test_the_automatic_box(self):
        page = self.page()
        self.assertTrue(page.auto_check_box.isChecked())
        page.auto_check_box.setChecked(False)
        self.assertFalse(self.lens.cfg["check_updates"])
        self.assertFalse(self.saved[-1]["check_updates"])

    def test_without_lens_no_update_controls(self):
        page = AboutPage()
        self.addCleanup(page.deleteLater)
        self.assertFalse(hasattr(page, "check_button"))


if __name__ == "__main__":
    unittest.main()

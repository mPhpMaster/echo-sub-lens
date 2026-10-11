# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: a network blip does not give up on Google's services for good, and the desktop or the taskbar
never hides a chosen window's translations."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ECHOSUB_LENS_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_data"))

import requests  # noqa: E402

from echosub_lens import app_windows, translate  # noqa: E402


class Clock:
    """time.monotonic and time.sleep that only move when told (sleeping moves them too)."""

    def __init__(self, now=1000.0):
        self.now = now

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.0)


def json_reply(*lines):
    reply = mock.Mock(status_code=200)
    reply.json.return_value = [[["\n".join(lines), "x"]], None, "en"]
    return reply


class GoogleBlipTest(unittest.TestCase):
    def make(self):
        g = translate.GoogleTranslator()
        g.MIN_INTERVAL = 0.0
        g.RETRY_DELAYS = ()
        g._session = mock.Mock()
        return g

    def test_a_connection_error_does_not_mark_chromes_service_broken(self):
        g = self.make()
        answers = {translate.MANY_URL: requests.exceptions.ConnectionError("blip"),
                   translate.JSON_URL: json_reply("مرحبا", "عالم")}

        def post(url, **kw):
            answer = answers[url]
            if isinstance(answer, Exception):
                raise answer
            return answer

        g._session.post.side_effect = post
        self.assertEqual(g.translate_many(["Hello", "world"], "auto", "ar"), ["مرحبا", "عالم"])  # asked elsewhere
        self.assertEqual(g._many_broken_until, 0.0)
        good = mock.Mock(status_code=200)
        good.json.return_value = [["أ", "en"], ["ب", "en"]]
        answers[translate.MANY_URL] = good
        self.assertEqual(g.translate_many(["A", "B"], "auto", "ar"), ["أ", "ب"])
        self.assertEqual(g._session.post.call_args.args[0], translate.MANY_URL)  # Chrome's service again

    def test_a_timeout_or_a_server_error_does_not_mark_the_json_service_broken(self):
        g = self.make()
        g._many_broken_until = float("inf")  # only the JSON service and the web page
        page = mock.Mock()
        page.translate.return_value = "مرحبا"
        g._cls = lambda source, target: page
        server_error = mock.Mock(status_code=503)
        server_error.raise_for_status.side_effect = requests.exceptions.HTTPError(response=server_error)
        for failure in (requests.exceptions.ReadTimeout("slow"), None):
            g._session.post.side_effect = failure
            g._session.post.return_value = server_error
            self.assertEqual(g.translate("Hello", "auto", "ar"), "مرحبا")  # the web page, for this request
            self.assertEqual(g._web_only_until, 0.0)

    def test_a_malformed_answer_marks_chromes_service_broken_only_for_a_while(self):
        g = self.make()
        clock = Clock()
        wrong = mock.Mock(status_code=200)
        wrong.json.return_value = [["only one", "en"]]  # two texts asked, one answered
        g._session.post.side_effect = lambda url, **kw: wrong if url == translate.MANY_URL else json_reply("أ", "ب")
        with mock.patch.object(translate.time, "monotonic", clock.monotonic), \
                mock.patch.object(translate.time, "sleep", clock.sleep):
            self.assertEqual(g.translate_many(["A", "B"], "auto", "ar"), ["أ", "ب"])
            self.assertEqual(g._many_broken_until, clock.now + g.BROKEN_FOR)
            g._session.post.reset_mock()
            g.translate_many(["C", "D"], "auto", "ar")
            self.assertNotIn(translate.MANY_URL, [c.args[0] for c in g._session.post.call_args_list])
            clock.now += g.BROKEN_FOR + 1
            g._session.post.reset_mock()
            g.translate_many(["E", "F"], "auto", "ar")
            self.assertEqual(g._session.post.call_args_list[0].args[0], translate.MANY_URL)  # tried again

    def test_an_http_4xx_marks_the_json_service_broken_for_a_while(self):
        g = self.make()
        g._many_broken_until = float("inf")
        page = mock.Mock()
        page.translate.return_value = "مرحبا"
        g._cls = lambda source, target: page
        bad = mock.Mock(status_code=400)
        bad.raise_for_status.side_effect = requests.exceptions.HTTPError(response=bad)
        g._session.post.return_value = bad
        self.assertEqual(g.translate("Hello", "auto", "ar"), "مرحبا")
        self.assertGreater(g._web_only_until, translate.time.monotonic())
        self.assertLessEqual(g._web_only_until, translate.time.monotonic() + g.BROKEN_FOR)

    def test_the_halves_asked_again_wait_their_turn(self):
        g = translate.GoogleTranslator()
        clock = Clock()
        sent = []

        def ask(text, key):
            sent.append(clock.now)
            lines = text.split("\n")
            return " ".join(lines) if len(lines) == 4 else text.upper()

        g._ask = ask
        g._many_broken_until = float("inf")  # straight to the lines joined in one text
        with mock.patch.object(translate.time, "monotonic", clock.monotonic), \
                mock.patch.object(translate.time, "sleep", clock.sleep):
            self.assertEqual(g._google(["a", "b", "c", "d"], "ar"), ["A", "B", "C", "D"])
        self.assertEqual(len(sent), 3)
        gaps = [b - a for a, b in zip(sent, sent[1:])]
        self.assertTrue(all(gap >= g.MIN_INTERVAL - 1e-9 for gap in gaps), gaps)

    def test_which_failures_are_the_networks(self):
        def http(status):
            return requests.exceptions.HTTPError(response=mock.Mock(status_code=status))

        for error in (requests.exceptions.ConnectionError(), requests.exceptions.ReadTimeout(),
                      requests.exceptions.ConnectTimeout(), http(500), http(503)):
            self.assertTrue(translate.transient(error), error)
        for error in (ValueError("not JSON"), http(400), http(404), KeyError(0), requests.exceptions.HTTPError()):
            self.assertFalse(translate.transient(error), error)


class CoversTest(unittest.TestCase):
    TARGET = (0, 0, 1000, 1000)

    def test_the_desktop_and_the_taskbar_never_cover(self):
        for cls, title in (("Progman", "Program Manager"), ("WorkerW", ""), ("WorkerW", "x"),
                           ("Shell_TrayWnd", "Taskbar"), ("Shell_SecondaryTrayWnd", "x")):
            self.assertFalse(app_windows.covers(self.TARGET, (0, 0, 1000, 1000), cls, title, True), cls)

    def test_hidden_titles_and_untitled_windows_never_cover(self):
        self.assertFalse(app_windows.covers(self.TARGET, self.TARGET, "X", "Program Manager", True))
        self.assertFalse(app_windows.covers(self.TARGET, self.TARGET, "X", "", True))

    def test_a_window_below_never_covers(self):
        self.assertFalse(app_windows.covers(self.TARGET, self.TARGET, "Notepad", "Untitled - Notepad", False))

    def test_a_window_above_covering_half_covers_and_a_tenth_does_not(self):
        self.assertTrue(app_windows.covers(self.TARGET, (0, 0, 1000, 500), "Notepad", "Notes", True))
        self.assertFalse(app_windows.covers(self.TARGET, (0, 0, 1000, 100), "Notepad", "Notes", True))
        self.assertFalse(app_windows.covers(self.TARGET, (2000, 0, 3000, 1000), "Notepad", "Notes", True))


class FakeUser32:
    """Top-level windows in z-order, front first."""

    def __init__(self, order, foreground):
        self.order, self.foreground = order, foreground

    def GetWindow(self, hwnd, cmd):
        assert cmd == app_windows.GW_HWNDPREV
        index = self.order.index(int(hwnd))
        return self.order[index - 1] if index else None

    def GetForegroundWindow(self):
        return self.foreground

    def GetAncestor(self, hwnd, flags):
        return hwnd

    def GetWindowThreadProcessId(self, hwnd, pid):
        pid._obj.value = 1  # never Lens itself

    def IsIconic(self, hwnd):
        return False


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class HiddenBehindTest(unittest.TestCase):
    def run_with(self, order, foreground, cls, title="Some window"):
        fake = FakeUser32(order, foreground)

        def dwm(hwnd, attribute, rect):
            rect.left, rect.top, rect.right, rect.bottom = 0, 0, 1000, 1000
            return True

        with mock.patch.object(app_windows, "_user32", return_value=fake), \
                mock.patch.object(app_windows, "_dwm", dwm), \
                mock.patch.object(app_windows, "class_of", return_value=cls), \
                mock.patch.object(app_windows, "title_of", return_value=title):
            return app_windows.hidden_behind(20)

    def test_a_window_above_hides_and_one_below_does_not(self):
        self.assertTrue(self.run_with([10, 20, 30], 10, "Notepad"))
        self.assertFalse(self.run_with([10, 20, 30], 30, "Notepad"))  # the chosen window is in front of it

    def test_the_desktop_in_the_foreground_does_not_hide(self):
        self.assertFalse(self.run_with([10, 20, 30], 10, "Progman", "Program Manager"))
        self.assertFalse(self.run_with([10, 20, 30], 10, "Shell_TrayWnd", ""))

    def test_above_walks_up_the_z_order(self):
        fake = FakeUser32([1, 2, 3, 4], None)
        with mock.patch.object(app_windows, "_user32", return_value=fake):
            self.assertTrue(app_windows.above(4, 1))
            self.assertFalse(app_windows.above(2, 3))


if __name__ == "__main__":
    unittest.main()

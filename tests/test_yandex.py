# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""Yandex Translate (no network: the session is a fake)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from echosub_lens import translate, translate_yandex  # noqa: E402
from echosub_lens.translate_yandex import YandexTranslator  # noqa: E402


class Response:
    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("no JSON")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}", response=self)


class Session:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.headers = {}
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.handler(url, dict(kwargs["params"][:3]), [v for k, v in kwargs["params"] if k == "text"])

    def close(self):
        self.closed = True


def upper(url, params, texts):
    """Yandex's answer with every text "translated" to upper case."""
    return Response(body={"code": 200, "lang": "en-" + params["lang"], "text": [t.upper() for t in texts]})


def make(handler):
    yandex = YandexTranslator()
    yandex.MIN_INTERVAL = 0.0
    yandex.RETRY_DELAYS = ()
    session = Session(handler)
    yandex._session = session
    return yandex, session


class YandexTest(unittest.TestCase):
    def test_module_settings(self):
        self.assertFalse(translate_yandex.NEEDS_KEY)
        self.assertEqual(translate_yandex.KEY_FIELDS, [])
        self.assertEqual(translate_yandex.KEY_URL, "")
        self.assertTrue({"ar", "ary", "fr", "en", "zh", "he", "jw"} <= translate_yandex.LANGUAGES)
        self.assertFalse({"yue", "oc", "sa"} & translate_yandex.LANGUAGES)
        YandexTranslator(api_keys={"yandex": "ignored"})  # takes keys like the others, needs none

    def test_many_texts_in_one_request(self):
        yandex, session = make(upper)
        texts = ["- Where are you going?", "", "Save as", "Updates, man."]
        self.assertEqual(yandex.translate_many(texts, "en", "fr"),
                         ["- WHERE ARE YOU GOING?", "", "SAVE AS", "UPDATES, MAN."])
        self.assertEqual(len(session.calls), 1)
        url, kwargs = session.calls[0]
        self.assertTrue(url.endswith("/api/v1/tr.json/translate"))
        params = dict(kwargs["params"][:3])
        self.assertEqual(params["srv"], "tr-url-widget")
        self.assertEqual(params["lang"], "fr")  # source found by Yandex
        self.assertRegex(params["id"], r"^[0-9a-f]{32}-1-0$")
        self.assertEqual([v for k, v in kwargs["params"] if k == "text"],
                         ["- Where are you going?", "Save as", "Updates, man."])
        self.assertEqual(yandex.translate("Save as", "auto", "ar"), "SAVE AS")
        self.assertRegex(dict(session.calls[1][1]["params"][:3])["id"], r"-2-0$")

    def test_answers_are_trimmed_and_empty_ones_keep_the_text(self):
        answers = {"Going?": "  ذاهب ؟ ", "Hm": ""}
        yandex, _ = make(lambda url, params, texts: Response(body={"code": 200, "text": [answers[t] for t in texts]}))
        self.assertEqual(yandex.translate_many(["Going?", "Hm"], "auto", "ar"), ["ذاهب ؟", "Hm"])

    def test_requests_stay_under_the_size_limit(self):
        yandex, session = make(upper)
        yandex.LIMIT = 300
        texts = [f"Line number {i} of a long subtitle file." for i in range(40)]
        self.assertEqual(yandex.translate_many(texts, "en", "de"), [t.upper() for t in texts])
        self.assertGreater(len(session.calls), 1)
        for _, kwargs in session.calls:
            sent = [v for k, v in kwargs["params"] if k == "text"]
            self.assertLessEqual(sum(translate_yandex._size(t) for t in sent), 300)

    def test_a_long_text_is_cut_into_pieces(self):
        yandex, session = make(upper)
        yandex.LIMIT = 200
        text = " ".join(f"Sentence {i} is here." for i in range(40))
        self.assertEqual(yandex.translate(text, "en", "fr"), text.upper())
        self.assertGreater(len(session.calls), 1)

    def test_language_codes(self):
        yandex, session = make(upper)
        for ours, theirs in (("ary", "ar"), ("jw", "jv"), ("nn", "no"), ("zh", "zh"), ("he", "he"), ("fr", "fr")):
            yandex.translate("Hello", "en", ours)
            self.assertEqual(dict(session.calls[-1][1]["params"][:3])["lang"], theirs)
        for lang in ("yue", "sa", "auto", "xx"):
            with self.subTest(lang), self.assertRaises(translate.TranslatorError) as caught:
                yandex.translate("Hello", "en", lang)
            self.assertIn("Yandex Translate cannot translate into", str(caught.exception))

    def test_a_line_in_another_language_is_asked_again(self):
        """Yandex finds one source language per request: what it gave back unchanged is asked again."""
        def handler(url, params, texts):
            russian = [t for t in texts if t.startswith("Привет")]
            if russian and len(russian) < len(texts):  # took the request for Russian: English left as it was
                return Response(body={"code": 200, "text": [("BONJOUR" if t in russian else t) for t in texts]})
            return Response(body={"code": 200, "text": [("BONJOUR" if t in russian else t.upper()) for t in texts]})

        yandex, session = make(handler)
        self.assertEqual(yandex.translate_many(["Where?", "Привет", "OK 42", "123"], "auto", "fr"),
                         ["WHERE?", "BONJOUR", "OK 42", "123"])
        asked = [[v for k, v in kwargs["params"] if k == "text"] for _, kwargs in session.calls]
        # the numbers alone are never asked again; "OK 42" stays the same however it is asked
        self.assertEqual(asked, [["Where?", "Привет", "OK 42", "123"], ["Where?", "OK 42"], ["OK 42"]])

    def test_nothing_changed_is_not_asked_again(self):
        yandex, session = make(lambda url, params, texts: Response(body={"code": 200, "text": list(texts)}))
        self.assertEqual(yandex.translate_many(["Bonjour", "Merci"], "auto", "fr"), ["Bonjour", "Merci"])
        self.assertEqual(len(session.calls), 1)

    def test_too_many_requests_and_captcha_pause_it(self):
        for response in (Response(429, {"code": 429, "message": "Too many requests"}),
                         Response(403, {"code": 403, "message": "blocked"}),
                         Response(200, None, text="<html>captcha</html>"),
                         Response(200, {"code": 402, "message": "API key blocked"})):
            yandex, session = make(lambda url, params, texts, r=response: r)
            with self.subTest(response.text), self.assertRaises(translate.RateLimited) as caught:
                yandex.translate("Hello", "en", "fr")
            self.assertEqual(caught.exception.service, "Yandex Translate")
            with self.assertRaises(translate.RateLimited):  # paused: no new request
                yandex.translate("Hello", "en", "fr")
            self.assertEqual(len(session.calls), 1)

    def test_unsupported_direction(self):
        yandex, _ = make(lambda url, params, texts: Response(
            400, {"code": 501, "message": "The specified translation direction is not supported"}))
        with self.assertRaises(translate.TranslatorError):
            yandex.translate("Hello", "en", "fr")

    def test_server_error_tries_the_other_host_next(self):
        answers = [Response(503, None, text="down")]

        def handler(url, params, texts):
            return answers.pop(0) if answers else upper(url, params, texts)

        yandex, session = make(handler)
        with self.assertRaises(Exception) as caught:
            yandex.translate("Hello", "en", "fr")
        self.assertTrue(translate.transient(caught.exception))
        self.assertEqual(yandex.translate("Hello", "en", "fr"), "HELLO")
        self.assertNotEqual(session.calls[0][0], session.calls[1][0])

    def test_too_much_text_is_split(self):
        def handler(url, params, texts):
            if len(texts) > 1:
                return Response(413, {"code": 413, "message": "The text size exceeds the maximum"})
            return upper(url, params, texts)

        yandex, session = make(handler)
        self.assertEqual(yandex.translate_many(["a b", "c d", "e f"], "en", "fr"), ["A B", "C D", "E F"])

    def test_wrong_number_of_answers(self):
        yandex, _ = make(lambda url, params, texts: Response(body={"code": 200, "text": ["one"]}))
        with self.assertRaises(ValueError):
            yandex.translate_many(["a", "b"], "en", "fr")

    def test_close(self):
        yandex, session = make(upper)
        yandex.close()
        self.assertTrue(session.closed)


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""Naver Papago through its web page's service (no network: the session is a fake)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from echosub_lens import translate, translate_papago  # noqa: E402
from echosub_lens.translate_papago import PapagoTranslator  # noqa: E402


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
    """Answers each POST with `handler(data)` and remembers the requests."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.headers = {}

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.handler(kwargs["data"])

    def close(self):
        pass


def upper(data):
    """Papago "translating" to upper case, line breaks kept, the way the real service answers."""
    return Response(body={"srcLangType": "en", "tarLangType": data["target"], "translatedText": data["text"].upper(),
                          "engineType": "N2MT", "langDetection": {"nbests": []}})


def make(handler=upper):
    papago = PapagoTranslator()
    papago.MIN_INTERVAL = 0.0
    papago.RETRY_DELAYS = ()
    session = Session(handler)
    papago._session = session
    return papago, session


class RequestTest(unittest.TestCase):
    def test_a_text_is_posted_as_the_web_page_posts_it(self):
        papago, session = make()
        self.assertEqual(papago.translate("- Where are you going?", "auto", "ko"), "- WHERE ARE YOU GOING?")
        url, kwargs = session.calls[-1]
        self.assertEqual(url, "https://papago.naver.com/api/text/translation")
        self.assertEqual(kwargs["data"], {"source": "auto", "target": "ko", "text": "- Where are you going?",
                                          "dict": "false", "useGlossary": "false", "honorific": "false"})

    def test_no_key_and_no_signature_are_needed(self):
        papago, session = make()
        papago.translate("Save as", "en", "ja")
        headers = session.calls[-1][1]["headers"]
        self.assertNotIn("Authorization", headers)
        self.assertNotIn("Timestamp", headers)
        self.assertEqual(headers["Referer"], "https://papago.naver.com/")
        self.assertEqual(headers["Origin"], "https://papago.naver.com")
        self.assertFalse(translate_papago.NEEDS_KEY)
        self.assertEqual(translate_papago.KEY_FIELDS, [])
        self.assertEqual(translate_papago.KEY_URL, "")
        self.assertEqual(PapagoTranslator(api_keys={"papago_id": "x"}).NAME, "Papago")

    def test_lines_go_in_one_request_and_split_back(self):
        papago, session = make()
        self.assertEqual(papago.translate_many(["one", "", "two\nlines", "three"], "auto", "fr"),
                         ["ONE", "", "TWO LINES", "THREE"])
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0][1]["data"]["text"], "one\ntwo lines\nthree")

    def test_merged_lines_are_asked_again_in_halves(self):
        def merging(data):
            answer = upper(data)
            answer._body["translatedText"] = answer._body["translatedText"].replace("\n", " ")
            return answer

        papago, session = make(merging)
        self.assertEqual(papago.translate_many(["a", "b", "c"], "auto", "de"), ["A", "B", "C"])
        self.assertEqual(len(session.calls), 1 + 2 + 2)

    def test_a_long_text_is_cut_into_pieces(self):
        papago, session = make()
        text = ("Save as. " * 700).strip()
        self.assertEqual(papago.translate(text, "auto", "ko"), text.upper())
        self.assertGreater(len(session.calls), 1)
        self.assertTrue(all(len(kwargs["data"]["text"]) <= papago.CHUNK for _, kwargs in session.calls))


class LanguageTest(unittest.TestCase):
    def test_our_codes_become_papagos(self):
        self.assertEqual(PapagoTranslator.target("zh"), "zh-CN")
        self.assertEqual(PapagoTranslator.target("yue"), "zh-TW")
        self.assertEqual(PapagoTranslator.target("ary"), "ar")
        self.assertEqual(PapagoTranslator.target("ko"), "ko")
        self.assertEqual(PapagoTranslator.target("ar"), "ar")

    def test_the_source_is_given_when_papago_knows_it(self):
        self.assertEqual(PapagoTranslator.source("en"), "en")
        self.assertEqual(PapagoTranslator.source("zh"), "zh-CN")
        self.assertEqual(PapagoTranslator.source("auto"), "auto")
        self.assertEqual(PapagoTranslator.source(None), "auto")
        self.assertEqual(PapagoTranslator.source("tr"), "auto")  # Papago lacks Turkish: let it detect

    def test_the_languages_papago_translates_into(self):
        self.assertEqual(translate_papago.LANGUAGES, {"ko", "en", "ja", "zh", "yue", "es", "fr", "de", "ru", "pt",
                                                      "it", "vi", "th", "id", "hi", "ar", "ary"})

    def test_an_unsupported_target_says_so_without_a_request(self):
        papago, session = make()
        with self.assertRaises(translate.TranslatorError) as caught:
            papago.translate("Hello", "en", "tr")
        self.assertEqual(str(caught.exception), "Papago cannot translate into Turkish; choose another translator")
        self.assertEqual(session.calls, [])


class ErrorTest(unittest.TestCase):
    def test_too_many_requests_pauses_papago(self):
        papago, _ = make(lambda data: Response(429, text="Too Many Requests"))
        with self.assertRaises(translate.RateLimited) as caught:
            papago.translate("Hello", "en", "ko")
        self.assertEqual(caught.exception.service, "Papago")
        with self.assertRaises(translate.RateLimited):  # paused: not asked again right away
            papago.translate("Hello", "en", "ko")

    def test_a_ban_or_a_captcha_page_pauses_papago(self):
        for answer in (Response(403, text="Forbidden"), Response(200, text="<html>captcha</html>")):
            papago, session = make(lambda data, answer=answer: answer)
            with self.assertRaises(translate.RateLimited) as caught:
                papago.translate("Hello", "en", "ko")
            self.assertEqual(caught.exception.service, "Papago")

    def test_a_refusal_is_retried_once(self):
        answers = [Response(429, text="")]

        def handler(data):
            return answers.pop() if answers else upper(data)

        papago, session = make(handler)
        papago.RETRY_DELAYS = (0.0,)
        self.assertEqual(papago.translate("hello", "en", "ko"), "HELLO")
        self.assertEqual(len(session.calls), 2)

    def test_a_target_the_service_refuses(self):
        papago, _ = make(lambda data: Response(400, {"errorCode": "60104", "errorMessage": "Unsupported target"}))
        with self.assertRaises(translate.TranslatorError) as caught:
            papago.translate("Hello", "en", "ko")
        self.assertIn("Papago cannot translate into", str(caught.exception))

    def test_text_without_words_stays_as_it_is(self):
        papago, _ = make(lambda data: Response(400, {"errorCode": "60109", "errorMessage": "detection failed"}))
        self.assertEqual(papago.translate("12345", "auto", "ko"), "12345")

    def test_another_rejection_is_reported(self):
        papago, _ = make(lambda data: Response(400, {"errorCode": "60001", "errorMessage": "Text too long"}))
        with self.assertRaises(translate.TranslatorError) as caught:
            papago.translate("Hello", "en", "ko")
        self.assertIn("Text too long", str(caught.exception))

    def test_a_server_fault_is_raised_as_it_is(self):
        import requests

        papago, _ = make(lambda data: Response(502, text="Bad Gateway"))
        with self.assertRaises(requests.exceptions.HTTPError):
            papago.translate("Hello", "en", "ko")
        self.assertTrue(translate.transient(requests.exceptions.HTTPError(response=Response(502, text=""))))


if __name__ == "__main__":
    unittest.main()

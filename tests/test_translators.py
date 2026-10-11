# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""Online translators besides Google: Microsoft Bing, MyMemory and DeepL (no network: sessions are fakes)."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from echosub_lens import lens_translate, translate  # noqa: E402

BING_PAGE = ('<script>window._G={IG:"IG123",Lang:"en"};</script><div id="rich_tta" data-iid="translator.5023">'
             '<script>var params_AbusePreventionHelper = [1700000000000,"TOKEN-%d",3600000];</script>')


class Response:
    def __init__(self, status=200, body=None, text=None, url=""):
        self.status_code = status
        self._body = body
        self.text = text if text is not None else json.dumps(body)
        self.url = url

    def json(self):
        if self._body is None:
            raise ValueError("no JSON")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}", response=self)


class Session:
    """Answers requests from `handler(method, url, kwargs)` and remembers them."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.headers = {}
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.handler("GET", url, kwargs)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.handler("POST", url, kwargs)

    def close(self):
        self.closed = True


def fast(translator, session):
    translator.MIN_INTERVAL = 0.0
    translator.RETRY_DELAYS = ()
    translator._session = session
    return translator


class BingFake:
    """Bing's page and its translation service: lines are "translated" to upper case, line breaks kept."""

    def __init__(self, refuse=0, merge_lines=False):
        self.pages = 0
        self.refuse = refuse          # translation requests answered 401 first
        self.merge_lines = merge_lines

    def __call__(self, method, url, kwargs):
        if method == "GET":
            self.pages += 1
            return Response(text=BING_PAGE % self.pages, url="https://www.bing.com/translator")
        if self.refuse:
            self.refuse -= 1
            return Response(401, {"ShowCaptcha": False})
        text = kwargs["data"]["text"]
        if self.merge_lines:
            text = text.replace("\n", " ")
        return Response(body=[{"translations": [{"text": text.upper(), "to": kwargs["data"]["to"]}]}])


class BingTest(unittest.TestCase):
    def make(self, fake):
        session = Session(fake)
        return fast(translate.BingTranslator(), session), session

    def test_token_is_scraped_and_sent(self):
        bing, session = self.make(BingFake())
        self.assertEqual(bing.translate("hello", "auto", "fr"), "HELLO")
        method, url, kwargs = session.calls[-1]
        self.assertEqual((method, url), ("POST", "https://www.bing.com/ttranslatev3"))
        self.assertEqual(kwargs["params"]["IG"], "IG123")
        self.assertEqual(kwargs["params"]["IID"], "translator.5023")
        self.assertEqual(kwargs["data"]["token"], "TOKEN-1")
        self.assertEqual(kwargs["data"]["key"], "1700000000000")
        self.assertEqual(kwargs["data"]["fromLang"], "auto-detect")
        self.assertEqual(kwargs["data"]["to"], "fr")

    def test_the_token_is_kept_then_fetched_again_when_it_expires(self):
        fake = BingFake()
        bing, session = self.make(fake)
        bing.translate("one", "auto", "fr")
        bing.translate("two", "auto", "fr")
        self.assertEqual(fake.pages, 1)
        bing._auth_until = 0.0  # an hour later
        bing.translate("three", "auto", "fr")
        self.assertEqual(fake.pages, 2)
        self.assertEqual(session.calls[-1][2]["data"]["token"], "TOKEN-2")

    def test_a_refused_token_is_replaced_once(self):
        fake = BingFake(refuse=1)
        bing, session = self.make(fake)
        self.assertEqual(bing.translate("hello", "auto", "ar"), "HELLO")
        self.assertEqual(fake.pages, 2)
        self.assertEqual(session.calls[-1][2]["data"]["token"], "TOKEN-2")

    def test_still_refused_means_rate_limited(self):
        bing, _ = self.make(BingFake(refuse=10))
        with self.assertRaises(translate.RateLimited) as caught:
            bing.translate("hello", "auto", "ar")
        self.assertIn("Microsoft Translator", str(caught.exception))
        with self.assertRaises(translate.RateLimited):  # paused: no new request
            bing.translate("hello", "auto", "ar")

    def test_many_lines_in_one_request_each_answered_on_its_own(self):
        bing, session = self.make(BingFake())
        self.assertEqual(bing.translate_many(["a", "", "- Where are you going?", "c"], "auto", "fr"),
                         ["A", "", "- WHERE ARE YOU GOING?", "C"])
        posts = [c for c in session.calls if c[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][2]["data"]["text"], "a\n- Where are you going?\nc")

    def test_merged_lines_are_asked_again_in_halves(self):
        bing, _ = self.make(BingFake(merge_lines=True))
        self.assertEqual(bing.translate_many(["a", "b", "c"], "auto", "fr"), ["A", "B", "C"])

    def test_long_screens_are_split_into_requests_under_the_limit(self):
        bing, session = self.make(BingFake())
        bing.translate_many(["x" * 600, "y" * 600, "z"], "auto", "fr")
        posts = [c[2]["data"]["text"] for c in session.calls if c[0] == "POST"]
        self.assertTrue(all(len(p) <= translate.BingTranslator.CHUNK for p in posts))
        self.assertEqual(len(posts), 2)

    def test_language_codes(self):
        code = translate.BingTranslator.code
        self.assertEqual(code("zh"), "zh-Hans")
        self.assertEqual(code("no"), "nb")
        self.assertEqual(code("tl"), "fil")
        self.assertEqual(code("sr"), "sr-Cyrl")
        self.assertEqual(code("ary"), "ar")
        self.assertEqual(code("pt"), "pt")
        self.assertEqual(code("fr"), "fr")
        with self.assertRaises(translate.TranslatorError) as caught:
            code("jw")
        self.assertIn("Microsoft Translator cannot translate into", str(caught.exception))


def mymemory_answer(text, status=200, details="", matches=None, quota=False):
    return {"responseData": {"translatedText": text, "match": 0.9}, "responseStatus": status,
            "responseDetails": details, "quotaFinished": quota, "matches": matches or []}


class MyMemoryTest(unittest.TestCase):
    def test_one_request_per_text_and_no_email(self):
        session = Session(lambda m, u, k: Response(body=mymemory_answer(k["params"]["q"].upper())))
        mm = fast(translate.MyMemoryTranslator(), session)
        self.assertEqual(mm.translate_many(["hi", "bye"], "auto", "ar"), ["HI", "BYE"])
        self.assertEqual(len(session.calls), 2)
        params = session.calls[0][2]["params"]
        self.assertEqual(params, {"q": "hi", "langpair": "autodetect|ar"})
        self.assertNotIn("de", params)
        mm.translate("hi", "en", "zh")
        self.assertEqual(session.calls[-1][2]["params"]["langpair"], "en|zh-CN")

    def test_an_exact_or_machine_translation_beats_a_longer_remembered_sentence(self):
        matches = [{"segment": "Oh, I am a man of peace - where are you going?", "translation": "WRONG",
                    "match": 0.99, "created-by": "someone"},
                   {"segment": "- Where are you going?", "translation": "- إلى أين أنت ذاهب؟", "match": 0.85,
                    "created-by": "MT!"}]
        session = Session(lambda m, u, k: Response(body=mymemory_answer("WRONG", matches=matches)))
        mm = fast(translate.MyMemoryTranslator(), session)
        self.assertEqual(mm.translate("- Where are you going?", "en", "ar"), "- إلى أين أنت ذاهب؟")

    def test_html_entities_are_decoded(self):
        session = Session(lambda m, u, k: Response(body=mymemory_answer("l&#39;homme")))
        self.assertEqual(fast(translate.MyMemoryTranslator(), session).translate("the man", "auto", "fr"),
                         "l'homme")

    def test_daily_limit_used_up_is_a_clear_error(self):
        warning = ("MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY. NEXT AVAILABLE IN "
                   "10 HOURS 20 MINUTES 07 SECONDS")
        for answer in (Response(429, mymemory_answer(warning, status=429)),
                       Response(200, mymemory_answer(warning, quota=True))):
            mm = fast(translate.MyMemoryTranslator(), Session(lambda m, u, k, a=answer: a))
            with self.assertRaises(translate.TranslatorError) as caught:
                mm.translate("hello", "auto", "ar")
            self.assertIn("daily limit", str(caught.exception))

    def test_text_already_in_the_target_language_stays(self):
        answer = mymemory_answer("PLEASE SELECT TWO DISTINCT LANGUAGES", status=403,
                                 details="PLEASE SELECT TWO DISTINCT LANGUAGES")
        mm = fast(translate.MyMemoryTranslator(), Session(lambda m, u, k: Response(200, answer)))
        self.assertEqual(mm.translate("bonjour", "auto", "fr"), "bonjour")

    def test_long_text_is_sent_in_pieces(self):
        session = Session(lambda m, u, k: Response(body=mymemory_answer(k["params"]["q"])))
        mm = fast(translate.MyMemoryTranslator(), session)
        text = " ".join(["This is one sentence."] * 60)
        self.assertEqual(mm.translate(text, "auto", "fr"), text)
        self.assertGreater(len(session.calls), 1)
        self.assertTrue(all(len(c[2]["params"]["q"].encode()) <= 500 for c in session.calls))


class DeepLFake:
    def __init__(self, status=200):
        self.status = status

    def __call__(self, method, url, kwargs):
        if self.status != 200:
            return Response(self.status, {"message": "nope"})
        texts = [v for k, v in kwargs["data"] if k == "text"]
        return Response(body={"translations": [{"detected_source_language": "EN", "text": t.upper()}
                                               for t in texts]})


class DeepLTest(unittest.TestCase):
    def make(self, key="abc:fx", status=200):
        session = Session(DeepLFake(status))
        return fast(translate.DeepLTranslator(key), session), session

    def test_free_and_pro_keys_use_their_own_address(self):
        self.assertEqual(translate.DeepLTranslator("abc:fx").url, "https://api-free.deepl.com/v2/translate")
        self.assertEqual(translate.DeepLTranslator("abc").url, "https://api.deepl.com/v2/translate")

    def test_many_texts_in_one_request_with_the_key_in_the_header(self):
        deepl, session = self.make()
        self.assertEqual(deepl.translate_many(["a", "b", "", "c"], "auto", "en"), ["A", "B", "", "C"])
        self.assertEqual(len(session.calls), 1)
        method, url, kwargs = session.calls[0]
        self.assertEqual(url, "https://api-free.deepl.com/v2/translate")
        self.assertEqual(kwargs["headers"]["Authorization"], "DeepL-Auth-Key abc:fx")
        data = kwargs["data"]
        self.assertEqual([v for k, v in data if k == "text"], ["a", "b", "c"])
        self.assertIn(("target_lang", "EN-US"), data)
        self.assertNotIn("source_lang", [k for k, v in data])

    def test_more_than_fifty_texts_take_more_requests(self):
        deepl, session = self.make()
        deepl.translate_many([f"t{i}" for i in range(120)], "auto", "ar")
        self.assertEqual(len(session.calls), 3)
        self.assertIn(("target_lang", "AR"), session.calls[0][2]["data"])

    def test_language_codes(self):
        self.assertEqual(translate.DeepLTranslator.target("pt"), "PT-BR")
        self.assertEqual(translate.DeepLTranslator.target("zh"), "ZH-HANS")
        self.assertEqual(translate.DeepLTranslator.target("no"), "NB")
        self.assertEqual(translate.DeepLTranslator.target("fr"), "FR")

    def test_errors_say_what_to_do(self):
        for status, words in ((403, "key is not valid"), (456, "quota is used up"), (400, "cannot translate")):
            deepl, _ = self.make(status=status)
            with self.assertRaises(translate.TranslatorError) as caught:
                deepl.translate("hello", "auto", "ar")
            self.assertIn(words, str(caught.exception))

    def test_too_many_requests_pauses(self):
        deepl, _ = self.make(status=429)
        with self.assertRaises(translate.RateLimited) as caught:
            deepl.translate("hello", "auto", "ar")
        self.assertIn("DeepL", str(caught.exception))

    def test_close_closes_the_connection(self):
        deepl, session = self.make()
        deepl.close()
        self.assertTrue(session.closed)


class CreateTest(unittest.TestCase):
    def test_each_key_gives_its_translator(self):
        self.assertIsInstance(translate.create("bing", "cpu"), translate.BingTranslator)
        self.assertIsInstance(translate.create("mymemory", "cpu"), translate.MyMemoryTranslator)
        deepl = translate.create("deepl", "cpu", api_keys={"deepl": " k:fx "})
        self.assertIsInstance(deepl, translate.DeepLTranslator)
        self.assertEqual(deepl.api_key, "k:fx")

    def test_deepl_without_a_key_says_where_to_add_it(self):
        for keys in (None, {}, {"deepl": "  "}):
            with self.assertRaises(translate.TranslatorError) as caught:
                translate.create("deepl", "cpu", api_keys=keys)
            self.assertIn("Add your DeepL key in Translator settings", str(caught.exception))


class LensTranslatorTest(unittest.TestCase):
    def test_every_online_translator_gets_chunks_through_translate_many(self):
        backends = {
            "bing": fast(translate.BingTranslator(), Session(BingFake())),
            "mymemory": fast(translate.MyMemoryTranslator(),
                             Session(lambda m, u, k: Response(body=mymemory_answer(k["params"]["q"].upper())))),
            "deepl": fast(translate.DeepLTranslator("k:fx"), Session(DeepLFake())),
        }
        for key, backend in backends.items():
            with self.subTest(key), mock.patch.object(backend, "translate", side_effect=AssertionError):
                t = lens_translate.LensTranslator(key, factory=lambda k, d, b=backend: b)
                self.assertEqual(t.translate_many(["hi", "bye", "hi"], "ar"), ["HI", "BYE", "HI"])

    def test_api_keys_reach_the_factory(self):
        made = []
        t = lens_translate.LensTranslator("deepl", "cpu", api_keys={"deepl": "k"},
                                          factory=lambda k, d, api_keys=None: made.append(api_keys) or
                                          fast(translate.DeepLTranslator("k"), Session(DeepLFake())))
        t.translate_many(["x"], "fr")
        self.assertEqual(made, [{"deepl": "k"}])

    def test_choices_help_and_keys(self):
        for key in ("google", "bing", "mymemory", "deepl", "nllb-600m", "nllb-1.3b", "same"):
            self.assertIn(key, lens_translate.CHOICES)
            self.assertTrue(lens_translate.HELP[key])
        self.assertEqual(lens_translate.NEEDS_KEY, {"deepl"})
        self.assertEqual(lens_translate.resolve("bing", {"translator": "nllb-600m"}), "bing")
        self.assertEqual(lens_translate.resolve("deepl", {}), "deepl")
        self.assertEqual(lens_translate.resolve("same", {"translator": "none"}), "google")
        self.assertEqual(lens_translate.resolve("same", {"translator": "mymemory"}), "mymemory")
        self.assertEqual(lens_translate.resolve("nonsense", {}), "google")


if __name__ == "__main__":
    unittest.main()

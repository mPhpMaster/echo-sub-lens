# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""Yandex Translate without a key: the service behind Yandex's website translation widget (also used by Yandex
Browser). Many texts in one request, each answered on its own, the source language found by Yandex."""
import logging
import uuid
from urllib.parse import quote

from . import languages
from .translate import TranslatorError, _OnlineTranslator, _pieces, _Refused, transient

log = logging.getLogger(__name__)

NEEDS_KEY = False
KEY_FIELDS = []   # (name, label, help) the settings should ask for: none
KEY_URL = ""

# The widget's service; the second host answers the same requests when the first one cannot be reached
HOSTS = ("https://browser.translate.yandex.net", "https://translate.yandex.net")
PATH = "/api/v1/tr.json/translate"

# Our language codes Yandex writes differently; None: Yandex Translate cannot translate into it
YANDEX_CODES = {"ary": "ar", "jw": "jv", "nn": "no", "yue": None, "ln": None, "sn": None, "so": None, "ha": None,
                "yo": None, "as": None, "sd": None, "ps": None, "tk": None, "fo": None, "oc": None, "bo": None,
                "sa": None}
# Yandex's languages (from its getLangs answer) that are also ours
_YANDEX_LANGS = {
    "af", "am", "ar", "az", "ba", "be", "bg", "bn", "bs", "ca", "cs", "cy", "da", "de", "el", "en", "es", "et",
    "eu", "fa", "fi", "fr", "gl", "gu", "he", "hi", "hr", "ht", "hu", "hy", "id", "is", "it", "ja", "jv", "ka",
    "kk", "km", "kn", "ko", "lb", "lo", "lt", "lv", "mg", "mi", "mk", "ml", "mn", "mr", "ms", "mt", "my", "ne",
    "nl", "no", "pa", "pl", "pt", "ro", "ru", "si", "sk", "sl", "sq", "sr", "su", "sv", "sw", "ta", "te", "tg",
    "th", "tl", "tr", "tt", "uk", "ur", "uz", "vi", "yi", "zh",
}
# Our language codes it can translate into
LANGUAGES = {c for c in languages.LANGUAGES if YANDEX_CODES.get(c, c) in _YANDEX_LANGS}


def code(lang):
    """Yandex's code for our language `lang`, or TranslatorError when it cannot translate into it."""
    mapped = YANDEX_CODES.get(lang, lang)
    if not mapped or mapped == "auto" or mapped not in _YANDEX_LANGS:
        raise TranslatorError(f"Yandex Translate cannot translate into {languages.name(lang)}; "
                              "choose another translator")
    return mapped


def _size(text):
    """Characters `text` takes in the request's address."""
    return len(quote(text, safe="")) + 6  # "&text="


class YandexTranslator(_OnlineTranslator):
    """Yandex Translate's website widget service, without a key. Texts go in the address of a GET request (it
    refuses POST), so a request carries at most LIMIT characters of them. Yandex finds one source language for a
    whole request: a text it gave back unchanged in a request of several is asked again with the others like it,
    so a line in another language still gets translated."""

    NAME = "Yandex Translate"
    MIN_INTERVAL = 0.3
    LIMIT = 6000      # characters of (address-encoded) text per request; about 10,000 for the whole address works
    MANY = 100        # texts per request

    def __init__(self, api_keys=None):
        super().__init__()
        self._sid = uuid.uuid4().hex  # Yandex Browser's request id: a session, then a request number
        self._count = 0
        self._host = 0

    def _batches(self, texts):
        batches, current, size = [], [], 0
        for i, text in enumerate(texts):
            length = _size(text)
            if current and (len(current) >= self.MANY or size + length > self.LIMIT):
                batches.append(current)
                current, size = [], 0
            current.append(i)
            size += length
        if current:
            batches.append(current)
        return batches

    def _request(self, texts, src, tgt):
        to = code(tgt)
        if len(texts) == 1:  # a long text: cut at sentence ends, the pieces asked in as few requests as fit
            pieces = _pieces(texts[0], self.LIMIT, _size)
            answers = []
            for batch in self._batches(pieces):
                answers += self._ask([pieces[i] for i in batch], to)
            return [" ".join(a for a in answers if a)]
        return self._redo_unchanged(texts, self._ask(texts, to), to)

    def _redo_unchanged(self, texts, answers, to):
        """Texts given back as they were (taken to be in another language than the rest) asked again together,
        until a request changes none of them."""
        same = [i for i, (t, a) in enumerate(zip(texts, answers))
                if a.strip() == t.strip() and any(ch.isalpha() for ch in t)]
        if not same or len(same) == len(texts):
            return answers
        again = [texts[i] for i in same]
        redone = self._ask(again, to)
        if len(again) > 1:
            redone = self._redo_unchanged(again, redone, to)
        for i, answer in zip(same, redone):
            answers[i] = answer
        return answers

    def _ask(self, texts, to):
        """One request: the translations of `texts`, in order."""
        self._pace()
        self._count += 1
        params = [("srv", "tr-url-widget"), ("id", f"{self._sid}-{self._count}-0"), ("lang", to)]
        params += [("text", t) for t in texts]
        try:
            r = self._web_session().get(HOSTS[self._host] + PATH, params=params, timeout=self.TIMEOUT)
        except Exception as e:
            if transient(e):
                self._host = (self._host + 1) % len(HOSTS)  # the next request tries the other host
            raise
        if r.status_code >= 500:
            self._host = (self._host + 1) % len(HOSTS)
            r.raise_for_status()
        try:
            answer = r.json()
        except ValueError:
            answer = None
        status = answer.get("code") if isinstance(answer, dict) else None
        if r.status_code == 429 or status == 429:
            raise _Refused()
        if r.status_code in (401, 402, 403) or status in (401, 402, 403) or not isinstance(answer, dict):
            # A captcha page or a refusal: Yandex has blocked this address for a while
            log.info("Yandex Translate refused the request (HTTP %s)", r.status_code)
            raise _Refused()
        if status == 413 and len(texts) > 1:  # too much text after all: the halves on their own
            half = len(texts) // 2
            return self._ask(texts[:half], to) + self._ask(texts[half:], to)
        if status == 501:
            raise TranslatorError(f"Yandex Translate cannot translate this text into the chosen language ({to}); "
                                  "choose another translator")
        if status != 200:
            raise TranslatorError(f"Yandex Translate could not translate the text "
                                  f"({answer.get('message') or f'error {status}'})")
        out = answer.get("text")
        if not isinstance(out, list) or len(out) != len(texts):
            raise ValueError("Yandex Translate answered a different number of texts")
        return [str(a or "").strip() or t for a, t in zip(out, texts)]

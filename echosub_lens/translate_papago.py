# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""Naver Papago through the papago.naver.com web page's own service, without a key.

The page (a Next.js app) posts a plain form to /api/text/translation: no device ID, no signature and no
version key any more (the old /apis/n2mt/translate with its "PPG" HMAC header is gone). Line breaks are
kept, so many lines go in one request and split back. Papago knows only 16 languages."""
import logging

from . import languages
from .translate import _OnlineTranslator, _pieces, _Refused, TranslatorError

log = logging.getLogger(__name__)

NEEDS_KEY = False
KEY_FIELDS = []   # (name, label, help) the settings would ask for: none
KEY_URL = ""

PAGE = "https://papago.naver.com/"
TRANSLATE_URL = "https://papago.naver.com/api/text/translation"

# Our language codes Papago writes differently
PAPAGO_CODES = {"zh": "zh-CN", "yue": "zh-TW", "ary": "ar"}
# Papago's own languages (from the web page's list, October 2026)
PAPAGO_LANGUAGES = {"ko", "en", "ja", "zh-CN", "zh-TW", "es", "fr", "de", "ru", "pt", "it", "vi", "th", "id",
                    "hi", "ar"}
# Our languages Papago can translate into
LANGUAGES = frozenset(code for code in languages.LANGUAGES if PAPAGO_CODES.get(code, code) in PAPAGO_LANGUAGES)

UNSUPPORTED_TARGET = "60104"
DETECTION_FAILED = "60109"   # nothing to translate in the text (digits, symbols): it stays as it is


def code(lang):
    """Papago's code for one of our languages, or None when Papago lacks it."""
    papago = PAPAGO_CODES.get(lang, lang)
    return papago if papago in PAPAGO_LANGUAGES else None


class PapagoTranslator(_OnlineTranslator):
    """Naver Papago's web translator. Lines are sent together, joined by line breaks, and split back; when
    the count differs the halves are asked again, so each text keeps its own translation."""

    NAME = "Papago"
    MIN_INTERVAL = 0.5
    CHUNK = 3000      # characters per request (the web page takes 5000)

    def __init__(self, api_keys=None):
        """`api_keys` is accepted like the other translators' but not needed: the web service has no key."""
        super().__init__()

    @staticmethod
    def target(lang):
        papago = code(lang)
        if not papago:
            raise TranslatorError(f"Papago cannot translate into {languages.name(lang)}; choose another translator")
        return papago

    @staticmethod
    def source(lang):
        """The spoken language when Papago knows it, else "auto" (Papago detects it)."""
        return (code(lang) if lang and lang != "auto" else None) or "auto"

    def _batches(self, texts):
        batches, current, size = [], [], 0
        for i, text in enumerate(texts):
            length = len(text) + 1
            if current and size + length > self.CHUNK:
                batches.append(current)
                current, size = [], 0
            current.append(i)
            size += length
        if current:
            batches.append(current)
        return batches

    def _request(self, texts, src, tgt):
        to, source = self.target(tgt), self.source(src)
        if len(texts) == 1:
            return [" ".join(self._ask(piece, source, to) for piece in _pieces(texts[0], self.CHUNK))]
        return self._lines(texts, source, to)

    def _lines(self, texts, source, to):
        joined = "\n".join(t.replace("\n", " ") for t in texts)
        parts = [p.strip() for p in self._ask(joined, source, to).split("\n")]
        if len(parts) == len(texts):
            return parts
        if len(texts) == 1:
            return [" ".join(parts)]
        half = len(texts) // 2
        return self._lines(texts[:half], source, to) + self._lines(texts[half:], source, to)

    def _ask(self, text, source, target):
        self._pace()
        r = self._web_session().post(
            TRANSLATE_URL,
            data={"source": source, "target": target, "text": text, "dict": "false", "useGlossary": "false",
                  "honorific": "false"},
            headers={"Accept-Language": "en", "Origin": PAGE.rstrip("/"), "Referer": PAGE},
            timeout=self.TIMEOUT)
        if r.status_code in (403, 429):
            raise _Refused()   # too many requests, or this address is blocked for a while
        if r.status_code >= 500:
            r.raise_for_status()
        try:
            answer = r.json()
        except ValueError:
            answer = None
        if not isinstance(answer, dict):
            log.info("Papago answered something else than a translation (HTTP %s)", r.status_code)
            raise _Refused()   # a captcha or an error page
        if r.status_code < 400 and isinstance(answer.get("translatedText"), str):
            return answer["translatedText"]
        error = str(answer.get("errorCode") or "")
        if error == DETECTION_FAILED:
            return text
        if error == UNSUPPORTED_TARGET:
            raise TranslatorError(f"Papago cannot translate into this language ({target}); "
                                  "choose another translator")
        if r.status_code == 400:
            raise TranslatorError(f"Papago could not translate the text "
                                  f"({answer.get('errorMessage') or error or 'error 400'})")
        raise _Refused()

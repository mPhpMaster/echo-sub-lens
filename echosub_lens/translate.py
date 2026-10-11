# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""Translation backends: local NLLB-200 (CTranslate2) or Google Translate."""
import logging
import os
import re
import threading
import time

from . import config, languages

log = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？؟])\s+")
JSON_URL = "https://translate.googleapis.com/translate_a/single"  # Google Translate's own JSON service
# The service Chrome's translation extension uses: many texts in one request, each answered on its own (no
# lines to split back), and it keeps answering while the other two say "too many requests"
MANY_URL = "https://clients5.google.com/translate_a/t"


class NoTranslator:
    streaming = True

    def translate(self, text, src, tgt):
        return text


def cpu_threads():
    """Threads for translating on the processor: four, but always one core left for speech recognition.

    More is worse on a busy PC: with six threads on a six-core processor that a game and Discord were
    also using, a sentence took 23-56 s instead of 2.5-5 s.
    """
    return max(1, min(4, (os.cpu_count() or 4) - 1))


class NLLBTranslator:
    streaming = True

    def __init__(self, path, device="cuda", fast_cpu=True):
        import ctranslate2
        import sentencepiece as spm

        from . import cuda_libs

        if device == "cuda" and (ctranslate2.get_cuda_device_count() == 0 or not cuda_libs.available()):
            device = "cpu"  # no card, or none of NVIDIA's libraries: every sentence would fail on it
        self.device = device
        # On the processor a greedy search is about a fifth faster than two beams, for nearly the same words
        self.beam_size = 1 if device == "cpu" and fast_cpu else 2
        supported = ctranslate2.get_supported_compute_types(device)
        compute_type = "int8_float16" if "int8_float16" in supported else "int8"
        self.translator = ctranslate2.Translator(
            path, device=device, compute_type=compute_type, intra_threads=cpu_threads() if device == "cpu" else 4
        )
        self.sp = spm.SentencePieceProcessor(model_file=os.path.join(path, "sentencepiece.bpe.model"))

    def translate(self, text, src, tgt):
        return self.translate_many([text], src, tgt)[0]

    def close(self):
        """Free the model's memory (on the graphics card or in RAM) right away."""
        self.translator.unload_model()

    def translate_many(self, texts, src, tgt):
        """Several texts in one pass over the model (much faster on a graphics card than one by one)."""
        src_code, tgt_code = languages.nllb_code(src), languages.nllb_code(tgt)
        if not tgt_code:
            return list(texts)
        if not src_code:
            src_code = "eng_Latn"
        owners, batch = [], []
        for index, text in enumerate(texts):
            for sentence in (s for s in _SENTENCE_SPLIT.split(text or "") if s.strip()):
                owners.append(index)
                batch.append([src_code] + self.sp.encode(sentence, out_type=str) + ["</s>"])
        if not batch:
            return list(texts)
        results = self.translator.translate_batch(
            batch,
            target_prefix=[[tgt_code]] * len(batch),
            beam_size=self.beam_size,
            max_batch_size=32,
            max_decoding_length=200,
            repetition_penalty=1.1,
            no_repeat_ngram_size=4,
        )
        out = [[] for _ in texts]
        for index, r in zip(owners, results):
            tokens = r.hypotheses[0]
            if tokens and tokens[0] == tgt_code:
                tokens = tokens[1:]
            out[index].append(self.sp.decode(tokens).strip().lstrip("-–— ").strip())
        return [" ".join(parts).strip() if parts else text for parts, text in zip(out, texts)]


class RateLimited(Exception):
    """Google Translate refuses requests for a while; `retry_in` is how long Lens waits before asking again."""

    def __init__(self, retry_in):
        super().__init__(f"Google Translate is limiting requests; retrying in {round(retry_in)} s")
        self.retry_in = retry_in


class GoogleTranslator:
    streaming = False  # avoid hammering the web API with partial results
    MIN_INTERVAL = 0.35          # s between requests (Google allows about 5 per second)
    RETRY_DELAYS = (0.5, 1.5)    # s to wait before retrying a request Google refused
    COOLDOWN = (5.0, 60.0)       # s to pause after the retries fail: first time, maximum (doubles each time)

    def __init__(self, fallback=None):
        """`fallback`: (name, factory) of an offline translator used while Google refuses requests."""
        from deep_translator import GoogleTranslator as _GT
        from deep_translator.exceptions import TooManyRequests

        self._cls = _GT
        self._too_many = TooManyRequests
        self._cache = {}
        self._session = None
        self._web_only = False
        self._many_broken = False
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._paused_until = 0.0
        self._cooldown = self.COOLDOWN[0]
        self._fallback_spec = fallback
        self._fallback = None
        self._offline = False
        self.notify = lambda message: None  # set by the engine: shows a short status message

    def translate(self, text, src, tgt):
        if not text:
            return text
        return self.translate_many([text], src, tgt)[0]

    def translate_many(self, texts, src, tgt):
        """Many texts, each translated on its own, in as few requests as Google allows."""
        texts = list(texts)
        if not any(texts):
            return texts
        try:
            result = self._google(texts, tgt)
        except RateLimited:
            offline = self._offline_translator()
            if offline is None:
                raise
            if not self._offline:
                self._offline = True
                self.notify(f"Google Translate is limiting requests — translating offline with "
                            f"{self._fallback_spec[0]} for now")
            return [offline.translate(t, src, tgt) if t else t for t in texts]
        if self._offline:
            self._offline = False
            self.notify("Google Translate is available again")
        return result

    def close(self):
        if self._session is not None:
            self._session.close()
        close = getattr(self._fallback, "close", None)
        if close is not None:
            close()
        self._fallback = None

    def _offline_translator(self):
        if self._fallback is None and self._fallback_spec is not None:
            try:
                self._fallback = self._fallback_spec[1]()
            except Exception:
                log.exception("Could not load the offline translation model")
                self._fallback_spec = None
        return self._fallback

    def _ask_many(self, texts, key):
        """Chrome's service first; when it refuses or fails, the other services, many lines in one text."""
        refused = False
        if not self._many_broken:
            try:
                return self._ask_chrome(texts, key)
            except self._too_many:
                refused = True
            except Exception as e:
                log.warning("Google's Chrome translation service failed (%s); using the others", e)
                self._many_broken = True
        try:
            return self._ask_lines(texts, key)
        except self._too_many:
            raise
        except Exception:
            if refused:
                raise self._too_many()
            raise

    def _ask_chrome(self, texts, key):
        r = self._web_session().post(MANY_URL, params={"client": "dict-chrome-ex", "sl": "auto", "tl": key},
                                     data=[("q", t) for t in texts], timeout=10)
        if r.status_code == 429:
            raise self._too_many()
        r.raise_for_status()
        answer = r.json()
        if len(texts) == 1 and answer and isinstance(answer[0], str):
            answer = [answer]  # one text, sometimes answered without the list around it
        if not isinstance(answer, list) or len(answer) != len(texts):
            raise ValueError("Google answered a different number of texts")
        return [(a[0] if isinstance(a, list) else a) or t for a, t in zip(answer, texts)]

    def _ask_lines(self, texts, key):
        """Lines joined in one text: Google keeps line breaks, so the answer splits back line by line. When
        it merged some, the two halves are asked again (a few requests, not one per line)."""
        joined = "\n".join(t.replace("\n", " ") for t in texts)
        parts = [p.strip() for p in (self._ask(joined, key) or "").split("\n")]
        if len(parts) == len(texts):
            return parts
        if len(texts) == 1:
            return [" ".join(parts)]
        half = len(texts) // 2
        return self._ask_lines(texts[:half], key) + self._ask_lines(texts[half:], key)

    def _web_session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
            self._session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        return self._session

    def _ask(self, text, key):
        """One request: Google's JSON service (one kept-open connection, about 0.2 s), or, when that fails
        for another reason than too many requests, the translate.google.com page through deep-translator."""
        if not self._web_only:
            try:
                return self._ask_json(text, key)
            except self._too_many:
                raise
            except Exception as e:
                log.warning("Google's translation service failed (%s); using the web page instead", e)
                self._web_only = True
        if key not in self._cache:
            self._cache[key] = self._cls(source="auto", target=key)
        return self._cache[key].translate(text)

    def _ask_json(self, text, key):
        r = self._web_session().post(JSON_URL, params={"client": "gtx", "sl": "auto", "tl": key, "dt": "t"},
                                     data={"q": text}, timeout=10)
        if r.status_code == 429:
            raise self._too_many()
        r.raise_for_status()
        return "".join(part[0] for part in r.json()[0] if part and part[0])

    def _google(self, texts, tgt):
        key = languages.google_code(tgt)
        with self._lock:
            now = time.monotonic()
            if now < self._paused_until:
                raise RateLimited(self._paused_until - now)
            # While already translating offline, one try is enough to see whether Google is back
            for delay in (0.0,) + (() if self._offline else self.RETRY_DELAYS):
                time.sleep(max(delay, self._last_request + self.MIN_INTERVAL - time.monotonic(), 0.0))
                self._last_request = time.monotonic()
                try:
                    result = self._ask_many(texts, key)
                except self._too_many:
                    continue
                self._cooldown = self.COOLDOWN[0]
                return result
            # Still refused: stop asking for a while, longer each time it happens again
            self._paused_until = time.monotonic() + self._cooldown
            retry_in, self._cooldown = self._cooldown, min(self._cooldown * 2, self.COOLDOWN[1])
            raise RateLimited(retry_in)


def offline_fallback(device, downloader=None, fast_cpu=True):
    """(name, factory) for an NLLB model that is already downloaded, or None."""
    from .downloads import ModelDownloader

    downloader = downloader or ModelDownloader()
    for key in config.NLLB_REPOS:
        path = downloader.nllb_local(key)
        if path:
            return key.upper(), lambda: NLLBTranslator(path, device, fast_cpu)
    return None


def create(key, device, downloader=None, fast_cpu=True):
    if key == "google":
        return GoogleTranslator(offline_fallback(device, downloader, fast_cpu))
    if key in config.NLLB_REPOS:
        from .downloads import ModelDownloader

        return NLLBTranslator((downloader or ModelDownloader()).nllb(key), device, fast_cpu)
    return NoTranslator()

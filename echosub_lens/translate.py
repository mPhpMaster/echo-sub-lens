# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""Translation backends: local NLLB-200 (CTranslate2), or online: Google Translate, Microsoft Bing Translator,
MyMemory, DeepL (with the user's key)."""
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


def transient(error):
    """True for a failure of the network or a passing fault of Google's servers (no answer, a timeout, HTTP
    5xx): no reason to stop using a service. A malformed answer or another HTTP 4xx is."""
    import requests

    if isinstance(error, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        return True
    response = getattr(error, "response", None) if isinstance(error, requests.exceptions.HTTPError) else None
    return response is not None and (getattr(response, "status_code", None) or 0) >= 500


class RateLimited(Exception):
    """An online translator refuses requests for a while; `retry_in` is how long Lens waits before asking again."""

    def __init__(self, retry_in, service="Google Translate"):
        super().__init__(f"{service} is limiting requests; retrying in {round(retry_in)} s")
        self.retry_in = retry_in
        self.service = service


class TranslatorError(RuntimeError):
    """An online translator cannot do it (no key, a wrong key, its limit used up, a language it lacks): the
    message says so in words the user can act on."""


class GoogleTranslator:
    online = True     # many texts per request: LensTranslator sends them in chunks
    streaming = False  # avoid hammering the web API with partial results
    MIN_INTERVAL = 0.35          # s between requests (Google allows about 5 per second)
    BROKEN_FOR = 300.0           # s a service that answered nonsense is left alone before it is tried again
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
        self._web_only_until = 0.0     # a service that answered nonsense is left alone until then
        self._many_broken_until = 0.0
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
        refused = asked = False
        if time.monotonic() >= self._many_broken_until:
            asked = True
            try:
                return self._ask_chrome(texts, key)
            except self._too_many:
                refused = True
            except Exception as e:
                if transient(e):  # the network, not the service: only this request goes elsewhere
                    log.warning("Google's Chrome translation service did not answer (%s); asking another", e)
                else:
                    log.warning("Google's Chrome translation service failed (%s); using the others for %d s",
                                e, self.BROKEN_FOR)
                    self._many_broken_until = time.monotonic() + self.BROKEN_FOR
        try:
            return self._ask_lines(texts, key, paced=asked)
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

    def _ask_lines(self, texts, key, paced=False):
        """Lines joined in one text: Google keeps line breaks, so the answer splits back line by line. When
        it merged some, the two halves are asked again (a few requests, not one per line).

        `paced`: a request was already sent for these texts, so this one waits its turn (MIN_INTERVAL)."""
        if paced:
            self._pace()
        joined = "\n".join(t.replace("\n", " ") for t in texts)
        parts = [p.strip() for p in (self._ask(joined, key) or "").split("\n")]
        if len(parts) == len(texts):
            return parts
        if len(texts) == 1:
            return [" ".join(parts)]
        half = len(texts) // 2
        return self._ask_lines(texts[:half], key, paced=True) + self._ask_lines(texts[half:], key, paced=True)

    def _pace(self):
        """Wait until MIN_INTERVAL has passed since the last request (called with self._lock held)."""
        time.sleep(max(self._last_request + self.MIN_INTERVAL - time.monotonic(), 0.0))
        self._last_request = time.monotonic()

    def _web_session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
            self._session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        return self._session

    def _ask(self, text, key):
        """One request: Google's JSON service (one kept-open connection, about 0.2 s), or, when that fails
        for another reason than too many requests, the translate.google.com page through deep-translator."""
        if time.monotonic() >= self._web_only_until:
            try:
                return self._ask_json(text, key)
            except self._too_many:
                raise
            except Exception as e:
                if transient(e):  # the network, not the service: only this request goes to the web page
                    log.warning("Google's translation service did not answer (%s); asking the web page", e)
                else:
                    log.warning("Google's translation service failed (%s); using the web page for %d s",
                                e, self.BROKEN_FOR)
                    self._web_only_until = time.monotonic() + self.BROKEN_FOR
            self._pace()
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


BROWSER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                 "Chrome/126.0.0.0 Safari/537.36")  # Bing answers "401" to a shortened Chrome version


class _Refused(Exception):
    """The service said "too many requests" (or asked for a captcha): wait a little and ask again."""


def _pieces(text, limit, size=len):
    """`text` cut at sentence ends (or, for a very long sentence, anywhere) into pieces of at most `limit`."""
    if size(text) <= limit:
        return [text]
    pieces, current = [], ""
    for sentence in _SENTENCE_SPLIT.split(text):
        while size(sentence) > limit:  # one sentence longer than the limit: cut it at a space
            cut = limit
            while size(sentence[:cut]) > limit:
                cut -= 1
            space = sentence.rfind(" ", 0, cut)
            cut = space if space > 0 else cut
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:cut])
            sentence = sentence[cut:].lstrip()
        joined = f"{current} {sentence}" if current else sentence
        if size(joined) <= limit:
            current = joined
        else:
            pieces.append(current)
            current = sentence
    if current:
        pieces.append(current)
    return [p for p in pieces if p.strip()]


class _OnlineTranslator:
    """What Bing, MyMemory and DeepL share with GoogleTranslator: one kept-open connection, requests paced
    MIN_INTERVAL apart, a short retry when the service says "too many requests", then a pause (RateLimited)
    that grows each time it happens again. Subclasses give `_batches(texts)` (lists of positions sent
    together) and `_request(texts, src, tgt)` (their translations, in order)."""

    online = True
    streaming = False
    NAME = "The translator"
    MIN_INTERVAL = 0.3          # s between requests
    RETRY_DELAYS = (1.0,)       # s to wait before asking again after "too many requests"
    COOLDOWN = (5.0, 60.0)      # s to pause after the retries fail: first time, maximum (doubles each time)
    TIMEOUT = 15

    def __init__(self):
        self._session = None
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._paused_until = 0.0
        self._cooldown = self.COOLDOWN[0]
        self.notify = lambda message: None  # set by the engine: shows a short status message

    def translate(self, text, src, tgt):
        if not text:
            return text
        return self.translate_many([text], src, tgt)[0]

    def translate_many(self, texts, src, tgt):
        """Many texts, each translated on its own; an empty one stays empty."""
        texts = list(texts)
        todo = [i for i, t in enumerate(texts) if t and t.strip()]
        if not todo:
            return texts
        out = list(texts)
        with self._lock:
            for batch in self._batches([texts[i] for i in todo]):
                answers = self._with_retries([texts[todo[i]] for i in batch], src, tgt)
                for i, answer in zip(batch, answers):
                    out[todo[i]] = answer or texts[todo[i]]
        return out

    def close(self):
        if self._session is not None:
            self._session.close()
            self._session = None

    def _batches(self, texts):
        return [[i] for i in range(len(texts))]

    def _request(self, texts, src, tgt):
        raise NotImplementedError

    def _with_retries(self, texts, src, tgt):
        now = time.monotonic()
        if now < self._paused_until:
            raise RateLimited(self._paused_until - now, self.NAME)
        for delay in (0.0,) + self.RETRY_DELAYS:
            time.sleep(delay)
            try:
                result = self._request(texts, src, tgt)
            except _Refused:
                continue
            self._cooldown = self.COOLDOWN[0]
            return result
        self._paused_until = time.monotonic() + self._cooldown
        retry_in, self._cooldown = self._cooldown, min(self._cooldown * 2, self.COOLDOWN[1])
        raise RateLimited(retry_in, self.NAME)

    def _pace(self):
        """Wait until MIN_INTERVAL has passed since the last request (called with self._lock held)."""
        time.sleep(max(self._last_request + self.MIN_INTERVAL - time.monotonic(), 0.0))
        self._last_request = time.monotonic()

    def _web_session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
            self._session.headers["User-Agent"] = BROWSER_AGENT
        return self._session


# Our language codes Microsoft writes differently; None: Microsoft Translator cannot translate into it
BING_CODES = {"ary": "ar", "zh": "zh-Hans", "yue": "yue", "tl": "fil", "no": "nb", "nn": "nb", "sr": "sr-Cyrl",
              "mn": "mn-Cyrl", "tlh": "tlh-Latn", "jw": None, "su": None, "oc": None, "sa": None, "yi": None}
BING_PAGE = "https://www.bing.com/translator"


class BingTranslator(_OnlineTranslator):
    """Microsoft Bing's web translator, without a key: the page gives a token (good for about an hour) that
    the translation requests carry. Lines are sent together, joined by line breaks, and split back; when the
    count differs the halves are asked again, so each text keeps its own translation."""

    NAME = "Microsoft Translator"
    MIN_INTERVAL = 0.5
    CHUNK = 1000      # characters per request (the web page's limit)
    MARGIN = 60.0     # s before the token expires that a new one is fetched

    def __init__(self):
        super().__init__()
        self._auth = None     # (translate URL, IG, IID, key, token)
        self._auth_until = 0.0

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

    @staticmethod
    def code(lang):
        code = BING_CODES.get(lang, lang)
        if not code or code == "auto":
            raise TranslatorError(f"Microsoft Translator cannot translate into {languages.name(lang)}; "
                                  "choose another translator")
        return code

    def _request(self, texts, src, tgt):
        to = self.code(tgt)
        if len(texts) == 1:
            return [" ".join(self._ask(piece, to) for piece in _pieces(texts[0], self.CHUNK))]
        return self._lines(texts, to)

    def _lines(self, texts, to):
        parts = [p.strip() for p in self._ask("\n".join(t.replace("\n", " ") for t in texts), to).split("\n")]
        if len(parts) == len(texts):
            return parts
        if len(texts) == 1:
            return [" ".join(parts)]
        half = len(texts) // 2
        return self._lines(texts[:half], to) + self._lines(texts[half:], to)

    def _token(self, fresh=False):
        if fresh or self._auth is None or time.monotonic() >= self._auth_until:
            self._pace()
            r = self._web_session().get(BING_PAGE, timeout=self.TIMEOUT)
            r.raise_for_status()
            ig = re.search(r'IG:"([^"]+)"', r.text)
            helper = re.search(r"params_AbusePreventionHelper\s*=\s*\[\s*(\d+)\s*,\s*\"([^\"]+)\"\s*,\s*(\d+)", r.text)
            if not ig or not helper:
                raise ValueError("Microsoft's translator page has changed (no token on it)")
            iid = re.search(r'data-iid="(translator\.\d+)"', r.text)
            origin = re.match(r"https?://[^/]+", r.url or BING_PAGE)
            self._auth = ((origin.group(0) if origin else "https://www.bing.com") + "/ttranslatev3", ig.group(1),
                          iid.group(1) if iid else "translator.5028", helper.group(1), helper.group(2))
            self._auth_until = time.monotonic() + max(int(helper.group(3)) / 1000.0 - self.MARGIN, 60.0)
        return self._auth

    def _ask(self, text, to):
        """One text (or lines joined by line breaks); a new token once when the old one is refused."""
        for attempt in range(2):
            url, ig, iid, key, token = self._token(fresh=attempt > 0)
            self._pace()
            r = self._web_session().post(url, params={"isVertical": "1", "IG": ig, "IID": iid},
                                         data={"fromLang": "auto-detect", "to": to, "text": text,
                                               "token": token, "key": key},
                                         headers={"Referer": BING_PAGE}, timeout=self.TIMEOUT)
            if r.status_code == 429:
                raise _Refused()
            if r.status_code >= 500:
                r.raise_for_status()
            answer = None
            if r.status_code < 400:
                try:
                    answer = r.json()
                except ValueError:
                    answer = None
            if isinstance(answer, list) and answer and isinstance(answer[0], dict):
                return "".join(t.get("text", "") for t in answer[0].get("translations") or [])
            if isinstance(answer, dict) and answer.get("statusCode") == 429:
                raise _Refused()
            if isinstance(answer, dict) and answer.get("statusCode") == 400 and attempt:
                raise TranslatorError(f"Microsoft Translator did not accept the text "
                                      f"({answer.get('errorMessage') or 'error 400'})")
            # 4xx, a captcha or an answer without translations: the token is old or refused; a new one once
            log.info("Microsoft Translator refused the request (HTTP %s); fetching a new token", r.status_code)
        raise _Refused()


MYMEMORY_URL = "https://api.mymemory.translated.net/get"
MYMEMORY_CODES = {"ary": "ar", "zh": "zh-CN", "yue": "zh-TW", "jw": "jv"}


class MyMemoryTranslator(_OnlineTranslator):
    """MyMemory (Translated srl), without a key or an email address: one text per request, about 5000
    characters a day for each internet address."""

    NAME = "MyMemory"
    MIN_INTERVAL = 0.4
    LIMIT = 500   # bytes of text per request

    @staticmethod
    def code(lang):
        return MYMEMORY_CODES.get(lang, lang)

    def _request(self, texts, src, tgt):
        source = "autodetect" if not src or src == "auto" else self.code(src)
        size = lambda t: len(t.encode("utf-8"))  # noqa: E731
        return [" ".join(self._ask(piece, source, self.code(tgt)) for piece in _pieces(texts[0], self.LIMIT, size))]

    def _ask(self, text, source, target):
        import html

        self._pace()
        r = self._web_session().get(MYMEMORY_URL, params={"q": text, "langpair": f"{source}|{target}"},
                                    timeout=self.TIMEOUT)
        if r.status_code >= 500:
            r.raise_for_status()
        try:
            answer = r.json()
        except ValueError:
            answer = {}
        data = answer.get("responseData") or {}
        translated = str(data.get("translatedText") or "")
        try:
            status = int(answer.get("responseStatus") or r.status_code)
        except (TypeError, ValueError):
            status = r.status_code
        details = str(answer.get("responseDetails") or translated)
        if answer.get("quotaFinished") or "YOU USED ALL AVAILABLE FREE TRANSLATIONS" in (translated + details).upper():
            raise TranslatorError("MyMemory's free daily limit is used up (about 5000 characters a day); "
                                  "choose another translator or try again tomorrow")
        if status == 429:
            raise _Refused()
        if status != 200:
            if "DISTINCT LANGUAGES" in details.upper():
                return text  # already in the target language
            raise TranslatorError(f"MyMemory could not translate the text ({details or f'error {status}'})")
        return html.unescape(self._best(text, answer) or translated)

    @staticmethod
    def _best(text, answer):
        """A translation of exactly this text from the memory, or else the machine translation. MyMemory's
        main answer can be a remembered translation of a longer, different sentence that only contains it."""
        matches = [m for m in answer.get("matches") or [] if isinstance(m, dict) and m.get("translation")]
        wanted = " ".join(text.split()).casefold()
        exact = [m for m in matches if " ".join(str(m.get("segment") or "").split()).casefold() == wanted]
        if exact:
            return str(max(exact, key=lambda m: float(m.get("match") or 0)).get("translation"))
        machine = [m for m in matches if m.get("created-by") == "MT!"]
        return str(machine[0].get("translation")) if machine else None


# Our language codes DeepL writes differently (it wants a country for English and Portuguese)
DEEPL_TARGETS = {"en": "EN-US", "pt": "PT-BR", "zh": "ZH-HANS", "yue": "ZH-HANT", "no": "NB", "nn": "NB",
                 "ary": "AR"}
DEEPL_SOURCES = {"ary": "AR", "yue": "ZH", "no": "NB", "nn": "NB"}


class DeepLTranslator(_OnlineTranslator):
    """DeepL's API with the user's own key: a free key (ending in ":fx") uses api-free.deepl.com. Up to 50
    texts in one request, each answered on its own."""

    NAME = "DeepL"
    MIN_INTERVAL = 0.2
    MANY = 50              # texts per request (DeepL's limit)
    BYTES = 100 * 1024     # request size kept under DeepL's 128 KiB

    def __init__(self, api_key):
        super().__init__()
        self.api_key = api_key.strip()
        host = "api-free.deepl.com" if self.api_key.endswith(":fx") else "api.deepl.com"
        self.url = f"https://{host}/v2/translate"

    @staticmethod
    def target(lang):
        return DEEPL_TARGETS.get(lang, lang.upper())

    @staticmethod
    def source(lang):
        return DEEPL_SOURCES.get(lang, lang.split("-")[0].upper())

    def _batches(self, texts):
        batches, current, size = [], [], 0
        for i, text in enumerate(texts):
            length = len(text.encode("utf-8")) + 6
            if current and (len(current) >= self.MANY or size + length > self.BYTES):
                batches.append(current)
                current, size = [], 0
            current.append(i)
            size += length
        if current:
            batches.append(current)
        return batches

    def _request(self, texts, src, tgt):
        data = [("text", t) for t in texts] + [("target_lang", self.target(tgt)), ("preserve_formatting", "1")]
        if src and src != "auto":
            data.append(("source_lang", self.source(src)))
        self._pace()
        r = self._web_session().post(self.url, data=data, timeout=self.TIMEOUT,
                                     headers={"Authorization": f"DeepL-Auth-Key {self.api_key}"})
        if r.status_code in (401, 403):
            raise TranslatorError("The DeepL key is not valid; check it in Translator settings")
        if r.status_code == 456:
            raise TranslatorError("Your DeepL translation quota is used up (a free key has 500,000 characters "
                                  "a month); choose another translator until it renews")
        if r.status_code == 429:
            raise _Refused()
        if r.status_code == 400:
            try:
                message = r.json().get("message")
            except (ValueError, AttributeError):
                message = None
            raise TranslatorError(f"DeepL cannot translate into {languages.name(tgt)}"
                                  + (f" ({message})" if message else ""))
        r.raise_for_status()
        answers = r.json().get("translations") or []
        if len(answers) != len(texts):
            raise ValueError("DeepL answered a different number of texts")
        return [a.get("text") or t for a, t in zip(answers, texts)]


def offline_fallback(device, downloader=None, fast_cpu=True):
    """(name, factory) for an NLLB model that is already downloaded, or None."""
    from .downloads import ModelDownloader

    downloader = downloader or ModelDownloader()
    for key in config.NLLB_REPOS:
        path = downloader.nllb_local(key)
        if path:
            return key.upper(), lambda: NLLBTranslator(path, device, fast_cpu)
    return None


def create(key, device, downloader=None, fast_cpu=True, api_keys=None):
    """The translator for `key`; `api_keys`: the user's own keys, like {"deepl": "..."}."""
    if key == "google":
        return GoogleTranslator(offline_fallback(device, downloader, fast_cpu))
    if key == "bing":
        return BingTranslator()
    if key == "mymemory":
        return MyMemoryTranslator()
    if key == "deepl":
        api_key = str((api_keys or {}).get("deepl") or "").strip()
        if not api_key:
            raise TranslatorError("Add your DeepL key in Translator settings (a free key from deepl.com/pro-api)")
        return DeepLTranslator(api_key)
    if key == "yandex":
        from .translate_yandex import YandexTranslator

        return YandexTranslator(api_keys)
    if key == "papago":
        from .translate_papago import PapagoTranslator

        return PapagoTranslator(api_keys)
    if key in config.NLLB_REPOS:
        from .downloads import ModelDownloader

        return NLLBTranslator((downloader or ModelDownloader()).nllb(key), device, fast_cpu)
    return NoTranslator()

# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: translating many short texts at once, remembering what was already translated.

The screen is read again every second, and most of it has not changed: a cache keeps every text it
has seen, so only new text is sent to the translator. Google gets one request for many lines.
"""
import logging
import threading
from collections import OrderedDict

from . import config, languages, translate

log = logging.getLogger(__name__)

CACHE_SIZE = 5000
GOOGLE_CHUNK = 4000      # characters per Google request (its limit is 5000)
SIMILAR_MIN_LENGTH = 12  # shorter texts must match exactly ("Save" and "Same" are not the same)
SIMILAR_CHARS_PER_EDIT = 20  # a text read before may differ by one letter in this many and still count
SIMILAR_CANDIDATES = 600  # the most recent texts of about the same length are compared
NLLB_BATCH = 16         # texts per pass of the offline model: big enough to be fast, small enough to show progress
SAME = "same"            # the default: EchoSub's translator when EchoSub is on this PC, else Google
CHOICES = {
    SAME: "Default",
    "google": config.TRANSLATORS["google"],
    "bing": "Microsoft Bing Translator — online",
    "mymemory": "MyMemory — online, limited per day",
    "deepl": "DeepL — online, needs your free key",
    "nllb-600m": config.TRANSLATORS["nllb-600m"],
    "nllb-1.3b": config.TRANSLATORS["nllb-1.3b"],
}
ONLINE = {"google", "bing", "mymemory", "deepl"}  # the text read is sent to the service
NEEDS_KEY = {"deepl"}    # translators that work only with the user's own key (saved in api_keys)
HELP = {
    SAME: "The translator EchoSub uses when EchoSub is on this PC, else Google Translate.",
    "google": "Google Translate: free, no key, many languages; the text is sent to Google.",
    "bing": "Microsoft Bing Translator: free, no key, many languages; the text is sent to Microsoft.",
    "mymemory": "MyMemory: free, no key, about 5000 characters a day; the text is sent to MyMemory (Translated).",
    "deepl": "DeepL: very natural translations; needs your own key (free at deepl.com, 500,000 characters "
             "a month); the text is sent to DeepL.",
    "nllb-600m": "NLLB 600M: offline on this PC, nothing is sent anywhere; a one-time download, fast.",
    "nllb-1.3b": "NLLB 1.3B: offline on this PC, nothing is sent anywhere; a bigger download, more accurate.",
}


def resolve(choice, base_cfg):
    """The translator key to load: the Lens choice, or the default one (never "none": Lens must translate)."""
    key = base_cfg.get("translator", "google") if choice == SAME else choice
    return key if key in CHOICES and key != SAME else "google"


def already_in(text, target):
    """True when `text` is clearly written in the target language's alphabet (Arabic shown to an Arabic reader)."""
    script = languages.text_script(text)
    expected = languages.LANGUAGE_SCRIPTS.get(target)
    if script is None or not expected:
        return False
    return script in expected


class LensTranslator:
    """`translate_many(texts, target)` -> translations in the same order. Thread-safe, loads lazily."""

    def __init__(self, key, device="cuda", factory=translate.create, api_keys=None):
        """`api_keys`: the user's own keys ({"deepl": "..."}), given to the factory when there are some."""
        self.key = key
        self._device = device
        self._factory = factory
        self._api_keys = api_keys
        self._backend = None
        self._lock = threading.Lock()
        self._cache = OrderedDict()

    def _get_backend(self):
        if self._backend is None:
            device = self._device
            if device == "vulkan":
                device = "cuda"
            if self._api_keys is not None:
                self._backend = self._factory(self.key, device, api_keys=self._api_keys)
            else:
                self._backend = self._factory(self.key, device)
        return self._backend

    def release(self):
        """Let go of the translator's model (memory on the graphics card and in RAM); it loads again if it
        is needed. True when one was loaded."""
        with self._lock:  # never in the middle of a translation
            backend, self._backend = self._backend, None
        if backend is None:
            return False
        close = getattr(backend, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                log.exception("Could not close the translator")
        del backend
        import gc

        gc.collect()
        return True

    def cached(self, text, target):
        return self._cache.get((target, text))

    def translate_many(self, texts, target, progress=None):
        """`progress(done, total)` hears how many of the texts not translated before are done."""
        progress = progress or (lambda done, total: None)
        with self._lock:
            todo = [t for t in dict.fromkeys(texts) if (target, t) not in self._cache and not self._similar(t, target)]
            if todo:
                backend = self._get_backend()
                progress(0, len(todo))
                if getattr(backend, "online", False):  # Google, Bing, MyMemory, DeepL
                    results = self._online(backend, todo, target, progress)
                elif hasattr(backend, "translate_many"):  # NLLB: many texts per pass over the model
                    # A graphics card does the whole screen in one pass fastest; the processor is slow
                    # enough that showing progress batch by batch is worth a little time
                    batch = len(todo) if getattr(backend, "device", "cpu") != "cpu" else NLLB_BATCH
                    results = []
                    for start in range(0, len(todo), batch):
                        results += backend.translate_many(todo[start:start + batch], "auto", target)
                        progress(len(results), len(todo))
                else:
                    results = []
                    for text in todo:
                        results.append(backend.translate(text, "auto", target))
                        progress(len(results), len(todo))
                for text, result in zip(todo, results):
                    self._remember((target, text), result or text)
            return [self._cache.get((target, t), t) for t in texts]

    def _remember(self, key, value):
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)

    def _similar(self, text, target):
        """Reuse the translation of text read before that differs only by a letter or two (Windows read one
        letter differently this time): no new request, and nothing on the screen changes."""
        if len(text) < SIMILAR_MIN_LENGTH:
            return False
        from rapidfuzz import process
        from rapidfuzz.distance import Levenshtein

        # One letter in 20 may differ, never a number ("page 12" is not "page 13"), never a whole word
        # ("enabled" / "disabled" differ by three letters)
        allowed = max(1, len(text) // SIMILAR_CHARS_PER_EDIT)
        digits = _digits(text)
        candidates = []
        for lang, t in reversed(self._cache):
            if lang == target and abs(len(t) - len(text)) <= allowed and _digits(t) == digits:
                candidates.append(t)
                if len(candidates) >= SIMILAR_CANDIDATES:
                    break
        match = process.extractOne(text, candidates, scorer=Levenshtein.distance, score_cutoff=allowed)
        if match is None:
            return False
        self._remember((target, text), self._cache[(target, match[0])])
        return True

    @staticmethod
    def _online(backend, texts, target, progress=lambda done, total: None):
        """An online translator: many texts per call, each translated on its own (never two lines run into
        one); the backend splits a chunk further when its service takes less per request."""
        results = []
        for chunk in _chunks(texts, GOOGLE_CHUNK):
            results.extend(backend.translate_many(chunk, "auto", target))
            progress(len(results), len(texts))
        return results

    _google = _online  # its name before Bing, MyMemory and DeepL


def _digits(text):
    """The numbers in `text` ("12", "3.5"), not a 0 read for an O inside a word ("0ver")."""
    return " ".join(w for w in text.split() if sum(ch.isdigit() for ch in w) * 2 >= len(w))


def _chunks(texts, limit):
    chunk, size = [], 0
    for text in texts:
        if chunk and size + len(text) + 1 > limit:
            yield chunk
            chunk, size = [], 0
        chunk.append(text)
        size += len(text) + 1
    if chunk:
        yield chunk

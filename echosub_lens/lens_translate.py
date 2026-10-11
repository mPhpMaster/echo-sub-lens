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
    "nllb-600m": config.TRANSLATORS["nllb-600m"],
    "nllb-1.3b": config.TRANSLATORS["nllb-1.3b"],
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

    def __init__(self, key, device="cuda", factory=translate.create):
        self.key = key
        self._device = device
        self._factory = factory
        self._backend = None
        self._lock = threading.Lock()
        self._cache = OrderedDict()

    def _get_backend(self):
        if self._backend is None:
            device = self._device
            if device == "vulkan":
                device = "cuda"
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
                if isinstance(backend, translate.GoogleTranslator):
                    results = self._google(backend, todo, target, progress)
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

    @classmethod
    def _google(cls, backend, texts, target, progress=lambda done, total: None):
        """Many lines in one request: Google keeps line breaks, so the answer splits back line by line."""
        results = []
        for chunk in _chunks(texts, GOOGLE_CHUNK):
            results.extend(cls._google_lines(backend, chunk, target))
            progress(len(results), len(texts))
        return results

    @classmethod
    def _google_lines(cls, backend, lines, target):
        joined = "\n".join(t.replace("\n", " ") for t in lines)
        answer = backend.translate(joined, "auto", target)
        parts = [p.strip() for p in (answer or "").split("\n")]
        if len(parts) == len(lines):
            return parts
        if len(lines) == 1:
            return [" ".join(parts)]
        # Lines merged or split: ask again in two halves (a few requests, not one per line)
        half = len(lines) // 2
        return cls._google_lines(backend, lines[:half], target) + cls._google_lines(backend, lines[half:], target)


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

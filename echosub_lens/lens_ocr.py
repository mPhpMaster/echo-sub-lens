# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: reading the text on the screen with Windows' own OCR, and grouping it into blocks.

Windows 10 and 11 recognize text offline for every language whose OCR pack is installed
(Settings → Time & language → Language). Nothing is downloaded and nothing leaves the PC.
"""
import asyncio
import logging
import unicodedata
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

AUTO = "auto"            # the languages of the Windows user profile
MAX_SCALE = 2.0          # small text is read better when the picture is enlarged first
SCALE_BELOW = 1600       # ... but only pictures narrower than this are enlarged


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self):
        return self.x + self.w

    @property
    def bottom(self):
        return self.y + self.h

    def united(self, other):
        x, y = min(self.x, other.x), min(self.y, other.y)
        return Box(x, y, max(self.right, other.right) - x, max(self.bottom, other.bottom) - y)


@dataclass
class Line:
    """One line of text, as Windows read it: its words and where they are (image pixels)."""
    words: list           # [(text, Box)], left to right on the screen
    rtl_reader: bool = False  # read by a right-to-left recognizer (Arabic, Hebrew)

    @property
    def box(self):
        box = self.words[0][1]
        for _, other in self.words[1:]:
            box = box.united(other)
        return box

    @property
    def text(self):
        return logical_text([word for word, _ in sorted(self.words, key=lambda w: w[1].x)], self.rtl_reader)


# ---- the order of the words, for right-to-left writing ---------------------------
_LEADING_MARKS = ":;,.!?"


def _right_to_left(word):
    return any(unicodedata.bidirectional(ch) in ("R", "AL") for ch in word)


def _left_to_right(word):
    return any(unicodedata.bidirectional(ch) in ("L", "EN", "AN") for ch in word)


_MARKS = _LEADING_MARKS + "،؛؟"


def _leading_mark(word):
    """An English word with a mark in front of it and none behind (":Translated")."""
    return (len(word) > 1 and word[0] in _LEADING_MARKS and word[-1] not in _MARKS and _left_to_right(word)
            and not _right_to_left(word))


def _trailing_mark(word):
    """A word with a mark behind it and none in front ("Translated:", "الملف؟")."""
    return len(word) > 1 and word[-1] in _MARKS and word[0] not in _MARKS


def _runs(words):
    """[(right_to_left, [words])]: a word with no letters of its own stays with the run before it."""
    runs = []
    for word in words:
        rtl = _right_to_left(word) or (not _left_to_right(word) and bool(runs) and runs[-1][0])
        if runs and runs[-1][0] == rtl:
            runs[-1][1].append(word)
        else:
            runs.append((rtl, [word]))
    return runs


def _as_seen(runs):
    """An Arabic recognizer puts the mark of an English word on its other side: ":Translated" for
    "Translated:" (the mark on the right on the screen), and "Original:" for ":Original" where the mark stands
    at the left of the English words, after Arabic words — there it is the end of the English words in a line
    read right to left. Turned back to what is on the screen."""
    for i, (rtl, group) in enumerate(runs):
        if rtl:
            continue
        for j, word in enumerate(group):
            if _leading_mark(word):
                group[j] = word[1:] + word[0]
            elif j == 0 and i > 0 and _trailing_mark(word) and not _right_to_left(word):
                group[j] = word[-1] + word[:-1]
    return runs


def _starts_at_the_right(runs, words):
    """Whether a mixed line is read right to left. The word a mark (":") belongs to comes first: an English
    "Save as:" with Arabic to its right is an English line; ":Original / Translated" or "الإعدادات:" with
    English to its left is an Arabic one. Otherwise: most of the words."""
    left_to_right = right_to_left = 0
    for i, (rtl, group) in enumerate(runs):
        if rtl:
            right_to_left += _trailing_mark(group[0]) and i > 0  # its last word, on the left: English beside it
        else:
            left_to_right += _trailing_mark(group[-1]) and i + 1 < len(runs)
            right_to_left += _leading_mark(group[0]) and i > 0
    if left_to_right != right_to_left:
        return right_to_left > left_to_right
    rtl_words = sum(len(group) for rtl, group in runs if rtl)
    return rtl_words * 2 >= len(words)


def logical_text(words, rtl_reader=False):
    """`words` as Windows found them, left to right on the screen, in the order they are read. Windows hands an
    Arabic line over left to right — "الإغلاق قبل الملف احفظ" for "احفظ الملف قبل الإغلاق" — so its runs of
    right-to-left words are turned around, and so is the line itself when it is read right to left. An Arabic
    recognizer also puts the mark of an English word on the wrong side (":Translated"): put back."""
    words = list(words)
    if not any(_right_to_left(w) for w in words):
        if rtl_reader:
            words = [w[1:] + w[0] if _leading_mark(w) else w for w in words]
        return " ".join(words)
    runs = _runs(words)
    if rtl_reader:
        runs = _as_seen(runs)
    if not _starts_at_the_right(runs, words):
        return " ".join(word for rtl, group in runs for word in (reversed(group) if rtl else group))
    ordered = []
    for rtl, group in reversed(runs):
        if rtl:
            ordered.extend(reversed(group))
            continue
        if _leading_mark(group[0]):  # ":Original / Translated" on the screen: "Original / Translated:"
            mark, group = group[0][0], [group[0][1:]] + group[1:]
            group[-1] += mark
        ordered.extend(group)
    return " ".join(ordered)


# ---- which recognizer read a line best ------------------------------------------
_EDGE = "\"'“”‘’()[]{}<>«»,.:;!?¿¡-–—…/|*•·"
_INSIDE_OK = "'’-–.&/"


def word_quality(word):
    """1 for a word that looks like a word, 0 for what a recognizer makes of a script it does not know
    ("öJPJl", "e..o", "TransIated", "0riginaI"); None for punctuation alone."""
    core = word.strip(_EDGE)
    if not core:
        return None
    if all(ch.isdigit() or ch in ".,:/-%" for ch in core):
        return 1  # a number, a time, a date
    if any(not (ch.isalpha() or ch in _INSIDE_OK or unicodedata.category(ch).startswith("M")) for ch in core):
        return 0  # a digit or a symbol inside a word
    if any(ch in _INSIDE_OK and core[i - 1] in _INSIDE_OK for i, ch in enumerate(core) if i):
        return 0  # "e..o"
    scripts = {_script_of(ch) for ch in core if ch.isalpha()}
    if len(scripts) > 1:
        return 0  # Latin and Cyrillic letters in one word
    if scripts == {"latin"} and any(ch.isupper() and core[i - 1].islower() for i, ch in enumerate(core) if i):
        return 0  # "TransIated"
    return 1


def _script_of(ch):
    name = unicodedata.name(ch, "")
    for script in ("LATIN", "ARABIC", "CYRILLIC", "HEBREW", "GREEK", "CJK", "HIRAGANA", "KATAKANA", "HANGUL",
                   "THAI", "DEVANAGARI"):
        if name.startswith(script):
            return script.lower()
    return name.split(" ")[0].lower()


def line_quality(line):
    scores = [q for q in (word_quality(word) for word, _ in line.words) if q is not None]
    return sum(scores) / len(scores) if scores else 0.5


def _overlap(a, b):
    """Two readings of the same line: on the same height, and side by side."""
    tall = min(a.h, b.h)
    vertical = min(a.bottom, b.bottom) - max(a.y, b.y)
    across = min(a.right, b.right) - max(a.x, b.x)
    return tall > 0 and vertical > 0.5 * tall and across > 0


def best_lines(readings):
    """For every line on the picture, the reading that looks most like words; `readings` holds one list of
    lines per recognizer, the main one (the Windows display language) first, which wins a tie."""
    candidates = []
    for order, lines in enumerate(readings):
        for line in lines:
            letters = sum(ch.isalnum() for word, _ in line.words for ch in word)
            candidates.append((round(line_quality(line), 1), letters, -order, line))
    candidates.sort(key=lambda c: c[:3], reverse=True)
    chosen = []
    for quality, letters, order, line in candidates:
        if order != 0 and (quality < 0.8 or letters < 2.5 * len(line.words)):
            continue  # a line only another alphabet's recognizer saw: kept only when it clearly reads as words
        if not any(_overlap(line.box, other.box) for other in chosen):
            chosen.append(line)
    return sorted(chosen, key=lambda line: (line.box.y, line.box.x))


@dataclass
class Block:
    """Lines that belong together (a paragraph, a menu item, a caption): translated as one text."""
    lines: list = field(default_factory=list)
    translation: str = ""
    background: tuple = (255, 255, 255)
    foreground: tuple = (0, 0, 0)

    @property
    def box(self):
        box = self.lines[0].box
        for line in self.lines[1:]:
            box = box.united(line.box)
        return box

    @property
    def text(self):
        return " ".join(line.text for line in self.lines)

    @property
    def line_height(self):
        return sum(line.box.h for line in self.lines) / len(self.lines)


def available_languages():
    """BCP-47 tags of the OCR languages installed in Windows, e.g. ['ar-SA', 'en-US']."""
    try:
        from winrt.windows.media.ocr import OcrEngine
        return [str(lang.language_tag) for lang in OcrEngine.available_recognizer_languages]
    except Exception:
        log.exception("Windows OCR is not available")
        return []


def split_columns(line, gap_factor=2.2):
    """Cut a line where its words are far apart: two columns, a menu bar, a toolbar."""
    parts, current = [], [line.words[0]]
    for word in line.words[1:]:
        previous, box = current[-1][1], word[1]
        height = max(previous.h, box.h, 1)
        gap = max(box.x - previous.right, previous.x - box.right)  # right-to-left text runs leftwards
        if gap > gap_factor * height:
            parts.append(Line(current))
            current = []
        current.append(word)
    parts.append(Line(current))
    return parts


DASHES = "-–—‐−"


def spoken_line(text):
    """A subtitle line that starts with a dash: one person of a dialogue speaks, never joined to the line above."""
    return text.lstrip()[:1] in DASHES and len(text.strip()) > 1


def group_blocks(lines):
    """Join lines that read as one paragraph: same size, close below each other, overlapping sideways."""
    pieces = sorted((part for line in lines if line.words for part in split_columns(line)),
                    key=lambda line: (line.box.y, line.box.x))
    blocks = []
    for line in pieces:
        box = line.box
        if spoken_line(line.text):
            blocks.append(Block([line]))
            continue
        for block in reversed(blocks[-8:]):  # only blocks just above can continue
            last = block.lines[-1].box
            height = block.line_height
            same_size = 0.7 <= box.h / max(height, 1) <= 1.45
            close_below = -0.3 * height <= box.y - last.bottom <= 0.9 * height
            overlap = min(box.right, last.right) - max(box.x, last.x)
            if same_size and close_below and overlap > 0.3 * min(box.w, last.w):
                block.lines.append(line)
                break
        else:
            blocks.append(Block([line]))
    return blocks


def _ideographic(ch):
    """Chinese, Japanese and Korean characters: Windows reads each one as a word of its own."""
    return 0x2E80 <= ord(ch) <= 0x9FFF or 0xAC00 <= ord(ch) <= 0xD7AF or 0xF900 <= ord(ch) <= 0xFAFF


def worth_translating(text):
    """Skip numbers, prices, times and single symbols (they read the same in every language), and what
    Windows "read" in a picture, an icon or a pattern: mostly symbols, or a row of stray single letters."""
    chars = [ch for ch in text if not ch.isspace()]
    letters = sum(1 for ch in chars if ch.isalpha())
    if letters < 2 or letters < 0.45 * len(chars):
        return False
    if any(_ideographic(ch) for ch in chars):
        return True
    words = text.split()
    singles = sum(1 for w in words if len(w) == 1 and w not in ("a", "A", "I", "y", "o", "e", "&"))
    return not (len(words) >= 3 and singles > 0.6 * len(words))


class Reader:
    """Reads a QImage. One per worker thread; `language` is a tag from available_languages() or AUTO."""

    def __init__(self, language=AUTO, skip=()):
        """`skip`: language codes ("ar") whose alphabet need not be read by an extra recognizer: text already in
        the language translated into is left as it is (and Windows' Arabic recognizer is slow)."""
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine

        engine = None
        if language != AUTO:
            engine = OcrEngine.try_create_from_language(Language(language))
        automatic = engine is None
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError("Windows has no text recognition (OCR) language installed")
        self._engine = engine
        self.language = str(engine.recognizer_language.language_tag)
        # Automatic: every installed language with another alphabet reads too, and each line keeps the reading
        # that looks most like words — an English recognizer turns Arabic into "öJPJl" or misses it altogether
        self._engines = [engine]
        if automatic:
            alphabets = {tag_script(self.language)} | {tag_script(code) for code in skip if code}
            for lang in OcrEngine.available_recognizer_languages:
                tag = str(lang.language_tag)
                if tag_script(tag) not in alphabets:
                    other = OcrEngine.try_create_from_language(lang)
                    if other is not None:
                        self._engines.append(other)
                        alphabets.add(tag_script(tag))
        self.languages = [str(e.recognizer_language.language_tag) for e in self._engines]
        self.max_dimension = int(OcrEngine.max_image_dimension)
        self._loop = asyncio.new_event_loop()

    def close(self):
        self._loop.close()

    def read(self, image):
        """Lines found in `image` (a QImage); boxes are in the image's own pixels."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter

        scale = 1.0
        if image.width() < SCALE_BELOW:
            scale = min(MAX_SCALE, self.max_dimension / max(image.width(), image.height(), 1))
        scale = max(scale, 1.0)
        if scale > 1.0:
            image = image.scaled(int(image.width() * scale), int(image.height() * scale),
                                 Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        image = image.convertToFormat(QImage.Format_ARGB32)  # BGRA in memory on Windows
        width, height = image.width(), image.height()
        if max(width, height) > self.max_dimension:
            raise ValueError("The area is too large to read")
        writer = DataWriter()
        writer.write_bytes(bytes(image.constBits())[: width * height * 4])
        bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8,
                                                        width, height)
        readings = []
        for engine in self._engines:
            result = self._loop.run_until_complete(self._recognize(engine, bitmap))
            rtl = tag_script(str(engine.recognizer_language.language_tag)) in RTL_SCRIPTS
            lines = []
            for line in result.lines:
                words = []
                for word in line.words:
                    r = word.bounding_rect
                    words.append((str(word.text), Box(r.x / scale, r.y / scale, r.width / scale, r.height / scale)))
                if words:
                    lines.append(Line(words, rtl))
            readings.append(lines)
        return readings[0] if len(readings) == 1 else best_lines(readings)

    @staticmethod
    async def _recognize(engine, bitmap):
        return await engine.recognize_async(bitmap)


RTL_SCRIPTS = {"arabic", "hebrew"}
_TAG_SCRIPTS = {
    "ar": "arabic", "fa": "arabic", "ur": "arabic", "ps": "arabic", "he": "hebrew", "yi": "hebrew",
    "ru": "cyrillic", "uk": "cyrillic", "bg": "cyrillic", "be": "cyrillic", "kk": "cyrillic", "mk": "cyrillic",
    "sr": "cyrillic", "el": "greek", "zh": "han", "ja": "japanese", "ko": "hangul", "th": "thai",
    "hi": "devanagari",
}


def tag_script(tag):
    """The alphabet of an OCR language tag ("ar-SA" -> "arabic", "en-US" -> "latin"; "sr-Latn" -> "latin")."""
    if "latn" in tag.lower():
        return "latin"
    return _TAG_SCRIPTS.get(tag.split("-")[0].lower(), "latin")

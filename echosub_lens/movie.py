# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: all the text of a movie, as a subtitle file (.srt) that can then be translated.

Three places the text can come from:
  * the subtitles inside the movie file (a text track is read as it is; a picture track — Blu-ray or DVD —
    is read with Windows' own text recognition),
  * what is said: speech recognition (faster-whisper) on the movie's sound, on the graphics card when it can,
  * the text burned into the picture: a frame every half second, read with Windows' text recognition.

Every function takes progress(fraction 0..1, text) and should_cancel() callbacks, runs in a worker thread
and writes a standard SRT file. PyAV (installed with faster-whisper) reads every movie format with FFmpeg's
own libraries, so no ffmpeg.exe is needed.
"""
import gc
import logging
import os
import re
import textwrap
import time
from dataclasses import dataclass

from . import lens_ocr

log = logging.getLogger(__name__)

MOVIE_EXTENSIONS = (".mkv", ".mp4", ".avi", ".mov", ".webm", ".m4v", ".wmv", ".ts", ".flv")
OPEN_FILTER = ("Movies (" + " ".join("*" + e for e in MOVIE_EXTENSIONS) + ");;All files (*)")

TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text", "microdvd", "subviewer", "subviewer1",
               "jacosub", "sami", "realtext", "stl", "pjs", "mpl2", "vplayer", "eia_608"}
BITMAP_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}

# Speech recognition models (faster-whisper names), best first
MODELS = {
    "large-v3-turbo": "Large v3 Turbo — the best (about 1.6 GB)",
    "medium": "Medium — good, slower (about 1.5 GB)",
    "small": "Small — fast, less accurate (about 480 MB)",
    "base": "Base — fastest, least accurate (about 150 MB)",
}
DEFAULT_MODEL = "large-v3-turbo"
SPOKEN_LANGUAGES = ["en", "ar", "fr", "de", "es", "it", "pt", "ru", "tr", "fa", "ur", "hi", "zh", "ja", "ko", "nl",
                    "pl", "sv", "id", "he", "uk", "el"]

MAX_LINE = 42          # characters on a subtitle line
MAX_LINES = 2          # lines on the screen at once
SIMILAR = 90           # rapidfuzz ratio: two readings this close are the same subtitle read a little differently
LAST_CUE_SECONDS = 4.0  # how long a subtitle with no end stays
MAX_CUE_SECONDS = 7.0   # a subtitle longer than this is cut where a word ends
PAUSE_SECONDS = 1.0     # a pause this long between two words starts a new subtitle
REGIONS = {"bottom": "The bottom third (where subtitles are)", "whole": "The whole picture"}

# ISO 639-2 tags found in movie files -> the two-letter codes Lens uses
LANGUAGE_TAGS = {"eng": "en", "ara": "ar", "fre": "fr", "fra": "fr", "ger": "de", "deu": "de", "spa": "es",
                 "ita": "it", "por": "pt", "rus": "ru", "tur": "tr", "chi": "zh", "zho": "zh", "jpn": "ja",
                 "kor": "ko", "per": "fa", "fas": "fa", "hin": "hi", "urd": "ur", "dut": "nl", "nld": "nl",
                 "pol": "pl", "swe": "sv", "ind": "id", "heb": "he", "ukr": "uk", "gre": "el", "ell": "el"}


class Canceled(Exception):
    pass


class Unsupported(Exception):
    """The movie has no such text, or it is in a form Lens cannot read: the message says so in plain words."""


@dataclass
class Cue:
    start: float   # seconds
    end: float
    text: str


@dataclass
class Track:
    """A subtitle stream inside a movie file."""
    index: int          # the stream's index in the file
    language: str       # its language tag ("eng"), or ""
    title: str
    codec: str
    is_text: bool

    @property
    def code(self):
        """The two-letter language code ("en"), or "" when the track does not say."""
        tag = (self.language or "").lower()
        if tag in ("", "und"):
            return ""
        return LANGUAGE_TAGS.get(tag, tag)

    @property
    def label(self):
        from . import languages

        parts = [languages.name(self.code) if self.code else "Unknown language"]
        if self.title and self.title.lower() != parts[0].lower():
            parts.append(f"“{self.title}”")
        kind = "text" if self.is_text else ("pictures — read with text recognition" if self.codec in BITMAP_CODECS
                                            else f"{self.codec}, not readable")
        return f"#{self.index}: " + " ".join(parts) + f" ({kind})"


@dataclass
class Result:
    path: str
    cues: int
    note: str = ""      # e.g. "on the graphics card", "language: English"
    language: str = ""  # the language the subtitles are in, when it is known ("en")


def _nothing(*_):
    return None


def _never():
    return False


# ---- SRT ---------------------------------------------------------------------------
def srt_time(seconds):
    """3723.5 -> '01:02:03,500'."""
    ms = max(0, int(round(seconds * 1000)))
    hours, ms = divmod(ms, 3600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def srt_text(cues):
    """Numbered cues in time order; empty ones are left out, and each one lasts at least a moment."""
    out, number = [], 0
    for cue in sorted(cues, key=lambda c: c.start):
        text = "\n".join(line.strip() for line in cue.text.strip().splitlines() if line.strip())
        if not text:
            continue
        number += 1
        end = max(cue.end, cue.start + 0.3)
        out.append(f"{number}\n{srt_time(cue.start)} --> {srt_time(end)}\n{text}\n")
    return "\n".join(out)


def write_srt(cues, path):
    """Writes the file (UTF-8 with BOM: every player and editor reads Arabic and other scripts right).
    Returns how many cues it holds."""
    text = srt_text(cues)
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="\n") as f:
        f.write(text)
    return text.count(" --> ")


def output_path(movie, language=""):
    """D:/films/Movie.mkv -> D:/films/Movie.en.srt (or Movie.text.srt when the language is not known)."""
    stem = os.path.splitext(movie)[0]
    return f"{stem}.{language or 'text'}.srt"


SRT_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def _seconds(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000


def read_srt(path):
    """The cues of an SRT file (any encoding the file translator reads), in the file's order."""
    from .lens_files import read_text

    cues = []
    for block in re.split(r"\n\s*\n", read_text(path).replace("\r\n", "\n").replace("\r", "\n")):
        lines = block.strip("\n").split("\n")
        for i, line in enumerate(lines[:2]):
            match = SRT_TIME.search(line)
            if match:
                g = match.groups()
                text = "\n".join(lines[i + 1:]).strip()
                cues.append(Cue(_seconds(*g[:4]), _seconds(*g[4:]), text))
                break
    return cues


def translated_path(path, target):
    """Where the translation goes: Movie.mkv or Movie.en.srt -> Movie.ar.srt; subs.srt -> subs.ar.srt.
    The same path back means the subtitles are already in that language."""
    from . import languages

    stem, ext = os.path.splitext(path)
    if ext.lower() == ".srt":
        base, last = os.path.splitext(stem)
        tag = last[1:].lower()
        if base and (tag == "text" or tag in languages.LANGUAGES or tag.split("-")[0] in LANGUAGE_TAGS.values()):
            stem = base
    return f"{stem}.{target}.srt"


TRANSLATE_BATCH = 40   # cues sent to the translator at once: big enough to be fast, small enough to show progress


def _dialogue(lines):
    """Lines of a dialogue ("- Where to?" / "- Home."): each speaker keeps its own line and dash."""
    return len(lines) > 1 and any(lens_ocr.spoken_line(line) for line in lines)


def _pieces(text):
    """What is sent to the translator for one cue: its lines as one sentence, or a dialogue line by line."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    if _dialogue(lines):
        return lines
    return [" ".join(lines)]


def _rebuilt(text, done):
    from .lens_overlay import split_like
    from .lens_scanner import keep_dash

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return text
    if _dialogue(lines):
        return "\n".join(keep_dash(line, done.get(line) or line) for line in lines)
    translation = (done.get(" ".join(lines)) or " ".join(lines)).strip()
    return split_like(translation, len(lines)) if len(lines) > 1 else translation


def translate_cues(cues, translator, target, progress=None, should_cancel=None, batch=TRANSLATE_BATCH):
    """The cues translated into `target` with `translator` (anything with translate_many(texts, target)),
    their times kept. A cue of two lines stays two lines; a dialogue keeps a line and a dash per speaker.
    The same text is translated once. progress(fraction, text) hears how far it is."""
    progress, should_cancel = progress or _nothing, should_cancel or _never
    texts = list(dict.fromkeys(piece for cue in cues for piece in _pieces(cue.text)))
    done = {}
    total = len(cues)
    progress(0.0, f"Translating 0 of {total} subtitles…")
    for start in range(0, len(texts), batch):
        if should_cancel():
            raise Canceled()
        chunk = texts[start:start + batch]
        done.update(zip(chunk, translator.translate_many(chunk, target)))
        share = len(done) / len(texts)
        progress(min(share, 0.99), f"Translating {int(round(total * share))} of {total} subtitles…")
    if should_cancel():
        raise Canceled()
    return [Cue(cue.start, cue.end, _rebuilt(cue.text, done)) for cue in cues]


def translate_srt(path, out_path, translator, target, progress=None, should_cancel=None):
    """Translates an SRT file into another one (numbers and times kept). Returns how many cues it holds."""
    cues = translate_cues(read_srt(path), translator, target, progress, should_cancel)
    (progress or _nothing)(1.0, "Saving the translated subtitles…")
    return write_srt(cues, out_path)


# ---- cleaning and shaping text -----------------------------------------------------------
ASS_TAG = re.compile(r"\{\\[^}]*\}")
HTML_TAG = re.compile(r"</?(?:i|b|u|s|font)(?:\s[^>]*)?>", re.IGNORECASE)


def clean_ass(text):
    """'{\\an8}{\\i1}Hello{\\i0}\\Nworld' -> 'Hello\\nworld': no style codes, real line breaks."""
    text = ASS_TAG.sub("", text)
    text = HTML_TAG.sub("", text)
    text = text.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.replace("\r", "").split("\n")]
    return "\n".join(line for line in lines if line)


def ass_dialogue_text(event):
    """The text of an ASS event as FFmpeg gives it: 'ReadOrder,Layer,Style,Name,MarginL,MarginR,MarginV,
    Effect,Text' (or an old-style 'Dialogue: Layer,Start,End,Style,...,Text')."""
    if event.startswith("Dialogue:"):
        return event.split(",", 9)[-1]
    parts = event.split(",", 8)
    return parts[8] if len(parts) == 9 else event


def wrap_lines(text, width=MAX_LINE):
    lines = []
    for paragraph in text.splitlines():
        lines += textwrap.wrap(paragraph, width, break_long_words=False, break_on_hyphens=False) or []
    return lines


def words_to_cues(words, width=MAX_LINE, max_lines=MAX_LINES, max_seconds=MAX_CUE_SECONDS, pause=PAUSE_SECONDS):
    """[(start, end, word)] with their own times (Whisper's word timestamps) -> cues that each fit on
    `max_lines` lines and end where a sentence ends, the speaker pauses, or it would stay too long."""
    cues, current = [], []

    def close():
        if current:
            text = "\n".join(wrap_lines("".join(w for _, _, w in current).strip(), width))
            if text:
                cues.append(Cue(current[0][0], current[-1][1], text))
            current.clear()

    for start, end, word in words:
        if current:
            text = "".join(w for _, _, w in current + [(start, end, word)]).strip()
            if (len(wrap_lines(text, width)) > max_lines or start - current[-1][1] > pause
                    or end - current[0][0] > max_seconds):
                close()
        current.append((start, end, word))
        if word.strip()[-1:] in ".?!…。？！" and len("".join(w for _, _, w in current)) > 12:
            close()
    close()
    return cues


def split_cue(cue, width=MAX_LINE, max_lines=MAX_LINES):
    """A long sentence becomes several cues of at most `max_lines` lines of about `width` characters,
    the time shared out by length (what is said longer stays longer)."""
    lines = wrap_lines(cue.text, width)
    if len(lines) <= max_lines:
        return [Cue(cue.start, cue.end, "\n".join(lines))] if lines else []
    chunks = ["\n".join(lines[i:i + max_lines]) for i in range(0, len(lines), max_lines)]
    total = sum(len(c) for c in chunks)
    cues, start = [], cue.start
    span = max(cue.end - cue.start, 0.0)
    for i, chunk in enumerate(chunks):
        end = cue.end if i == len(chunks) - 1 else start + span * len(chunk) / total
        cues.append(Cue(start, end, chunk))
        start = end
    return cues


def _same(a, b, similar=SIMILAR):
    if a == b:
        return True
    from rapidfuzz import fuzz

    return fuzz.ratio(a, b) >= similar


def merge_readings(readings, every, similar=SIMILAR, gap=1):
    """[(seconds, text)] read from frames `every` seconds apart -> cues. The same text (or nearly: OCR reads
    a letter differently now and then) seen on frames in a row is one cue, from the first frame it was seen
    to the next frame after the last; a text missed on up to `gap` frames does not cut it in two. The
    spelling read most often wins."""
    cues, current = [], None   # current: {"start", "last", "texts": {text: count}, "missed"}

    def close():
        if current is not None:
            best = max(current["texts"].items(), key=lambda kv: (kv[1], len(kv[0])))[0]
            cues.append(Cue(current["start"], current["last"] + every, best))

    for seconds, text in readings:
        text = (text or "").strip()
        if current is not None:
            known = max(current["texts"], key=current["texts"].get)
            if text and _same(text, known, similar):
                current["texts"][text] = current["texts"].get(text, 0) + 1
                current["last"] = seconds
                current["missed"] = 0
                continue
            if not text and current["missed"] < gap:
                current["missed"] += 1
                continue
            close()
            current = None
        if text:
            current = {"start": seconds, "last": seconds, "texts": {text: 1}, "missed": 0}
    close()
    return cues


# ---- what is inside the file ---------------------------------------------------------
def _open(path):
    import av

    try:
        return av.open(path)
    except Exception as e:
        raise Unsupported(f"This file could not be opened as a movie: {e}") from e


def _duration(container, stream=None):
    if stream is not None and stream.duration and stream.time_base:
        return float(stream.duration * stream.time_base)
    if container.duration:
        return container.duration / 1_000_000
    return 0.0


def subtitle_tracks(path):
    """The subtitle streams inside the movie file."""
    tracks = []
    with _open(path) as container:
        for stream in container.streams.subtitles:
            ctx = stream.codec_context
            codec = (ctx.name if ctx is not None else "") or ""
            meta = stream.metadata or {}
            title = meta.get("title", "") or meta.get("handler_name", "").strip()
            if title.lower().endswith("handler"):
                title = ""          # MP4's "SubtitleHandler" says nothing
            tracks.append(Track(stream.index, stream.language or meta.get("language", ""), title, codec,
                                codec in TEXT_CODECS))
    return tracks


def has_video(path):
    with _open(path) as container:
        return bool(container.streams.video)


def extract_track(path, stream_index, out_path, progress=None, should_cancel=None, ocr_language=lens_ocr.AUTO,
                  reader=None):
    """Writes the subtitle stream `stream_index` as an SRT file. A text track is copied (style codes left out);
    a picture track (Blu-ray PGS, DVD) is read with Windows' text recognition."""
    progress, should_cancel = progress or _nothing, should_cancel or _never
    with _open(path) as container:
        stream = next((s for s in container.streams if s.index == stream_index), None)
        if stream is None or stream.type != "subtitle":
            raise Unsupported(f"The movie has no subtitle track #{stream_index}")
        codec = stream.codec_context.name if stream.codec_context is not None else ""
        tag = (stream.language or (stream.metadata or {}).get("language", "") or "").lower()
        language = "" if tag in ("", "und") else LANGUAGE_TAGS.get(tag, tag)
        bitmap = codec in BITMAP_CODECS
        if codec not in TEXT_CODECS and not bitmap:
            raise Unsupported(f"Lens cannot read this kind of subtitles ({codec or 'unknown'})")
        own_reader = None
        if bitmap and reader is None:
            reader = own_reader = _make_reader(ocr_language)
        try:
            cues = _read_track(container, stream, bitmap, reader, progress, should_cancel)
        finally:
            if own_reader is not None:
                own_reader.close()
    if not cues:
        raise Unsupported("This subtitle track is empty")
    progress(1.0, "Saving the subtitles…")
    return Result(out_path, write_srt(cues, out_path), language=language)


def _make_reader(language):
    try:
        return lens_ocr.Reader(language)
    except Exception as e:
        raise Unsupported(f"Windows text recognition is not available: {e}") from e


def _read_track(container, stream, bitmap, reader, progress, should_cancel):
    duration = _duration(container)
    base = stream.time_base
    ctx = stream.codec_context
    cues, open_cue = [], None   # open_cue: a picture subtitle shown until the next one (or a clear) comes
    what = "Reading the picture subtitles" if bitmap else "Reading the subtitles"
    last_report = 0.0
    for packet in container.demux(stream):
        if should_cancel():
            raise Canceled()
        if packet.pts is None:
            continue
        start = float(packet.pts * base)
        if open_cue is not None and open_cue.end <= open_cue.start:
            open_cue.end = start
            open_cue = None
        end = start + float(packet.duration * base) if packet.duration else 0.0
        texts, shown_ms = [], 0
        if hasattr(ctx, "decode2"):
            sset = ctx.decode2(packet)
            rects = list(sset.rects) if sset is not None else []
            shown_ms = getattr(sset, "end_display_time", 0) if sset is not None else 0
        else:
            rects = packet.decode()
        for rect in rects:
            text = _rect_text(rect, reader)
            if text:
                texts.append(text)
        if not end and 0 < shown_ms < 600_000:
            end = start + shown_ms / 1000
        if texts:
            cue = Cue(start, end, "\n".join(texts))
            cues.append(cue)
            if not end:
                open_cue = cue
        if duration and time.monotonic() - last_report > 0.2:
            last_report = time.monotonic()
            progress(min(start / duration, 0.99), f"{what}… {len(cues)} so far")
    if open_cue is not None and open_cue.end <= open_cue.start:
        open_cue.end = open_cue.start + LAST_CUE_SECONDS
    return cues


def _rect_text(rect, reader):
    kind = type(rect).__name__
    if kind == "BitmapSubtitle":
        return bitmap_text(rect, reader)
    dialogue = getattr(rect, "dialogue", None)
    if dialogue:
        return clean_ass(dialogue.decode("utf-8", "replace") if isinstance(dialogue, bytes) else str(dialogue))
    ass = getattr(rect, "ass", None)
    if ass:
        ass = ass.decode("utf-8", "replace") if isinstance(ass, bytes) else str(ass)
        return clean_ass(ass_dialogue_text(ass))
    text = getattr(rect, "text", None)
    if text:
        return clean_ass(text.decode("utf-8", "replace") if isinstance(text, bytes) else str(text))
    return ""


def bitmap_gray(indices, palette, width, height, stride):
    """A picture subtitle (palette indices + 0xAARRGGBB palette) as dark text on white, the way Windows reads
    text best: the light letters over their dark outline become dark letters on paper."""
    import numpy as np

    pixels = np.frombuffer(indices, np.uint8)[: stride * height].reshape(height, stride)[:, :width]
    colors = np.frombuffer(palette, np.uint32)
    if colors.size < 256:
        colors = np.concatenate([colors, np.zeros(256 - colors.size, np.uint32)])
    alpha = ((colors >> 24) & 255).astype(np.float32) / 255
    light = (0.299 * ((colors >> 16) & 255) + 0.587 * ((colors >> 8) & 255) + 0.114 * (colors & 255))
    gray = 255 - (light * alpha)    # over black, then turned over: light text -> dark text on white
    return gray.astype(np.uint8)[pixels]


def bitmap_text(rect, reader):
    if reader is None:
        return ""
    planes = rect.planes
    if len(planes) < 2 or not rect.width or not rect.height:
        return ""
    indices, palette = bytes(planes[0]), bytes(planes[1])
    stride = max(rect.width, len(indices) // rect.height)
    gray = bitmap_gray(indices, palette, rect.width, rect.height, stride)
    return read_gray(reader, gray)


def read_gray(reader, gray, pad=16):
    """Reads a grayscale numpy picture with Windows OCR: its lines, top to bottom."""
    import numpy as np
    from PySide6.QtGui import QImage

    gray = np.pad(gray, pad, constant_values=255)
    gray = np.ascontiguousarray(gray)
    h, w = gray.shape
    image = QImage(gray.data, w, h, w, QImage.Format_Grayscale8).copy()
    lines = reader.read(image)
    return "\n".join(line.text for line in sorted(lines, key=lambda line: line.box.y)).strip()


# ---- what is said ------------------------------------------------------------------------
def pick_compute_type(device):
    """int8 on the processor; on the graphics card the fastest it has (a GTX 10xx has no fast float16)."""
    if device == "cpu":
        return "int8"
    import ctranslate2

    supported = ctranslate2.get_supported_compute_types("cuda")
    for compute in ("int8_float16", "int8", "float32"):
        if compute in supported:
            return compute
    return "float32"


def load_whisper(folder, device="cuda"):
    """(WhisperModel, device used): the graphics card when it can, else the processor."""
    from faster_whisper import WhisperModel

    if device == "cuda":
        try:
            import ctranslate2

            from . import cuda_libs

            cuda_libs.make_visible()
            if ctranslate2.get_cuda_device_count() > 0:
                return WhisperModel(folder, device="cuda", compute_type=pick_compute_type("cuda")), "cuda"
        except Exception:
            log.exception("The speech model could not run on the graphics card; using the processor")
    threads = max(2, min(8, (os.cpu_count() or 4) - 1))
    return WhisperModel(folder, device="cpu", compute_type="int8", cpu_threads=threads), "cpu"


def release_whisper(model):
    """Lets go of the model now (its graphics-card memory too), not whenever Python gets round to it."""
    inner = getattr(model, "model", None)
    try:
        if inner is not None and hasattr(inner, "unload_model"):
            inner.unload_model()
    except Exception:
        log.exception("Could not unload the speech model")
    del inner, model
    gc.collect()


def transcribe(path, out_path, model=DEFAULT_MODEL, language=None, device="cuda", progress=None,
               should_cancel=None, downloader=None, loader=None):
    """Speech recognition on the movie's sound (faster-whisper reads the sound of any movie itself, with
    PyAV), only where someone speaks (voice detection). `loader(folder, device)` -> (model, device)
    replaces load_whisper in tests."""
    progress, should_cancel = progress or _nothing, should_cancel or _never
    loader = loader or load_whisper
    if downloader is None:
        from .downloads import ModelDownloader

        downloader = ModelDownloader(on_event=lambda e: _download_progress(e, progress), should_cancel=should_cancel)
    progress(0.0, "Getting the speech recognition model ready…")
    folder = downloader.whisper(model)
    if should_cancel():
        raise Canceled()
    progress(0.0, "Loading the speech recognition model…")
    whisper, used = loader(folder, device)
    try:
        try:
            cues, info = _transcribe(whisper, path, language, used, progress, should_cancel)
        except (Canceled, Unsupported):
            raise
        except Exception:
            if used != "cuda":
                raise
            log.exception("Speech recognition failed on the graphics card; trying the processor")
            release_whisper(whisper)
            whisper = None
            progress(0.0, "The graphics card could not do it — loading the model on the processor…")
            whisper, used = loader(folder, "cpu")
            cues, info = _transcribe(whisper, path, language, used, progress, should_cancel)
    finally:
        if whisper is not None:
            release_whisper(whisper)
    if not cues:
        raise Unsupported("No speech was found in this movie")
    progress(1.0, "Saving the subtitles…")
    from . import languages

    where = "on the graphics card" if used == "cuda" else "on the processor"
    spoken = getattr(info, "language", "") or language or ""
    note = f"{where}, {languages.name(spoken)}" if spoken else where
    return Result(out_path, write_srt(cues, out_path), note, spoken)


def _download_progress(event, progress):
    if event.get("state") in ("start", "progress") and event.get("total"):
        share = event["done"] / event["total"]
        progress(0.0, f"Downloading the {event['title'][:1].lower() + event['title'][1:]}… {share:.0%}")


def _transcribe(whisper, path, language, device, progress, should_cancel):
    where = "graphics card" if device == "cuda" else "processor"
    progress(0.0, f"Reading the sound of the movie and finding speech (on the {where})…")
    try:
        segments, info = whisper.transcribe(path, language=language or None, vad_filter=True, beam_size=5,
                                            condition_on_previous_text=False, word_timestamps=True)
    except Exception as e:
        if "Invalid data" in str(e) or "audio" in str(e).lower():
            raise Unsupported(f"The sound of this movie could not be read: {e}") from e
        raise
    duration = float(getattr(info, "duration", 0) or 0)
    cues = []
    for segment in segments:
        if should_cancel():
            raise Canceled()
        text = (segment.text or "").strip()
        words = getattr(segment, "words", None)
        if words:  # each word's own time: subtitles cut where sentences end, on time
            cues += words_to_cues([(w.start, w.end, w.word) for w in words])
        elif text:
            cues += split_cue(Cue(segment.start, segment.end, text))
        if duration:
            progress(min(segment.end / duration, 0.99),
                     f"Recognizing speech on the {where}… {srt_time(segment.end)[:8]} of {srt_time(duration)[:8]}")
    return cues, info


# ---- text burned into the picture ---------------------------------------------------------
MIN_TEXT_PIXELS = 60     # fewer light pixels than this: no subtitle-like text on the frame
SAME_MASK = 0.04         # light pixels that moved, as a share: below it the subtitle did not change


def crop(rgb, region):
    """The part of a frame (numpy H×W×3) where the text is looked for."""
    if region == "bottom":
        return rgb[rgb.shape[0] * 2 // 3:, :, :]
    return rgb


def light_text_mask(rgb):
    """Where subtitle-coloured text is: white or light grey, or yellow (numpy H×W×3 -> H×W bool)."""
    import numpy as np

    rgb = rgb.astype(np.int16)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    low = np.minimum(np.minimum(r, g), b)
    high = np.maximum(np.maximum(r, g), b)
    white = (low > 170) & (high - low < 60)
    yellow = (r > 170) & (g > 170) & (b < 130) & (np.abs(r - g) < 50)
    return white | yellow


def _blocks_text(lines):
    """Paragraphs of what was read, top to bottom, a line break between lines; junk left out."""
    parts = []
    for block in sorted(lens_ocr.group_blocks(lines), key=lambda b: (b.box.y, b.box.x)):
        if lens_ocr.worth_translating(block.text):
            parts.append("\n".join(line.text for line in block.lines))
    return "\n".join(parts)


def frame_text(reader, rgb):
    """The text Windows reads on a picture as it is (numpy H×W×3)."""
    import numpy as np
    from PySide6.QtGui import QImage

    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    image = QImage(rgb.data, w, h, w * 3, QImage.Format_RGB888).copy()
    return _blocks_text(reader.read(image))


def mask_text(reader, mask):
    """The text of the light letters alone, drawn dark on white: nothing of the picture behind them is left
    for the text recognition to mistake for letters."""
    import numpy as np
    from PySide6.QtGui import QImage

    gray = np.ascontiguousarray(np.where(mask, 0, 255).astype(np.uint8))
    h, w = gray.shape
    image = QImage(gray.data, w, h, w, QImage.Format_Grayscale8).copy()
    return _blocks_text(reader.read(image))


def _thumb(rgb):
    return rgb[::8, ::8, :].astype("int16")


class BurnedReader:
    """Reads one sampled frame after another, and reads again only what changed."""

    def __init__(self, reader):
        self.reader = reader
        self.mask, self.mask_result = None, ""
        self.thumb, self.thumb_result = None, ""
        self.ocr_calls = 0

    def read(self, rgb):
        import numpy as np

        mask = light_text_mask(rgb)
        count = int(mask.sum())
        if count >= MIN_TEXT_PIXELS:
            if self.mask is not None and self.mask.shape == mask.shape:
                moved = int(np.count_nonzero(mask ^ self.mask))
                if moved <= SAME_MASK * max(count, int(self.mask.sum())) and self.mask_result:
                    return self.mask_result      # the same light letters: the same subtitle
            self.ocr_calls += 1
            text = mask_text(self.reader, mask)
            self.mask, self.mask_result = mask, text
            if text:
                return text
        else:
            self.mask, self.mask_result = None, ""
        # no light subtitle: the text of the picture as it is (coloured or dark text), unless it did not change
        thumb = _thumb(rgb)
        if self.thumb is not None and thumb.shape == self.thumb.shape and \
                float(np.abs(thumb - self.thumb).mean()) < 1.5:
            return self.thumb_result
        self.ocr_calls += 1
        self.thumb, self.thumb_result = thumb, frame_text(self.reader, rgb)
        return self.thumb_result


def read_burned(path, out_path, language=lens_ocr.AUTO, every=0.5, region="bottom", progress=None,
                should_cancel=None, reader=None):
    """Reads the text burned into the picture: a frame every `every` seconds, the `region` of it ("bottom"
    third or "whole"), read with Windows' text recognition; the same text on frames in a row is one cue.
    White and yellow subtitles are read from their letters alone (exact, even over a busy picture)."""
    progress, should_cancel = progress or _nothing, should_cancel or _never
    own_reader = None
    if reader is None:
        reader = own_reader = _make_reader(language)
    burned = BurnedReader(reader)
    readings = []
    try:
        with _open(path) as container:
            if not container.streams.video:
                raise Unsupported("This file has no picture")
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"            # decode on every core
            try:
                stream.codec_context.skip_frame = "NONREF"   # frames nothing else is built from are skipped
            except Exception:
                pass
            duration = _duration(container, stream)
            next_time, last_report, found = 0.0, 0.0, 0
            for frame in container.decode(stream):
                if should_cancel():
                    raise Canceled()
                seconds = frame.time
                if seconds is None or seconds + 1e-6 < next_time:
                    continue
                next_time = seconds + every
                text = burned.read(crop(frame.to_ndarray(format="rgb24"), region))
                if text and (not readings or not _same(text, readings[-1][1])):
                    found += 1
                readings.append((seconds, text))
                if time.monotonic() - last_report > 0.25:
                    last_report = time.monotonic()
                    share = min(seconds / duration, 0.99) if duration else 0.0
                    progress(share, f"Reading the text in the picture… {srt_time(seconds)[:8]}"
                                    + (f" of {srt_time(duration)[:8]}" if duration else "") + f", {found} found")
    finally:
        if own_reader is not None:
            own_reader.close()
    log.info("Burned-in text: %d frames read, %d with text recognition", len(readings), burned.ocr_calls)
    cues = merge_readings(readings, every)
    if not cues:
        raise Unsupported("No text was found in the picture")
    progress(1.0, "Saving the subtitles…")
    return Result(out_path, write_srt(cues, out_path))


def cli(args):
    """`EchoSubLens.exe --movie-to-srt MOVIE [--from inside|inside:N|speech|burned] [--out FILE] [--language L]
    [--to LANG [--translator KEY]]`: the subtitles of a movie without opening a window (a batch of movies, or a
    check that it all works); with `--to`, translated too, into MOVIE.LANG.srt next to them.
    Returns the exit code; what happened is written to Lens's log and to `--out`.log next to the file."""
    import argparse

    from PySide6.QtGui import QGuiApplication

    from . import lens_translate

    parser = argparse.ArgumentParser(prog="EchoSubLens.exe --movie-to-srt")
    parser.add_argument("movie")
    parser.add_argument("--from", dest="source", default="auto")
    parser.add_argument("--out", default="")
    parser.add_argument("--language", default="")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--to", default="", help="translate the subtitles into this language too (ar, fr…)")
    parser.add_argument("--translator", default="", choices=list(lens_translate.CHOICES),
                        help="who translates: google, bing, mymemory, deepl (needs your key, saved in Lens), "
                             "nllb-600m, nllb-1.3b (default: the one Lens uses)")
    options = parser.parse_args(args)
    app = QGuiApplication.instance() or QGuiApplication(["EchoSubLens"])  # text recognition works on Qt images
    source = options.source
    if source == "auto":
        text = [t for t in subtitle_tracks(options.movie) if t.is_text]
        source = f"inside:{text[0].index}" if text else "speech"
    out = options.out or output_path(options.movie, options.language or ("text" if source == "burned" else ""))
    started = time.monotonic()
    try:
        if source.startswith("inside"):
            index = int(source.split(":")[1]) if ":" in source else subtitle_tracks(options.movie)[0].index
            result = extract_track(options.movie, index, out)
        elif source == "speech":
            result = transcribe(options.movie, out, model=options.model, language=options.language or None)
        elif source == "burned":
            result = read_burned(options.movie, out, language=options.language or lens_ocr.AUTO)
        else:
            parser.error(f"unknown source {source!r}")
    except Exception as e:
        log.exception("Movie to subtitles failed")
        _cli_report(out, f"failed: {e}")
        return 1
    finally:
        del app
    report = f"{result.cues} subtitles in {time.monotonic() - started:.1f} s ({source}) {result.note}".strip()
    log.info("Movie to subtitles: %s -> %s", report, result.path)
    code = 0
    if options.to:
        try:
            report += "; " + _cli_translate(result, options.to, options.translator)
        except Exception as e:
            log.exception("Translating the movie subtitles failed")
            report += f"; translating failed: {e}"
            code = 1
    _cli_report(out, report)
    return code


def _cli_translate(result, target, choice):
    """`--to`: the subtitles just written, translated into `target`. Returns what happened, in words."""
    from . import config, cuda_libs, languages, lens_app, lens_translate

    out = translated_path(result.path, target)
    if result.language == target or os.path.abspath(out) == os.path.abspath(result.path):
        return f"already in {languages.name(target)}, not translated"
    base_cfg = config.load()
    saved = lens_app.load_config()
    key = lens_translate.resolve(choice or saved.get("translator", lens_translate.SAME), base_cfg)
    online = key in lens_translate.ONLINE
    device = "cpu"
    if not online and base_cfg.get("device", "cuda") != "cpu":
        cuda_libs.make_visible()
        device = "cuda" if cuda_libs.available() else "cpu"
    api_keys = saved.get("api_keys")  # the user's own keys (DeepL), saved on the Translator page
    api_keys = dict(api_keys) if isinstance(api_keys, dict) else {}
    translator = lens_translate.LensTranslator(key, device, api_keys=api_keys)
    started = time.monotonic()
    try:
        count = translate_srt(result.path, out, translator, target)
        # NLLB uses the processor when the graphics card cannot run it
        device = getattr(getattr(translator, "_backend", None), "device", device)
    finally:
        translator.release()
    backend = lens_translate.CHOICES.get(key, key).split(" — ")[0]
    where = "online" if online else ("on the graphics card" if device == "cuda" else "on the processor")
    text = (f"translated into {languages.name(target)} with {backend} ({where}): "
            f"{count} subtitles in {time.monotonic() - started:.1f} s -> {out}")
    log.info("Movie subtitles %s", text)
    return text


def _cli_report(out, text):
    try:
        with open(out + ".log", "w", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass

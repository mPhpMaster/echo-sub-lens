# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: Movie to subtitles — SRT writing, cleaning and shaping cues, and its window."""
import os
import shutil
import sys
import tempfile
import time
import unittest
from fractions import Fraction
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ECHOSUB_LENS_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_data"))

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from echosub_lens import lens_app, lens_movie_window, lens_ocr, lens_shortcuts, lens_ui, movie  # noqa: E402
from echosub_lens.movie import Cue  # noqa: E402

app = QApplication.instance() or QApplication([])

ASS_HEADER = (b"[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize\n"
              b"Style: Default,Arial,16\n\n[Events]\n"
              b"Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")


def make_movie(path, cues, codec="subrip"):
    """A tiny 3-second movie with a subtitle track, made with PyAV itself."""
    import av
    from av.subtitles.subtitle import SubtitleSet

    container = av.open(path, "w")
    video = container.add_stream("mpeg4", rate=10)
    video.width, video.height, video.pix_fmt = 64, 48, "yuv420p"
    subs = container.add_stream(codec)
    subs.time_base = subs.codec_context.time_base = Fraction(1, 1000)
    subs.codec_context.subtitle_header = ASS_HEADER
    subs.metadata["language"] = "eng"
    subs.metadata["title"] = "English"
    for i in range(30):
        frame = av.VideoFrame.from_ndarray(np.zeros((48, 64, 3), np.uint8), format="rgb24")
        frame.pts = i
        for packet in video.encode(frame):
            container.mux(packet)
    for packet in video.encode():
        container.mux(packet)
    for start, end, text in cues:
        packet = subs.codec_context.encode_subtitle(
            SubtitleSet.create(("0,0,Default,,0,0,0,," + text).encode(), 0, end - start, start))
        packet.stream = subs
        packet.pts = packet.dts = start
        packet.duration = end - start
        container.mux(packet)
    container.close()


class SrtTest(unittest.TestCase):
    def test_times(self):
        self.assertEqual(movie.srt_time(0), "00:00:00,000")
        self.assertEqual(movie.srt_time(3723.5), "01:02:03,500")
        self.assertEqual(movie.srt_time(59.9996), "00:01:00,000")
        self.assertEqual(movie.srt_time(-1), "00:00:00,000")

    def test_numbered_in_order_without_empty_cues(self):
        text = movie.srt_text([Cue(5, 6, "Second"), Cue(1, 2.25, " First \n\n line two "), Cue(3, 4, "  ")])
        self.assertEqual(text, "1\n00:00:01,000 --> 00:00:02,250\nFirst\nline two\n\n"
                               "2\n00:00:05,000 --> 00:00:06,000\nSecond\n")

    def test_written_as_utf8_with_bom_and_read_back_by_the_file_translator(self):
        from echosub_lens import lens_files

        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, "film.ar.srt")
        self.assertEqual(movie.write_srt([Cue(0, 1, "مرحبا"), Cue(1, 1, "زمن قصير")], path), 2)
        with open(path, "rb") as f:
            self.assertTrue(f.read().startswith(b"\xef\xbb\xbf1\n"))
        text = lens_files.read_text(path)
        self.assertIn("00:00:01,000 --> 00:00:01,300", text)  # a cue lasts at least a moment
        self.assertEqual(lens_files.text_lines(text), ["مرحبا", "زمن قصير"])

    def test_output_names(self):
        self.assertEqual(movie.output_path(os.path.join("D:", "films", "Film.mkv"), "en"),
                         os.path.join("D:", "films", "Film.en.srt"))
        self.assertTrue(movie.output_path("Film.mp4").endswith("Film.text.srt"))


class FakeTranslator:
    """A translator stand-in: capitals, and the dash dropped the way real translators often drop it."""

    def __init__(self, key="google"):
        self.key = key
        self.calls = []
        self.released = False

    def translate_many(self, texts, target, progress=None):
        self.calls.append((list(texts), target))
        return [text.lstrip("- ").upper() for text in texts]

    def release(self):
        self.released = True


class TranslateCuesTest(unittest.TestCase):
    def test_lines_dialogues_and_repeats(self):
        cues = [Cue(0, 1, "Hello there my\ngood old friend"), Cue(1, 2, "- Where to?\n- Home."),
                Cue(2, 3, "Hello there my\ngood old friend"), Cue(3, 4, "One line")]
        translator, steps = FakeTranslator(), []
        out = movie.translate_cues(cues, translator, "fr", lambda f, t: steps.append((f, t)), batch=2)
        self.assertEqual([(c.start, c.end) for c in out], [(c.start, c.end) for c in cues])
        self.assertEqual(out[0].text.split("\n"), ["HELLO THERE MY", "GOOD OLD FRIEND"])  # two lines stay two
        self.assertEqual(out[1].text, "- WHERE TO?\n- HOME.")   # each speaker: own line, own dash
        self.assertEqual(out[3].text, "ONE LINE")
        sent = [t for texts, _ in translator.calls for t in texts]
        self.assertEqual(sent, ["Hello there my good old friend", "- Where to?", "- Home.", "One line"])
        self.assertEqual({target for _, target in translator.calls}, {"fr"})
        self.assertEqual(steps[0], (0.0, "Translating 0 of 4 subtitles…"))
        self.assertEqual(steps[1][1], "Translating 2 of 4 subtitles…")
        self.assertLess(steps[-1][0], 1.0)

    def test_cancel(self):
        calls = []
        with self.assertRaises(movie.Canceled):
            movie.translate_cues([Cue(0, 1, "a"), Cue(1, 2, "b")], FakeTranslator(), "ar",
                                 should_cancel=lambda: bool(calls.append(1)) or len(calls) > 1, batch=1)

    def test_translated_names_never_take_the_original_name(self):
        join = os.path.join
        self.assertEqual(movie.translated_path(join("D:", "Film.mkv"), "ar"), join("D:", "Film.ar.srt"))
        self.assertEqual(movie.translated_path(join("D:", "Film.en.srt"), "ar"), join("D:", "Film.ar.srt"))
        self.assertEqual(movie.translated_path(join("D:", "Film.text.srt"), "fr"), join("D:", "Film.fr.srt"))
        self.assertEqual(movie.translated_path(join("D:", "x.srt"), "ar"), join("D:", "x.ar.srt"))
        self.assertEqual(movie.translated_path(join("D:", "Film.2020.srt"), "ar"), join("D:", "Film.2020.ar.srt"))
        self.assertEqual(movie.translated_path(join("D:", "Film.ar.srt"), "ar"), join("D:", "Film.ar.srt"))

    def test_an_srt_file_translated_keeps_numbers_and_times(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        path, out = os.path.join(folder, "Film.en.srt"), os.path.join(folder, "Film.ar.srt")
        movie.write_srt([Cue(1, 2.5, "- Hi.\n- Hello."), Cue(3, 4, "Bye")], path)
        self.assertEqual(movie.read_srt(path), [Cue(1, 2.5, "- Hi.\n- Hello."), Cue(3, 4, "Bye")])
        self.assertEqual(movie.translate_srt(path, out, FakeTranslator(), "ar"), 2)
        with open(out, encoding="utf-8-sig") as f:
            self.assertEqual(f.read(), "1\n00:00:01,000 --> 00:00:02,500\n- HI.\n- HELLO.\n\n"
                                       "2\n00:00:03,000 --> 00:00:04,000\nBYE\n")

    def test_command_line_translates_after_writing(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        film, out = os.path.join(folder, "Film.mkv"), os.path.join(folder, "x.srt")
        made = []

        def extract(path, index, out_path):
            movie.write_srt([Cue(0, 1, "Hi")], out_path)
            return movie.Result(out_path, 1, language="en")

        def translator(key, device):
            made.append(FakeTranslator(key))
            return made[-1]

        with mock.patch.object(movie, "extract_track", side_effect=extract), \
                mock.patch("echosub_lens.lens_translate.LensTranslator", side_effect=translator), \
                mock.patch("echosub_lens.config.load", return_value={"translator": "google"}):
            code = movie.cli([film, "--from", "inside:2", "--out", out, "--to", "ar", "--translator", "google"])
        self.assertEqual(code, 0)
        self.assertEqual(made[0].key, "google")
        self.assertTrue(made[0].released)
        self.assertEqual(movie.read_srt(os.path.join(folder, "x.ar.srt")), [Cue(0, 1, "HI")])
        with open(out + ".log", encoding="utf-8") as f:
            self.assertIn("translated into Arabic with Google", f.read())


class CleaningTest(unittest.TestCase):
    def test_ass_codes_go_and_line_breaks_stay(self):
        self.assertEqual(movie.clean_ass(r"{\an8}{\i1}Hello{\i0}\Nworld"), "Hello\nworld")
        self.assertEqual(movie.clean_ass(r"{\c&H0&}<i>Hi</i>\hthere{\r}"), "Hi there")
        self.assertEqual(movie.clean_ass("<font color=\"#fff\">a  b</font>\n\n c "), "a b\nc")
        self.assertEqual(movie.clean_ass("2 < 3 > 1"), "2 < 3 > 1")

    def test_ass_events(self):
        self.assertEqual(movie.ass_dialogue_text("0,0,Default,,0,0,0,,Hello, you"), "Hello, you")
        self.assertEqual(movie.ass_dialogue_text("Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hi, all"),
                         "Hi, all")

    def test_track_labels(self):
        track = movie.Track(2, "ara", "Forced", "hdmv_pgs_subtitle", False)
        self.assertEqual(track.code, "ar")
        self.assertIn("Arabic", track.label)
        self.assertIn("text recognition", track.label)
        self.assertEqual(movie.Track(3, "und", "", "subrip", True).code, "")


class ShapingTest(unittest.TestCase):
    def test_short_text_stays_one_cue(self):
        self.assertEqual(movie.split_cue(Cue(1, 3, "Hello there")), [Cue(1, 3, "Hello there")])

    def test_long_text_is_split_into_two_line_cues_with_the_time_shared(self):
        text = " ".join(["word"] * 40)  # 199 characters: five lines of 42 at most
        cues = movie.split_cue(Cue(10, 20, text))
        self.assertEqual(len(cues), 3)
        for cue in cues:
            lines = cue.text.split("\n")
            self.assertLessEqual(len(lines), 2)
            self.assertTrue(all(len(line) <= 42 for line in lines))
        self.assertEqual(cues[0].start, 10)
        self.assertEqual(cues[-1].end, 20)
        for a, b in zip(cues, cues[1:]):
            self.assertAlmostEqual(a.end, b.start)
        self.assertGreater(cues[0].end - cues[0].start, cues[-1].end - cues[-1].start)  # the short last one
        self.assertEqual(" ".join(c.text.replace("\n", " ") for c in cues), text)

    def test_words_become_cues_at_sentence_ends_pauses_and_two_lines(self):
        words = [(0.0, 0.4, " Hello"), (0.4, 0.8, " there,"), (0.8, 1.2, " my"), (1.2, 1.6, " friend."),
                 (2.0, 2.3, " Yes."),  # a short sentence stays with the next one
                 (2.3, 2.6, " Come"), (2.6, 3.0, " in"), (5.0, 5.4, " now")]  # a pause starts a new cue
        words += [(6.0 + i * 0.3, 6.3 + i * 0.3, " word") for i in range(20)]  # 99 characters: more than 2 lines
        cues = movie.words_to_cues(words)
        self.assertEqual(cues[0], Cue(0.0, 1.6, "Hello there, my friend."))
        self.assertEqual(cues[1], Cue(2.0, 3.0, "Yes. Come in"))
        self.assertEqual(cues[2].start, 5.0)
        self.assertTrue(cues[2].text.startswith("now word"))
        for cue in cues:
            lines = cue.text.split("\n")
            self.assertLessEqual(len(lines), 2)
            self.assertTrue(all(len(line) <= 42 for line in lines))
            self.assertLessEqual(cue.end - cue.start, 7.0)
        self.assertEqual(sum(c.text.count("word") for c in cues), 20)

    def test_readings_merge_into_cues(self):
        readings = [(0.0, ""), (0.5, "Hello there"), (1.0, "Hello there"), (1.5, "Hel1o there"), (2.0, ""),
                    (2.5, "Hello there"), (3.0, ""), (3.5, ""), (4.0, "Goodbye"), (4.5, "Something else")]
        cues = movie.merge_readings(readings, 0.5)
        self.assertEqual(cues, [Cue(0.5, 3.0, "Hello there"), Cue(4.0, 4.5, "Goodbye"),
                                Cue(4.5, 5.0, "Something else")])

    def test_a_missed_frame_too_many_ends_the_cue(self):
        readings = [(0, "One"), (1, ""), (2, ""), (3, "One")]
        self.assertEqual(movie.merge_readings(readings, 1), [Cue(0, 1, "One"), Cue(3, 4, "One")])

    def test_picture_subtitles_become_dark_text_on_white(self):
        # 2 × 3 picture with a stride of 4: white letters, black outline, transparent around
        indices = bytes([0, 1, 2, 9, 2, 1, 0, 9])
        palette = np.array([0x00000000, 0xFF000000, 0xFFFFFFFF], np.uint32).tobytes()
        gray = movie.bitmap_gray(indices, palette, 3, 2, 4)
        self.assertEqual(gray.tolist(), [[255, 255, 0], [0, 255, 255]])


class FakeReader:
    """Windows OCR stand-in: 'reads' the brightness of the picture as text."""

    def __init__(self):
        self.calls = 0

    def read(self, image):
        self.calls += 1
        bright = image.pixelColor(image.width() // 2, image.height() // 2).red()
        if bright < 100:
            return []
        word = "Hello" if bright < 200 else "World"
        box = lens_ocr.Box(10, 10, 60, 20)
        return [lens_ocr.Line([(word, box), ("friends", lens_ocr.Box(75, 10, 60, 20))])]

    def close(self):
        pass


class PyAVTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)

    def test_text_tracks_are_found_and_extracted(self):
        for name, codec in (("film.mkv", "subrip"), ("film.mp4", "mov_text")):
            path = os.path.join(self.folder, name)
            make_movie(path, [(0, 1000, "Hello world"), (1500, 2500, r"Second {\i1}line{\i0}\Nand more")], codec)
            tracks = movie.subtitle_tracks(path)
            self.assertEqual(len(tracks), 1)
            self.assertTrue(tracks[0].is_text)
            self.assertEqual(tracks[0].code, "en")
            out = os.path.join(self.folder, name + ".srt")
            steps = []
            result = movie.extract_track(path, tracks[0].index, out, lambda f, t: steps.append((f, t)))
            self.assertEqual(result.cues, 2)
            with open(out, encoding="utf-8-sig") as f:
                self.assertEqual(f.read(), "1\n00:00:00,000 --> 00:00:01,000\nHello world\n\n"
                                           "2\n00:00:01,500 --> 00:00:02,500\nSecond line\nand more\n")
            self.assertEqual(steps[-1][0], 1.0)

    def test_a_canceled_extraction_saves_nothing(self):
        path = os.path.join(self.folder, "film.mkv")
        make_movie(path, [(0, 1000, "Hello")])
        out = os.path.join(self.folder, "out.srt")
        with self.assertRaises(movie.Canceled):
            movie.extract_track(path, 1, out, should_cancel=lambda: True)
        self.assertFalse(os.path.exists(out))

    def test_burned_text_is_read_every_half_second_and_merged(self):
        import av

        path = os.path.join(self.folder, "burned.mp4")
        container = av.open(path, "w")
        video = container.add_stream("mpeg4", rate=10)
        video.width, video.height, video.pix_fmt = 64, 48, "yuv420p"
        for i in range(40):  # 4 s: dark, "Hello" from 1 s, "World" from 2.5 s
            level = 0 if i < 10 else (150 if i < 25 else 250)
            frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), level, np.uint8), format="rgb24")
            frame.pts = i
            for packet in video.encode(frame):
                container.mux(packet)
        for packet in video.encode():
            container.mux(packet)
        container.close()
        reader = FakeReader()
        out = os.path.join(self.folder, "burned.srt")
        result = movie.read_burned(path, out, every=0.5, region="whole", reader=reader)
        self.assertEqual(result.cues, 2)
        with open(out, encoding="utf-8-sig") as f:
            text = f.read()
        self.assertIn("1\n00:00:01,000 --> 00:00:02,500\nHello friends\n", text)
        self.assertIn("2\n00:00:02,500 --> 00:00:04,000\nWorld friends\n", text)
        self.assertLess(reader.calls, 8)  # pictures that did not change are not read again

    def test_speech_uses_the_model_and_splits_long_segments(self):
        class Segment:
            def __init__(self, start, end, text):
                self.start, self.end, self.text = start, end, text

        class Info:
            duration, language = 20.0, "en"

        class Model:
            released = False

            def transcribe(self, path, **options):
                self.options = options
                return iter([Segment(0, 2, " Hi. "), Segment(5, 20, " ".join(["talk"] * 30))]), Info()

        model = Model()
        downloader = mock.Mock()
        downloader.whisper.return_value = "D:/models/turbo"
        out = os.path.join(self.folder, "speech.srt")
        with mock.patch.object(movie, "release_whisper") as release:
            result = movie.transcribe("film.mkv", out, downloader=downloader, loader=lambda f, d: (model, "cuda"))
        downloader.whisper.assert_called_once_with("large-v3-turbo")
        release.assert_called_once_with(model)
        self.assertTrue(model.options["vad_filter"])
        self.assertEqual(result.cues, 3)
        self.assertEqual(result.note, "on the graphics card, English")

    def test_speech_falls_back_to_the_processor(self):
        class Broken:
            def transcribe(self, *a, **k):
                raise RuntimeError("cublas64_12.dll is not found")

        class Works:
            def transcribe(self, *a, **k):
                return iter([type("S", (), {"start": 0, "end": 1, "text": "Hi"})()]), type("I", (), {"duration": 1})()

        loads = []

        def loader(folder, device):
            loads.append(device)
            return (Broken(), "cuda") if device == "cuda" else (Works(), "cpu")

        downloader = mock.Mock()
        downloader.whisper.return_value = "x"
        with mock.patch.object(movie, "release_whisper"), self.assertLogs("echosub_lens.movie", "ERROR"):
            result = movie.transcribe("f.mkv", os.path.join(self.folder, "s.srt"), downloader=downloader,
                                      loader=loader)
        self.assertEqual(loads, ["cuda", "cpu"])
        self.assertEqual(result.note, "on the processor")


class MovieWindowTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.notes = []
        patches = [mock.patch.object(lens_ui.hotkeys.GlobalHotkeys, "enable_keys", return_value=[]),
                   mock.patch("echosub_lens.lens_menu.lens_ocr.available_languages", return_value=["en-US"]),
                   mock.patch("echosub_lens.lens_movie_window.lens_ocr.available_languages",
                              return_value=["en-US"]),
                   mock.patch.object(lens_ui.LensScanner, "start", lambda scanner: scanner._timer.start())]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.lens = lens_ui.LensApp(app, dict(lens_app.DEFAULTS), lambda cfg: None,
                                    base_cfg={"target_lang": "ar", "translator": "google"})
        self.lens.activity.tell = self.notes.append
        self.translator = FakeTranslator()
        for name, fake in (("translator_for", mock.Mock(return_value=self.translator)),
                           ("release_translator", mock.Mock())):
            p = mock.patch.object(self.lens, name, fake)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._close)
        self.film = os.path.join(self.folder, "Film.mkv")
        open(self.film, "wb").close()

    def _close(self):
        if self.lens.movie_window is not None:
            self.lens.movie_window.shutdown()
        self.lens.stop()
        self.lens.tray.hide()
        self.lens.overlay.close()

    def wait_done(self, window):
        deadline = time.monotonic() + 5
        while window._worker is not None:  # cleared on the Qt thread when the result arrives
            app.processEvents()
            if time.monotonic() > deadline:
                self.fail("the work did not finish")
            time.sleep(0.01)
        app.processEvents()

    def test_one_window_from_the_menu_key_and_window(self):
        self.assertIn("movie", lens_shortcuts.ACTIONS)
        self.assertEqual(lens_shortcuts.defaults()["movie"], "")
        texts = [a.text() for a in self.lens.menu.actions()]
        file_item = next(i for i, t in enumerate(texts) if t.startswith("Translate a file"))
        self.assertTrue(texts[file_item + 1].startswith("Translate a movie (subtitles to .srt)"))
        self.lens.actions()["movie"]()
        window = self.lens.movie_window
        self.assertTrue(window.isVisible())
        self.assertIs(self.lens.open_movie(), window)
        self.lens.open_window().movie_button.click()
        self.assertIs(self.lens.movie_window, window)
        self.lens.main_window.hide()

    def test_tracks_inside_are_offered_and_finishing_offers_translate(self):
        tracks = [movie.Track(2, "eng", "", "subrip", True), movie.Track(3, "ara", "", "hdmv_pgs_subtitle", False)]

        def extract(path, index, out, progress, cancel, ocr_language):
            progress(0.5, "Reading the subtitles… 3 so far")
            movie.write_srt([Cue(0, 1, "Hi")], out)
            return movie.Result(out, 1)

        with mock.patch.object(movie, "subtitle_tracks", return_value=tracks), \
                mock.patch.object(movie, "extract_track", side_effect=extract) as extract_track:
            window = self.lens.open_movie(self.film)
            self.assertEqual(window.source(), "inside")
            self.assertEqual(window.track_box.count(), 2)
            self.assertEqual(window.out_edit.text(), os.path.join(self.folder, "Film.en.srt"))
            window.track_box.setCurrentIndex(1)
            window.track_box.activated.emit(1)
            self.assertEqual(window.out_edit.text(), os.path.join(self.folder, "Film.ar.srt"))
            self.assertTrue(window.done_row.isHidden())
            self.assertTrue(window.start())
            self.wait_done(window)
        self.assertEqual(extract_track.call_args[0][1], 3)
        self.assertFalse(window.done_row.isHidden())
        self.assertIn("1 subtitles saved", window.status.text())
        self.assertIn("Movie subtitles saved: Film.ar.srt (1 subtitles)", self.notes)
        self.assertEqual(self.lens.cfg["last_folder"], self.folder)
        self.assertIn("Not translated: they are already in Arabic", window.status.text())  # Arabic into Arabic
        self.lens.translator_for.assert_not_called()
        self.assertTrue(window.open_translated_button.isHidden())
        with mock.patch.object(self.lens, "open_file") as open_file:
            window.translate_button.click()
        open_file.assert_called_once_with(os.path.join(self.folder, "Film.ar.srt"), "ar", "same")

    def test_translated_as_soon_as_written_into_any_language_with_any_translator(self):
        tracks = [movie.Track(2, "eng", "", "subrip", True)]

        def extract(path, index, out, progress, cancel, ocr_language):
            movie.write_srt([Cue(0, 1, "Hello there my\ngood old friend"), Cue(2, 3, "- Where to?\n- Home.")], out)
            return movie.Result(out, 2, language="en")

        with mock.patch.object(movie, "subtitle_tracks", return_value=tracks), \
                mock.patch.object(movie, "extract_track", side_effect=extract):
            window = self.lens.open_movie(self.film)
            self.assertEqual(window.target(), "ar")       # the language Lens translates into now
            self.assertEqual(window.translator_key(), "same")
            self.assertTrue(window.translate_after.isChecked())
            window.target_box.setCurrentIndex(window.target_box.findData("fr"))
            window.translator_box.setCurrentIndex(window.translator_box.findData("nllb-600m"))
            hint = window.translator_box.itemData(window.translator_box.currentIndex(),
                                                  lens_movie_window.Qt.ToolTipRole)
            self.assertIn("offline", hint)
            self.assertTrue(window.start())
            self.assertFalse(window.target_box.isEnabled())
            self.wait_done(window)
        self.lens.translator_for.assert_called_once_with("nllb-600m")
        self.lens.release_translator.assert_called_once_with(self.translator)
        translated = os.path.join(self.folder, "Film.fr.srt")
        self.assertEqual(window.translated, translated)
        self.assertEqual(movie.read_srt(translated), [Cue(0, 1, "HELLO THERE MY\nGOOD OLD FRIEND"),
                                                      Cue(2, 3, "- WHERE TO?\n- HOME.")])
        self.assertTrue(os.path.exists(os.path.join(self.folder, "Film.en.srt")))  # the original stays
        self.assertIn("Translated subtitles saved: Film.fr.srt", self.notes)
        self.assertIn("Translated into French", window.status.text())
        self.assertFalse(window.open_translated_button.isHidden())
        with mock.patch.object(lens_movie_window.QDesktopServices, "openUrl") as open_url:
            window.open_translated_button.click()
        self.assertEqual(open_url.call_args[0][0].toLocalFile(), translated.replace("\\", "/"))
        with mock.patch.object(self.lens, "open_file") as open_file:
            window.translate_button.click()
        open_file.assert_called_once_with(os.path.join(self.folder, "Film.en.srt"), "fr", "nllb-600m")

    def test_a_translation_already_there_is_replaced_only_when_asked(self):
        tracks = [movie.Track(2, "eng", "", "subrip", True)]
        open(os.path.join(self.folder, "Film.ar.srt"), "w").close()
        with mock.patch.object(movie, "subtitle_tracks", return_value=tracks), \
                mock.patch.object(lens_movie_window.QMessageBox, "question",
                                  return_value=lens_movie_window.QMessageBox.No) as question, \
                mock.patch.object(movie, "extract_track") as extract:
            window = self.lens.open_movie(self.film)
            self.assertFalse(window.start())
        self.assertIn("Film.ar.srt already exists", question.call_args[0][2])
        extract.assert_not_called()
        window.translate_after.setChecked(False)  # not translating: the file there does not matter
        with mock.patch.object(movie, "extract_track", side_effect=movie.Unsupported("empty")):
            self.assertTrue(window.start())
            self.wait_done(window)
        self.lens.translator_for.assert_not_called()

    def test_a_failed_translation_keeps_the_subtitles(self):
        tracks = [movie.Track(2, "eng", "", "subrip", True)]

        def extract(path, index, out, progress, cancel, ocr_language):
            movie.write_srt([Cue(0, 1, "Hi")], out)
            return movie.Result(out, 1, language="en")

        self.translator.translate_many = mock.Mock(side_effect=RuntimeError("no internet"))
        with mock.patch.object(movie, "subtitle_tracks", return_value=tracks), \
                mock.patch.object(movie, "extract_track", side_effect=extract), \
                self.assertLogs("echosub_lens.lens_movie_window", "ERROR"):
            window = self.lens.open_movie(self.film)
            window.start()
            self.wait_done(window)
        self.assertIn("Translating them failed: no internet", window.status.text())
        self.assertFalse(window.done_row.isHidden())
        self.assertEqual(window.translated, "")
        self.lens.release_translator.assert_called_once_with(self.translator)

    def test_no_subtitles_inside_means_speech_with_its_choices(self):
        with mock.patch.object(movie, "subtitle_tracks", return_value=[]):
            window = self.lens.open_movie(self.film)
        self.assertFalse(window.source_inside.isEnabled())
        self.assertEqual(window.source(), "speech")
        self.assertIn("No subtitles", window.track_box.currentText())
        self.assertEqual(window.out_edit.text(), os.path.join(self.folder, "Film.text.srt"))
        window.spoken_box.setCurrentIndex(window.spoken_box.findData("fr"))
        window.spoken_box.activated.emit(window.spoken_box.currentIndex())
        self.assertTrue(window.out_edit.text().endswith("Film.fr.srt"))
        window.set_source("burned")
        self.assertTrue(window.out_edit.text().endswith("Film.text.srt"))
        window.ocr_box.setCurrentIndex(window.ocr_box.findData("en-US"))
        window.ocr_box.activated.emit(window.ocr_box.currentIndex())
        self.assertTrue(window.out_edit.text().endswith("Film.en.srt"))
        window.set_source("speech")
        window.model_box.setCurrentIndex(window.model_box.findData("small"))
        with mock.patch.object(movie, "transcribe", side_effect=movie.Unsupported("No speech was found")) as run:
            window.start()
            self.wait_done(window)
        self.assertEqual(run.call_args.kwargs["model"], "small")
        self.assertEqual(run.call_args.kwargs["language"], "fr")
        self.assertIn("No speech was found", window.status.text())
        self.assertTrue(window.done_row.isHidden())
        self.assertTrue(any("No speech was found" in n for n in self.notes))

    def test_cancel(self):
        def slow(path, out, **kwargs):
            while not kwargs["should_cancel"]():
                time.sleep(0.01)
            raise movie.Canceled()

        with mock.patch.object(movie, "subtitle_tracks", return_value=[]), \
                mock.patch.object(movie, "read_burned", side_effect=slow):
            window = self.lens.open_movie(self.film)
            window.set_source("burned")
            window.start()
            self.assertTrue(window.cancel_button.isEnabled())
            self.assertFalse(window.choose_button.isEnabled())
            window.cancel_button.click()
            self.wait_done(window)
        self.assertIn("Canceled", window.status.text())
        self.assertTrue(window.start_button.isEnabled())

    def test_time_left(self):
        from echosub_lens.lens_movie_window import time_left

        self.assertEqual(time_left(1, 0.5), "")
        self.assertEqual(time_left(30, 0.5), "about 30 s left")
        self.assertEqual(time_left(600, 0.5), "about 10 min left")
        self.assertEqual(time_left(3600, 0.25), "about 3 h 0 min left")


if __name__ == "__main__":
    unittest.main()

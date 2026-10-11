# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: "Movie to subtitles" — all the text of a movie in an .srt file, ready to translate.

The work runs in a thread of its own (echosub_lens/movie.py); the window shows what it is doing and the
time left, and when it is done offers the folder and the file translator.
"""
import logging
import os
import threading
import time

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QProgressBar, QPushButton, QRadioButton, QVBoxLayout, QWidget)

from . import config, languages, lens_ocr, movie
from .lens_window import STYLE

log = logging.getLogger(__name__)

TITLE = "Movie to subtitles"
SOURCES = ("inside", "speech", "burned")


class _Signals(QObject):
    progress = Signal(float, str)
    done = Signal(object)        # movie.Result
    failed = Signal(str)
    canceled = Signal()


def time_left(elapsed, fraction):
    """'about 3 min left', once there is enough done to tell."""
    if fraction < 0.02 or elapsed < 2:
        return ""
    seconds = elapsed * (1 - fraction) / fraction
    if seconds < 60:
        return f"about {max(1, int(seconds))} s left"
    if seconds < 3600:
        return f"about {int(round(seconds / 60))} min left"
    return f"about {int(seconds // 3600)} h {int(seconds % 3600 // 60)} min left"


class MovieWindow(QWidget):
    """One per Lens (LensApp.open_movie): pick a movie, where its text comes from, and where the .srt goes."""

    def __init__(self, lens):
        super().__init__(None, Qt.Window)
        self.lens = lens
        self.movie_path = ""
        self.tracks = []
        self.result = None
        self._custom_out = False
        self._worker = None
        self._cancel = threading.Event()
        self._started = 0.0
        self.setObjectName("lens_window")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowTitle(f"{TITLE} — EchoSub Lens")
        self.setWindowIcon(QIcon(os.path.join(config.ASSETS_DIR, "echosub.ico")))
        self.setStyleSheet(STYLE)
        self.resize(760, 700)
        self.signals = _Signals()
        self.signals.progress.connect(self._progress)
        self.signals.done.connect(self._done)
        self.signals.failed.connect(self._failed)
        self.signals.canceled.connect(self._canceled)
        self._build()
        self._update()

    # ---- layout --------------------------------------------------------------
    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(4)
        heading = QLabel(TITLE)
        heading.setObjectName("title")
        layout.addWidget(heading)
        intro = QLabel("All the text of a movie in a subtitle file (.srt). Then translate it with Lens, or open it "
                       "with the movie in any player.")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._section(layout, "The movie")
        self.movie_edit = QLineEdit()
        self.movie_edit.setReadOnly(True)
        self.movie_edit.setPlaceholderText("No movie chosen yet")
        self.choose_button = QPushButton("Choose a movie…")
        self.choose_button.clicked.connect(lambda: self.choose_movie())
        layout.addWidget(self._row(self.movie_edit, self.choose_button, stretch=False))

        self._section(layout, "Where the text comes from")
        self.sources = QButtonGroup(self)
        self.source_inside = QRadioButton("Subtitles inside the movie")
        self.track_box = QComboBox()
        self.track_box.setMinimumWidth(320)
        self.track_box.activated.connect(lambda _: self._suggest_output())
        layout.addWidget(self._row(self.source_inside, self.track_box))
        self.inside_help = self._help(layout, "The fastest and the most exact: the movie file carries the "
                                              "subtitles itself. Picture subtitles (Blu-ray, DVD) are read with "
                                              "Windows' text recognition.")
        self.source_speech = QRadioButton("What is said (speech recognition)")
        self.spoken_box = QComboBox()
        self.spoken_box.addItem("Automatic (detect the language)", "")
        for code in movie.SPOKEN_LANGUAGES:
            self.spoken_box.addItem(languages.name(code), code)
        self.spoken_box.activated.connect(lambda _: self._suggest_output())
        self.model_box = QComboBox()
        for name, text in movie.MODELS.items():
            self.model_box.addItem(text, name)
        layout.addWidget(self._row(self.source_speech, self.spoken_box, self.model_box))
        self._help(layout, "Listens to the movie and writes down every word spoken — for movies with no subtitles. "
                           "Runs on your NVIDIA graphics card (much faster), or the processor when there is none.")
        self.source_burned = QRadioButton("Text burned into the picture")
        self.region_box = QComboBox()
        for key, text in movie.REGIONS.items():
            self.region_box.addItem(text, key)
        self.ocr_box = QComboBox()
        self.ocr_box.addItem("Automatic (Windows display languages)", lens_ocr.AUTO)
        for tag in lens_ocr.available_languages():
            self.ocr_box.addItem(f"{languages.name(tag.split('-')[0].lower())} ({tag})", tag)
        self.ocr_box.activated.connect(lambda _: self._suggest_output())
        layout.addWidget(self._row(self.source_burned, self.region_box, self.ocr_box))
        self._help(layout, "Subtitles that are part of the picture itself: Lens looks at the movie twice a second "
                           "and reads them with Windows' text recognition. Slower — it goes through the whole movie.")
        for i, button in enumerate((self.source_inside, self.source_speech, self.source_burned)):
            self.sources.addButton(button, i)
        self.sources.idClicked.connect(lambda _: self._source_changed())

        self._section(layout, "Save the subtitles as")
        self.out_edit = QLineEdit()
        self.out_edit.textEdited.connect(self._out_edited)
        self.save_as_button = QPushButton("Save as…")
        self.save_as_button.clicked.connect(self.choose_output)
        layout.addWidget(self._row(self.out_edit, self.save_as_button, stretch=False))

        layout.addSpacing(14)
        self.start_button = QPushButton("▶  Start")
        self.start_button.setObjectName("start")
        self.start_button.setCursor(Qt.PointingHandCursor)
        self.start_button.clicked.connect(self.start)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel)
        layout.addWidget(self._row(self.start_button, self.cancel_button))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setStyleSheet("QProgressBar { background: #1A2120; border: none; border-radius: 4px; }"
                                        "QProgressBar::chunk { background: #14B8A6; border-radius: 4px; }")
        layout.addSpacing(6)
        layout.addWidget(self.progress_bar)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.left_label = QLabel("")
        self.left_label.setObjectName("muted")
        layout.addWidget(self._row(self.status, self.left_label, stretch=False))

        self.folder_button = QPushButton("Open the folder")
        self.folder_button.clicked.connect(self.open_folder)
        self.translate_button = QPushButton("Translate the subtitles…")
        self.translate_button.setObjectName("start")
        self.translate_button.setCursor(Qt.PointingHandCursor)
        self.translate_button.clicked.connect(self.translate)
        self.done_row = self._row(self.translate_button, self.folder_button)
        layout.addWidget(self.done_row)
        layout.addStretch(1)

    @staticmethod
    def _section(layout, text):
        label = QLabel(text)
        label.setObjectName("section")
        layout.addWidget(label)

    @staticmethod
    def _help(layout, text):
        label = QLabel(text)
        label.setObjectName("help")
        label.setWordWrap(True)
        layout.addWidget(label)
        return label

    @staticmethod
    def _row(*widgets, stretch=True):
        line = QWidget()
        row = QHBoxLayout(line)
        row.setContentsMargins(0, 4, 0, 0)
        row.setSpacing(10)
        for i, w in enumerate(widgets):
            row.addWidget(w, 1 if (not stretch and i == 0) else 0)
        if stretch:
            row.addStretch(1)
        return line

    # ---- choices -------------------------------------------------------------
    def choose_movie(self, path=None):
        """Pick the movie (a dialog when `path` is None) and read which subtitles it carries."""
        if self.busy():
            return False
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Choose a movie", self.lens.cfg.get("last_folder") or "",
                                                  movie.OPEN_FILTER)
            if not path:
                return False
        path = os.path.normpath(path)
        self.lens._set(last_folder=os.path.dirname(path))
        self.movie_path = path
        self.movie_edit.setText(path)
        self.result = None
        self._custom_out = False
        try:
            self.tracks = movie.subtitle_tracks(path)
        except Exception as e:
            log.exception("Could not read %s", path)
            self.tracks = []
            self.movie_path = ""
            self.movie_edit.setText("")
            self._say(f"This file could not be opened as a movie: {e}")
            self._update()
            return False
        self.track_box.clear()
        for track in self.tracks:
            self.track_box.addItem(track.label, track.index)
            if not track.is_text and track.codec not in movie.BITMAP_CODECS:
                item = self.track_box.model().item(self.track_box.count() - 1)
                item.setEnabled(False)
        readable = [t for t in self.tracks if t.is_text or t.codec in movie.BITMAP_CODECS]
        if readable:
            self.track_box.setCurrentIndex(self.track_box.findData(readable[0].index))
            self.source_inside.setChecked(True)
        elif self.tracks:
            self.track_box.insertItem(0, "Lens cannot read the subtitles of this movie", None)
            self.track_box.setCurrentIndex(0)
        else:
            self.track_box.addItem("No subtitles inside this movie", None)
        self.source_inside.setEnabled(bool(readable))
        if not readable and (self.sources.checkedId() < 0 or self.source_inside.isChecked()):
            self.source_speech.setChecked(True)
        self._suggest_output()
        found = f"{len(readable)} subtitle track(s) inside" if readable else "no subtitles inside"
        self._say(f"{os.path.basename(path)}: {found}. Choose where the text comes from, then press Start.")
        self._update()
        return True

    def source(self):
        return SOURCES[self.sources.checkedId()] if self.sources.checkedId() >= 0 else None

    def set_source(self, name):
        {"inside": self.source_inside, "speech": self.source_speech, "burned": self.source_burned}[name].setChecked(
            True)
        self._source_changed()

    def _source_changed(self):
        self._suggest_output()
        self._update()

    def _language(self):
        """The language the subtitles will be in, for the file name ("" when not known before)."""
        source = self.source()
        if source == "inside":
            index = self.track_box.currentData()
            track = next((t for t in self.tracks if t.index == index), None)
            return track.code if track else ""
        if source == "speech":
            return self.spoken_box.currentData() or ""
        if source == "burned":
            tag = self.ocr_box.currentData()
            return "" if tag == lens_ocr.AUTO else tag.split("-")[0].lower()
        return ""

    def _suggest_output(self):
        if self.movie_path and not self._custom_out:
            self.out_edit.setText(movie.output_path(self.movie_path, self._language()))
        self._update()

    def _out_edited(self, _text):
        self._custom_out = True
        self._update()

    def choose_output(self):
        start = self.out_edit.text() or (movie.output_path(self.movie_path) if self.movie_path else "")
        path, _ = QFileDialog.getSaveFileName(self, "Save the subtitles as", start, "Subtitles (*.srt)")
        if path:
            if not path.lower().endswith(".srt"):
                path += ".srt"
            self.out_edit.setText(os.path.normpath(path))
            self._custom_out = True
        self._update()

    # ---- the work --------------------------------------------------------------
    def busy(self):
        return self._worker is not None and self._worker.is_alive()

    def start(self):
        if self.busy() or not self.movie_path or self.source() is None:
            return False
        out = self.out_edit.text().strip()
        if not out:
            return False
        if os.path.abspath(out) == os.path.abspath(self.movie_path):
            self._say("Choose another name for the subtitles: the movie itself is never changed.")
            return False
        if os.path.exists(out) and QMessageBox.question(
                self, TITLE, f"{os.path.basename(out)} already exists. Replace it?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return False
        source = self.source()
        job = self._job(source, out)
        if job is None:
            return False
        self.result = None
        self._cancel.clear()
        self._started = time.monotonic()
        self.progress_bar.setValue(0)
        self.left_label.setText("")
        what = {"inside": "Reading the subtitles inside", "speech": "Recognizing the speech in",
                "burned": "Reading the text in the picture of"}[source]
        self._say(f"{what} {os.path.basename(self.movie_path)}…")
        self.lens._notify(f"{what} {os.path.basename(self.movie_path)}…")
        self._worker = threading.Thread(target=self._work, args=(job,), name="lens-movie", daemon=True)
        self._worker.start()
        self._update()
        return True

    def _job(self, source, out):
        """The function the worker runs, with everything it needs (read from the controls here)."""
        path, progress, cancel = self.movie_path, self.signals.progress.emit, self._cancel.is_set
        if source == "inside":
            index = self.track_box.currentData()
            if index is None:
                self._say("This movie has no subtitles Lens can read: choose speech or the picture.")
                return None
            ocr = self.lens.cfg.get("ocr_language", lens_ocr.AUTO)
            return lambda: movie.extract_track(path, index, out, progress, cancel, ocr_language=ocr)
        if source == "speech":
            model, language = self.model_box.currentData(), self.spoken_box.currentData() or None
            device = self.lens.base_cfg.get("device", "cuda")
            device = "cpu" if device == "cpu" else "cuda"
            return lambda: movie.transcribe(path, out, model=model, language=language, device=device,
                                            progress=progress, should_cancel=cancel)
        region, ocr = self.region_box.currentData(), self.ocr_box.currentData()
        return lambda: movie.read_burned(path, out, language=ocr, region=region, progress=progress,
                                         should_cancel=cancel)

    def _work(self, job):
        try:
            result = job()
        except movie.Canceled:
            self.signals.canceled.emit()
            return
        except movie.Unsupported as e:
            self.signals.failed.emit(str(e))
            return
        except Exception as e:
            log.exception("Movie to subtitles failed for %s", self.movie_path)
            self.signals.failed.emit(str(e) or e.__class__.__name__)
            return
        if self._cancel.is_set():
            self.signals.canceled.emit()
        else:
            self.signals.done.emit(result)

    def cancel(self):
        if self.busy():
            self._cancel.set()
            self._say("Stopping…")
            self._update()

    # ---- results (Qt thread) ---------------------------------------------------
    def _progress(self, fraction, text):
        if not self.busy() and self.result is not None:
            return
        self.progress_bar.setValue(int(max(0.0, min(fraction, 1.0)) * 1000))
        self.status.setText(text)
        self.left_label.setText(time_left(time.monotonic() - self._started, fraction))

    def _done(self, result):
        self._worker = None
        self.result = result
        self.progress_bar.setValue(1000)
        self.left_label.setText("")
        took = time.monotonic() - self._started
        note = f" ({result.note})" if result.note else ""
        self._say(f"Done in {int(took // 60)} min {int(took % 60)} s{note}: {result.cues} subtitles saved in "
                  f"{result.path}")
        self.lens._notify(f"Movie subtitles saved: {os.path.basename(result.path)} ({result.cues} subtitles)")
        self._update()

    def _failed(self, message):
        self._worker = None
        self.progress_bar.setValue(0)
        self.left_label.setText("")
        self._say(f"Could not make the subtitles: {message}")
        self.lens._notify(f"Movie subtitles: {message}")
        self._update()

    def _canceled(self):
        self._worker = None
        self.progress_bar.setValue(0)
        self.left_label.setText("")
        self._say("Canceled — nothing was saved.")
        self.lens._notify("Movie subtitles canceled")
        self._update()

    def _say(self, text):
        self.status.setText(text)

    def _update(self):
        busy = self.busy()
        for widget in (self.choose_button, self.source_speech, self.source_burned, self.track_box, self.spoken_box,
                       self.model_box, self.region_box, self.ocr_box, self.out_edit, self.save_as_button):
            widget.setEnabled(not busy)
        self.source_inside.setEnabled(not busy and any(t.is_text or t.codec in movie.BITMAP_CODECS
                                                       for t in self.tracks))
        self.start_button.setEnabled(not busy and bool(self.movie_path) and self.source() is not None
                                     and bool(self.out_edit.text().strip()))
        self.cancel_button.setEnabled(busy and not self._cancel.is_set())
        self.progress_bar.setVisible(busy or self.result is not None)
        self.done_row.setVisible(self.result is not None and not busy)

    # ---- when it is done ---------------------------------------------------------
    def open_folder(self):
        if self.result is None:
            return
        import subprocess

        path = os.path.normpath(self.result.path)
        try:
            subprocess.Popen(["explorer", f"/select,{path}"])
        except OSError:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
        self.lens._notify("The folder of the subtitles opens")

    def translate(self):
        """The file translator, with the new subtitles: it keeps the numbers and the times."""
        if self.result is not None:
            return self.lens.open_file(self.result.path)
        return None

    def present(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def shutdown(self):
        """Lens is closing: stop the work without asking."""
        self._cancel.set()
        self.hide()

    def closeEvent(self, event):
        if self.busy():
            if QMessageBox.question(self, TITLE, "Stop making the subtitles?", QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) != QMessageBox.Yes:
                event.ignore()
                return
            self._cancel.set()
        super().closeEvent(event)

# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: the window that shows a translated file, page by page, and saves a translated copy."""
import logging
import os
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget)

from . import languages, lens_files, lens_ocr, lens_translate

log = logging.getLogger(__name__)

TRANSLATOR_HINTS = {
    lens_translate.SAME: "The translator EchoSub uses (Google when EchoSub is not on this PC)",
    "google": "Google Translate: online and fast; needs the internet",
    "nllb-600m": "NLLB 600M: offline, on the graphics card (or the processor); downloaded the first time",
    "nllb-1.3b": "NLLB 1.3B: offline and more accurate, on the graphics card (or the processor); "
                 "downloaded the first time",
}


class _Signals(QObject):
    # Every signal starts with the run it belongs to: results of a run that was replaced are dropped.
    page = Signal(int, int, object, object)  # run, index, original QImage, translated blocks
    texts = Signal(int, object)              # run, {original: translation} of a Word or text file
    progress = Signal(int, str, int)         # run, what is being done, and how far it got (0-100)
    failed = Signal(int, str)
    done = Signal(int)


class FileWindow(QWidget):
    """Translates `path` in a thread of its own; pages appear as they are done. The language and the
    translator can be changed at the top: the file is translated again from the start.

    `choose_translator(choice)` gives a translator for a key of lens_translate.CHOICES (None: the translator
    cannot be changed here); `release_translator(translator)` lets go of one no longer used; `target_choices`
    is [(code, name)...] with (None, None) between the quick languages and the rest; `notify(text)` says
    something on the screen."""

    def __init__(self, path, target, translator, ocr_language, style, open_another=None, choose_translator=None,
                 release_translator=None, target_choices=None, translator_choice=None, notify=None):
        super().__init__(None, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.path, self.target, self.style = path, target, style
        self.translator, self.translator_choice = translator, translator_choice
        self.ocr_language = ocr_language
        self.kind = lens_files.kind(path)
        self._open_another = open_another
        self._choose_translator = choose_translator
        self._release_translator = release_translator or (lambda translator: None)
        self._notify = notify or (lambda text: None)
        self._target_choices = target_choices
        self.originals, self.blocks, self.rendered = {}, {}, {}
        self.translations = None
        self.finished = False
        self.run = 0
        self._cancel = threading.Event()
        self.resize(1100, 780)
        self._build()
        self.signals = _Signals()
        self.signals.page.connect(self._page_done)
        self.signals.texts.connect(self._texts_done)
        self.signals.progress.connect(self._progress)
        self.signals.failed.connect(self._failed)
        self.signals.done.connect(self._done)
        self._start()

    # ---- layout --------------------------------------------------------------
    def _build(self):
        layout = QVBoxLayout(self)
        layout.addLayout(self._build_choices())
        bar = QHBoxLayout()
        self.status = QLabel("Starting…")
        bar.addWidget(self.status, 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setFormat("%p%")
        bar.addWidget(self.progress_bar)
        self.original_button = QPushButton("Show the original")
        self.original_button.setCheckable(True)
        self.original_button.toggled.connect(self._show_pages)
        self.copy_button = QPushButton("Copy all translated text")
        self.copy_button.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.text_view.toPlainText()))
        self.save_button = QPushButton("Save translated copy…")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save)
        another = QPushButton("Translate another file…")
        another.clicked.connect(lambda: self._open_another and self._open_another())
        for widget in (self.original_button, self.copy_button, self.save_button, another):
            bar.addWidget(widget)
        layout.addLayout(bar)

        splitter = QSplitter(Qt.Horizontal)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        holder = QWidget()
        self.page_box = QVBoxLayout(holder)
        self.page_box.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.scroll.setWidget(holder)
        self.original_text = QPlainTextEdit()
        self.original_text.setReadOnly(True)
        self.text_view = QPlainTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setPlaceholderText("The translated text appears here, ready to select and copy.")
        pictures = self.kind in ("image", "pdf")
        splitter.addWidget(self.scroll if pictures else self.original_text)
        splitter.addWidget(self.text_view)
        splitter.setSizes([700, 400] if pictures else [550, 550])
        self.original_button.setVisible(pictures)
        layout.addWidget(splitter, 1)
        self.page_labels = []

    def _build_choices(self):
        row = QHBoxLayout()
        row.addWidget(QLabel("Translate into"))
        self.target_combo = QComboBox()
        self.target_combo.setMaxVisibleItems(20)
        choices = self._target_choices or [(code, languages.name(code)) for code in
                                           sorted(languages.LANGUAGES, key=languages.name)]
        for code, name in choices:
            if code is None:
                self.target_combo.insertSeparator(self.target_combo.count())
            elif code:  # "" (Lens's default) means nothing to a file: it has a language of its own
                self.target_combo.addItem(name, code)
        if self.target_combo.findData(self.target) < 0:
            self.target_combo.insertItem(0, languages.name(self.target), self.target)
        self.target_combo.setCurrentIndex(self.target_combo.findData(self.target))
        self.target_combo.setToolTip("The language the file is translated into")
        self.target_combo.currentIndexChanged.connect(self._target_chosen)
        row.addWidget(self.target_combo)
        row.addSpacing(16)
        row.addWidget(QLabel("with"))
        self.translator_combo = QComboBox()
        for key, label in lens_translate.CHOICES.items():
            self.translator_combo.addItem(label, key)
            self.translator_combo.setItemData(self.translator_combo.count() - 1, TRANSLATOR_HINTS.get(key, label),
                                              Qt.ToolTipRole)
        index = self.translator_combo.findData(self.translator_choice)
        self.translator_combo.setCurrentIndex(max(index, 0))
        if self._choose_translator is None:
            self.translator_combo.setEnabled(False)
            self.translator_combo.setToolTip("The translator Lens uses")
        else:
            self.translator_combo.setToolTip("The translator: Google is online and fast, NLLB works offline "
                                             "on the graphics card")
        self.translator_combo.currentIndexChanged.connect(self._translator_chosen)
        row.addWidget(self.translator_combo)
        row.addStretch(1)
        return row

    # ---- choosing the language and the translator ---------------------------------
    def _target_chosen(self, _index):
        code = self.target_combo.currentData()
        if code and code != self.target:
            self.set_target(code)

    def _translator_chosen(self, _index):
        key = self.translator_combo.currentData()
        if key and key != self.translator_choice:
            self.set_translator(key)

    def set_target(self, code):
        """Translate the file again, from the start, into `code`."""
        self.target = code
        self._sync_combo(self.target_combo, code)
        self._start()
        self._notify(f"Translating {os.path.basename(self.path)} into {languages.name(code)}")

    def set_translator(self, choice):
        """Translate the file again, from the start, with `choice` (a key of lens_translate.CHOICES)."""
        if self._choose_translator is None:
            return
        previous = self.translator
        self.translator = self._choose_translator(choice)
        self.translator_choice = choice
        self._sync_combo(self.translator_combo, choice)
        self._start()
        if previous is not None and previous is not self.translator:
            self._release_translator(previous)  # does nothing to the translator Lens itself uses
        self._notify(f"Translating {os.path.basename(self.path)} into {languages.name(self.target)} "
                     f"with {lens_translate.CHOICES.get(choice, choice).split(' — ')[0]}")

    @staticmethod
    def _sync_combo(combo, value):
        index = combo.findData(value)
        if index >= 0 and index != combo.currentIndex():
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)

    def suggested_name(self):
        return lens_files.output_name(self.path, self.target)

    def _start(self):
        """(Re)start translating the whole file with the current language and translator."""
        self._cancel.set()  # the run before stops at its next step; what it still sends is dropped
        self._cancel = threading.Event()
        self.run += 1
        self.originals, self.blocks, self.rendered = {}, {}, {}
        self.translations = None
        self.finished = False
        for label in self.page_labels:
            self.page_box.removeWidget(label)
            label.deleteLater()
        self.page_labels = []
        self.original_text.clear()
        self.text_view.clear()
        self.save_button.setEnabled(False)
        self.status.setText("Starting…")
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.setWindowTitle(f"{os.path.basename(self.path)} → {languages.name(self.target)} — EchoSub Lens")
        self.text_view.setLayoutDirection(Qt.RightToLeft if self.target in languages.RTL else Qt.LeftToRight)
        threading.Thread(target=self._work, args=(self.run, self._cancel, self.translator, self.target,
                                                  self.ocr_language), name="lens-file", daemon=True).start()

    # ---- the worker thread -----------------------------------------------------
    def _work(self, run, cancel, translator, target, ocr_language):
        try:
            if self.kind in ("image", "pdf"):
                self._work_pages(run, cancel, translator, target, ocr_language)
            else:
                self._work_texts(run, cancel, translator, target)
            if not cancel.is_set():
                self.signals.done.emit(run)
        except Exception as e:
            if cancel.is_set():
                return  # replaced or closed: its troubles no longer matter
            log.exception("Could not translate %s", self.path)
            self.signals.failed.emit(run, str(e) or e.__class__.__name__)

    def _work_pages(self, run, cancel, translator, target, ocr_language):
        reader = lens_ocr.Reader(ocr_language)
        total = lens_files.page_count(self.path)
        for index, image in enumerate(lens_files.pages(self.path)):
            if cancel.is_set():
                return
            self.signals.progress.emit(run, f"Reading and translating page {index + 1} of {total}…",
                                       100 * index // max(total, 1))
            blocks = lens_files.translate_page(reader, translator, image, target)
            if cancel.is_set():
                return
            self.signals.page.emit(run, index, image, blocks)

    def _work_texts(self, run, cancel, translator, target):
        if self.kind == "word":
            texts = lens_files.word_texts(self.path)
        else:
            texts = lens_files.text_lines(lens_files.read_text(self.path))
        self.signals.progress.emit(run, f"Translating {len(texts)} paragraphs…", 0)
        translations = {}
        step = 40
        for start in range(0, len(texts), step):
            if cancel.is_set():
                return
            chunk = texts[start:start + step]
            translations.update(zip(chunk, translator.translate_many(chunk, target)))
            done = min(start + step, len(texts))
            self.signals.progress.emit(run, f"Translated {done} of {len(texts)} paragraphs…",
                                       100 * done // len(texts))
        if not cancel.is_set():
            self.signals.texts.emit(run, translations)

    # ---- results (Qt thread) ---------------------------------------------------
    def _progress(self, run, text, percent):
        if run != self.run:
            return
        self.status.setText(text)
        self.progress_bar.setValue(percent)
        self.progress_bar.show()

    def _page_done(self, run, index, image, blocks):
        if run != self.run:
            return
        self.originals[index], self.blocks[index] = image, blocks
        self.rendered[index] = lens_files.render(image, blocks, self.target, self.style)
        label = QLabel()
        label.setAlignment(Qt.AlignCenter)
        self.page_labels.append(label)
        self.page_box.addWidget(label)
        self._show_page(index)
        self._fill_text()

    def _show_pages(self, *_):
        for index in self.rendered:
            self._show_page(index)

    def _show_page(self, index):
        image = self.originals[index] if self.original_button.isChecked() else self.rendered[index]
        width = max(self.scroll.viewport().width() - 24, 200)
        pixmap = QPixmap.fromImage(image)
        if pixmap.width() > width:
            pixmap = pixmap.scaledToWidth(width, Qt.SmoothTransformation)
        self.page_labels[index].setPixmap(pixmap)

    def _fill_text(self):
        parts = []
        for index in sorted(self.blocks):
            if len(self.blocks) > 1:
                parts.append(f"— Page {index + 1} —")
            parts += [block.translation for block in self.blocks[index]]
        self.text_view.setPlainText("\n".join(parts))

    def _texts_done(self, run, translations):
        if run != self.run:
            return
        self.translations = translations
        if self.kind == "word":
            original = "\n\n".join(lens_files.word_texts(self.path))
            translated = "\n\n".join(translations.get(t, t) for t in lens_files.word_texts(self.path))
        else:
            original = lens_files.read_text(self.path)
            translated = lens_files.translated_text(original, translations)
        self.original_text.setPlainText(original)
        self.text_view.setPlainText(translated)

    def _done(self, run):
        if run != self.run:
            return
        self.finished = True
        self.save_button.setEnabled(True)
        self.progress_bar.hide()
        self.status.setText("Done — select text on the right to copy it, or save a translated copy.")

    def _failed(self, run, message):
        if run != self.run:
            return
        self.progress_bar.hide()
        self.status.setText(f"Could not translate this file: {message}")

    def restyle(self, style):
        """The Lens look changed: draw the translations again."""
        self.style = style
        for index in list(self.rendered):
            self.rendered[index] = lens_files.render(self.originals[index], self.blocks[index], self.target, style)
        self._show_pages()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._show_pages()

    # ---- saving ----------------------------------------------------------------
    def save(self):
        suggested = self.suggested_name()
        filters = {"image": "PNG picture (*.png);;JPEG picture (*.jpg)", "pdf": "PDF document (*.pdf)",
                   "word": "Word document (*.docx)"}.get(self.kind, f"Text file (*{os.path.splitext(self.path)[1]})")
        out, _ = QFileDialog.getSaveFileName(self, "Save the translated copy", suggested, filters)
        if not out:
            return
        try:
            self.write(out)
        except Exception as e:
            log.exception("Could not save %s", out)
            QMessageBox.warning(self, "EchoSub Lens", f"Could not save the file: {e}")
            return
        self.status.setText(f"Saved: {out}")

    def write(self, out):
        if os.path.abspath(out) == os.path.abspath(self.path):
            raise ValueError("Choose another name: the original file is kept as it is")
        if self.kind in ("image", "pdf"):
            images = [self.rendered[i] for i in sorted(self.rendered)]
            sizes = None
            if self.kind == "pdf":
                from PySide6.QtCore import QSizeF

                sizes = [QSizeF(img.width() / lens_files.PDF_SCALE, img.height() / lens_files.PDF_SCALE)
                         for img in images]
            lens_files.save_pages(images, out, sizes)
        elif self.kind == "word":
            lens_files.write_word(self.path, out, self.translations)
        else:
            lens_files.write_text(out, lens_files.translated_text(lens_files.read_text(self.path),
                                                                  self.translations))

    def closeEvent(self, event):
        self._cancel.set()
        super().closeEvent(event)

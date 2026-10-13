# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: the capture window — a part of the screen, kept as a picture, with its text.

The picture is read at once (Windows OCR) and translated with the language and the translator chosen at the
top. The user switches between the original and the translated picture, selects and copies the text, copies
or saves the picture, or closes the window.
"""
import logging
import os
import threading
import time
import unicodedata

from urllib.parse import quote_plus

from PySide6.QtCore import QBuffer, QIODevice, QMimeData, QObject, QStandardPaths, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMenu, QPlainTextEdit,
                               QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget)

from . import languages, lens_files, lens_ocr, lens_style, lens_translate
from .lens_about import ACCENT
from .lens_file_window import TRANSLATOR_HINTS
from .lens_menu import fill_translator_combo
from .lens_overlay import split_like
from .lens_text_window import plain, reading_order
from .lens_window import STYLE

log = logging.getLogger(__name__)

ORIGINAL, TRANSLATED = "original", "translated"
TITLE = "Capture — EchoSub Lens"
NO_TEXT = "No text was found in this picture"
MAX_SCREEN_SHARE = 0.8      # the window is never larger than this part of the screen
TEXT_HEIGHT = 150           # the text box under the picture
MIN_SCALE = 0.5             # a picture is never shown smaller than this: it scrolls instead

CAPTURE_STYLE = STYLE + f"""
QWidget#capture_window {{ background: #111615; }}
QPlainTextEdit {{ background: #151B1A; border: 1px solid #24302E; border-radius: 8px; padding: 6px;
                  selection-background-color: #16302C; font-size: 12pt; }}
QScrollArea#picture {{ background: #0B0F0E; border: 1px solid #24302E; border-radius: 8px; }}
QLabel#status {{ color: #9CA3AF; }}
QPushButton#segment {{ border-radius: 0; padding: 6px 16px; }}
QPushButton#segment:checked {{ background: #16302C; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton#primary {{ background: {ACCENT}; color: #04211D; border: none; font-weight: 700; }}
QPushButton#primary:hover {{ background: #2DD4BF; }}
QSplitter::handle {{ background: transparent; }}
"""


def right_to_left(text):
    """Most of the letters of `text` are written right to left (Arabic, Hebrew…)."""
    rtl = ltr = 0
    for ch in text:
        kind = unicodedata.bidirectional(ch)
        if kind in ("R", "AL"):
            rtl += 1
        elif kind == "L":
            ltr += 1
    return rtl > ltr


def read_lines(image, language):
    """The lines of `image`, read by a Reader made and closed in the calling (worker) thread."""
    from .lens_scanner import binarized, has_contrast

    reader = lens_ocr.Reader(language)
    try:
        lines = reader.read(image)
        if len(lines) < 2 and has_contrast(image):  # little or nothing read: once more in black and white
            lines = max(lines, reader.read(binarized(image)), key=len)
        return lines
    finally:
        reader.close()


def blocks_of(lines):
    """Every block of text in the picture, in reading order (text in the target language too)."""
    return reading_order([b for b in lens_ocr.group_blocks(lines) if b.text.strip()])


def needs_translation(block, target):
    return lens_ocr.worth_translating(block.text) and not lens_translate.already_in(block.text, target)


def translated(block):
    """The block was really translated: something to draw over the picture."""
    return bool(block.translation) and block.translation.strip() != block.text.strip()


def shown_text(blocks):
    """The text box: each line of the picture on a line of its own (`blocks` holds the lines of each block);
    a block of several lines (a paragraph) stands apart from the rest with an empty line."""
    out = []
    for lines in blocks:
        if out and (len(lines) > 1 or out[-1][1]):
            out.append(("", False))
        out.extend((text, len(lines) > 1) for text in lines)
    return "\n".join(text for text, _ in out)


GOOGLE_SEARCH = "https://www.google.com/search?q="
GOOGLE_LENS = "https://lens.google.com/"
LENS_UPLOAD = "https://lens.google.com/v3/upload"
BROWSER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                 "Chrome/129.0.0.0 Safari/537.36")
MAX_QUERY = 1500  # characters of text in a Google search (a longer address is refused)


def text_search_url(text):
    """Google's search for `text` (the direction marks left out, every line one space)."""
    words = " ".join(plain(text).split())
    return GOOGLE_SEARCH + quote_plus(words[:MAX_QUERY])


def png_bytes(image):
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def lens_search_url(png, session=None):
    """Sends the picture to Google Lens, as its own page does, and returns the address of the results.
    Raises when Google does not answer with one."""
    import requests

    session = session or requests.Session()
    response = session.post(LENS_UPLOAD, params={"hl": "en", "re": "df", "st": str(int(time.time() * 1000)),
                                                 "ep": "gsbubb"},
                            files={"encoded_image": ("capture.png", png, "image/png")},
                            headers={"User-Agent": BROWSER_AGENT}, allow_redirects=False, timeout=30)
    url = response.headers.get("Location", "")
    if response.status_code not in (301, 302, 303, 307) or not url.startswith("https://"):
        raise RuntimeError(f"Google Lens answered {response.status_code}")
    return url


class _Signals(QObject):
    # Each signal starts with its run: what an older run (another language, another translator) sends is dropped.
    read = Signal(int, object)         # run, the lines read in the picture
    translated = Signal(int, object)   # run, the blocks with their translations
    progress = Signal(int, str)
    failed = Signal(int, str)
    lens_found = Signal(str)           # the address of Google Lens's results for the picture
    lens_failed = Signal(str)


class TextBox(QPlainTextEdit):
    """Read-only text whose copies (Ctrl+C, the right-click menu) leave the invisible direction marks out."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)

    def createMimeDataFromSelection(self):
        data = QMimeData()
        data.setText(plain(self.textCursor().selectedText().replace("\u2029", "\n")))
        return data


class CaptureWindow(QWidget):
    """A captured part of the screen (`image`, a QImage in the screen's pixels), read and translated.

    `lens` is the LensApp: its language, translator, OCR language and look are the starting choices; the
    translators come from `lens.translator_for()` and go back with `lens.release_translator()`."""

    def __init__(self, image, lens, parent=None):
        super().__init__(parent, Qt.Window | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("capture_window")
        self.setWindowTitle(TITLE)
        self.setStyleSheet(CAPTURE_STYLE)
        self.lens = lens
        self.ratio = image.devicePixelRatio() or 1.0
        self.picture = QImage(image)           # read and drawn in its own pixels
        self.picture.setDevicePixelRatio(1.0)
        self.taken = time.localtime()
        self.target = lens.target
        self.translator_choice = lens.cfg.get("translator") or lens_translate.SAME
        self.ocr_language = lens.cfg.get("ocr_language") or lens_ocr.AUTO
        overlay = getattr(lens, "overlay", None)
        self.style = getattr(overlay, "style", None) or lens_style.merged(lens.cfg.get("style"))
        self.translator = lens.translator_for(self.translator_choice)
        self.lines = None          # what the reading found (None: not read yet)
        self.blocks = []           # in reading order; translations once translated
        self.rendered = None       # the picture with the translations drawn over it
        self.view = ORIGINAL
        self._auto_view = True     # the first translation chooses the view, until the user chooses one
        self.finished = False      # the last run is over (done, nothing found, or failed)
        self.run = 0
        self._cancel = threading.Event()
        self._released = False
        self._lens_busy = False  # a picture on its way to Google Lens
        self.signals = _Signals()
        self.signals.read.connect(self._read_done)
        self.signals.translated.connect(self._translated)
        self.signals.progress.connect(self._progress)
        self.signals.failed.connect(self._failed)
        self.signals.lens_found.connect(self._lens_found)
        self.signals.lens_failed.connect(self._lens_failed)
        self._build()
        self._size_to_picture()
        self._show_view()
        self._start()

    # ---- layout --------------------------------------------------------------
    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self.original_button = QPushButton("Original")
        self.translated_button = QPushButton("Translated")
        self.view_group = QButtonGroup(self)
        self.view_group.setExclusive(True)
        for button, view, tip in ((self.original_button, ORIGINAL, "The picture and its text as they are"),
                                  (self.translated_button, TRANSLATED, "The picture and its text translated")):
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, v=view: self.set_view(v))
            self.view_group.addButton(button)
        segments = QHBoxLayout()
        segments.setSpacing(0)
        segments.addWidget(self.original_button)
        segments.addWidget(self.translated_button)
        top.addLayout(segments)
        top.addSpacing(16)
        top.addWidget(QLabel("Translate into"))
        self.target_combo = QComboBox()
        self.target_combo.setMaxVisibleItems(20)
        for code, name in self.lens._target_choices():
            if code is None:
                self.target_combo.insertSeparator(self.target_combo.count())
            elif code:  # "" (Lens's default) is left out: the window shows the language itself
                self.target_combo.addItem(name, code)
        if self.target_combo.findData(self.target) < 0:
            self.target_combo.insertItem(0, languages.name(self.target), self.target)
        self.target_combo.setCurrentIndex(self.target_combo.findData(self.target))
        self.target_combo.setToolTip("The language the picture's text is translated into")
        self.target_combo.currentIndexChanged.connect(self._target_chosen)
        top.addWidget(self.target_combo)
        top.addWidget(QLabel("with"))
        self.translator_combo = QComboBox()
        fill_translator_combo(self.translator_combo, TRANSLATOR_HINTS)
        self.translator_combo.setCurrentIndex(max(self.translator_combo.findData(self.translator_choice), 0))
        self.translator_combo.setToolTip("The translator: the online ones get the text; NLLB works offline")
        self.translator_combo.currentIndexChanged.connect(self._translator_chosen)
        for combo, letters in ((self.target_combo, 14), (self.translator_combo, 22)):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(letters)  # long names do not make the window wide
        top.addWidget(self.translator_combo)
        top.addStretch(1)
        layout.addLayout(top)

        self.status = QLabel("Starting…")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("picture")
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setWidgetResizable(False)
        self.picture_label = QLabel()
        self.picture_label.setAlignment(Qt.AlignCenter)
        self.picture_label.setStyleSheet("background: transparent;")
        self.scroll.setWidget(self.picture_label)
        self.text_box = TextBox()
        self.text_box.setMinimumHeight(60)
        self.splitter.addWidget(self.scroll)
        self.splitter.addWidget(self.text_box)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        layout.addWidget(self.splitter, 1)

        bottom = QHBoxLayout()
        self.copy_text_button = QPushButton("Copy text")
        self.copy_text_button.setToolTip("Copy all the text of this view (select a part to copy only that)")
        self.copy_text_button.clicked.connect(self.copy_text)
        self.copy_picture_button = QPushButton("Copy picture")
        self.copy_picture_button.setToolTip("Copy the picture of this view, to paste it anywhere")
        self.copy_picture_button.clicked.connect(self.copy_picture)
        self.search_button = QPushButton("Search Google ▾")
        self.search_button.setToolTip("Search Google for this picture (Google Lens) or for its text")
        self.search_menu = QMenu(self.search_button)
        self.search_picture_action = self.search_menu.addAction("Search by the picture (Google Lens)",
                                                                self.search_picture)
        self.search_text_action = self.search_menu.addAction("Search for the text", self.search_text)
        self.search_menu.aboutToShow.connect(self._search_menu_opening)
        self.search_button.setMenu(self.search_menu)
        self.save_button = QPushButton("Save picture…")
        self.save_button.setObjectName("primary")
        self.save_button.setToolTip("Save the picture of this view as PNG or JPG")
        self.save_button.clicked.connect(self.save)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip("Close this window (Esc)")
        self.cancel_button.clicked.connect(self.close)
        for widget in (self.copy_text_button, self.copy_picture_button, self.search_button):
            bottom.addWidget(widget)
        bottom.addStretch(1)
        bottom.addWidget(self.save_button)
        bottom.addWidget(self.cancel_button)
        layout.addLayout(bottom)
        QShortcut(QKeySequence(Qt.Key_Escape), self, self.close)

    def _size_to_picture(self):
        """As large as the picture (plus the bars and the text), never more than 80 % of the screen."""
        screen = (self._screen() or QGuiApplication.primaryScreen()).availableGeometry()
        width = round(self.picture.width() / self.ratio)
        height = round(self.picture.height() / self.ratio)
        max_w, max_h = int(screen.width() * MAX_SCREEN_SHARE), int(screen.height() * MAX_SCREEN_SHARE)
        chrome_h = 190 + TEXT_HEIGHT
        w = max(640, min(width + 40, max_w))
        h = max(420, min(height + chrome_h, max_h))
        self.resize(w, h)
        self.splitter.setSizes([max(h - chrome_h, 120), TEXT_HEIGHT])
        self.move(screen.x() + (screen.width() - w) // 2, screen.y() + (screen.height() - h) // 2)

    @staticmethod
    def _screen():
        from PySide6.QtGui import QCursor

        return QGuiApplication.screenAt(QCursor.pos())

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
        """Translate the text again, into `code`."""
        self.target = code
        self._sync_combo(self.target_combo, code)
        self._start()

    def set_translator(self, choice):
        """Translate the text again with `choice` (a key of lens_translate.CHOICES); the old one is let go."""
        previous = self.translator
        self.translator = self.lens.translator_for(choice)
        self.translator_choice = choice
        self._sync_combo(self.translator_combo, choice)
        self._start()
        if previous is not None and previous is not self.translator:
            self.lens.release_translator(previous)  # does nothing to the translator Lens itself uses

    def key_changed(self, service):
        """The user's key for the translator `service` changed: a translator of that service is replaced by a
        new one (with the new key), and the text is translated again. True when it was."""
        if self._released or getattr(self.translator, "key", None) != service:
            return False
        self.set_translator(self.translator_choice)
        return True

    @staticmethod
    def _sync_combo(combo, value):
        index = combo.findData(value)
        if index >= 0 and index != combo.currentIndex():
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)

    # ---- the work ------------------------------------------------------------------
    def _start(self):
        """(Re)start: read the picture when it was not read yet, then translate its text."""
        self._cancel.set()  # the run before stops at its next step; what it still sends is dropped
        self._cancel = threading.Event()
        self.run += 1
        self.rendered = None
        self.finished = False
        for block in self.blocks:
            block.translation = ""
        self.status.setText("Reading the text…" if self.lines is None else
                            f"Translating into {languages.name(self.target)}…")
        if self.view == TRANSLATED:
            self._show_view()
        lines = None if self.lines is None else list(self.lines)
        threading.Thread(target=self._work, args=(self.run, self._cancel, self.translator, self.target,
                                                  self.ocr_language, lines),
                         name="lens-capture", daemon=True).start()

    def _work(self, run, cancel, translator, target, ocr_language, lines):
        """Worker thread: reads (once), then translates. Never touches a widget."""
        stage = "read"
        try:
            if lines is None:
                self.signals.progress.emit(run, "Reading the text…")
                lines = read_lines(self.picture, ocr_language)
                if cancel.is_set():
                    return
                self.signals.read.emit(run, lines)
            blocks = blocks_of(lines)
            todo = [b for b in blocks if needs_translation(b, target)]
            if todo:
                stage = "translate"
                self.signals.progress.emit(run, f"Translating into {languages.name(target)}…")

                def report(done, total):
                    if not cancel.is_set():
                        self.signals.progress.emit(run, f"Translating… {done} of {total}")
                texts = translator.translate_many([b.text for b in todo], target, progress=report)
                if cancel.is_set():
                    return
                from .lens_scanner import colors_of, image_array, keep_dash

                pixels = image_array(self.picture)
                for block, text in zip(todo, texts):
                    block.translation = keep_dash(block.text, text) or ""
                    block.background, block.foreground = colors_of(pixels, block.box)
            if not cancel.is_set():
                self.signals.translated.emit(run, blocks)
        except Exception as e:
            if cancel.is_set():
                return  # replaced or closed: its troubles no longer matter
            log.exception("Could not %s the captured picture", stage)
            what = "read the text" if stage == "read" else "translate the text"
            self.signals.failed.emit(run, f"Could not {what}: {str(e) or e.__class__.__name__}")

    # ---- results (Qt thread) ---------------------------------------------------
    def _progress(self, run, text):
        if run == self.run:
            self.status.setText(text)

    def _read_done(self, run, lines):
        if run != self.run:
            return
        self.lines = lines
        self.blocks = blocks_of(lines)
        if not self.blocks:
            self.status.setText(NO_TEXT)
        self._show_view()

    def _translated(self, run, blocks):
        if run != self.run:
            return
        self.blocks = blocks
        shown = [b for b in blocks if translated(b)]
        self.rendered = lens_files.render(self.picture, shown, self.target, self.style) if shown else None
        self.finished = True
        name = languages.name(self.target)
        if not blocks:
            self.status.setText(NO_TEXT)
        elif not shown:
            self.status.setText(f"The text is already in {name} — select it to copy it.")
        else:
            self.status.setText(f"Translated into {name} — select text to copy it, or save the picture.")
        if self._auto_view:  # the first result chooses the view; after that it is the user's choice
            self._auto_view = False
            self.view = TRANSLATED if shown else ORIGINAL
        self._show_view()

    def _failed(self, run, message):
        if run != self.run:
            return
        self.finished = True
        self.status.setText(message)
        self.view = ORIGINAL
        self._show_view()

    def restyle(self, style):
        """The Lens look changed: draw the translations again."""
        self.style = style
        shown = [b for b in self.blocks if translated(b)]
        if self.rendered is not None and shown:
            self.rendered = lens_files.render(self.picture, shown, self.target, style)
            self._show_view()

    # ---- the two views ---------------------------------------------------------------
    def set_view(self, view):
        self._auto_view = False
        self.view = TRANSLATED if view == TRANSLATED else ORIGINAL
        self._show_view()

    def current_image(self):
        """The picture of this view, in the screen's pixels (devicePixelRatio as captured)."""
        image = QImage(self.rendered if self.view == TRANSLATED and self.rendered is not None else self.picture)
        image.setDevicePixelRatio(self.ratio)
        return image

    def original_text(self):
        return shown_text([[line.text for line in b.lines] for b in self.blocks])

    def translated_text(self):
        if not self.finished:
            return ""
        return shown_text([split_like(b.translation, len(b.lines)).split("\n") if translated(b)
                           else [line.text for line in b.lines] for b in self.blocks])

    def current_text(self):
        return self.translated_text() if self.view == TRANSLATED else self.original_text()

    def _show_view(self):
        self.original_button.setChecked(self.view == ORIGINAL)
        self.translated_button.setChecked(self.view == TRANSLATED)
        self._show_picture()
        text = self.current_text()
        if self.view == TRANSLATED:
            rtl = self.target in languages.RTL
            placeholder = "The translated text appears here." if self.blocks or self.lines is None else NO_TEXT
        else:
            rtl = right_to_left(text)
            placeholder = "Reading the text…" if self.lines is None else NO_TEXT
        if not rtl:
            text = "\n".join(line.lstrip("\u200f") for line in text.split("\n"))
        direction = Qt.RightToLeft if rtl else Qt.LeftToRight
        self.text_box.setLayoutDirection(direction)
        option = self.text_box.document().defaultTextOption()
        option.setTextDirection(direction)
        option.setAlignment(Qt.AlignRight if rtl else Qt.AlignLeft)
        self.text_box.document().setDefaultTextOption(option)
        self.text_box.setPlaceholderText(placeholder)
        if text != self.text_box.toPlainText():
            self.text_box.setPlainText(text)

    def _show_picture(self):
        """The picture fits the space, keeps its shape, and is drawn sharp in the window's own pixels."""
        image = self.rendered if self.view == TRANSLATED and self.rendered is not None else self.picture
        logical_w, logical_h = image.width() / self.ratio, image.height() / self.ratio
        viewport = self.scroll.viewport().size()
        avail_w, avail_h = max(viewport.width() - 8, 50), max(viewport.height() - 8, 50)
        scale = min(1.0, avail_w / max(logical_w, 1), avail_h / max(logical_h, 1))
        scale = max(scale, min(MIN_SCALE, 1.0))
        screen_ratio = self.devicePixelRatioF() or 1.0
        w, h = max(1, round(logical_w * scale)), max(1, round(logical_h * scale))
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(1.0)
        pw, ph = max(1, round(w * screen_ratio)), max(1, round(h * screen_ratio))
        if (pw, ph) != (pixmap.width(), pixmap.height()):
            pixmap = pixmap.scaled(pw, ph, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        pixmap.setDevicePixelRatio(screen_ratio)
        self.picture_label.setPixmap(pixmap)
        self.picture_label.resize(w, h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._show_picture()

    def showEvent(self, event):
        super().showEvent(event)
        self._show_picture()

    # ---- copying and saving ----------------------------------------------------------
    def copy_text(self):
        text = plain(self.current_text())
        QGuiApplication.clipboard().setText(text)
        self.lens._notify("Text copied" if text else "There is no text to copy")

    def copy_picture(self):
        QGuiApplication.clipboard().setImage(self.current_image())
        self.lens._notify("Picture copied")

    # ---- searching Google ----------------------------------------------------------
    def _search_text(self):
        """The text selected, or else all the text of this view."""
        selected = self.text_box.textCursor().selectedText().replace("\u2029", "\n")
        return plain(selected or self.current_text()).strip()

    def _search_menu_opening(self):
        text = self._search_text()
        selected = self.text_box.textCursor().hasSelection()
        self.search_text_action.setEnabled(bool(text))
        self.search_text_action.setText("Search for the selected text" if selected and text else
                                        "Search for the text" if text else "Search for the text (none read)")

    def search_text(self):
        text = self._search_text()
        if not text:
            self.lens._notify("There is no text to search for")
            return
        QDesktopServices.openUrl(QUrl(text_search_url(text)))
        self.lens._notify("Searching Google for the text, in your browser")

    def search_picture(self):
        """The picture of this view goes to Google Lens (only now, when asked); its results open in the browser."""
        if self._lens_busy:
            return
        self._lens_busy = True
        self.search_button.setEnabled(False)
        self.status.setText("Sending the picture to Google Lens…")
        png = png_bytes(self.current_image())

        def work():
            try:
                self.signals.lens_found.emit(lens_search_url(png))
            except Exception as e:
                log.warning("Google Lens search failed: %s", e)
                self.signals.lens_failed.emit(str(e) or e.__class__.__name__)
        threading.Thread(target=work, name="lens-google-lens", daemon=True).start()

    def _lens_found(self, url):
        self._lens_busy = False
        self.search_button.setEnabled(True)
        QDesktopServices.openUrl(QUrl(url))
        self.status.setText("Google Lens's results are open in your browser.")
        self.lens._notify("Google Lens opened in your browser")

    def _lens_failed(self, message):
        """No answer from Google: the picture goes on the clipboard and Google Lens opens, to paste it there."""
        self._lens_busy = False
        self.search_button.setEnabled(True)
        QGuiApplication.clipboard().setImage(self.current_image())
        QDesktopServices.openUrl(QUrl(GOOGLE_LENS))
        self.status.setText(f"Could not send the picture ({message}). It is copied: paste it in Google Lens (Ctrl+V).")
        self.lens._notify("Picture copied — paste it in Google Lens (Ctrl+V)")

    def suggested_name(self):
        stamp = time.strftime("%Y-%m-%d %H-%M-%S", self.taken)
        if self.view == TRANSLATED and self.rendered is not None:
            return f"Capture {stamp} ({languages.name(self.target)}).png"
        return f"Capture {stamp}.png"

    def save(self):
        folder = self.lens.cfg.get("last_folder") or QStandardPaths.writableLocation(
            QStandardPaths.PicturesLocation) or ""
        suggested = os.path.join(folder, self.suggested_name()) if folder else self.suggested_name()
        out, chosen = QFileDialog.getSaveFileName(self, "Save the picture", suggested,
                                                  "PNG picture (*.png);;JPEG picture (*.jpg *.jpeg)")
        if not out:
            return None
        if os.path.splitext(out)[1].lower() not in (".png", ".jpg", ".jpeg"):
            out += ".jpg" if "JPEG" in (chosen or "") else ".png"
        try:
            self.write(out)
        except Exception as e:
            log.exception("Could not save %s", out)
            self.status.setText(f"Could not save the picture: {e}")
            return None
        self.lens._set(last_folder=os.path.dirname(out))
        self.status.setText(f"Picture saved: {out}")
        self.lens._notify(f"Picture saved: {os.path.basename(out)}")
        return out

    def write(self, out):
        image = self.current_image()
        image.setDevicePixelRatio(1.0)
        if out.lower().endswith((".jpg", ".jpeg")):
            image = image.convertToFormat(QImage.Format_RGB32)  # JPEG has no transparency
            ok = image.save(out, "JPG", 92)
        else:
            ok = image.save(out, "PNG")
        if not ok:
            raise OSError(f"Could not write {out}")

    # ---- closing ---------------------------------------------------------------------
    def release(self):
        """Stop the work and hand back the window's own translator (Lens keeps the one it uses)."""
        self._cancel.set()
        if not self._released:
            self._released = True
            if self.translator is not None:
                self.lens.release_translator(self.translator)

    def closeEvent(self, event):
        self.release()
        super().closeEvent(event)

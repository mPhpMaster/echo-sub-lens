# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: its window — every option on a page of its own, each with a line that says what it does.

A click on the tray icon opens it. Closing it only hides it: Lens keeps running by the clock.
"""
import os

from PySide6.QtCore import QRect, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QMessageBox, QPushButton, QRadioButton, QScrollArea,
                               QStackedWidget, QVBoxLayout, QWidget)

from . import APP_NAME, __version__, config, languages, lens_follow, lens_ocr, lens_translate
from .lens_about import ACCENT, AboutPage
from .lens_menu import LANGUAGE_SETTINGS, PEEK_CHOICES, screen_label, translator_groups, translator_help
from . import lens_shortcuts
from .lens_shortcuts import ShortcutsPage
from .lens_style import AppearancePage

PAGES = (("translate", "Translate"), ("languages", "Languages"), ("translator", "Translator"),
         ("appearance", "Appearance"), ("shortcuts", "Keyboard shortcuts"), ("about", "About"))
DEFAULT_SIZE = (860, 640)

DEEPL_KEY_URL = "https://www.deepl.com/pro-api"
PRIVACY_NOTE = "Online translators get the text Lens reads; offline ones keep everything on this PC."

STYLE = f"""
QWidget#lens_window, QWidget#page {{ background: #111615; }}
QWidget {{ color: #E5E7EB; font-size: 10pt; }}
QScrollArea {{ background: #111615; border: none; }}
QWidget#sidebar {{ background: #0B0F0E; border-right: 1px solid #1F2725; }}
QListWidget#nav {{ background: transparent; border: none; outline: 0; font-size: 11pt; }}
QListWidget#nav::item {{ padding: 10px 14px; margin: 2px 8px; border-radius: 8px; color: #C7CDD3; }}
QListWidget#nav::item:hover:!selected {{ background: #151C1B; }}
QListWidget#nav::item:selected {{ background: #16302C; color: white; }}
QLabel#brand {{ font-size: 12pt; font-weight: 700; }}
QLabel#title {{ font-size: 18pt; font-weight: 700; }}
QLabel#section {{ font-size: 12pt; font-weight: 700; color: {ACCENT}; padding-top: 14px; }}
QLabel#muted, QLabel#help {{ color: #9CA3AF; }}
QLabel#help {{ padding-left: 28px; padding-bottom: 6px; }}
QLabel#option {{ font-size: 11pt; font-weight: 600; }}
QFrame#card {{ background: #172120; border: 1px solid #24302E; border-radius: 12px; }}
QLabel#state {{ font-size: 16pt; font-weight: 700; }}
QPushButton {{ background: #1F2928; border: 1px solid #2B3432; border-radius: 6px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #6B7280; }}
QPushButton#start {{ background: {ACCENT}; color: #04211D; border: none; border-radius: 10px; padding: 14px 30px;
                     font-size: 13pt; font-weight: 700; }}
QPushButton#start:hover {{ background: #2DD4BF; }}
QPushButton#start[running="true"] {{ background: #B91C1C; color: white; }}
QPushButton#start[running="true"]:hover {{ background: #DC2626; }}
QPushButton#flat {{ background: transparent; border: none; color: {ACCENT}; padding: 2px 28px; text-align: left; }}
QPushButton#flat:hover {{ color: #2DD4BF; text-decoration: underline; }}
QPushButton#exit {{ background: transparent; border: none; color: #9CA3AF; padding: 8px 22px; text-align: left; }}
QPushButton#exit:hover {{ color: #FCA5A5; }}
QComboBox, QSpinBox, QKeySequenceEdit {{ background: #1A2120; border: 1px solid #2B3432; border-radius: 6px;
                                         padding: 5px 8px; min-height: 20px; }}
QLineEdit {{ background: #1A2120; color: #E5E7EB; border: 1px solid #2B3432; border-radius: 4px;
             padding: 3px 6px; }}
QComboBox QAbstractItemView {{ background: #1A2120; selection-background-color: #16302C; }}
QTableWidget {{ background: #151B1A; gridline-color: #24302E; border: 1px solid #24302E; }}
QHeaderView::section {{ background: #1A2120; color: #9CA3AF; border: none; padding: 6px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2B3432; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3B4643; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #2B3432; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none;
                                                                                        height: 0; width: 0; }}
QRadioButton, QCheckBox#option {{ font-size: 11pt; font-weight: 600; spacing: 8px; }}
"""


def device_text(base_cfg):
    """Where the offline translators run on this PC, in plain words."""
    from . import cuda_libs

    if base_cfg.get("device", "cuda") in ("cuda", "vulkan") and cuda_libs.available():
        return "On this PC the offline translators run on your NVIDIA graphics card."
    return ("On this PC the offline translators run on the processor (no NVIDIA graphics card libraries were "
            "found): slower than a graphics card, but they work.")


class LensWindow(QWidget):
    """The window of `lens` (a LensApp): every change goes through LensApp, so the menu and the notes follow."""

    def __init__(self, lens):
        super().__init__()
        self.lens = lens
        self._syncing = False
        self.setObjectName("lens_window")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(os.path.join(config.ASSETS_DIR, "echosub.ico")))
        self.setStyleSheet(STYLE)
        self.setMinimumSize(720, 520)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._sidebar())
        self.stack = QStackedWidget()
        row.addWidget(self.stack, 1)
        self._translate_page()
        self._languages_page()
        self._translator_page()
        self._appearance_page()
        self._shortcuts_page()
        self._about_page()
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self._restore_geometry()
        self.refresh()

    # ---- the frame -----------------------------------------------------------
    def _sidebar(self):
        side = QWidget()
        side.setObjectName("sidebar")
        side.setAttribute(Qt.WA_StyledBackground, True)
        side.setFixedWidth(220)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(0, 18, 0, 12)
        brand = QHBoxLayout()
        brand.setContentsMargins(18, 0, 12, 12)
        icon = QLabel()
        icon.setPixmap(QIcon(os.path.join(config.ASSETS_DIR, "echosub.ico")).pixmap(QSize(32, 32)))
        brand.addWidget(icon)
        name = QLabel(f"{APP_NAME}<br><span style='color:#9CA3AF; font-size:8.5pt; font-weight:400'>"
                      f"v{__version__}</span>")
        name.setObjectName("brand")
        brand.addWidget(name, 1)
        layout.addLayout(brand)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.addItems([title for _, title in PAGES])
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.nav, 1)
        note = QLabel("Closing this window keeps Lens running — its icon is by the clock.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        note.setContentsMargins(22, 0, 12, 4)
        note.setStyleSheet("font-size: 8.5pt;")
        layout.addWidget(note)
        quit_button = QPushButton("Exit EchoSub Lens")
        quit_button.setObjectName("exit")
        quit_button.setCursor(Qt.PointingHandCursor)
        quit_button.clicked.connect(self.lens.quit)
        layout.addWidget(quit_button)
        return side

    def _page(self, title, intro):
        content = QWidget()
        content.setObjectName("page")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(34, 26, 34, 26)
        layout.setSpacing(4)
        heading = QLabel(title)
        heading.setObjectName("title")
        layout.addWidget(heading)
        if intro:
            text = QLabel(intro)
            text.setObjectName("muted")
            text.setWordWrap(True)
            layout.addWidget(text)
        layout.addSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        self.stack.addWidget(scroll)
        return layout

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

    def _reset_button(self, layout, page):
        button = QPushButton("Reset this page")
        button.setToolTip("Put the settings on this page back to the defaults")
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(lambda: self.reset_page(page))
        layout.addSpacing(16)
        layout.addWidget(self._row(button))
        return button

    def reset_page(self, page, ask=True):
        title = self.lens.RESET_TITLES.get(page, page)
        if ask and QMessageBox.question(self, "Reset", f"Put the {title} settings back to the defaults?",
                                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        taken = self.lens.reset(page)
        if page == "appearance":
            self.appearance.load(self.lens.overlay.style)
        elif page == "shortcuts":
            self.shortcuts.load(lens_shortcuts.defaults())
            if taken:
                self.shortcuts.show_unavailable(taken)
            self._keys_edited()
        self.refresh()

    @staticmethod
    def _row(*widgets):
        """Widgets side by side, as one line of the page."""
        line = QWidget()
        row = QHBoxLayout(line)
        row.setContentsMargins(0, 4, 0, 0)
        row.setSpacing(10)
        for w in widgets:
            row.addWidget(w)
        row.addStretch(1)
        return line

    @staticmethod
    def _check(text):
        box = QCheckBox(text)
        box.setObjectName("option")
        return box

    def _labeled(self, text, widget):
        label = QLabel(text)
        label.setObjectName("option")
        return self._row(label, widget)

    # ---- the pages -----------------------------------------------------------
    def _translate_page(self):
        layout = self._page("Translate", "")
        card = QFrame()
        card.setObjectName("card")
        inside = QHBoxLayout(card)
        inside.setContentsMargins(22, 18, 22, 18)
        words = QVBoxLayout()
        self.state_title = QLabel()
        self.state_title.setObjectName("state")
        self.state_text = QLabel()
        self.state_text.setObjectName("muted")
        self.state_text.setWordWrap(True)
        words.addWidget(self.state_title)
        words.addWidget(self.state_text)
        inside.addLayout(words, 1)
        self.start_button = QPushButton()
        self.start_button.setObjectName("start")
        self.start_button.setCursor(Qt.PointingHandCursor)
        self.start_button.clicked.connect(lambda: self.lens.toggle())
        inside.addWidget(self.start_button)
        layout.addWidget(card)

        self._section(layout, "What to translate")
        self.modes = QButtonGroup(self)
        self.mode_screen = QRadioButton("A whole screen")
        self.screen_box = QComboBox()
        self.screen_box.activated.connect(lambda _: self._changed(self.lens.use_screen, self.screen_box.currentData()))
        layout.addWidget(self._row(self.mode_screen, self.screen_box))
        self._help(layout, "Every piece of text on one of your monitors.")
        self.mode_area = QRadioButton("An area of the screen")
        self.area_button = QPushButton("Choose area…")
        self.area_button.clicked.connect(lambda: self._changed(self.lens.choose_area))
        layout.addWidget(self._row(self.mode_area, self.area_button))
        self._help(layout, "Drag a rectangle anywhere — only the text inside it is translated.")
        self.mode_mouse = QRadioButton("A lens around the mouse")
        self.lens_box = QComboBox()
        for name, size in lens_follow.SIZES.items():
            self.lens_box.addItem(f"{name.capitalize()} ({size[0]} × {size[1]})", list(size))
        self.lens_box.activated.connect(lambda _: self._changed(self.lens.set_lens_size, self.lens_box.currentData()))
        self.frame_box = QCheckBox("Show its frame")
        self.frame_box.toggled.connect(lambda on: self._changed(self.lens.set_lens_frame, on))
        layout.addWidget(self._row(self.mode_mouse, self.lens_box, self.frame_box))
        self._help(layout, "Only the text near the pointer, in a small box that follows the mouse. Stop on a line "
                           "and its translation appears.")
        self.mode_window = QRadioButton("A window")
        self.window_box = QComboBox()
        self.window_box.setMinimumWidth(320)
        self.window_box.setMaxVisibleItems(20)
        self.window_box.activated.connect(lambda _: self._window_chosen())
        self.windows_button = QPushButton("↻")
        self.windows_button.setToolTip("Read the list of open windows again")
        self.windows_button.setFixedWidth(40)
        self.windows_button.clicked.connect(self._fill_windows)
        layout.addWidget(self._row(self.mode_window, self.window_box, self.windows_button))
        self._help(layout, "Only the text of one program's window — wherever you move it, whatever its size. While "
                           "it is minimized or behind another window, nothing is translated.")
        for i, button in enumerate((self.mode_screen, self.mode_area, self.mode_mouse, self.mode_window)):
            self.modes.addButton(button, i)
        self.modes.idClicked.connect(self._mode_chosen)

        self._section(layout, "Where the translations show")
        self.text_window_box = self._check("Translation window")
        self.text_window_box.toggled.connect(lambda on: self._changed(self.lens.show_text_window, on))
        layout.addWidget(self.text_window_box)
        self._help(layout, "The translated text in a box of its own, above all windows — easy to read, select "
                           "and copy.")
        self.only_window_box = self._check("Only in the translation window")
        self.only_window_box.toggled.connect(self._only_window)
        layout.addWidget(self.only_window_box)
        self._help(layout, "Nothing is drawn over the screen while the translation window is open.")
        self.capture_box = self._check("Show translations in screenshots and screen sharing")
        self.capture_box.toggled.connect(lambda on: self._changed(self.lens._set_capture_visible, on))
        layout.addWidget(self.capture_box)
        self._help(layout, "Discord, Teams, OBS and screenshots show the translated screen. Untick it to keep the "
                           "translations to yourself.")
        self.select_box = self._check("Select mode: translations take the mouse")
        self.select_box.toggled.connect(lambda on: self._changed(self.lens.set_select_mode, on))
        layout.addWidget(self.select_box)
        self._help(layout, "Select and copy translations without holding Ctrl. Off, clicks go through to the page "
                           "underneath — hold Ctrl to select.")
        self.peek_box = QComboBox()
        for seconds, text in PEEK_CHOICES:
            self.peek_box.addItem(text, seconds)
        self.peek_box.activated.connect(lambda _: self._changed(self.lens._set_peek, self.peek_box.currentData()))
        layout.addWidget(self._labeled("Look under the translations for changes", self.peek_box))
        self._help(layout, "How often Lens looks at the page under its translations, for text that changed. "
                           "Less often is lighter on the PC.")

        self._section(layout, "Capture")
        self.capture_button = QPushButton("Capture part of the screen…")
        self.capture_button.clicked.connect(self._capture)
        layout.addWidget(self._row(self.capture_button))
        self._help(layout, "Drag a rectangle: the picture opens in a window with its text, original or translated "
                           "— copy the text or save the picture.")

        self._section(layout, "Files")
        self.file_button = QPushButton("Translate a file…")
        self.file_button.clicked.connect(lambda: self.lens.open_file())
        layout.addWidget(self._row(self.file_button))
        self._help(layout, "A picture, a PDF, a Word document or a text or subtitle file, in a window of its own. "
                           "The original is never changed.")
        self.movie_button = QPushButton("Translate a movie…")
        self.movie_button.clicked.connect(lambda: self.lens.open_movie())
        layout.addWidget(self._row(self.movie_button))
        self._help(layout, "All the text of a movie in a subtitle file (.srt) — from the subtitles inside it, from "
                           "what is said (speech recognition), or from text burned into the picture — then "
                           "translate it.")
        self._reset_button(layout, "translate")
        layout.addStretch(1)

    def _languages_page(self):
        layout = self._page("Languages", "The language you read, and the language of the text on the screen.")
        self.target_box = QComboBox()
        self.target_box.setMaxVisibleItems(20)
        for code, text in self.lens._target_choices():
            if code is None:
                self.target_box.insertSeparator(self.target_box.count())
            else:
                self.target_box.addItem(text, code)
        self.target_box.activated.connect(
            lambda _: self._changed(self.lens.set_target, self.target_box.currentData() or None))
        layout.addWidget(self._labeled("Translate into", self.target_box))
        self._help(layout, "The language the translations are written in. Default is EchoSub's caption language "
                           "(or your Windows language).")
        layout.addSpacing(8)
        self.ocr_box = QComboBox()
        self.ocr_box.activated.connect(
            lambda _: self._changed(self.lens.set_ocr_language, self.ocr_box.currentData()))
        self._fill_ocr_languages()
        layout.addWidget(self._labeled("Text on the screen is in", self.ocr_box))
        self._help(layout, "Windows reads the screen with its own text recognition, on your PC. Automatic uses "
                           "your Windows display languages.")
        add = QPushButton("Add a language to read…")
        add.setObjectName("flat")
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(LANGUAGE_SETTINGS)))
        layout.addWidget(add)
        self._help(layout, "Opens Windows Settings → Time & language → Language: add the language with its "
                           "Optical character recognition feature, then open this window again.")
        self._reset_button(layout, "languages")
        layout.addStretch(1)

    def _fill_ocr_languages(self):
        self.ocr_box.clear()
        self.ocr_box.addItem("Automatic (Windows display languages)", lens_ocr.AUTO)
        for tag in lens_ocr.available_languages():
            self.ocr_box.addItem(f"{languages.name(tag.split('-')[0].lower())} ({tag})", tag)

    def _translator_page(self):
        layout = self._page("Translator", "Who translates the text Lens reads. Only one is in use at a time, so "
                                          "switching frees the memory of the one before.")
        self.privacy_note = QLabel(PRIVACY_NOTE)
        self.privacy_note.setObjectName("muted")
        self.privacy_note.setWordWrap(True)
        layout.addWidget(self.privacy_note)
        layout.addSpacing(8)
        self.translators = QButtonGroup(self)
        self.translator_buttons = {}
        self.key_edits = {}          # translator -> {name in api_keys: QLineEdit}
        self.translator_sections = []
        for title, keys in translator_groups():
            if title:
                self._section(layout, title)
                self.translator_sections.append(title)
            for key in keys:
                button = QRadioButton(lens_translate.CHOICES[key])
                self.translators.addButton(button)
                self.translator_buttons[key] = button
                button.clicked.connect(lambda _=False, key=key: self._changed(self.lens.set_translator, key))
                layout.addWidget(button)
                help_text = translator_help(key)
                if key == lens_translate.SAME:
                    now = lens_translate.resolve(key, self.lens.base_cfg)
                    help_text += f" Here: {lens_translate.CHOICES[now]}."
                self._help(layout, help_text)
                if getattr(lens_translate, "KEY_FIELDS", {}).get(key):
                    self._key_rows(layout, key)
                layout.addSpacing(4)
        self._section(layout, "This PC")
        self.device_label = QLabel(device_text(self.lens.base_cfg))
        self.device_label.setObjectName("muted")
        self.device_label.setWordWrap(True)
        layout.addWidget(self.device_label)
        reset = self._reset_button(layout, "translator")
        reset.setToolTip("Back to the Default translator. Your keys are kept.")
        layout.addStretch(1)

    def _key_rows(self, layout, translator):
        """Under a translator that needs the user's own key (or keys): each one hidden as it is typed, Show,
        Save, and a link to get one."""
        name_of = self._translator_label(translator)
        edits = {}
        fields = lens_translate.KEY_FIELDS[translator]
        for index, (name, label, placeholder) in enumerate(fields):
            edit = QLineEdit()
            edit.setEchoMode(QLineEdit.Password)
            edit.setPlaceholderText(placeholder)
            edit.setMinimumWidth(320)
            edit.setText(self.lens.cfg.get("api_keys", {}).get(name, ""))
            edit.returnPressed.connect(lambda t=translator: self.save_keys_of(t))
            edits[name] = edit
            caption = QLabel(label)
            caption.setMinimumWidth(110)
            widgets = [caption, edit]
            if index == len(fields) - 1:
                show = QPushButton("Show")
                show.setCheckable(True)
                show.setToolTip("Show or hide the key")
                show.toggled.connect(lambda shown, t=translator, b=show: self._show_keys(t, shown, b))
                save = QPushButton("Save the key" if len(fields) == 1 else "Save the keys")
                save.setToolTip(f"Keep it in Lens's settings on this PC; {name_of} uses it from now on")
                save.clicked.connect(lambda _=False, t=translator: self.save_keys_of(t))
                widgets += [show, save]
            line = self._row(*widgets)
            line.setContentsMargins(28, 0, 0, 0)
            layout.addWidget(line)
        self.key_edits[translator] = edits
        url = getattr(lens_translate, "KEY_URLS", {}).get(translator)
        if url:
            get_key = QPushButton("Get a free key…")
            get_key.setObjectName("flat")
            get_key.setCursor(Qt.PointingHandCursor)
            get_key.setToolTip(url)
            get_key.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            layout.addWidget(self._row(get_key))
        if translator == "deepl":  # the names the DeepL key had first
            self.deepl_key, self.show_key, self.save_key = edits["deepl"], show, save

    @staticmethod
    def _translator_label(key):
        return lens_translate.CHOICES.get(key, key).split(" — ")[0]

    def _show_keys(self, translator, shown, button):
        for edit in self.key_edits[translator].values():
            edit.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        button.setText("Hide" if shown else "Show")

    def save_keys_of(self, translator):
        edits = self.key_edits[translator]
        self.lens.set_api_keys(translator, {name: edit.text() for name, edit in edits.items()})
        for name, edit in edits.items():
            edit.setText(self.lens.cfg["api_keys"].get(name, ""))

    def save_deepl_key(self):
        self.save_keys_of("deepl")

    def _appearance_page(self):
        layout = self._page("Appearance", "How the translations look over the screen. Every change shows at once.")
        self.appearance = AppearancePage(self.lens.overlay.style)
        self.appearance.changed.connect(self.lens.apply_style)
        layout.addWidget(self.appearance)
        self._reset_button(layout, "appearance")
        layout.addStretch(1)

    def _shortcuts_page(self):
        layout = self._page("Keyboard shortcuts", "A key for every option. Press Save to use the new keys.")
        self.shortcuts = ShortcutsPage(self.lens.keys)
        self.shortcuts.setMinimumHeight(460)
        self.shortcuts.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.shortcuts, 1)
        self.keys_note = QLabel()
        self.keys_note.setObjectName("muted")
        self.save_keys = QPushButton("Save the keys")
        self.save_keys.clicked.connect(self.apply_keys)
        self.undo_keys = QPushButton("Undo my changes")
        self.undo_keys.clicked.connect(lambda: self.shortcuts.load(self.lens.keys))
        self.reset_keys = QPushButton("Reset this page")
        self.reset_keys.setToolTip("The default keys")
        self.reset_keys.clicked.connect(lambda: self.reset_page("shortcuts"))
        line = self._row(self.save_keys, self.undo_keys, self.reset_keys, self.keys_note)
        layout.addWidget(line)
        self.shortcuts.changed.connect(self._keys_edited)
        self._keys_edited()

    def _about_page(self):
        layout = self._page("About", "")
        self.about = AboutPage(on_support=self.lens.donate)
        layout.addWidget(self.about)
        layout.addStretch(1)

    # ---- choices -------------------------------------------------------------
    def _changed(self, apply, *args):
        """A choice made here: LensApp applies it (and says so on the screen), then the tray menu follows."""
        if self._syncing:
            return
        apply(*args)
        self.lens.refresh_menu()
        self.refresh()

    def _capture(self):
        """This window steps aside (minimized), so the screen under it can be captured."""
        self.showMinimized()
        self.lens.capture_screen()

    def _mode_chosen(self, index):
        if index == 0:
            self._changed(self.lens.use_screen, self.screen_box.currentData())
        elif index == 1:
            self._changed(self.lens.use_area)
        elif index == 2:
            self._changed(self.lens.use_mouse_mode, True)
        else:
            self._window_chosen()

    def _fill_windows(self):
        from . import app_windows

        before = self._syncing
        self._syncing = True
        self.window_box.clear()
        for window in app_windows.list_windows():
            self.window_box.addItem(window.label, window)
        cfg = self.lens.cfg
        for i in range(self.window_box.count()):
            w = self.window_box.itemData(i)
            if w.title == cfg["window_title"] and w.program == cfg["window_program"]:
                self.window_box.setCurrentIndex(i)
                break
        if not self.window_box.count():
            self.window_box.addItem("No other window is open", None)
        self._syncing = before

    def _window_chosen(self):
        window = self.window_box.currentData()
        if window is None:
            self.refresh()
            return
        self._changed(self.lens.use_window, window)

    def _only_window(self, on):
        window = self.lens.text_window
        if not self._syncing and window is not None:
            window.only_here.setChecked(on)  # the translation window tells Lens, as when it is ticked there

    def _keys_edited(self):
        dirty = self.shortcuts.value() != self.lens.keys
        self.save_keys.setEnabled(dirty and self.shortcuts.valid())
        self.undo_keys.setEnabled(dirty)
        if dirty:
            self.keys_note.setText("")

    def apply_keys(self):
        """Use the new keys — unless Windows will not give some of them (another program holds them)."""
        if not self.shortcuts.valid():
            return False
        taken = self.lens.apply_keys(self.shortcuts.value())
        if taken:
            self.shortcuts.show_unavailable(taken)
            return False
        self._keys_edited()
        self.keys_note.setText("Saved: the new keys work now.")
        return True

    # ---- showing what Lens does now --------------------------------------------
    def show_page(self, name):
        names = [n for n, _ in PAGES]
        if name in names:
            self.nav.setCurrentRow(names.index(name))

    def current_page(self):
        return PAGES[self.nav.currentRow()][0]

    def present(self, page=None):
        """Show the window in front, on `page` (or where it was)."""
        if not self.isVisible():
            current = self.ocr_box.currentData()
            self._syncing = True
            self._fill_ocr_languages()  # a language may have been added in Windows meanwhile
            self._fill_windows()  # windows opened and closed meanwhile
            self.ocr_box.setCurrentIndex(max(0, self.ocr_box.findData(current)))
            self._syncing = False
            if not self.undo_keys.isEnabled():
                self.shortcuts.load(self.lens.keys)
            self.refresh()
        if page:
            self.show_page(page)
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh(self):
        """Every control as Lens is now (after a key, the tray menu or the translation window changed it)."""
        cfg, before = self.lens.cfg, self._syncing
        self._syncing = True
        try:
            mode = cfg["mode"]
            {"screen": self.mode_screen, "area": self.mode_area, "mouse": self.mode_mouse,
             "window": self.mode_window}.get(
                mode, self.mode_screen).setChecked(True)
            self._fill_screens()
            size = list(cfg["lens_size"])
            self.lens_box.setCurrentIndex(self.lens_box.findData(size))
            self.frame_box.setChecked(bool(cfg["lens_frame"]))
            self.text_window_box.setChecked(self.lens.text_window is not None)
            self.only_window_box.setEnabled(self.lens.text_window is not None)
            self.only_window_box.setChecked(self.lens.text_window is not None and not cfg["overlay_visible"])
            self.capture_box.setChecked(bool(cfg["capture_visible"]))
            self.select_box.setChecked(bool(cfg["select_mode"]))
            self.peek_box.setCurrentIndex(max(0, self.peek_box.findData(cfg["peek_seconds"])))
            self.target_box.setCurrentIndex(max(0, self.target_box.findData(cfg["target_lang"] or "")))
            self.ocr_box.setCurrentIndex(max(0, self.ocr_box.findData(cfg["ocr_language"])))
            button = self.translator_buttons.get(cfg["translator"])
            if button is not None:
                button.setChecked(True)
        finally:
            self._syncing = before
        self.refresh_state()

    def _fill_screens(self):
        screens = QGuiApplication.screens()
        primary = QGuiApplication.primaryScreen().name()
        names = [s.name() for s in screens]
        if [self.screen_box.itemData(i) for i in range(self.screen_box.count())] != names:
            self.screen_box.clear()
            for index, screen in enumerate(screens):
                self.screen_box.addItem(screen_label(index, screen), screen.name())
        self.screen_box.setCurrentIndex(max(0, self.screen_box.findData(self.lens.cfg["screen"] or primary)))

    def refresh_state(self):
        """The big button and the line above it: translating or stopped, what and into which language."""
        running = self.lens.scanner.running
        language = languages.name(self.lens.target)
        if running:
            self.state_title.setText("Translating")
            self.state_text.setText(f"{self.lens._where().capitalize()}, into {language}.")
            self.start_button.setText("■  Stop translating")
        else:
            self.state_title.setText("Stopped")
            self.state_text.setText(f"Press Start to translate {self.lens._where()} into {language}.")
            self.start_button.setText("▶  Start translating")
        self.start_button.setProperty("running", running)
        self.start_button.style().unpolish(self.start_button)
        self.start_button.style().polish(self.start_button)

    # ---- place and size ------------------------------------------------------------
    def _restore_geometry(self):
        saved = self.lens.cfg.get("window_geometry")
        if isinstance(saved, list) and len(saved) == 4:
            rect = QRect(*saved)
            if any(s.availableGeometry().intersects(rect) for s in QGuiApplication.screens()):
                self.setGeometry(rect)
                return
        self.resize(*DEFAULT_SIZE)

    def hideEvent(self, event):
        g = self.geometry()
        self.lens._set(window_geometry=[g.x(), g.y(), g.width(), g.height()])
        super().hideEvent(event)

    def closeEvent(self, event):
        event.ignore()  # Lens keeps running by the clock
        self.hide()

# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: the tray icon's menu. Every item shows the key the user gave it in Settings."""
from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QMenu

from . import DONATE_URL, languages, lens_follow, lens_ocr, lens_shortcuts, lens_translate
from .languages import QUICK_TARGETS

PEEK_CHOICES = ((0, "Never"), (1, "Every second"), (2, "Every 2 seconds"), (5, "Every 5 seconds"),
                (10, "Every 10 seconds"))
LANGUAGE_SETTINGS = "ms-settings:regionlanguage"


ONLINE_TRANSLATORS = getattr(lens_translate, "ONLINE", {"google", "bing", "mymemory", "deepl"})
TRANSLATOR_HELP = {  # used until lens_translate.HELP has a line for the key
    lens_translate.SAME: "EchoSub's translator when EchoSub is on this PC; otherwise Google Translate.",
    "google": "Online and fast. Uses no memory and no graphics card, but needs the internet and sends the text to "
              "Google — which may slow down or refuse very many requests.",
    "bing": "Online, from Microsoft. Needs the internet and sends the text to Microsoft.",
    "mymemory": "Online and free, but only a limited amount of text per day. Sends the text to MyMemory.",
    "deepl": "Online and very natural. Needs your own free DeepL key (below) and sends the text to DeepL.",
    "yandex": "Online, from Yandex. Needs the internet and sends the text to Yandex.",
    "papago": "Online, from Naver. Best for Korean, Japanese and Chinese; 16 languages. Sends the text to Naver.",
    "nllb-600m": "Offline, on your graphics card (or the processor). Private: nothing leaves your PC. "
                 "About 600 MB, downloaded the first time you choose it.",
    "nllb-1.3b": "Offline and more accurate, but slower and needs more memory. Private: nothing leaves your PC. "
                 "About 1.3 GB, downloaded the first time you choose it.",
}


def translator_help(key):
    """One line on what the translator `key` is."""
    return getattr(lens_translate, "HELP", {}).get(key) or TRANSLATOR_HELP.get(key, "")


def translator_groups():
    """[(title, [keys])] of lens_translate.CHOICES: Default alone, then the online ones, then the offline ones."""
    keys = list(lens_translate.CHOICES)
    online = [k for k in keys if k in ONLINE_TRANSLATORS]
    offline = [k for k in keys if k != lens_translate.SAME and k not in ONLINE_TRANSLATORS]
    groups = [("", [lens_translate.SAME])] if lens_translate.SAME in keys else []
    return groups + [(title, group) for title, group in (("Online", online), ("Offline, on this PC", offline))
                     if group]


def fill_translator_combo(combo, hints=None):
    """The translators in a QComboBox (data: the key), the groups apart, each with a tooltip."""
    from PySide6.QtCore import Qt

    hints = hints or {}
    for _, keys in translator_groups():
        if combo.count():
            combo.insertSeparator(combo.count())
        for key in keys:
            combo.addItem(lens_translate.CHOICES[key], key)
            combo.setItemData(combo.count() - 1, hints.get(key) or translator_help(key) or
                              lens_translate.CHOICES[key], Qt.ToolTipRole)


def screen_label(index, screen):
    size = screen.size()
    return f"Screen {index + 1} — {screen.name()} ({size.width()} × {size.height()})"


class LensMenuMixin:
    def _key(self, name):
        key = lens_shortcuts.native(self.keys.get(name, ""))
        return f"\t{key}" if key else ""

    def _item(self, menu, text, name, slot=None, checkable=False, checked=False):
        action = QAction(text + self._key(name), menu, checkable=checkable, checked=checked)
        if slot is None:
            slot = self.actions()[name]
        (action.toggled if checkable else action.triggered).connect(lambda *_: slot())
        menu.addAction(action)
        return action

    def refresh_menu(self):
        """Build the menu again: after new keys, or a choice made with a key rather than the menu."""
        old = getattr(self, "menu", None)
        self.menu = self._build_menu()
        self.tray.setContextMenu(self.menu)
        if old is not None:
            old.deleteLater()

    def _build_menu(self):
        """In groups: open and start; what to translate; languages; where translations show; the rest."""
        m = QMenu()
        opener = m.addAction("Open EchoSub Lens…", lambda: self.open_window())
        m.setDefaultAction(opener)  # shown in bold: what a click on the icon does
        self.act_running = self._item(m, "Translating", "toggle", self.toggle, checkable=True,
                                      checked=self.scanner.running)
        self._item(m, "Capture part of the screen…", "snip")
        m.addSeparator()

        what = m.addMenu("What to translate")
        self._item(what, "An area of the screen (choose it)…", "area")
        self.screen_menu = what.addMenu("A whole screen")
        self._fill_screens()
        self.windows_menu = what.addMenu("A window")
        self.windows_menu.aboutToShow.connect(self._fill_windows)
        self.act_mouse = self._item(what, "A lens around the mouse", "mouse_mode", lambda: None, checkable=True,
                                    checked=self.cfg["mode"] == "mouse")
        self.act_mouse.toggled.connect(self.use_mouse_mode)
        self._build_lens_menu(what)
        self._item(m, "Translate a file (picture, PDF, Word, text)…", "file")
        self._item(m, "Translate a movie (subtitles to .srt)…", "movie")
        m.addSeparator()

        self._choice_menu(m, "Translate into", self._target_choices(), lambda: self.cfg["target_lang"] or "",
                          lambda code: self.set_target(code or None))
        self._build_language_menu(m)
        choices = []
        for _, keys in translator_groups():
            if choices:
                choices.append(("", ""))  # a line between Default, the online and the offline translators
            choices += [(key, lens_translate.CHOICES[key]) for key in keys]
        self._choice_menu(m, "Translator", choices, lambda: self.cfg["translator"], self.set_translator)
        m.addSeparator()

        self.act_text_window = self._item(m, "Translation window (the text in a box, above all windows)",
                                          "text_window", lambda: None, checkable=True,
                                          checked=self.text_window is not None)
        self.act_text_window.toggled.connect(self.show_text_window)
        self._item(m, "Copy all translations", "copy_all")
        shown = m.addMenu("Translations on the screen")
        self._item(shown, "Appearance (font, size, colours, direction)…", "appearance")
        self.act_capture = self._item(shown, "Show them in screenshots and screen sharing", "capture",
                                      lambda: None, checkable=True, checked=self.cfg["capture_visible"])
        self.act_capture.toggled.connect(self._set_capture_visible)
        self.act_capture.setToolTip("Discord, Teams, OBS and screenshots show the translated screen")
        self.act_select = self._item(shown, "Select mode: they take the mouse", "select_mode", lambda: None,
                                     checkable=True, checked=self.cfg["select_mode"])
        self.act_select.toggled.connect(self.set_select_mode)
        tip = shown.addAction("Tip: hold Ctrl to select and copy a translation")
        tip.setEnabled(False)
        self._choice_menu(shown, "Look under them for changes", list(PEEK_CHOICES),
                          lambda: self.cfg["peek_seconds"], self._set_peek)
        m.addSeparator()

        self._item(m, "Settings…", "settings")
        m.addAction("About EchoSub Lens…", lambda: self.open_window("about"))
        donate = m.addAction("♥ Support the developer (donate)…", self.donate)
        donate.setToolTip(DONATE_URL)
        m.addSeparator()
        self._item(m, "Exit", "quit")
        return m

    def _fill_screens(self):
        self.screen_menu.clear()
        group = QActionGroup(self.screen_menu)
        primary = QGuiApplication.primaryScreen().name()
        for index, screen in enumerate(QGuiApplication.screens()):
            chosen = self.cfg["mode"] == "screen" and (self.cfg["screen"] or primary) == screen.name()
            key = self._key(f"screen_{index + 1}") if index < 3 else ""
            a = QAction(screen_label(index, screen) + key, self.screen_menu, checkable=True, checked=chosen)
            a.triggered.connect(lambda _=False, name=screen.name(): self.use_screen(name))
            group.addAction(a)
            self.screen_menu.addAction(a)

    def _fill_windows(self):
        """The windows open now, read again each time the menu opens."""
        from . import app_windows

        self.windows_menu.clear()
        group = QActionGroup(self.windows_menu)
        windows = app_windows.list_windows()
        for window in windows:
            chosen = (self.cfg["mode"] == "window" and window.title == self.cfg["window_title"]
                      and window.program == self.cfg["window_program"])
            a = QAction(window.label.replace("&", "&&"), self.windows_menu, checkable=True, checked=chosen)
            a.triggered.connect(lambda _=False, w=window: self.use_window(w))
            group.addAction(a)
            self.windows_menu.addAction(a)
        if not windows:
            none = self.windows_menu.addAction("No other window is open")
            none.setEnabled(False)

    def _build_lens_menu(self, m):
        menu = m.addMenu("Lens size and frame")
        group = QActionGroup(menu)
        for name, (w, h) in lens_follow.SIZES.items():
            a = QAction(f"{name.capitalize()} ({w} × {h})", menu, checkable=True,
                        checked=list(self.cfg["lens_size"]) == [w, h])
            a.triggered.connect(lambda _=False, size=(w, h): self.set_lens_size(list(size)))
            group.addAction(a)
            menu.addAction(a)
        menu.addSeparator()
        self._item(menu, "Bigger", "lens_bigger")
        self._item(menu, "Smaller", "lens_smaller")
        menu.addSeparator()
        frame = self._item(menu, "Show the lens frame", "", lambda: None, checkable=True,
                           checked=self.cfg["lens_frame"])
        frame.toggled.connect(self.set_lens_frame)

    def _target_choices(self):
        default_lang = languages.name(self.base_cfg.get("target_lang", "ar"))
        choices = [("", f"Default ({default_lang})")]
        choices += [(code, languages.name(code)) for code in QUICK_TARGETS]
        others = sorted((c for c in languages.LANGUAGES if c not in QUICK_TARGETS), key=languages.name)
        return choices + [(None, None)] + [(code, languages.name(code)) for code in others]

    def _build_language_menu(self, m):
        installed = lens_ocr.available_languages()
        choices = [(lens_ocr.AUTO, "Automatic (Windows display languages)")]
        choices += [(tag, tag) for tag in installed]
        menu = self._choice_menu(m, "Text on the screen is in", choices, lambda: self.cfg["ocr_language"],
                                 self.set_ocr_language)
        menu.addSeparator()
        menu.addAction("Add a language to read…", lambda: QDesktopServices.openUrl(QUrl(LANGUAGE_SETTINGS)))

    def _choice_menu(self, parent, title, choices, current, choose):
        """A submenu of radio items; a (None, None) choice starts a "More" submenu for the rest, a ("", "") one
        is a line between items."""
        menu = parent.addMenu(title)
        group = QActionGroup(menu)
        target = menu
        for value, text in choices:
            if value is None and text is None:
                target = menu.addMenu("More languages")
                continue
            if value == "" and text == "":
                target.addSeparator()
                continue
            a = QAction(text, target, checkable=True, checked=current() == value)
            a.triggered.connect(lambda _=False, v=value: choose(v))
            group.addAction(a)
            target.addAction(a)
        return menu

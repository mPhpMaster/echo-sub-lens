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
        m = QMenu()
        opener = m.addAction("Open EchoSub Lens…", lambda: self.open_window())
        m.setDefaultAction(opener)  # shown in bold: what a click on the icon does
        m.addSeparator()
        self.act_running = self._item(m, "Translating", "toggle", self.toggle, checkable=True,
                                      checked=self.scanner.running)
        self._item(m, "Choose an area of the screen…", "area")
        self.screen_menu = m.addMenu("Translate a whole screen")
        self._fill_screens()
        self.act_mouse = self._item(m, "Lens around the mouse", "mouse_mode", lambda: None, checkable=True,
                                    checked=self.cfg["mode"] == "mouse")
        self.act_mouse.toggled.connect(self.use_mouse_mode)
        self._build_lens_menu(m)
        self._item(m, "Translate a file (picture, PDF, Word, text)…", "file")
        m.addSeparator()
        self._choice_menu(m, "Translate into", self._target_choices(), lambda: self.cfg["target_lang"] or "",
                          lambda code: self.set_target(code or None))
        self._build_language_menu(m)
        self._choice_menu(m, "Translator", list(lens_translate.CHOICES.items()), lambda: self.cfg["translator"],
                          self.set_translator)
        self._item(m, "Appearance (font, size, colours)…", "appearance")
        self.act_capture = self._item(m, "Show translations in screenshots and screen sharing", "capture",
                                      lambda: None, checkable=True, checked=self.cfg["capture_visible"])
        self.act_capture.toggled.connect(self._set_capture_visible)
        self.act_capture.setToolTip("Discord, Teams, OBS and screenshots show the translated screen")
        self._choice_menu(m, "Look under the translations for changes", list(PEEK_CHOICES),
                          lambda: self.cfg["peek_seconds"], self._set_peek)
        m.addSeparator()
        self.act_select = self._item(m, "Select mode: translations take the mouse", "select_mode", lambda: None,
                                     checkable=True, checked=self.cfg["select_mode"])
        self.act_select.toggled.connect(self.set_select_mode)
        tip = m.addAction("Tip: hold Ctrl to select and copy a translation")
        tip.setEnabled(False)
        self._item(m, "Copy all translations", "copy_all")
        self.act_text_window = self._item(m, "Translation window (the text in a box, above all windows)",
                                          "text_window", lambda: None, checkable=True,
                                          checked=self.text_window is not None)
        self.act_text_window.toggled.connect(self.show_text_window)
        m.addSeparator()
        self._item(m, "Settings (every option, look, keyboard shortcuts)…", "settings")
        m.addAction("About EchoSub Lens…", lambda: self.open_window("about"))
        donate = m.addAction("♥ Support the developer (donate)…", self.donate)
        donate.setToolTip(DONATE_URL)
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
        """A submenu of radio items; a (None, None) choice starts a "More" submenu for the rest."""
        menu = parent.addMenu(title)
        group = QActionGroup(menu)
        target = menu
        for value, text in choices:
            if value is None and text is None:
                target = menu.addMenu("More languages")
                continue
            a = QAction(text, target, checkable=True, checked=current() == value)
            a.triggered.connect(lambda _=False, v=value: choose(v))
            group.addAction(a)
            target.addAction(a)
        return menu

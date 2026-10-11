# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: its icon by the clock, its keys, and what ties the scanner to the overlay."""
import logging
import os
import threading

from PySide6.QtCore import QRect, QTimer
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QIcon, QPainter, QPen
from PySide6.QtWidgets import QFileDialog, QMessageBox, QSystemTrayIcon

from . import (app_windows, config, downloads, hotkeys, languages, lens_files, lens_follow, lens_shortcuts,
               lens_translate, translate)
from .lens_busy import Activity, BusyIndicator
from .lens_app import LENS_NAME, ctrl_held
from .lens_menu import LensMenuMixin
from .lens_overlay import LensOverlay
from .lens_picker import RegionPicker
from .lens_scanner import LensScanner
from .languages import QUICK_TARGETS

log = logging.getLogger(__name__)

MOUSE_INTERVAL_MS = 250  # the lens looks more often: it is small, and the pointer has just stopped


def lens_icon(active):
    """EchoSub's icon with a magnifying glass: green while translating, grey when stopped."""
    pm = QIcon(f"{config.ASSETS_DIR}/echosub.ico").pixmap(64, 64)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    color = QColor("#43A047" if active else "#757575")
    p.setPen(QPen(QColor(15, 23, 42), 9))
    p.drawLine(52, 52, 62, 62)
    p.drawEllipse(30, 30, 24, 24)
    p.setPen(QPen(color, 5))
    p.drawLine(52, 52, 61, 61)
    p.setBrush(QColor(255, 255, 255, 200))
    p.drawEllipse(30, 30, 24, 24)
    p.end()
    return QIcon(pm)


class LensApp(LensMenuMixin):
    def __init__(self, qt, cfg, save, base_cfg=None):
        self.qt = qt
        self.cfg = cfg
        self.main_window = None      # the EchoSub Lens window: a click on the tray icon opens it
        self._save = save
        self.base_cfg = base_cfg if base_cfg is not None else config.load()  # the defaults
        self.keys = lens_shortcuts.merged(cfg.get("shortcuts"))
        self.picker = None
        self._mouse_area = None      # (QScreen, QRect) of the lens around the mouse
        self._window = None          # app_windows.AppWindow translated in mode "window"
        self._window_spot = None     # where it was last seen; None: minimized, gone or behind another window
        self._anchor = None          # where the pointer was when the lens was last placed
        self.file_windows = []
        self.text_window = None
        self._save_text_window = QTimer()
        self._save_text_window.setSingleShot(True)
        self._save_text_window.setInterval(600)
        self._save_text_window.timeout.connect(lambda: self._set(text_window_settings=self.text_window.settings())
                                               if self.text_window is not None else None)
        self.overlay = LensOverlay(cfg["style"])
        self.overlay.capture_hidden = not cfg["capture_visible"]
        self.scanner = LensScanner(cfg["interval_ms"])
        self.scanner.covered = self.overlay.covered
        self.scanner.peeking = self.overlay.set_peeking
        self.scanner.paused = lambda: ctrl_held() or self.overlay.engaged() or self._window_away()
        self.scanner.blocked = self._under_own_windows
        self.scanner.peek_every = cfg["peek_seconds"]
        self.scanner.result.connect(self._show)
        self.scanner.stale.connect(self.overlay.clear)
        self.scanner.status.connect(self._on_status)
        self.busy = BusyIndicator()
        self.busy.screen_of = lambda: self.area()[0]
        self.activity = Activity()
        self.activity.changed.connect(self.busy.set)
        self.activity.told.connect(self.busy.notice)
        self.scanner.working.connect(
            lambda on: self.activity.say("screen", "Reading and translating the screen…" if on else "", 0))
        self.scanner.progress.connect(
            lambda text, percent: self.scanner._busy and self.activity.say("screen", text, percent))
        self._translators = {}
        self.hotkeys = hotkeys.GlobalHotkeys()
        self.mouse_timer = QTimer()
        self.mouse_timer.setInterval(60)
        self.mouse_timer.timeout.connect(self._follow_ctrl)

        self.tray = QSystemTrayIcon(lens_icon(False))
        self.refresh_menu()
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()
        taken = self.register_keys(self.keys)
        if taken:
            log.warning("Lens keys already in use by another program: %s", ", ".join(taken))
        if cfg["text_window"]:
            self.show_text_window(True)
        QGuiApplication.instance().screenAdded.connect(lambda _: self._fill_screens())
        QGuiApplication.instance().screenRemoved.connect(lambda _: self._fill_screens())
        self.start()

    # ---- what every menu item and key does -----------------------------------
    def actions(self):
        return {
            "toggle": self.toggle,
            "area": self.choose_area,
            "file": self.open_file,
            "mouse_mode": lambda: self.act_mouse.toggle(),
            "lens_bigger": lambda: self.resize_lens(True),
            "lens_smaller": lambda: self.resize_lens(False),
            "screen_1": lambda: self.use_screen_number(1),
            "screen_2": lambda: self.use_screen_number(2),
            "screen_3": lambda: self.use_screen_number(3),
            "language_next": self.next_language,
            "translator_next": self.next_translator,
            "select_mode": lambda: self.act_select.toggle(),
            "copy_all": self.overlay.copy_all,
            "text_window": lambda: self.act_text_window.toggle(),
            "capture": lambda: self.act_capture.toggle(),
            "appearance": lambda: self.open_settings("appearance"),
            "settings": lambda: self.open_window(),
            "quit": self.quit,
        }

    def register_keys(self, keys):
        """Give every action its key, everywhere in Windows. Returns the keys another program holds."""
        actions, bindings = self.actions(), []
        for name, text in keys.items():
            parsed = hotkeys.parse(text)
            if parsed and name in actions:
                bindings.append((lens_shortcuts.native(text), parsed[0], parsed[1],
                                 lambda name=name: self.actions()[name]()))
        return self.hotkeys.enable_keys(self.qt, bindings)

    def apply_keys(self, keys):
        taken = self.register_keys(keys)
        if taken:
            self.register_keys(self.keys)  # keep the keys that worked before
            return taken
        self.keys = dict(keys)
        self._set(shortcuts=self.keys)
        self.refresh_menu()
        return []

    # ---- settings ------------------------------------------------------------
    @property
    def target(self):
        return self.cfg["target_lang"] or self.base_cfg.get("target_lang", "ar")

    def _set(self, **values):
        self.cfg.update(values)
        try:
            self._save(self.cfg)
        except OSError:
            log.exception("Could not save the Lens settings")
        if self.main_window is not None and self.main_window.isVisible():
            self.main_window.refresh()  # a key or the tray menu changed it: the window shows it too

    def translator(self):
        key = lens_translate.resolve(self.cfg["translator"], self.base_cfg)
        for other in [k for k in self._translators if k != key]:
            self._switch_off(other, self._translators.pop(other))  # one translator at a time: a faster PC
        if key not in self._translators:
            from . import cuda_libs

            device = self.base_cfg.get("device", "cuda")
            if device in ("cuda", "vulkan") and not cuda_libs.available():
                device = "cpu"
            fast_cpu = self.base_cfg.get("fast_cpu_translation", True)
            self._translators[key] = lens_translate.LensTranslator(
                key, device, factory=lambda k, d: self._load_translator(k, d, fast_cpu))
        return self._translators[key]

    def _load_translator(self, key, device, fast_cpu):
        """Runs in a worker thread the first time a translator is needed; says so on the screen."""
        name = self._translator_name(key)
        self.activity.say("translator", f"Loading the translator ({name})…")
        downloader = downloads.ModelDownloader(on_event=self._download_event)
        try:
            if key == "google":  # no offline model waiting in the memory: Google alone
                backend = translate.GoogleTranslator()
                backend.notify = self.activity.tell
            else:
                backend = translate.create(key, device, downloader=downloader, fast_cpu=fast_cpu)
            where = {"cuda": " on the graphics card", "cpu": " on the processor"}.get(
                getattr(backend, "device", ""), "")
            self.activity.tell(f"Translator ready: {name}{where}")
            return backend
        except Exception as e:
            self.activity.tell(f"Could not load the translator ({name}): {e}")
            raise
        finally:
            self.activity.say("translator")

    @staticmethod
    def _translator_name(key):
        return config.TRANSLATORS.get(key, key).split(" — ")[0]

    def _switch_off(self, key, translator):
        """A translator no longer used lets go of its model, in the background (it may be translating)."""
        name = self._translator_name(key)

        def run():
            self.activity.say("switch_off", f"Switching off the translator ({name})…")
            try:
                if translator.release():
                    self.activity.tell(f"Translator switched off ({name}): its memory is free")
            finally:
                self.activity.say("switch_off")
        threading.Thread(target=run, name="lens-switch-off", daemon=True).start()

    def _download_event(self, event):
        if event["state"] in ("start", "progress"):
            done, total = event["done"], event["total"]
            share = ""
            if total:
                share = f" {done * 100 // total}% ({downloads.human_size(done)} of {downloads.human_size(total)})"
            title = event["title"][:1].lower() + event["title"][1:]
            self.activity.say("translator", f"Downloading the {title}…{share}", done * 100 // total if total else -1)
        elif event["state"] == "finished":
            self.activity.say("translator", "Loading the translator…")

    def set_target(self, code):
        self._set_and_restart(target_lang=code)
        self._notify(f"Translating into {languages.name(self.target)}")

    def set_translator(self, key):
        self._set_and_restart(translator=key)
        self.translator()  # switches the one used before off now, not when Lens starts again
        self._notify(f"Translator: {lens_translate.CHOICES[key]}")

    def set_ocr_language(self, tag):
        self._set_and_restart(ocr_language=tag)
        self._notify("Reading the screen in your Windows display languages" if tag == "auto"
                     else f"Reading the screen in {tag}")

    def set_select_mode(self, on):
        self._set(select_mode=on)
        if hasattr(self, "act_select"):
            self.act_select.blockSignals(True)
            self.act_select.setChecked(on)
            self.act_select.blockSignals(False)
        self._notify("Select mode on: translations take the mouse" if on
                     else "Select mode off: hold Ctrl to select a translation")

    def next_language(self):
        order = [""] + QUICK_TARGETS
        current = self.cfg["target_lang"] or ""
        code = order[(order.index(current) + 1) % len(order)] if current in order else order[0]
        self.set_target(code or None)
        self.refresh_menu()

    def next_translator(self):
        order = list(lens_translate.CHOICES)
        current = self.cfg["translator"]
        key = order[(order.index(current) + 1) % len(order)] if current in order else order[0]
        self.set_translator(key)
        self.refresh_menu()

    def donate(self):
        """The developer's PayPal page, in the browser: optional, Lens never asks."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from . import DONATE_URL

        QDesktopServices.openUrl(QUrl(DONATE_URL))
        self._notify("Thank you! The donation page opens in your browser")

    def _notify(self, text):
        self.activity.tell(text)  # on the screen, in the corner

    # ---- the area ------------------------------------------------------------
    def area(self):
        """(QScreen, QRect relative to it) of what is translated now."""
        if self.cfg["mode"] == "mouse":
            return self._mouse_area or self._lens_at(QCursor.pos())
        if self.cfg["mode"] == "window":
            spot = self._find_window_spot()
            if spot is not None:
                return spot
            primary = QGuiApplication.primaryScreen()
            return primary, QRect(0, 0, 1, 1)  # nothing to translate until the window shows again
        screens = QGuiApplication.screens()
        screen = next((s for s in screens if s.name() == self.cfg["screen"]), QGuiApplication.primaryScreen())
        whole = QRect(0, 0, screen.geometry().width(), screen.geometry().height())
        if self.cfg["mode"] == "area" and self.cfg["area"]:
            rect = QRect(*self.cfg["area"]).intersected(whole)
            if not rect.isEmpty():
                return screen, rect
        return screen, whole

    def choose_area(self):
        if self.picker is not None:
            return
        self.overlay.clear()
        self.overlay.hide()
        self.picker = RegionPicker(self._area_chosen)
        self.picker.start()

    def _area_chosen(self, result):
        self.picker = None
        if result is None:
            if self.scanner.running:
                self._apply_area()
            return
        screen, rect = result
        self._set(mode="area", screen=screen.name(), area=[rect.x(), rect.y(), rect.width(), rect.height()])
        self._fill_screens()
        self._sync_mouse_item()
        self.start()
        self._notify(f"Translating {self._where()} into {languages.name(self.target)}")

    def use_area(self):
        """The area chosen last time again, or a new one when there is none yet."""
        if not self.cfg["area"]:
            self.choose_area()
            return
        self._set(mode="area")
        self._mouse_area = self._anchor = None
        self._fill_screens()
        self._sync_mouse_item()
        self.start()
        self._notify(f"Translating {self._where()} into {languages.name(self.target)}")

    # ---- one window ----------------------------------------------------------------
    def use_window(self, window):
        """Translate only `window` (an app_windows.AppWindow), wherever it goes."""
        self._window = window
        self._window_spot = None
        self._set(mode="window", window_title=window.title, window_program=window.program)
        self._fill_screens()
        self._sync_mouse_item()
        self.start()
        self._notify(f"Translating {self._where()}")

    def _chosen_window(self):
        """The window chosen, found again by its title and program after Lens started or it was opened again."""
        if self._window is not None and app_windows.alive(self._window.hwnd):
            return self._window
        self._window = app_windows.find(self.cfg["window_title"], self.cfg["window_program"])
        return self._window

    def _find_window_spot(self):
        window = self._chosen_window()
        return app_windows.spot(window.hwnd) if window is not None else None

    def _window_away(self):
        return self.cfg["mode"] == "window" and self._window_spot is None

    def _follow_window(self):
        """Move with the window; hide the translations while it is minimized, closed or behind another one."""
        window = self._chosen_window()
        spot = None
        if window is not None and not app_windows.hidden_behind(window.hwnd):
            spot = app_windows.spot(window.hwnd)
        key = None if spot is None else (spot[0].name(), spot[1].getRect())
        if key == self._window_spot:
            return
        self._window_spot = key
        self.overlay.clear()
        self.scanner.restart()
        if spot is None:
            self.overlay.hide()
        else:
            self._apply_area()

    def use_screen(self, name):
        self._set(mode="screen", screen=name)
        self._fill_screens()
        self._sync_mouse_item()
        self.start()
        self._notify(f"Translating {self._where()} ({name})")

    # ---- the lens around the mouse -------------------------------------------
    def use_mouse_mode(self, on):
        if on == (self.cfg["mode"] == "mouse"):
            return
        if on:
            self._set(mode_before_mouse=self.cfg["mode"], mode="mouse")
        else:
            self._set(mode=self.cfg["mode_before_mouse"] if self.cfg["mode_before_mouse"] != "mouse" else "screen")
        self._mouse_area = self._anchor = None
        self._fill_screens()
        self._sync_mouse_item()
        self.start()
        self._notify(f"Translating {self._where()}")

    def _sync_mouse_item(self):
        self.act_mouse.blockSignals(True)
        self.act_mouse.setChecked(self.cfg["mode"] == "mouse")
        self.act_mouse.blockSignals(False)

    def _lens_at(self, pointer):
        screen = QGuiApplication.screenAt(pointer) or QGuiApplication.primaryScreen()
        top_left = screen.geometry().topLeft()
        return screen, lens_follow.lens_rect(pointer - top_left, screen.geometry().size(), self.cfg["lens_size"])

    def _follow_mouse(self):
        """Move the lens with the pointer; the translation comes back once the pointer rests."""
        if self.picker is not None or ctrl_held() or self.overlay.engaged():
            return  # selecting or copying: the lens stays where it is
        pointer = QCursor.pos()
        if not lens_follow.should_move(self._anchor, pointer, self.cfg["lens_size"]):
            return
        self._anchor = pointer
        self._mouse_area = self._lens_at(pointer)
        self.overlay.clear()
        self.scanner.restart()
        self._apply_area()

    def set_lens_size(self, size):
        self._set(lens_size=list(size))
        self._relens()

    def resize_lens(self, bigger):
        screen = self.area()[0]
        self.set_lens_size(lens_follow.resized(self.cfg["lens_size"], bigger, screen.geometry().size()))
        self._notify(f"Lens: {self.cfg['lens_size'][0]} × {self.cfg['lens_size'][1]}")

    def set_lens_frame(self, on):
        self._set(lens_frame=on)
        self._relens()

    def _relens(self):
        self._anchor = None
        if self.cfg["mode"] == "mouse" and self.scanner.running:
            self._follow_mouse()

    def use_screen_number(self, number):
        screens = QGuiApplication.screens()
        if number <= len(screens):
            self.use_screen(screens[number - 1].name())
        else:
            self._notify(f"There is no screen {number}")

    def _apply_area(self):
        screen, rect = self.area()
        self.scanner.area = (screen, rect)
        frame = self.cfg["mode"] == "mouse" and self.cfg["lens_frame"]
        self.overlay.cover(rect.translated(screen.geometry().topLeft()), screen.devicePixelRatio(), frame)

    # ---- start / stop --------------------------------------------------------
    def start(self):
        if self.picker is not None:
            return
        mouse = self.cfg["mode"] == "mouse"
        if mouse:
            self._anchor = QCursor.pos()
            self._mouse_area = self._lens_at(self._anchor)
        self.scanner.set_interval(MOUSE_INTERVAL_MS if mouse else self.cfg["interval_ms"])
        self.scanner.trim_edges = mouse
        self._apply_area()
        self.scanner.target = self.target
        self.scanner.ocr_language = self.cfg["ocr_language"]
        self.scanner.translator = self.translator()
        self.scanner.start()
        self.mouse_timer.start()
        self._update_state()

    def stop(self):
        self.scanner.stop()
        self.mouse_timer.stop()
        self.activity.say("screen")
        self.overlay.clear()
        self.overlay.hide()
        self._update_state()

    def toggle(self):
        if self.scanner.running:
            self.stop()
            self._notify("Translation stopped")
        else:
            self.start()
            self._notify(f"Translating {self._where()} into {languages.name(self.target)}")

    def _where(self):
        _, rect = self.area()
        if self.cfg["mode"] == "window":
            return f"the window “{self.cfg['window_title'][:50]}”"
        return {"screen": "the whole screen", "mouse": "the lens around the mouse"}.get(
            self.cfg["mode"], f"an area of {rect.width()} × {rect.height()}")

    def _set_and_restart(self, **values):
        self._set(**values)
        self.scanner.restart()
        if self.scanner.running:
            self.start()

    def _update_state(self):
        running = self.scanner.running
        self.act_running.blockSignals(True)
        self.act_running.setChecked(running)
        self.act_running.blockSignals(False)
        self.tray.setIcon(lens_icon(running))
        _, rect = self.area()
        where = {"screen": "a whole screen", "mouse": "the lens around the mouse"}.get(
            self.cfg["mode"], f"an area of {rect.width()} × {rect.height()}")
        state = f"translating {where} into {languages.name(self.target)}" if running else "stopped"
        self.tray.setToolTip(f"{LENS_NAME} — {state}")
        if self.main_window is not None and self.main_window.isVisible():
            self.main_window.refresh_state()

    def _show(self, blocks):
        if self.cfg["overlay_visible"]:
            self.overlay.show_blocks(blocks, self.target)
        if self.text_window is not None:
            self.text_window.show_blocks(blocks, self.target)
        self._update_state()

    # ---- the translation window ------------------------------------------------
    def show_text_window(self, on):
        if on and self.text_window is None:
            from .lens_text_window import TextWindow

            self.text_window = TextWindow(self.cfg["text_window_settings"], self.cfg["overlay_visible"])
            self.text_window.changed.connect(self._text_window_changed)
            self.text_window.closed.connect(lambda: self.show_text_window(False))
            if self.overlay._blocks:
                self.text_window.show_blocks(self.overlay._blocks, self.target)
            self.text_window.show()
        elif not on and self.text_window is not None:
            window, self.text_window = self.text_window, None
            self._set(text_window_settings=window.settings(), overlay_visible=True)
            window.closed.disconnect()
            window.close()
            window.deleteLater()
            if self.scanner.running:
                self.scanner.restart()  # draw the translations over the screen again
        else:
            return
        self._set(text_window=on)
        self._notify("Translation window open" if on
                     else "Translation window closed: translations show on the screen")
        self.act_text_window.blockSignals(True)
        self.act_text_window.setChecked(on)
        self.act_text_window.blockSignals(False)

    def _under_own_windows(self):
        """Where the translation window lies over the area Lens reads (relative to it): left alone, not read."""
        window = self.text_window
        if window is None or not window.isVisible() or window.isMinimized() or self.scanner.area is None:
            return []
        screen, rect = self.scanner.area
        area = rect.translated(screen.geometry().topLeft())
        part = window.frameGeometry().intersected(area)
        return [] if part.isEmpty() else [part.translated(-area.topLeft())]

    def _text_window_changed(self, settings):
        if settings["overlay"] != self.cfg["overlay_visible"]:
            self._set(overlay_visible=settings["overlay"])
            if settings["overlay"]:
                self.scanner.restart()
                self._notify("Translations show on the screen too")
            else:
                self.overlay.clear()
                self._notify("Translations show only in the translation window")
        self._save_text_window.start()

    def _on_status(self, text):
        log.warning("Lens: %s", text)
        self.tray.setToolTip(f"{LENS_NAME} — {text}"[:120])
        self.activity.tell(text)

    def _follow_ctrl(self):
        self.overlay.set_interactive(self.cfg["select_mode"] or ctrl_held())
        if self.cfg["mode"] == "mouse" and self.scanner.running:
            self._follow_mouse()
        elif self.cfg["mode"] == "window" and self.scanner.running:
            self._follow_window()

    # ---- tray ----------------------------------------------------------------
    def _tray_activated(self, reason):
        """A click on the icon opens the window (the right-click menu and Ctrl+Alt+T start and stop)."""
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_window()

    def already_running(self):
        """Lens was opened again while it runs: show its window."""
        self.open_window()

    def open_window(self, page=None):
        """The EchoSub Lens window, in front, on `page` (translate, languages, translator, appearance,
        shortcuts, about) or where it was."""
        if self.main_window is None:
            from .lens_window import LensWindow

            self.main_window = LensWindow(self)
        self.main_window.present(page)
        return self.main_window

    # ---- files ---------------------------------------------------------------
    def open_file(self, path=None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(None, "Translate a file", self.cfg.get("last_folder") or "",
                                                  lens_files.OPEN_FILTER)
            if not path:
                return None
        if lens_files.kind(path) is None:
            QMessageBox.information(None, LENS_NAME, "Lens translates pictures, PDF documents, Word documents "
                                                     "(.docx) and text files. An old .doc file can be saved as "
                                                     ".docx from Word first.")
            return None
        self._set(last_folder=os.path.dirname(path))
        from .lens_file_window import FileWindow

        window = FileWindow(path, self.target, self.translator(), self.cfg["ocr_language"], self.cfg["style"],
                            open_another=self.open_file)
        self.file_windows.append(window)
        window.destroyed.connect(lambda *_: self.file_windows.remove(window) if window in self.file_windows
                                 else None)
        window.show()
        return window

    # ---- settings and screen sharing ----------------------------------------------
    def open_settings(self, tab="appearance"):
        """The window, on the Appearance or the Keyboard shortcuts page."""
        return self.open_window(tab)

    def apply_style(self, style):
        self.overlay.set_style(style)
        self._set(style=self.overlay.style)
        for window in self.file_windows:
            window.restyle(self.overlay.style)

    def _set_capture_visible(self, visible):
        self._set(capture_visible=visible)
        self.overlay.set_capture_hidden(not visible)
        self._notify("Translations show in screenshots and screen sharing" if visible
                     else "Translations are left out of screenshots and screen sharing")

    RESET_TITLES = {"translate": "Translate", "languages": "Languages", "translator": "Translator",
                    "appearance": "Appearance", "shortcuts": "Keyboard shortcuts"}

    def reset(self, page):
        """One page of settings back as it was when Lens was installed. Returns the keys another program holds
        (the shortcuts page), else []."""
        from . import lens_ocr
        from .lens_app import DEFAULTS
        from .lens_style import DEFAULT_STYLE

        taken = []
        tell, self.activity.tell = self.activity.tell, (lambda text: None)  # one note at the end, not one each
        try:
            if page == "translate":
                if self.text_window is not None and not self.cfg["overlay_visible"]:
                    self.text_window.only_here.setChecked(False)
                self.set_lens_size(list(DEFAULTS["lens_size"]))
                self.set_lens_frame(DEFAULTS["lens_frame"])
                self._set_capture_visible(DEFAULTS["capture_visible"])
                self.set_select_mode(DEFAULTS["select_mode"])
                self._set_peek(DEFAULTS["peek_seconds"])
                running = self.scanner.running
                self.use_screen(DEFAULTS["screen"])
                if not running:
                    self.stop()
            elif page == "languages":
                self._set_and_restart(target_lang=DEFAULTS["target_lang"], ocr_language=lens_ocr.AUTO)
            elif page == "translator":
                self.set_translator(DEFAULTS["translator"])
            elif page == "appearance":
                self.apply_style(dict(DEFAULT_STYLE))
            elif page == "shortcuts":
                taken = self.apply_keys(lens_shortcuts.defaults())
        finally:
            self.activity.tell = tell
        self.refresh_menu()
        self._notify(f"{self.RESET_TITLES.get(page, page)}: back to the default settings")
        return taken

    def _set_peek(self, seconds):
        self._set(peek_seconds=seconds)
        self.scanner.peek_every = seconds
        self._notify(f"Looking under the translations every {seconds} s" if seconds
                     else "Not looking under the translations")

    def quit(self):
        self.scanner.stop()
        self.scanner.capturer.close()
        self.hotkeys.disable()
        self.busy.clear()
        if self.text_window is not None:
            self._set(text_window_settings=self.text_window.settings())
            self.text_window.closed.disconnect()
            self.text_window.close()
        for window in list(self.file_windows):
            window.close()
        if self.main_window is not None:
            self.main_window.hide()  # keeps its place for next time
        self.tray.hide()
        self.qt.quit()

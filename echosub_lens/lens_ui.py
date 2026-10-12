# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: its icon by the clock, its keys, and what ties the scanner to the overlay."""
import ctypes
import logging
import os
import sys
import threading
import time

from PySide6.QtCore import QObject, QRect, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QIcon, QPainter, QPen
from PySide6.QtWidgets import QFileDialog, QMessageBox, QSystemTrayIcon

from . import (app_windows, config, downloads, hotkeys, languages, lens_capture, lens_files, lens_follow,
               lens_shortcuts, lens_translate, translate)
from .lens_busy import Activity, BusyIndicator
from .lens_app import LENS_NAME, ctrl_held
from .lens_menu import LensMenuMixin
from .lens_overlay import LensOverlay
from .lens_picker import CAPTURE_HINT, RegionPicker
from .lens_scanner import LensScanner
from .languages import QUICK_TARGETS

log = logging.getLogger(__name__)

MOUSE_INTERVAL_MS = 250  # the lens looks more often: it is small, and the pointer has just stopped
CAPTURE_SETTLE_MS = 150  # after the picker's shades close: Windows takes them off the screen before the photo
WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11


def _keep_out_of_photos(widgets, out):
    """Windows leaves these windows out of screen photos (`out`), or shows them in photos again. GDI photos
    see it at once."""
    if sys.platform != "win32":
        return
    for widget in widgets:
        try:
            ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()),
                                                          WDA_EXCLUDEFROMCAPTURE if out else WDA_NONE)
        except Exception:
            log.debug("Could not change the screenshot affinity of %r", widget, exc_info=True)


class MissingKey(RuntimeError):
    """The translator chosen needs the user's own key (DeepL), and none was saved."""


class _KeyRequest(QObject):
    """From the thread that loads a translator to the Qt thread: a key is needed (opens the Translator page)."""
    needed = Signal(str)


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
        self._next_window_search = 0.0
        self._anchor = None          # where the pointer was when the lens was last placed
        self.file_windows = []
        self.capture_windows = []    # "Capture part of the screen": one window for each picture
        self._capturing = False      # from the picker's start until the photo is taken: the scanner waits
        self.movie_window = None     # "Movie to subtitles": one window, kept
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
        self.scanner.paused = lambda: self._capturing or ctrl_held() or self.overlay.engaged() or self._window_away()
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
        self._key_request = _KeyRequest()
        self._key_request.needed.connect(self._ask_for_key)
        self._asked_for_key = set()  # asked once; again after the key or the translator changes
        self._stopped_for_key = False
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
            "snip": self.capture_screen,
            "file": self.open_file,
            "movie": self.open_movie,
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
            self._translators[key] = self._new_translator(key)
        return self._translators[key]

    def _new_translator(self, key):
        from . import cuda_libs

        device = self.base_cfg.get("device", "cuda")
        if device in ("cuda", "vulkan") and not cuda_libs.available():
            device = "cpu"
        fast_cpu = self.base_cfg.get("fast_cpu_translation", True)
        return lens_translate.LensTranslator(key, device, factory=lambda k, d: self._load_translator(k, d, fast_cpu))

    def translator_for(self, choice=None):
        """A translator for a file or a movie: `choice` is a key of lens_translate.CHOICES (None: the one Lens
        uses now). The one Lens uses is shared; another one is the job's own — `release_translator()` lets go
        of it (its model leaves the memory) when the job's window closes."""
        if choice is None:
            return self.translator()
        key = lens_translate.resolve(choice, self.base_cfg)
        if key == lens_translate.resolve(self.cfg["translator"], self.base_cfg):
            return self.translator()
        return self._new_translator(key)

    def release_translator(self, translator):
        """After a job: its own translator is switched off; the one Lens uses stays."""
        if translator is None or translator in self._translators.values():
            return
        self._switch_off(translator.key, translator)

    def _load_translator(self, key, device, fast_cpu):
        """Runs in a worker thread the first time a translator is needed; says so on the screen."""
        name = self._translator_name(key)
        self.activity.say("translator", f"Loading the translator ({name})…")
        downloader = downloads.ModelDownloader(on_event=self._download_event)
        try:
            if self.missing_key(key):
                self._key_request.needed.emit(key)  # the note and the Translator page, in the Qt thread
                raise MissingKey(self._key_note(key))
            if key == "google":  # no offline model waiting in the memory: Google alone
                backend = translate.GoogleTranslator()
            else:
                backend = translate.create(key, device, downloader=downloader, fast_cpu=fast_cpu,
                                           api_keys=dict(self.cfg.get("api_keys") or {}))
            if hasattr(backend, "notify"):
                backend.notify = self.activity.tell
            where = {"cuda": " on the graphics card", "cpu": " on the processor"}.get(
                getattr(backend, "device", ""), "")
            self.activity.tell(f"Translator ready: {name}{where}")
            return backend
        except MissingKey:
            raise
        except Exception as e:
            self.activity.tell(f"Could not load the translator ({name}): {e}")
            raise
        finally:
            self.activity.say("translator")

    @staticmethod
    def _translator_name(key):
        return (config.TRANSLATORS.get(key) or lens_translate.CHOICES.get(key) or key).split(" — ")[0]

    # ---- translators that need the user's own key (DeepL) ------------------------
    def missing_key(self, key):
        """True when the translator `key` needs the user's key (or keys) and one was not saved."""
        if key not in getattr(lens_translate, "NEEDS_KEY", ()):
            return False
        saved = self.cfg.get("api_keys") or {}
        fields = getattr(lens_translate, "KEY_FIELDS", {}).get(key) or [(key, "", "")]
        return any(not str(saved.get(name) or "").strip() for name, _, _ in fields)

    def _key_note(self, key):
        return f"{self._translator_name(key)} needs your key: open Translator settings"

    def _ask_for_key(self, key):
        """A translator was chosen without its key: say so, and open the page where the key is typed — once
        for each choice, not every time a text is translated."""
        if key in self._asked_for_key or not self.missing_key(key):
            return
        self._asked_for_key.add(key)
        self._notify(self._key_note(key))
        self.open_window("translator")

    def set_api_key(self, service, key):
        """Save the user's key for `service` (e.g. "deepl")."""
        self.set_api_keys(service, {service: key})

    def set_api_keys(self, service, values):
        """Save the user's keys for the translator `service` ({name in api_keys: value}); a translator of that
        service already loaded is switched off, so the next text is translated with the new keys."""
        values = {name: (value or "").strip() for name, value in values.items()}
        key = all(values.values())
        keys = dict(self.cfg.get("api_keys") or {})
        if all(keys.get(name, "") == value for name, value in values.items()):
            self._notify(f"{self._translator_name(service)} key saved" if key else
                         f"No {self._translator_name(service)} key")
            return
        keys.update(values)
        self._set(api_keys=keys)
        self._asked_for_key.discard(service)
        if service in self._translators:  # a new one, with the new key (and no translations kept from before)
            self._switch_off(service, self._translators.pop(service))
        self._notify(f"{self._translator_name(service)} key saved" if key else
                     f"{self._translator_name(service)} key removed")
        if lens_translate.resolve(self.cfg["translator"], self.base_cfg) != service:
            return
        if self.scanner.running:
            self.scanner.restart()
            self.start()  # the new translator, with the new key
        elif self._stopped_for_key and key:
            self.start()  # stopped only for want of the key

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
        self._asked_for_key.clear()  # chosen again: say again if its key is missing
        self._notify(f"Translator: {lens_translate.CHOICES[key]}")  # first: a missing key is said after it
        self._set_and_restart(translator=key)
        self.translator()  # switches the one used before off now, not when Lens starts again
        needed = lens_translate.resolve(key, self.base_cfg)
        if self.missing_key(needed):
            self._ask_for_key(needed)
        elif self._stopped_for_key:  # stopped for a missing key, and another translator chosen: go on
            self.start()

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
        if self.picker is not None or self._capturing:
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

    # ---- capture part of the screen ------------------------------------------------
    def capture_screen(self):
        """Drag a rectangle: its picture opens in a window of its own, with its text, original or translated.

        The screen translation is not disturbed: it waits while the rectangle is dragged and goes on after."""
        if self.picker is not None or self._capturing:
            return  # one picker at a time (choosing an area uses it too)
        self._capturing = True
        self.picker = RegionPicker(self._capture_chosen, CAPTURE_HINT)
        self.picker.start()

    def _capture_chosen(self, result):
        self.picker = None
        if result is None:
            self._capturing = False
            self._notify("Capture canceled")
            return
        screen, rect = result
        # The shades are closed; Windows takes them off the screen before the photo, not in it.
        QTimer.singleShot(CAPTURE_SETTLE_MS, lambda: self._take_capture(screen, rect))

    def _take_capture(self, screen, rect):
        try:
            image = self._photo_without_lens(screen, rect)
        except Exception:
            log.exception("Could not capture the screen")
            image = None
        finally:
            self._capturing = False
        if image is None or image.isNull():
            self._notify("Could not capture the screen")
            return None
        window = self.open_capture(image)
        self._notify(f"Captured {rect.width()} × {rect.height()} — reading the text…")
        return window

    def _photo_without_lens(self, screen, rect):
        """`rect` of `screen` as it is under Lens: its translations, the lens frame and its own windows are
        left out (the note in the corner always is)."""
        own = [w for w in (self.text_window, self.main_window) if w is not None and w.isVisible()]
        self.overlay.set_peeking(True)
        _keep_out_of_photos(own, True)
        try:
            image = lens_capture.gdi(screen, rect)
        finally:
            _keep_out_of_photos(own, False)
            self.overlay.set_peeking(False)
        if image is not None and not image.isNull():
            image.setDevicePixelRatio(screen.devicePixelRatio())
        return image

    def open_capture(self, image):
        """A window with the picture `image` (a QImage in the screen's own pixels) and its text."""
        from .lens_capture_window import CaptureWindow

        window = CaptureWindow(image, self)
        self.capture_windows.append(window)

        def closed(*_):
            if window in self.capture_windows:
                self.capture_windows.remove(window)
        window.destroyed.connect(closed)
        window.show()
        return window

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
        now = time.monotonic()
        if now < self._next_window_search:
            return None  # gone: look for it once a second, not on every tick of the 60 ms timer
        self._next_window_search = now + 1.0
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
        if self.picker is not None or self._capturing or ctrl_held() or self.overlay.engaged():
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
        key = lens_translate.resolve(self.cfg["translator"], self.base_cfg)
        if self.missing_key(key):  # nothing to translate with: the note and the Translator page instead
            if self.scanner.running:
                self.stop()
            self._stopped_for_key = True
            self._ask_for_key(key)
            self._update_state()
            return
        self._stopped_for_key = False
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
        self._stopped_for_key = False
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
            self._asked_for_key.clear()  # started by hand: say again if a key is missing
            self.start()
            if self.scanner.running:
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
        state = f"translating {self._where()} into {languages.name(self.target)}" if running else "stopped"
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
    def open_file(self, path=None, target=None, translator=None):
        """Translate a file in a window of its own: into `target` (a language code; None: Lens's language)
        with `translator` (a key of lens_translate.CHOICES; None: the one Lens uses)."""
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

        window = FileWindow(path, target or self.target, self.translator_for(translator), self.cfg["ocr_language"],
                            self.cfg["style"], open_another=self.open_file, choose_translator=self.translator_for,
                            release_translator=self.release_translator, target_choices=self._target_choices(),
                            translator_choice=translator or self.cfg["translator"], notify=self._notify)
        self.file_windows.append(window)

        def closed(*_):
            if window in self.file_windows:
                self.file_windows.remove(window)
            # The translator the window ended with (it may have been changed there); a plain Python attribute,
            # still readable after the Qt window itself is gone.
            self.release_translator(window.translator)
        window.destroyed.connect(closed)
        window.show()
        return window

    def open_movie(self, path=None):
        """The "Movie to subtitles" window (one, kept between uses), in front; with `path`, that movie chosen."""
        if self.movie_window is None:
            from .lens_movie_window import MovieWindow

            self.movie_window = MovieWindow(self)
        self.movie_window.present()
        if path:
            self.movie_window.choose_movie(path)
        return self.movie_window

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
                if self.scanner.running:
                    self.use_screen(DEFAULTS["screen"])
                else:  # stopped: stays stopped (use_screen would start it)
                    self._set(mode="screen", screen=DEFAULTS["screen"])
                    self._fill_screens()
                    self._sync_mouse_item()
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
        if taken:
            self._notify("The default keys were not used: another program holds " + ", ".join(taken))
        else:
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
        for window in list(self.file_windows) + list(self.capture_windows):
            window.close()
        if self.movie_window is not None:
            self.movie_window.shutdown()
        if self.main_window is not None:
            self.main_window.hide()  # keeps its place for next time
        self.tray.hide()
        self.qt.quit()

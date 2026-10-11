# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: its icon by the clock, its keys, and what ties the scanner to the overlay."""
import logging
import os

from PySide6.QtCore import QRect, QTimer
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QIcon, QPainter, QPen
from PySide6.QtWidgets import QFileDialog, QMessageBox, QSystemTrayIcon

from . import config, downloads, hotkeys, languages, lens_files, lens_follow, lens_shortcuts, lens_translate, translate
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
        self._save = save
        self.base_cfg = base_cfg if base_cfg is not None else config.load()  # the defaults
        self.keys = lens_shortcuts.merged(cfg.get("shortcuts"))
        self.picker = None
        self._mouse_area = None      # (QScreen, QRect) of the lens around the mouse
        self._anchor = None          # where the pointer was when the lens was last placed
        self.settings_window = None
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
        self.scanner.paused = lambda: ctrl_held() or self.overlay.engaged()
        self.scanner.blocked = self._under_own_windows
        self.scanner.peek_every = cfg["peek_seconds"]
        self.scanner.result.connect(self._show)
        self.scanner.stale.connect(self.overlay.clear)
        self.scanner.status.connect(self._on_status)
        self.busy = BusyIndicator()
        self.busy.screen_of = lambda: self.area()[0]
        self.activity = Activity()
        self.activity.changed.connect(self.busy.set)
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
            "settings": lambda: self.open_settings("shortcuts"),
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

    def translator(self):
        key = lens_translate.resolve(self.cfg["translator"], self.base_cfg)
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
        name = config.TRANSLATORS.get(key, key).split(" — ")[0]
        self.activity.say("translator", f"Loading the translator ({name})…")
        downloader = downloads.ModelDownloader(on_event=self._download_event)
        try:
            return translate.create(key, device, downloader=downloader, fast_cpu=fast_cpu)
        finally:
            self.activity.say("translator")

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

    def set_translator(self, key):
        self._set_and_restart(translator=key)

    def next_language(self):
        order = [""] + QUICK_TARGETS
        current = self.cfg["target_lang"] or ""
        code = order[(order.index(current) + 1) % len(order)] if current in order else order[0]
        self.set_target(code or None)
        self.refresh_menu()
        self._notify(f"Translating into {languages.name(self.target)}")

    def next_translator(self):
        order = list(lens_translate.CHOICES)
        current = self.cfg["translator"]
        key = order[(order.index(current) + 1) % len(order)] if current in order else order[0]
        self.set_translator(key)
        self.refresh_menu()
        self._notify(f"Translator: {lens_translate.CHOICES[key]}")

    def _notify(self, text):
        self.tray.showMessage(LENS_NAME, text, QSystemTrayIcon.Information, 2500)

    # ---- the area ------------------------------------------------------------
    def area(self):
        """(QScreen, QRect relative to it) of what is translated now."""
        if self.cfg["mode"] == "mouse":
            return self._mouse_area or self._lens_at(QCursor.pos())
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

    def use_screen(self, name):
        self._set(mode="screen", screen=name)
        self._fill_screens()
        self._sync_mouse_item()
        self.start()

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
        self.stop() if self.scanner.running else self.start()

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
            else:
                self.overlay.clear()
        self._save_text_window.start()

    def _on_status(self, text):
        log.warning("Lens: %s", text)
        self.tray.setToolTip(f"{LENS_NAME} — {text}"[:120])

    def _follow_ctrl(self):
        self.overlay.set_interactive(self.cfg["select_mode"] or ctrl_held())
        if self.cfg["mode"] == "mouse" and self.scanner.running:
            self._follow_mouse()

    # ---- tray ----------------------------------------------------------------
    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.toggle()

    def already_running(self):
        self.tray.showMessage(LENS_NAME, "Lens is already running — its icon is by the clock. "
                                         "Click it to start or stop; right-click for the menu.",
                              QSystemTrayIcon.Information, 5000)

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

    # ---- settings window and screen sharing --------------------------------------
    def open_settings(self, tab="appearance"):
        from .lens_settings import LensSettings

        if self.settings_window is not None:
            self.settings_window.show_tab(tab)
            self.settings_window.raise_()
            self.settings_window.activateWindow()
            return
        self.settings_window = LensSettings(self.cfg["style"], self.keys, self.apply_style, self.apply_keys, tab)
        self.settings_window.finished.connect(self._settings_closed)
        self.settings_window.show()

    def apply_style(self, style):
        self.overlay.set_style(style)
        self._set(style=self.overlay.style)
        for window in self.file_windows:
            window.restyle(self.overlay.style)

    def _settings_closed(self, _result):
        self.settings_window.deleteLater()
        self.settings_window = None

    def _set_capture_visible(self, visible):
        self._set(capture_visible=visible)
        self.overlay.set_capture_hidden(not visible)

    def _set_peek(self, seconds):
        self._set(peek_seconds=seconds)
        self.scanner.peek_every = seconds

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
        self.tray.hide()
        self.qt.quit()

# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: translates the text in an area of the screen, or a whole screen, in place.

Its own icon by the clock, its own settings (lens.json) and its own process: it runs on its own,
or beside EchoSub (the live-captions app). `python -m echosub_lens`, or EchoSubLens.exe.
"""
import ctypes
import json
import logging
import os
import sys

from . import cuda_setup, logging_setup

LENS_NAME = "EchoSub Lens"
INSTANCE_KEY = "EchoSub-Lens-single-instance"
MUTEX_NAME = "EchoSubLensMutex"  # the installer checks it too
VK_CONTROL = 0x11
OLD_INTERVAL_MS = 700  # the default before 1.1.0
OLD_PEEK_SECONDS = 5   # the default before 1.1.1, when looking under the translations made them blink

DEFAULTS = {
    "mode": "screen",            # screen | area | mouse (a lens that follows the pointer) | window
    "window_title": "",          # mode "window": the window chosen (found again by its title and program)
    "window_program": "",
    "mode_before_mouse": "screen",
    "lens_size": [480, 180],     # the lens around the mouse, logical pixels
    "lens_frame": True,          # a thin frame shows where the lens is
    "screen": "",                # screen name ("" = the main screen)
    "area": None,                # [x, y, w, h] relative to that screen, for mode "area"
    "target_lang": None,         # None = EchoSub's caption language
    "ocr_language": "auto",
    "translator": "same",
    "style": None,               # how translations look: lens_style.DEFAULT_STYLE, changed in Appearance
    "capture_visible": True,     # translations show in screenshots and screen sharing (Discord, OBS)
    "peek_seconds": 1,           # how often Lens looks at the page under its translations; 0: never
    "select_mode": False,        # translations take the mouse even without Ctrl
    "interval_ms": 400,          # ms between photos while nothing changes (faster while it settles)
    "shortcuts": None,           # {action: key}, see lens_shortcuts.ACTIONS; None = the default keys
    "last_folder": "",           # where "Translate a file" opens
    "text_window": False,        # the translation window (the translated text in a box, above all windows)
    "text_window_settings": None,  # its place, size, font size and "original text too"
    "overlay_visible": True,     # the translations over the screen (turned off from the translation window)
    "window_geometry": None,     # [x, y, w, h] of the EchoSub Lens window (a click on the tray icon opens it)
    "api_keys": {"deepl": ""},   # the user's own keys for online translators that need one (Translator page)
    "check_updates": True,       # ask GitHub once a day whether a newer version is out (About page)
    "update_checked": 0,         # when it last asked (unix time): at most once a day, across restarts
    "update_skipped": "",        # a version the user chose to skip
}


def config_path():
    from . import config
    return os.path.join(config.DATA_DIR, "lens.json")


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(config_path(), encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    from .lens_style import merged

    cfg["style"] = merged(cfg["style"])
    saved_keys = cfg["api_keys"] if isinstance(cfg["api_keys"], dict) else {}
    cfg["api_keys"] = {**DEFAULTS["api_keys"], **{k: str(v or "") for k, v in saved_keys.items()}}  # new ones too
    if cfg["interval_ms"] == OLD_INTERVAL_MS:  # the old default, saved with every setting: now quicker
        cfg["interval_ms"] = DEFAULTS["interval_ms"]
    if cfg["peek_seconds"] == OLD_PEEK_SECONDS:
        cfg["peek_seconds"] = DEFAULTS["peek_seconds"]
    return cfg


def save_config(cfg):
    with open(config_path(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def launch_command(frozen=None, executable=None):
    """The command that starts Lens in a process of its own."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    executable = executable or sys.executable
    if frozen:
        return executable, []
    folder, name = os.path.split(executable)
    windowless = os.path.join(folder, "pythonw.exe")
    if name.lower() == "python.exe" and os.path.exists(windowless):
        executable = windowless
    return executable, ["-m", "echosub_lens"]


def launch():
    from PySide6.QtCore import QProcess

    program, args = launch_command()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return QProcess.startDetached(program, args, root)[0]


def ctrl_held():
    if sys.platform != "win32":
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)


def main():
    cuda_setup.setup()
    logging_setup.setup()
    log = logging.getLogger("echosub_lens")
    if "--movie-to-srt" in sys.argv:  # no window, and not only one copy: it runs beside a running Lens
        from . import movie

        return movie.cli(sys.argv[sys.argv.index("--movie-to-srt") + 1:])
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from . import __version__, config
    from .single_instance import claim_mutex, claim_server

    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"EchoSub.Lens.{__version__}")
        main.mutex, first = claim_mutex(MUTEX_NAME)
        if not first:
            _wake_running_copy()
            return 0
    qt = QApplication(sys.argv)
    qt.setApplicationName(LENS_NAME)
    qt.setWindowIcon(QIcon(os.path.join(config.ASSETS_DIR, "echosub.ico")))
    qt.setQuitOnLastWindowClosed(False)
    server = claim_server(qt, INSTANCE_KEY)
    if server is None:
        _wake_running_copy()
        return 0
    from .lens_busy import SplashScreen

    splash = SplashScreen()
    splash.start()
    log.info("%s %s starting", LENS_NAME, __version__)
    splash.show_message("Loading…")
    from .lens_ui import LensApp

    splash.show_message("Getting the screen reader ready…")
    lens = LensApp(qt, load_config(), save_config)
    splash.show_message("Ready — the Lens icon is by the clock")
    splash.finish()
    main.splash = splash
    for path in files_to_open(sys.argv[1:]):  # "EchoSubLens.exe report.pdf"
        lens.open_file(path)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), lens.already_running()))
    main.server = server
    code = qt.exec()
    log.info("%s exited", LENS_NAME)
    return code


def files_to_open(args):
    return [a for a in args if not a.startswith("-") and os.path.isfile(a)]


def _wake_running_copy():
    """Opened again while running: tell the running copy, which says where its icon is."""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalSocket

    QCoreApplication.instance() or QCoreApplication(sys.argv)
    probe = QLocalSocket()
    probe.connectToServer(INSTANCE_KEY)
    if probe.waitForConnected(500):
        probe.write(b"show")
        probe.waitForBytesWritten(300)
        probe.disconnectFromServer()


if __name__ == "__main__":
    sys.exit(main())

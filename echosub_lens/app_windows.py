# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: the windows of other programs — a list to choose one from, and where it is now.

Lens translates the part of the screen a chosen window covers, and follows it when it moves or changes size.
"""
import ctypes
import os
import sys
from ctypes import wintypes as wt
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication

GW_OWNER = 4
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
HIDDEN_TITLES = {"Program Manager", "Windows Input Experience", "Settings", "Microsoft Text Input Application"}


@dataclass
class AppWindow:
    hwnd: int
    title: str
    program: str = ""

    @property
    def label(self):
        title = self.title if len(self.title) <= 60 else self.title[:57] + "…"
        return f"{title}  ({self.program})" if self.program else title


def _user32():
    user32 = ctypes.windll.user32
    user32.IsWindow.argtypes = user32.IsWindowVisible.argtypes = user32.IsIconic.argtypes = [wt.HWND]
    user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
    user32.GetWindow.restype = wt.HWND
    user32.GetWindowTextLengthW.argtypes = [wt.HWND]
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    return user32


def _dwm(hwnd, attribute, value):
    try:
        return ctypes.windll.dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), wt.DWORD(attribute), ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0
    except OSError:
        return False


def program_name(pid):
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wt.HANDLE
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wt.DWORD(1024)
        path = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(wt.HANDLE(handle), 0, path, ctypes.byref(size)):
            return os.path.splitext(os.path.basename(path.value))[0]
        return ""
    finally:
        kernel32.CloseHandle(wt.HANDLE(handle))


def title_of(hwnd):
    user32 = _user32()
    length = user32.GetWindowTextLengthW(hwnd)
    if not length:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def list_windows():
    """The windows on the taskbar, in front-to-back order (never Lens's own)."""
    if sys.platform != "win32":
        return []
    user32 = _user32()
    own, found = os.getpid(), []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def visit(hwnd, _):
        cloaked = wt.DWORD(0)
        if (hwnd and user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, GW_OWNER)
                and not (_dwm(hwnd, DWMWA_CLOAKED, cloaked) and cloaked.value)):
            title = title_of(hwnd)
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if title and title not in HIDDEN_TITLES and pid.value != own:
                found.append(AppWindow(int(hwnd), title, program_name(pid.value)))
        return True

    user32.EnumWindows(visit, 0)
    return found


def find(title, program, windows=None):
    """The window chosen before (Lens started again): the same title in the same program, or else the
    program's only window, or else a window with that title."""
    windows = list_windows() if windows is None else windows
    for test in (lambda w: w.title == title and w.program == program,
                 lambda w: program and w.program == program and sum(x.program == program for x in windows) == 1,
                 lambda w: title and w.title == title):
        match = next((w for w in windows if test(w)), None)
        if match is not None:
            return match
    return None


def alive(hwnd):
    return sys.platform == "win32" and bool(_user32().IsWindow(hwnd))


def hidden_behind(hwnd):
    """True when another program's window in front covers much of `hwnd` (its text is not what Lens should
    translate then). Lens's own windows, the taskbar and the desktop never count."""
    if sys.platform != "win32":
        return False
    user32 = _user32()
    user32.GetForegroundWindow.restype = wt.HWND
    user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    user32.GetAncestor.restype = wt.HWND
    front = user32.GetForegroundWindow()
    if not front or int(front) == hwnd or int(user32.GetAncestor(front, 3) or 0) == hwnd:  # GA_ROOTOWNER
        return False
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(front, ctypes.byref(pid))
    if pid.value == os.getpid() or not title_of(front) or user32.IsIconic(front):
        return False
    a, b = wt.RECT(), wt.RECT()
    if not (_dwm(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, a) and _dwm(int(front), DWMWA_EXTENDED_FRAME_BOUNDS, b)):
        return False
    w = min(a.right, b.right) - max(a.left, b.left)
    h = min(a.bottom, b.bottom) - max(a.top, b.top)
    area = max((a.right - a.left) * (a.bottom - a.top), 1)
    return w > 0 and h > 0 and w * h > 0.3 * area


def spot(hwnd):
    """(QScreen, QRect relative to it, in Qt's pixels) of the window now; None while it is minimized or gone."""
    if sys.platform != "win32":
        return None
    user32 = _user32()
    if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):
        return None
    bounds = wt.RECT()
    if not _dwm(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, bounds):
        if not user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(bounds)):
            return None
    return to_screen(bounds.left, bounds.top, bounds.right, bounds.bottom)


def to_screen(left, top, right, bottom, screens=None):
    """Windows' pixels to the screen the window is mostly on and a rectangle relative to it (Qt's pixels)."""
    centre = QPoint((left + right) // 2, (top + bottom) // 2)
    best = None
    for screen in screens or QGuiApplication.screens():
        g, ratio = screen.geometry(), screen.devicePixelRatio()
        physical = QRect(g.topLeft(), g.size() * ratio)  # on Windows Qt keeps each screen's corner in pixels
        if physical.contains(centre) or best is None:
            best = (screen, g, ratio)
            if physical.contains(centre):
                break
    screen, g, ratio = best
    rect = QRect(round((left - g.x()) / ratio), round((top - g.y()) / ratio),
                 round((right - left) / ratio), round((bottom - top) / ratio))
    rect = rect.intersected(QRect(0, 0, g.width(), g.height()))
    return None if rect.isEmpty() else (screen, rect)

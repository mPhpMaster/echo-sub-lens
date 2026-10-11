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
GW_HWNDPREV = 3
GA_ROOTOWNER = 3
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
HIDDEN_TITLES = {"Program Manager", "Windows Input Experience", "Settings", "Microsoft Text Input Application"}
# The desktop (clicking it makes it the foreground window) and the taskbars: they never hide a window
DESKTOP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


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
    user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wt.HWND
    user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    user32.GetAncestor.restype = wt.HWND
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


def class_of(hwnd):
    buffer = ctypes.create_unicode_buffer(256)
    return buffer.value if _user32().GetClassNameW(hwnd, buffer, 256) else ""


def above(hwnd, front):
    """True when `front` is above `hwnd` in the z-order (walking up from `hwnd` reaches it)."""
    user32 = _user32()
    current = hwnd
    for _ in range(10000):  # never loop for ever, should the z-order change while it is walked
        current = user32.GetWindow(current, GW_HWNDPREV)
        if not current:
            return False
        if int(current) == int(front):
            return True
    return False


def covers(target, front, front_class, front_title, front_above):
    """Whether the foreground window hides the chosen one: it is above it in the z-order, is a real window
    (not the desktop, the taskbar or an untitled one) and covers more than 30 % of it. `target` and `front`
    are (left, top, right, bottom) rectangles."""
    if not front_above or front_class in DESKTOP_CLASSES or not front_title or front_title in HIDDEN_TITLES:
        return False
    w = min(target[2], front[2]) - max(target[0], front[0])
    h = min(target[3], front[3]) - max(target[1], front[1])
    area = max((target[2] - target[0]) * (target[3] - target[1]), 1)
    return w > 0 and h > 0 and w * h > 0.3 * area


def hidden_behind(hwnd):
    """True when another program's window in front covers much of `hwnd` (its text is not what Lens should
    translate then). Lens's own windows, the taskbar and the desktop never count."""
    if sys.platform != "win32":
        return False
    user32 = _user32()
    front = user32.GetForegroundWindow()
    if not front or int(front) == hwnd or int(user32.GetAncestor(front, GA_ROOTOWNER) or 0) == hwnd:
        return False
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(front, ctypes.byref(pid))
    if pid.value == os.getpid() or user32.IsIconic(front):
        return False
    a, b = wt.RECT(), wt.RECT()
    if not (_dwm(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, a) and _dwm(int(front), DWMWA_EXTENDED_FRAME_BOUNDS, b)):
        return False
    return covers((a.left, a.top, a.right, a.bottom), (b.left, b.top, b.right, b.bottom), class_of(front),
                  title_of(front), above(hwnd, front))


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

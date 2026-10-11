# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: photographing the screen.

On the main screen through DirectX (Desktop Duplication, about 5 ms for a whole 1080p screen, and it
also sees games that GDI photographs as a black picture); elsewhere, or if DirectX is not available
(a remote desktop, an old driver), through GDI (Qt's grabWindow, about 35 ms). Both leave out the
windows Windows is told to keep out of screenshots, which Lens relies on.
"""
import logging

from PySide6.QtGui import QGuiApplication, QImage

log = logging.getLogger(__name__)


def gdi(screen, rect):
    return screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height()).toImage()


class Capturer:
    def __init__(self, enabled=True):
        self._camera = None
        self._failed = not enabled

    def grab(self, screen, rect, clean=False):
        """`rect` (logical pixels, relative to `screen`) as a QImage in the screen's own pixels.

        `clean`: right after a window was left out of screenshots. GDI shows that at once; DirectX only
        in the next frame Windows puts together, which may be one already on its way."""
        if not clean and not self._failed and screen is QGuiApplication.primaryScreen():
            try:
                image = self._directx(screen, rect)
                if image is not None:
                    return image
            except Exception:
                log.exception("DirectX screen capture failed; using GDI from now on")
                self._failed = True
                self.close()
        return gdi(screen, rect)

    def _directx(self, screen, rect):
        if self._camera is None:
            import dxcam

            self._camera = dxcam.create(output_color="BGRA")
            if self._camera is None:
                raise RuntimeError("no DirectX output for the main screen")
        ratio = screen.devicePixelRatio()
        width, height = self._camera.width, self._camera.height
        left, top = max(0, int(rect.x() * ratio)), max(0, int(rect.y() * ratio))
        right = min(width, int(round((rect.x() + rect.width()) * ratio)))
        bottom = min(height, int(round((rect.y() + rect.height()) * ratio)))
        if right <= left or bottom <= top:
            return None
        frame = self._camera.grab(region=(left, top, right, bottom), new_frame_only=False)
        if frame is None:
            return None  # nothing yet (just started): GDI this once
        h, w = frame.shape[:2]
        image = QImage(frame.data, w, h, frame.strides[0], QImage.Format_ARGB32).copy()  # BGRA in memory
        image.setDevicePixelRatio(ratio)
        return image

    def close(self):
        if self._camera is not None:
            try:
                self._camera.release()
            except Exception:
                pass
            self._camera = None

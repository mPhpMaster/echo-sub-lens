# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: a picture searched with Google Lens, in the user's own browser.

Google ties the results of an uploaded picture to the browser that uploaded it: results of an upload made by
Lens itself open empty in the user's browser. So the browser uploads it: Lens writes a small page holding the
picture, which sends it to Google Lens at once — exactly as dropping the picture on lens.google.com does — and
Google answers with its results page. Nothing is sent before the user asks; the page is deleted the next time.
"""
import base64
import os
import time

from . import config

UPLOAD = "https://lens.google.com/v3/upload"
PAGE_NAME = "google-lens.html"

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Google Lens — EchoSub Lens</title>
<style>body {{ font: 16px "Segoe UI", sans-serif; background: #111615; color: #e5e7eb; margin: 3em; }}
img {{ max-width: 480px; max-height: 240px; border: 1px solid #2b3432; margin-top: 1em; }}</style></head>
<body>
<p id="status">Sending the picture to Google Lens…</p>
<form id="lens" method="post" enctype="multipart/form-data" action="{action}">
<input type="file" name="encoded_image" id="picture" style="display:none">
<noscript><p>Turn JavaScript on for this page, or drop the picture on <a href="https://lens.google.com/">Google
Lens</a>.</p></noscript>
</form>
<img alt="" src="data:image/png;base64,{data}">
<script>
(function () {{
  try {{
    var raw = atob("{data}"), bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    var picture = new File([bytes], "capture.png", {{type: "image/png"}});
    var files = new DataTransfer();
    files.items.add(picture);
    document.getElementById("picture").files = files.files;
    document.getElementById("lens").submit();
  }} catch (e) {{
    document.getElementById("status").innerHTML = "This browser could not send the picture by itself. Save it " +
      "(right-click it below) and drop it on <a href='https://lens.google.com/'>Google Lens</a>.";
  }}
}})();
</script>
</body></html>
"""


def upload_address(now=None):
    stamp = int((now if now is not None else time.time()) * 1000)
    return f"{UPLOAD}?ep=gsbubb&hl=en&re=df&st={stamp}"


def page(png, now=None):
    """The page that sends `png` (bytes) to Google Lens as soon as the browser opens it."""
    return PAGE.format(action=upload_address(now), data=base64.b64encode(png).decode("ascii"))


def page_path():
    folder = os.path.join(config.DATA_DIR, "search")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, PAGE_NAME)


def search_picture(png, opener=None):
    """Opens the user's browser on Google Lens's results for `png`. Returns what to tell the user; raises OSError
    when the page cannot be written or opened."""
    path = page_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write(page(png))
    if opener is None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            raise OSError("no browser opened the page")
    else:
        opener(path)
    return "Google Lens is opening in your browser"

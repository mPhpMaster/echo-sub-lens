# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""EchoSub Lens: is a newer version out? One question to GitHub — the latest release's version number.

Nothing about the user is sent (no key, no account, no ID): only the app's name and version, as the User-Agent.
Lens never downloads or runs an installer by itself: it says a version is out and opens its page.
"""
import logging
import os
import re
import threading

from . import REPO_URL, __version__

log = logging.getLogger(__name__)

API_URL = "https://api.github.com/repos/mPhpMaster/echo-sub-lens/releases/latest"
RELEASES_URL = f"{REPO_URL}/releases"
TIMEOUT = 10                  # s
CHECK_EVERY = 24 * 60 * 60    # s: at most one automatic check a day, across restarts
FIRST_CHECK_MS = 20 * 1000    # after Lens starts
TICK_MS = 60 * 60 * 1000      # while it runs, it asks itself every hour whether a day has passed
INSTALLER = re.compile(r"^EchoSubLens-Setup-.*\.exe$", re.IGNORECASE)
_VERSION = re.compile(r"^\s*v?(\d+(?:\.\d+)*)", re.IGNORECASE)


def parse_version(text):
    """"v1.8.1" → (1, 8, 1); a tuple is kept; None when it is not a version."""
    if isinstance(text, tuple):
        return text if text and all(isinstance(n, int) for n in text) else None
    match = _VERSION.match(str(text or ""))
    return tuple(int(n) for n in match.group(1).split(".")) if match else None


def newer(a, b):
    """True when version `a` is newer than `b` ("1.10.0" > "1.9.2"; "1.9" == "1.9.0"). Not a version: False."""
    a, b = parse_version(a), parse_version(b)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def release_page(version):
    """The page of that release on GitHub."""
    return f"{RELEASES_URL}/tag/v{version}" if version else f"{RELEASES_URL}/latest"


def latest_release(session=None):
    """(version "1.9.0", url, notes) of the latest release — url: its installer when it has one, else its page.
    None when GitHub cannot be reached or answers something else; never raises."""
    try:
        if session is None:
            import requests

            session = requests
        response = session.get(API_URL, timeout=TIMEOUT,
                               headers={"User-Agent": f"EchoSub-Lens/{__version__}",
                                        "Accept": "application/vnd.github+json"})
        if response.status_code != 200:
            log.info("Update check: GitHub answered %s", response.status_code)
            return None
        data = response.json()
        version = parse_version(data.get("tag_name")) if isinstance(data, dict) else None
        if version is None:
            log.info("Update check: no version in GitHub's answer")
            return None
        version = ".".join(str(n) for n in version)
        url = next((a.get("browser_download_url") for a in data.get("assets") or []
                    if isinstance(a, dict) and INSTALLER.match(str(a.get("name") or ""))
                    and a.get("browser_download_url")), None)
        url = url or data.get("html_url") or release_page(version)
        return version, str(url), str(data.get("body") or "")
    except Exception as error:  # no internet, a timeout, not JSON: Lens just does not know
        log.info("Update check failed: %s", error)
        return None


def due(cfg, now):
    """An automatic check now? Only when it is on and the last one was a day ago (or the clock went back)."""
    if not cfg.get("check_updates", True):
        return False
    try:
        checked = float(cfg.get("update_checked") or 0)
    except (TypeError, ValueError):
        checked = 0.0
    return not 0 <= now - checked < CHECK_EVERY


def in_background(work):
    """Run `work` in a thread of its own: GitHub may take up to TIMEOUT seconds, the window keeps answering."""
    thread = threading.Thread(target=work, name="lens-update-check", daemon=True)
    thread.start()
    return thread


def automatic_allowed():
    """No automatic checks without a screen (tests run "offscreen": nobody to tell, and no network wanted)."""
    return (not os.environ.get("ECHOSUB_LENS_NO_UPDATE_CHECK")
            and os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen")

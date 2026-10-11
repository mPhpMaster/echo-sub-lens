# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""Where EchoSub Lens keeps its settings, logs and models, and the defaults its "Default" choices follow."""
import json
import os
import sys

from . import APP_ID

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(PACKAGE_DIR, "assets")
FROZEN = getattr(sys, "frozen", False)  # running from a PyInstaller build
LOCAL = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")


def _data_dir():
    """ECHOSUB_LENS_DATA_DIR overrides it; a build uses %LOCALAPPDATA%\\EchoSubLens; from source, the project folder."""
    override = os.environ.get("ECHOSUB_LENS_DATA_DIR")
    if override:
        return os.path.abspath(override)
    if FROZEN:
        return os.path.join(LOCAL, APP_ID)
    return os.path.dirname(PACKAGE_DIR)


DATA_DIR = _data_dir()
os.makedirs(DATA_DIR, exist_ok=True)

# EchoSub (the live-captions app), when it is installed: Lens shares its models and follows its choices
ECHOSUB_DIR = os.path.join(LOCAL, "EchoSub")
ECHOSUB_SETTINGS = os.path.join(ECHOSUB_DIR, "settings.json")


def _models_dir():
    """EchoSub's model folder when it exists (the translation models are downloaded only once), else our own."""
    override = os.environ.get("ECHOSUB_MODELS_DIR")
    if override:
        return override
    shared = os.path.join(ECHOSUB_DIR, "models")
    return shared if os.path.isdir(shared) else os.path.join(DATA_DIR, "models")


MODELS_DIR = _models_dir()

TRANSLATORS = {
    "nllb-600m": "NLLB 600M — offline (fast)",
    "nllb-1.3b": "NLLB 1.3B — offline (more accurate)",
    "google": "Google Translate — needs internet",
    "none": "No translation (original text only)",
}

NLLB_REPOS = {
    "nllb-600m": "JustFrederik/nllb-200-distilled-600M-ct2-int8",
    "nllb-1.3b": "JustFrederik/nllb-200-distilled-1.3B-ct2-int8",
}


def windows_language():
    """The Windows display language, when Lens can translate into it; else English."""
    from PySide6.QtCore import QLocale

    from . import languages

    code = QLocale.system().name().split("_")[0].lower()
    return code if code in languages.LANGUAGES else "en"


def load(path=None):
    """The defaults "Default" stands for: EchoSub's own settings if EchoSub is on this PC, else Windows'
    language and Google Translate (nothing to download)."""
    base = {"target_lang": None, "translator": "google", "device": "cuda", "fast_cpu_translation": True}
    try:
        with open(path or ECHOSUB_SETTINGS, encoding="utf-8") as f:
            saved = json.load(f)
        base.update({k: saved[k] for k in base if k in saved})
    except (OSError, ValueError):
        pass
    if not base["target_lang"]:
        base["target_lang"] = windows_language()
    return base

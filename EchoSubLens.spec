# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
#
# PyInstaller build of EchoSub Lens (one-folder app). Run through scripts\build.ps1, which also creates
# build\version_info.txt.
# NVIDIA's CUDA libraries are not bundled: an NVIDIA PC downloads them on first use (echosub_lens/cuda_libs.py),
# or uses EchoSub's copy when EchoSub is installed.
import os

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(SPECPATH)

datas = [(os.path.join(ROOT, "echosub_lens", "assets"), "echosub_lens/assets")]
for doc in ("LICENSE", "README.md", "CHANGELOG.md"):
    datas.append((os.path.join(ROOT, doc), "."))

binaries = collect_dynamic_libs("ctranslate2")

hiddenimports = (
    collect_submodules("winrt")  # Windows OCR
    + collect_submodules("dxcam")  # DirectX screen capture
    + ["sentencepiece", "deep_translator", "PySide6.QtPdf", "PySide6.QtNetwork", "rapidfuzz"]
)

a = Analysis(
    [os.path.join(ROOT, "launcher.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "torch", "tensorflow", "IPython", "pytest",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick3D",
              "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EchoSubLens",
    icon=os.path.join(ROOT, "echosub_lens", "assets", "echosub.ico"),
    version=os.path.join(ROOT, "build", "version_info.txt"),
    console=False,
    disable_windowed_traceback=False,
    upx=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="EchoSubLens",
)

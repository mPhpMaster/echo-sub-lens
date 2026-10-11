# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Mohammad Al-Safadi
"""Entry point of the PyInstaller build (EchoSubLens.exe); from source, use `python -m echosub_lens`."""
import sys

from echosub_lens.lens_app import main

if __name__ == "__main__":
    sys.exit(main())

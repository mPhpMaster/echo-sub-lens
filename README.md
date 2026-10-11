# EchoSub Lens

<a href="https://paypal.me/mfsafadi"><img alt="Support the developer on PayPal" src="https://img.shields.io/badge/support-PayPal-E11D48.svg"></a>

**Translate the text on your screen, in place.** Pick an area, a whole screen, or let a small lens follow your
mouse — every piece of text in it is shown translated, right on top of the original, while you keep using the
page or program underneath. It also translates files: pictures, PDF, Word and subtitles.

EchoSub Lens is the screen-translation tool of [EchoSub](https://github.com/mPhpMaster/echo-sub) (live, translated
captions for anything your PC plays), released here as a project of its own. It runs on its own, or beside EchoSub.

<p align="center"><img src="docs/images/window-translate.png" alt="The EchoSub Lens window: Start / Stop and what to translate" width="760"></p>

## Screenshots

| | |
|---|---|
| ![Languages](docs/images/window-languages.png) | ![Translator](docs/images/window-translator.png) |
| *Languages: what to translate into, and the language of the text on the screen* | *Translator: Google online, or NLLB offline on your graphics card* |
| ![Appearance](docs/images/window-appearance.png) | ![Keyboard shortcuts](docs/images/window-shortcuts.png) |
| *Appearance: font, size and colours, shown at once* | *A keyboard shortcut for every option* |
| ![Translation window](docs/images/translation-window.png) | ![About](docs/images/window-about.png) |
| *The translation window: a dialogue keeps a line, and its dash, for each speaker* | *About: version, author, links and ♥ support* |

<p align="center"><img src="docs/images/progress-note.png" alt="A note in the corner of the screen while Lens translates"><br>
<i>Everything Lens does shows in a small note in the corner of the screen.</i></p>

The pictures are made by `scripts\screenshots.py`.

## Features

**One window for everything**
- **Click the Lens icon by the clock** to open the EchoSub Lens window: a big *Start / Stop translating* button
  that shows what Lens is doing, and every option on a page of its own — *Translate*, *Languages*, *Translator*,
  *Appearance*, *Keyboard shortcuts* and *About* — each with a line that says what it does. Closing the window keeps
  Lens running by the clock; right-click the icon for the quick menu.
- *About*: the version, the author and his links (GitHub, LinkedIn, Discord, email), the license, the changelog
  and the source code.

**What to translate**
- **An area** you drag on any screen (`Ctrl+Alt+R`), or **a whole screen** — one of your monitors.
- **A lens around the mouse** (`Ctrl+Alt+M`): only the text near the pointer, in a small framed box that follows
  the mouse. Stop on a line and its translation appears about half a second later. Four sizes, bigger / smaller.
- **A file** (`Ctrl+Alt+F`): pictures (PNG, JPG, BMP, GIF, TIFF, WebP), PDF (scanned ones too), Word `.docx`
  (headings, tables, bold and colours kept — only the words change), text and subtitle files (`.txt`, `.srt`, `.md`,
  `.csv`; subtitle numbers and times stay). Save a translated copy; the original is never changed.

**The translated text in a window of its own**
- **Translation window** (`Ctrl+Alt+W`): a window that stays above all others with the translated text in a box —
  read it, select it, copy it. Show the original under each line, change the font size, and keep or hide the
  translations over the screen.

**While it translates**
- Press `Ctrl+Alt+T` — or *Start / Stop* in the window, or *Translating* in the right-click menu — to start or stop.
- Clicks, scrolling and typing go straight through to the page or program underneath.
- **Hold `Ctrl`** and the translations take the mouse: select a word or a sentence and press `Ctrl+C`, or
  right-click for *Copy this translation*, *Copy the original text* or *Copy all translations*. Hovering with `Ctrl`
  held shows the original. *Select mode* keeps them that way without holding `Ctrl`.
- The translations stay on the screen until the new ones are ready — no blinking over a video. Text already
  translated is remembered, so only new text is translated again.
- **Subtitles:** a dialogue (`- Where to?` / `- Home.`) keeps a line and a dash for each speaker, and a sentence
  over two lines is shown on two lines.

**Screen sharing**
- Translations show in screenshots and screen sharing (Discord, Teams, OBS), so the people watching see the
  translated screen — or untick *Show translations in screenshots and screen sharing* to keep them to yourself.
- Lens never reads its own words back: it leaves them out when it compares the screen, reads text only from a
  picture without them, and every second looks underneath for text that changed (adjustable, or never) — in a few milliseconds,
  without anything changing on the screen.

**Your way**
- *Appearance*: font, size (fitted to the original, larger or smaller, or one fixed size), bold, text and background
  colours (the page's own, or yours), background opacity and rounded corners — every change shows at once.
- *Keyboard shortcuts*: a key for every option — any key or combination, F-keys, the keypad and media keys too, with
  or without `Ctrl`, `Alt`, `Shift` or the Windows key. Keys used twice or held by another program are pointed out.
- Translate into ~200 languages; the text on the screen is read in any language whose Windows OCR pack is installed.

## How it works

- **Reading the screen:** Windows' own text recognition (Windows.Media.Ocr), on your PC and offline. To read another
  language, add it in *Settings → Time & language → Language* with its *Optical character recognition* feature.
- **Translating:** NLLB-200 (offline, on your graphics card or processor) or Google Translate. *Default* follows
  EchoSub's choice when EchoSub is installed (and shares its downloaded models); otherwise it is your Windows
  language and Google Translate, which needs nothing downloaded. An NLLB model (about 600 MB or 1.3 GB) is
  downloaded the first time you choose it. Only one translator is loaded at a time: choosing another one frees the
  memory of the one before. Google gets every text of the screen in one request and answers each on its own; when
  one of its services says "too many requests", Lens asks another, and only pauses for a few seconds if all refuse.

## Requirements

- Windows 10 version 2004 or later, or Windows 11 (64-bit).
- Python 3.10 to run from source.
- An NVIDIA graphics card is optional: NLLB runs on the processor too (it uses EchoSub's NVIDIA libraries if they
  are there).

## Install

Download `EchoSubLens-Setup-<version>.exe` from [Releases](https://github.com/mPhpMaster/echo-sub-lens/releases)
and run it — no administrator rights needed. You can check the download against the `.sha256` file next to it.

## Run from source

```bat
py -3.10 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
run.bat
```

Or `.venv\Scripts\python -m echosub_lens [file]` — a file name opens that file to translate.
Settings are kept in `lens.json` and the log in `logs\lens.log`, in the project folder (or in
`%LOCALAPPDATA%\EchoSubLens` for a built copy; `ECHOSUB_LENS_DATA_DIR` overrides it).

## Build the installer

Needs Python 3.10 and [Inno Setup 6](https://jrsoftware.org/isdl.php).

```bat
powershell -ExecutionPolicy Bypass -File scripts\build-installer.ps1
```

It builds `dist\EchoSubLens\EchoSubLens.exe` with PyInstaller (`scripts\build.ps1`), then
`dist\installer\EchoSubLens-Setup-<version>.exe` and its SHA-256 hash.

## Tests

```bat
.venv\Scripts\python -m unittest discover -s tests
.venv\Scripts\python -m flake8 echosub_lens tests
```

The tests need no OCR language, model or network.

## Privacy

The picture of your screen never leaves your PC: it is read by Windows on your computer. With an NLLB translator
nothing is sent anywhere. With Google Translate, the text Lens read is sent to Google to be translated, and
Google's privacy policy applies. Lens keeps no history of what it read.

## Support the developer

EchoSub Lens is free and always will be. If it helps you, you can support its development with a donation through
**[PayPal](https://paypal.me/mfsafadi)** — it is also in the app, under *♥ Support the developer* in the tray menu and on the *About* page.
It is entirely optional: nothing is held back, and Lens never asks.

## License

EchoSub Lens is Copyright © 2026 Mohammad Al-Safadi and licensed under the
[GNU General Public License v3.0](LICENSE) (GPL-3.0-only). It is built on PySide6 / Qt (LGPL-3.0), CTranslate2 (MIT),
SentencePiece (Apache-2.0), NumPy (BSD-3-Clause), Hugging Face Hub (Apache-2.0), deep-translator (MIT),
requests (Apache-2.0) and PyWinRT (MIT); the NLLB-200 models are CC-BY-NC-4.0 (Meta), downloaded on first use.

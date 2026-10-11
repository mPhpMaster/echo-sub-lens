# EchoSub Lens

**Translate the text on your screen, in place.** Pick an area, a whole screen, or let a small lens follow your
mouse — every piece of text in it is shown translated, right on top of the original, while you keep using the
page or program underneath. It also translates files: pictures, PDF, Word and subtitles.

EchoSub Lens is the screen-translation tool of [EchoSub](https://github.com/mPhpMaster/echo-sub) (live, translated
captions for anything your PC plays), released here as a project of its own. It runs on its own, or beside EchoSub.

## Features

**What to translate**
- **An area** you drag on any screen (`Ctrl+Alt+R`), or **a whole screen** — one of your monitors.
- **A lens around the mouse** (`Ctrl+Alt+M`): only the text near the pointer, in a small framed box that follows
  the mouse. Stop on a line and its translation appears about half a second later. Four sizes, bigger / smaller.
- **A file** (`Ctrl+Alt+F`): pictures (PNG, JPG, BMP, GIF, TIFF, WebP), PDF (scanned ones too), Word `.docx`
  (headings, tables, bold and colours kept — only the words change), text and subtitle files (`.txt`, `.srt`, `.md`,
  `.csv`; subtitle numbers and times stay). Save a translated copy; the original is never changed.

**While it translates**
- Click the tray icon or press `Ctrl+Alt+T` to start or stop.
- Clicks, scrolling and typing go straight through to the page or program underneath.
- **Hold `Ctrl`** and the translations take the mouse: select a word or a sentence and press `Ctrl+C`, or
  right-click for *Copy this translation*, *Copy the original text* or *Copy all translations*. Hovering with `Ctrl`
  held shows the original. *Select mode* keeps them that way without holding `Ctrl`.
- While the page scrolls or changes, the translations step aside and come back once it settles. Text already
  translated is remembered, so only new text is translated again.

**Screen sharing**
- Translations show in screenshots and screen sharing (Discord, Teams, OBS), so the people watching see the
  translated screen — or untick *Show translations in screenshots and screen sharing* to keep them to yourself.
- Lens never reads its own words back: it leaves them out when it compares the screen, reads text only from a
  picture without them, and every few seconds looks underneath for text that changed (adjustable, or never).

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
  downloaded the first time you choose it.

## Requirements

- Windows 10 version 2004 or later, or Windows 11 (64-bit).
- Python 3.10 to run from source.
- An NVIDIA graphics card is optional: NLLB runs on the processor too (it uses EchoSub's NVIDIA libraries if they
  are there).

## Run from source

```bat
py -3.10 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
run.bat
```

Or `.venv\Scripts\python -m echosub_lens [file]` — a file name opens that file to translate.
Settings are kept in `lens.json` and the log in `logs\lens.log`, in the project folder (or in
`%LOCALAPPDATA%\EchoSubLens` for a built copy; `ECHOSUB_LENS_DATA_DIR` overrides it).

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

## License

EchoSub Lens is Copyright © 2026 Mohammad Al-Safadi and licensed under the
[GNU General Public License v3.0](LICENSE) (GPL-3.0-only). It is built on PySide6 / Qt (LGPL-3.0), CTranslate2 (MIT),
SentencePiece (Apache-2.0), NumPy (BSD-3-Clause), Hugging Face Hub (Apache-2.0), deep-translator (MIT),
requests (Apache-2.0) and PyWinRT (MIT); the NLLB-200 models are CC-BY-NC-4.0 (Meta), downloaded on first use.

# Changelog

All notable changes to EchoSub Lens are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [1.1.1] — 2026-10-11

### Fixed
- **Subtitles over a video no longer blink or get skipped.** With *Show translations in screenshots* on, every move of the video behind a subtitle took the translation away so Lens could look underneath, and since a video never stands still, Lens then waited up to 3 seconds before reading again: lines came late, blinked, or were missed altogether. Now the translation stays on the screen until the next one is ready, Lens looks underneath it in a few milliseconds (Windows leaves it out of that one photo; on the screen nothing changes), and a picture that keeps moving is read at least every 0.6 seconds.
- Looking under the translations is now invisible, so it happens every second by default (it was every 5 seconds; *Every second* is a new choice).

## [1.1.0] — 2026-10-11

### Added
- **Translation window** (`Ctrl+Alt+W`, or *Translation window* in the menu): a window above all others with the translated text of the screen in a box, in reading order, ready to read, select and copy. Optionally with the original text under each line, a larger or smaller font, and with or without the translations over the screen. It remembers its place and size.
- **Splash screen** while Lens starts, saying what it is getting ready.
- **A progress bar on the screen while Lens works:** reading and translating the screen, loading the translator, or downloading a translation model (with how much is done). It sits in the corner of the screen being translated, never takes the mouse, and is left out of screenshots so Lens never reads it back. Translating a file shows a progress bar too.

### Faster
- **The graphics card is used when EchoSub is installed:** Lens now finds the NVIDIA libraries in EchoSub's own folder. Before, it translated on the processor — about 12 times slower (a whole screen: 1.2 s instead of 10 s and more).
- **Google Translate is asked through its quick JSON service** over one kept-open connection, instead of the translate.google.com page; lines it merges are asked again in halves, not one request per line (which could take a minute).
- **DirectX screen capture** on the main screen: about 5 ms instead of 35 ms, and it also sees games that a normal capture shows as black. Other screens, or PCs without it, use the normal capture.
- **Quicker to notice:** while the screen changes Lens looks every 0.12 s, so a page is read as soon as it is still; when nothing changes it looks every 0.4 s (was 0.7 s).
- **Text read with one letter different keeps its translation** (no new request, nothing redrawn) — but never when a number or a whole word differs.
- **One pass for the whole screen** on a graphics card.

### Better
- **Text still being written out** (a game's dialogue appearing letter by letter) is translated once it is complete, not half-way.
- **Fewer junk translations:** what Windows "reads" in pictures, icons and patterns — mostly symbols, or rows of stray single letters — is left alone.
- **A second, black-and-white read** when the first finds (almost) nothing: light text on busy or dark backgrounds.
- The translation window now **shows in screenshots and screen sharing**; Lens leaves the part of the screen under it alone instead. Its option is now *Show translations only in this window* — closing the window puts them back on the screen.

### Fixed
- **Translations no longer vanish and come back by themselves.** Every few seconds Lens looks at the page under its translations; it used to make them invisible for that moment, and a page with anything moving (a clock, a blinking cursor) made it take them away and draw them again. Now they stay on the screen and only Lens's own photo leaves them out; if the page did change, it is read again in the background, and translations that come back the same are not drawn again.

## [1.0.0] — 2026-10-11

First release as a project of its own. Lens also ships inside [EchoSub](https://github.com/mPhpMaster/echo-sub) 1.17.0.

### Added
- **Translate the screen in place:** an area you drag, a whole screen, or a lens that follows the mouse. Every piece of text is shown translated right on top of the original.
- **Work underneath:** clicks, scrolling and typing go through to the page or program; hold `Ctrl` (or turn on *Select mode*) to select and copy a translation, its original, or everything.
- **Screen sharing:** translations show in screenshots, Discord, Teams and OBS — or only on your own screen. Lens never reads its own translations back.
- **Translate a file:** pictures, PDF (scanned too), Word `.docx` (formatting kept) and text or subtitle files; save a translated copy.
- **Appearance:** font, size, bold, text and background colours, opacity and rounded corners, applied live.
- **A key for every option:** any key or combination, set in *Settings → Keyboard shortcuts*.
- Offline OCR with Windows' own text recognition; offline translation with NLLB-200, or Google Translate.
- **Windows installer** (`EchoSubLens-Setup-1.0.0.exe`): installs for your user without administrator rights, with a Start-menu entry and an optional start with Windows.

# Changelog

All notable changes to EchoSub Lens are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [1.1.0] — 2026-10-11

### Added
- **Translation window** (`Ctrl+Alt+W`, or *Translation window* in the menu): a window above all others with the translated text of the screen in a box, in reading order, ready to read, select and copy. Optionally with the original text under each line, a larger or smaller font, and with or without the translations over the screen. It remembers its place and size.
- **Splash screen** while Lens starts, saying what it is getting ready.
- **A progress bar on the screen while Lens works:** reading and translating the screen, loading the translator, or downloading a translation model (with how much is done). It sits in the corner of the screen being translated, never takes the mouse, and is left out of screenshots so Lens never reads it back. Translating a file shows a progress bar too.

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

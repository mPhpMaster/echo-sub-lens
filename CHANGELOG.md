# Changelog

All notable changes to EchoSub Lens are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [1.9.0] — 2026-10-12

### Added
- **New versions are announced.** Once a day Lens asks GitHub for the latest version number (nothing about you is sent); when there is a newer one, a note says so and the menu offers *Update to …*, which opens the download page. The *About* page has *Check for updates*, *Skip this version*, and a box to turn the daily check off.

### Fixed
- The capture window keeps every line of the picture on a line of its own, in the original and in the translation.
- A colon or a question mark in a line that mixes Arabic and English is back where it belongs.
- A new DeepL key reaches the file, movie and capture windows already open, not only the screen.

## [1.8.1] — 2026-10-12

### Fixed
- **Arabic text is read now.** With *Automatic*, Lens read only with the recognizer of the Windows display language (English here), which turns Arabic into "öJPJl" or misses it. Now every installed recognizer with another alphabet (Arabic, Russian…) reads too, and each line keeps the reading that looks most like words. Arabic words come in the right order (Windows hands them over left to right), and the colon an Arabic recognizer puts in front of an English word goes back.
- Translating the screen does not read the alphabet of the language translated into (that text is left as it is), so translating into Arabic stays as fast as before; Windows' Arabic recognizer is slow on a whole screen.

## [1.8.0] — 2026-10-12

### Added
- **Capture part of the screen** (`Ctrl+Alt+S`, the menu, or the Lens window): drag a rectangle and the picture opens in a window of its own, with the text Lens read in it. Switch between *Original* and *Translated* (the picture with the translations drawn over it, and the translated text), choose the language and the translator, select and copy any part of the text, copy or save the picture — or cancel. Lens's own translations are never in the capture.

## [1.7.0] — 2026-10-12

### Added
- **Yandex Translate** and **Papago** (Naver) too, both without an account: Yandex for many languages (strong for Russian), Papago for 16 (strong for Korean, Japanese and Chinese; Arabic included). A language a translator cannot write is said clearly, with a suggestion to choose another one.
- The *Translator* page asks for the keys of any translator that needs some, the same way it asks for DeepL's.

## [1.6.0] — 2026-10-12

### Added
- **More online translators besides Google:** **Microsoft Bing Translator** (no account needed), **MyMemory** (no account; a limited amount of text a day) and **DeepL** (with your own free DeepL key, entered on the *Translator* page). They can be chosen for the screen, for files and for movies, in the Lens window, the menu and on the command line (`--translator bing`).
- The *Translator* page groups the translators into online and offline (on this PC), with a line on each and a note on what online translators receive.

## [1.5.0] — 2026-10-12

### Added
- **Any language, any translator, for movies and files.** The movie window has *Translate into* and *with* (Google, NLLB 600M or 1.3B): with *Translate them as soon as they are written*, a translated copy (`Movie.ar.srt`) is written right after the subtitles, dialogue dashes and line breaks kept. The file translator window has the same two choices at the top: change one and the file is translated again.
- A translator chosen for a movie or a file is its own: the one translating the screen stays as it is, and the other one leaves the memory when its window closes.
- Command line: `--to LANG --translator KEY`, e.g. `EchoSubLens.exe --movie-to-srt Movie.mkv --to ar --translator google`.

## [1.4.0] — 2026-10-12

### Added
- **Movies to subtitles (.srt).** *Translate a movie…* (menu, the Lens window, or a key of your own): choose a movie and Lens writes all its text into an .srt file — from the subtitles inside the file, from what is said (speech recognition with Whisper on the graphics card, sharing EchoSub's models), or from text burned into the picture. Then *Translate the subtitles…* translates the .srt and saves a translated copy, with the numbers and times kept. Also from the command line: `EchoSubLens.exe --movie-to-srt Movie.mkv --from speech`.

### Fixed
- **Text direction was the wrong way round** for the translations over the screen: *Right to left* put them on the left and *Left to right* on the right (Qt turns "right" into left in a right-to-left label unless told the side is absolute).
- **The transparent translation window really is see-through now.** Ticking *Transparent* on an open window made it black (Windows makes a window see-through only when it is created, so Lens now makes it again), and unticking it left the window's frame and size wrong. It keeps its place and size, however often it is switched.
- Clicking the desktop or the taskbar no longer hides the translations of a chosen window; only a window really in front of it does.
- A network blip no longer sends Google translations to the slower services for the rest of the session: they are tried again, and a service that answered badly is left alone for 5 minutes only.
- Copying a translation no longer copies the invisible direction marks with it.
- *Reset this page* on the Translate page no longer starts translating when it was stopped; the tray tooltip names the chosen window.
- **An English word no longer turns an Arabic line around.** A line that started with an English word ("Windows هو نظام تشغيل") was laid out left to right, so the words came out of order; every line now carries an invisible direction mark.

## [1.3.0] — 2026-10-12

### Added
- **Translate a window.** Choose a program's window — a video player, a game, a chat — in the Lens window or the right-click menu, and only its text is translated, wherever you move it and whatever its size. While it is minimized, closed or behind another program's window, the translations step aside and Lens waits. Lens finds it again by its title and program the next time it starts.
- **A transparent translation window**, with how much of its background is left (0–100 %): no title bar then, the words stay solid with a shadow, an empty spot moves it and the corner resizes it.
- **Text direction**: automatic (from the language), right to left or left to right — in the translation window, and for the translations over the screen (*Appearance → Text direction*).
- **Reset this page** on every page of the Lens window (Translate, Languages, Translator, Appearance, Keyboard shortcuts) puts its settings back to the defaults, after asking.

### Changed
- **The right-click menu is in groups**: open and start; *What to translate* (an area, a whole screen, a window, the lens around the mouse) and a file; the languages and the translator; the translation window, copying, and *Translations on the screen* (appearance, screenshots, select mode, looking underneath); settings, about and support; exit.

## [1.2.0] — 2026-10-12

### Added
- **The EchoSub Lens window.** A click on the Lens icon by the clock no longer turns translation on and off: it opens a window with a big *Start / Stop translating* button that says what Lens is doing, and every option on a page of its own — *Translate* (a whole screen, an area or the lens around the mouse; where the translations show), *Languages*, *Translator*, *Appearance*, *Keyboard shortcuts* — each with a line that says what it does. Closing it keeps Lens running; right-click the icon for the quick menu.
- **About page**, like EchoSub's: version, author, GitHub / LinkedIn / Discord / email, ♥ support, license, changelog and source code.
- Screenshots in the README.

### Fixed
- **Google Translate no longer keeps stopping.** Lens now asks the Google service Chrome's translation extension uses: every text of the screen in one request, each answered on its own. When one Google service says "too many requests", Lens asks another, and if all refuse it pauses 5 seconds (it was 20 seconds, doubling up to 5 minutes). On this PC the old service was refusing every request while the new one answered in 0.1 s.
- **Dialogue lines are no longer run together.** A subtitle line that starts with a dash (`- Where to?` / `- Home.`) is a text of its own, translated on its own, and keeps its dash (on the right in Arabic). Google's answers are no longer split back from one long text, so two lines can never come back as one.
- **A sentence over two lines is shown on two lines**, of about the same length, instead of one.

## [1.1.2] — 2026-10-11

### Added
- **♥ Support the developer**: an item in the tray menu opens the developer's PayPal page, for anyone who wants to give something back. Optional and quiet: no pop-ups, no reminders, nothing held back. The repository page gets a *Sponsor* button too.

### Changed
- **One translator at a time.** Choosing another translator switches the one before off at once, and its model leaves the graphics card and the memory (about 700 MB for NLLB-600M). Google Translate no longer keeps an offline model waiting in the memory, so with Google nothing heavy runs on the PC.
- **Everything Lens does shows on the screen**, in the corner: reading and translating (now at once, not only when it takes long, ending with how many texts were translated), loading, switching off or changing the translator, starting and stopping, the language, the translation window, screenshots, and every problem (Google limiting requests, a translator that could not load). These notes used to be balloons by the clock, or nothing.

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

# book2audio

Record a video of yourself turning a book's pages, and book2audio turns it into an audiobook with chapter markers to listen to on your phone. Photos and screenshots of pages work too. It also writes a transcript and a one-paragraph summary of each chapter, so you can catch up after a few days away from the book.

## Use it responsibly

book2audio is a personal hobby project for listening to books you could otherwise read yourself.

- **Only use it on books you have the right to copy for yourself:** books you own, books in the public domain, or your own writing.
- **Keep what it makes to yourself.** The audiobook, transcript and summaries are copies of the book. Don't share, upload, post, or sell them.
- It only works from recordings and images of pages: video, photos or screenshots. It doesn't open e-book, PDF or audiobook files, and it doesn't remove copy protection from anything.
- Text recognition and the voice run on your Mac. Proofreading and summaries send the book's text to Claude (see [Proofreading and summaries](#proofreading-and-summaries)); turn them off with `--no-cleanup --no-summary` to keep everything on your Mac.

This isn't legal advice. Copyright rules differ between countries, and you're responsible for how you use the tool.

## Requirements

- A Mac (tested on macOS 27 with Apple silicon; recent versions should work). Text recognition uses Apple's Vision framework, so it doesn't run on Windows or Linux.
- Xcode Command Line Tools: run `xcode-select --install` if you don't have them.
- Python 3.12 or 3.13 (the voice packages don't support 3.14 yet). If you don't have one: `brew install python@3.13`. `setup.sh` finds it even if your default `python3` is a different version.
- [Homebrew](https://brew.sh), or ffmpeg already installed.
- About 1 GB of free disk space (the voice model is ~340 MB).
- For proofreading and summaries: [Claude Code](https://claude.com/claude-code) (the `claude` command), logged in to your Claude account. Without it, book2audio still works and skips those two steps.

## Install

```sh
git clone https://github.com/jakemiller13/book2audio.git
cd book2audio
./setup.sh
```

`setup.sh` checks the requirements and tells you what's missing. It then installs everything inside this folder:

- a Python environment in `.venv`, with the pinned packages from `requirements.txt`
- the Kokoro voice model in `models/`, checked against known checksums
- ffmpeg, with Homebrew, if it isn't installed already
- the text recognition helper, compiled from `src/book2audio/ocr.swift`

It's safe to run again at any time, for example after moving the folder or pulling an update. Keep the folder at a normal-length path such as `~/book2audio`: the voice's pronunciation library can't load from a very deeply nested folder, and `setup.sh` tells you if that's the case.

## Quick start

```sh
./book2audio --voice-samples              # optional: hear the voices
./book2audio ~/Downloads/IMG_1234.MOV     # a video of you turning the pages
```

The results go in `output/`, named from what was read:

| File | What it is |
|---|---|
| `<Book> - Ch 20-21.m4b` | The audiobook, with a chapter marker for each chapter |
| `<Book> - Ch 20-21.md` | The transcript that was read aloud |
| `<Book> - Ch 20-21.summary.md` | A one-paragraph summary of each chapter |
| `<Book> - Ch 20-21.ocr.md` | The transcript before proofreading |

## Usage

```sh
# A video of you turning pages (pause about a second on each spread)
./book2audio ~/Downloads/IMG_1234.MOV

# Photos, or a folder of screenshots, with a different voice
./book2audio ~/Desktop/chapter32/ --voice george

# Several recordings at once, in any order; they're put together by page number
./book2audio ~/Downloads/IMG_1240.MOV ~/Downloads/IMG_1241.MOV -o "output/Chapters 10-13.m4b"

# Read each chapter's summary aloud after the chapter, as its own skippable chapter
./book2audio ~/Downloads/IMG_1234.MOV --speak-summary

# Summaries for a transcript you already have
./book2audio --summarize "output/My Book - Ch 20-21.md"
```

If pages are missing between or within recordings, the run prints which page numbers to re-shoot. Run `./book2audio --help` for the full list of options.

| Option | What it does |
|---|---|
| `--voice NAME` | Narrator, e.g. `am_michael` or just `michael`. Default `am_michael` |
| `--list-voices` | Lists every voice |
| `--voice-samples` | Writes a short clip of each suggested voice to `voice_samples/` and opens the folder |
| `--speak-summary` | Also reads each chapter's summary aloud, as a separate "Chapter 20 – Summary" chapter right after it |
| `--summarize FILE.md` | Writes summaries for an existing transcript, then stops |
| `--no-cleanup` | Skips proofreading |
| `--no-summary` | Skips the summaries |
| `-o FILE` | Output file. By default it's named from the contents, e.g. `output/<Book> - Ch 20-21.m4b` |
| `--title`, `--author` | Book title and author tags. By default they're read from the running heads (author on left-hand pages, title on right-hand pages). Use these for a single photo or a book laid out differently |
| `--text-only` | Writes the transcript and summaries, with no audio |
| `--video-every S` | Seconds between sampled video frames (default 0.5) |

Text recognition results are cached in `~/Library/Caches/book2audio`, so running the same recording again (with a different voice, say) skips that step. Delete the folder to clear the cache.

### Proofreading and summaries

Both steps use Claude through the `claude` command (Claude Code) and your existing login, with no extra setup. Both send the book's text to Anthropic.

- **Proofreading:** text recognition makes small mistakes, such as `rn` read as `m` or a dropped quotation mark, and the voice would read them aloud. The transcript goes to Claude in ~3,000-word parts, with instructions to fix recognition errors only, without rewording. If a part comes back more than 10% longer or shorter, it's thrown away and the original text is kept. The unproofread text is saved as `.ocr.md`.
- **Summaries:** a short paragraph (under about 90 words) per chapter, covering only the text that was read. A chapter the recording started partway through is summarized from the part that was read. With `--speak-summary`, each summary is also read aloud as its own chapter right after the chapter it covers, so you can skip it, or jump back to it before you carry on with the book.

If `claude` isn't installed, book2audio says so and carries on without these steps.

### Voices

Run `./book2audio --voice-samples` to hear the suggested narrators, or `./book2audio --list-voices` for all of them. Kokoro voice names start with a code: `am_` American male, `af_` American female, `bm_`/`bf_` British male/female. Good narrators to try: `am_michael`, `am_fenrir`, `am_onyx`, `bm_george`, `bm_fable`, `bm_lewis`, `af_heart`, `bf_emma`. You can give just the name part, e.g. `--voice fenrir`.

There's no speed option: audiobook players do variable speed better, and you can change it while you listen. Kokoro renders at about 6× real time on an M3 Pro (a 25-minute chapter takes about 4 minutes).

### Chapters

A page is treated as a chapter opening when it has a "Chapter N" line (or Prologue/Epilogue/Part/Interlude) near the top, an optional title under it, and then a tall blank band before the text starts. That's a common layout; a book that opens chapters differently may need `detect_chapter_opening` in `src/book2audio/layout.py` adjusted.

A recording usually starts partway through a chapter. That opening text gets its own chapter, named from the next chapter's number, e.g. "Chapter 31 (continued)".

A blank gap in the middle of a page (a scene break) is not a chapter. It becomes a 1.5-second pause in the audio and a `* * *` line in the transcript. A scene break that falls exactly at a page boundary can't be seen and is read straight through.

## Listening on your phone

The `.m4b` file is a standard audiobook file with chapter markers.

- **Easiest:** a free audiobook player such as BookPlayer. AirDrop the `.m4b` to your iPhone (it lands in Files), then in the player tap **+ → Import files** and pick it. The player remembers your place, shows chapters, does variable speed and works with CarPlay.
- **Apple Books:** connect the iPhone to the Mac, select it in the Finder sidebar, open the Audiobooks tab, drag the `.m4b` in and sync. Apple Books on the iPhone can't import audiobooks from Files directly.

## Tips for good captures

- Light the pages evenly and avoid glare. Keep fingers off the text.
- Hold the book open as flat as you can. Lines right at the spine are the hardest.
- Video is fine and quick; blurry frames during page turns are skipped automatically. Pause about a second on each spread. Frames are sampled every half second, so flipping faster than once per second can skip pages.
- Some shake and pages slipping mid-turn are fine; frames that can't be read cleanly are skipped as long as other frames show the same pages.
- Record in 1080p rather than 4K: text recognition works just as well and files are much smaller.

Rough timing on an M3 Pro: text recognition takes about 0.75× the video's length, and the voice takes about 1/6 of the audio's length (an hour of audio in ~10 minutes). Proofreading and summaries add a minute or two.

## How it works

The code is in `src/book2audio/`:

| Module | Step |
|---|---|
| `capture.py`, `ocr.swift` | Runs Apple's Vision framework on each photo or video frame: on the full image, on each page cropped at the detected spine, and as a "fast" pass that catches short lines the accurate pass misses. Vision handles one image at a time per process, so several helper processes run in parallel |
| `layout.py` | Splits each spread into pages; removes running heads, page numbers, text outside the page and blurry frames; orders lines on curved pages; finds paragraph indents, scene breaks and chapter openings |
| `ordering.py` | Matches the many views of each page by their text and keeps the clearest. Page numbers are decided by vote, with evidence from the facing page. A read page number is ignored unless another page has a number close to it, which filters misreads like "867" while still allowing recordings of separate parts of the book |
| `text.py` | Rebuilds paragraphs and writes the Markdown transcript |
| `claude.py` | Proofreading and summaries, through `claude -p` |
| `speech.py` | Kokoro speaks each chapter, and ffmpeg packages everything as an AAC `.m4b` with a chapter track |
| `cli.py` | The command line, tying the steps together |

## Troubleshooting

- **"book2audio isn't fully set up yet"**, or a Python error after moving the folder or upgrading Python: run `./setup.sh` again.
- **"Python 3.12 or 3.13 is needed":** run `brew install python@3.13`, then `./setup.sh`.
- **"Couldn't compile the OCR helper":** run `xcode-select --install`, then `./setup.sh`.
- **Pages missing or out of order:** re-shoot the pages the run lists, slower, and pass both recordings in one run.
- **No proofreading or summaries:** install Claude Code and run `claude` once to log in.

## Credits and third-party software

book2audio doesn't include any of these in the repository; `setup.sh` downloads them onto your Mac, each under its own license.

| Component | Used for | License |
|---|---|---|
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) model and voices | Speech | Apache-2.0 |
| [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) | Running Kokoro | MIT |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | Running the model | MIT |
| [espeak-ng](https://github.com/espeak-ng/espeak-ng) (via espeakng-loader) | Pronunciation | GPL-3.0 |
| [phonemizer](https://github.com/bootphon/phonemizer) | Pronunciation (used by kokoro-onnx) | GPL-3.0 |
| [FFmpeg](https://ffmpeg.org) (via Homebrew) | Encoding the `.m4b` | LGPL/GPL |
| NumPy, soundfile, and their dependencies | Audio arrays, WAV files | BSD/MIT-style |

Apple, Mac, macOS, iPhone, Apple Books and CarPlay are trademarks of Apple Inc. Claude and Claude Code are trademarks of Anthropic. BookPlayer is a third-party app. book2audio is an independent hobby project. It isn't affiliated with, sponsored by, or endorsed by any of these companies or projects.

## License

The book2audio code is MIT-licensed. See [LICENSE](LICENSE). The license covers this code only, not any book you use it on or anything it produces from one.

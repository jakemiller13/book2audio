# book2audio

Turn photos, screenshots, or a video of printed book pages into an audiobook with chapter markers, made entirely on your Mac, to listen to on your phone.

## Use it responsibly

book2audio is a personal hobby project for listening to books you could otherwise read yourself.

- **Only use it on books you have the right to copy for yourself:** books you own, books in the public domain, or your own writing.
- **Keep what it makes to yourself.** The audiobook and transcript are copies of the book. Don't share, upload, post, or sell them.
- It only works from pictures of printed pages. It doesn't read e-books or audiobook files, and it doesn't remove copy protection from anything.
- Everything runs on your Mac. The only exception is the optional `--cleanup` step, which sends the transcript text to Claude for proofreading (see below).

This isn't legal advice. Copyright rules differ between countries, and you're responsible for how you use the tool.

## Requirements

- A Mac (tested on macOS 27 with Apple silicon; recent versions should work). OCR uses Apple's Vision framework, so it doesn't run on Windows or Linux.
- Xcode Command Line Tools: run `xcode-select --install` if you don't have them.
- Python 3.10 or newer.
- [Homebrew](https://brew.sh), or ffmpeg already installed.
- About 1 GB of free disk space (the voice model is ~340 MB).
- Optional, for `--cleanup`: [Claude Code](https://claude.com/claude-code) (the `claude` command), or an `ANTHROPIC_API_KEY`.

## Quick start

```sh
git clone https://github.com/jakemiller13/book2audio.git
cd book2audio
./setup.sh                      # checks the requirements, then installs everything into this folder
./book2audio --voice-samples    # optional: hear the voices
./book2audio ~/Downloads/IMG_1234.MOV
```

The audiobook and its transcript are written to `output/`.

`setup.sh` creates a Python environment in `.venv`, installs the pinned packages from `requirements.txt`, downloads and checksums the Kokoro voice model into `models/`, installs ffmpeg with Homebrew if it's missing, and compiles the OCR helper. It's safe to run again at any time, for example after moving the folder. Keep the folder at a normal-length path such as `~/book2audio`: the voice's pronunciation library can't load from a very deeply nested folder, and `setup.sh` tells you if that's the case.

## Usage

```sh
# A video of you flipping pages (pause ~1-2 s on each spread)
./book2audio ~/Downloads/IMG_1234.MOV --cleanup     # -> output/<Book Title> - Ch 20-21.m4b

# Photos or a folder of screenshots
./book2audio ~/Desktop/chapter32/ --cleanup -o "output/Chapter 32.m4b"

# Several recordings at once, in any order; they're stitched by page number
./book2audio ~/Downloads/IMG_1240.MOV ~/Downloads/IMG_1241.MOV --cleanup -o "output/Chapters 10-13.m4b"
```

If pages are missing between or within recordings, the run prints which page numbers to re-shoot.

Each run writes the audiobook (`.m4b`) and a transcript (`.md`). With `--cleanup`, it also keeps the raw OCR text as `.ocr.md`.

| Option | What it does |
|---|---|
| `-o FILE` | Output file. Optional: by default it's named from the contents, e.g. `output/<Book Title> - Ch 20-21.m4b` |
| `--title`, `--author` | Book title and author tags. By default they're read from the running heads (author on left-hand pages, title on right-hand pages). Use these for a single photo or a book laid out differently |
| `--cleanup` | Has Claude fix OCR errors before speaking (see below) |
| `--voice NAME` | Kokoro voice. Default `am_michael` |
| `--voice-samples` | Writes a short clip of each suggested voice to `voice_samples/` and opens the folder |
| `--speed X` | Speaking speed, e.g. `0.9` or `1.15` |
| `--text-only` | Write the transcript only, with no audio |
| `--video-every S` | Seconds between sampled video frames (default 0.5) |

OCR results are cached in `~/Library/Caches/book2audio`, so re-rendering with a different voice or speed skips the OCR step. Delete that folder to clear it.

### Proofreading with `--cleanup`

OCR makes small mistakes, such as `rn` read as `m` or a dropped quotation mark. `--cleanup` sends the transcript to Claude in ~3,000-word chunks and asks it to fix recognition errors only, without rewording. A chunk whose length changes by more than 10% is rejected and the OCR text is kept.

It uses the `claude` command (Claude Code) and your existing login, with no extra setup. If `claude` isn't installed, it falls back to the Anthropic API with `ANTHROPIC_API_KEY`. Either way, the book's text is sent to Anthropic. Leave `--cleanup` off to keep everything on your Mac.

### Voices

Run `./book2audio --voice-samples` to hear the suggested narrators. Kokoro voice names start with a code: `am_` American male, `af_` American female, `bm_`/`bf_` British male/female. Good narrators to try: `am_michael`, `am_fenrir`, `am_onyx`, `bm_george`, `bm_fable`, `bm_lewis`, `af_heart`, `bf_emma`. Kokoro renders at about 6× real time on an M3 Pro (a 25-minute chapter takes about 4 minutes).

### Chapters

A page is treated as a chapter opening when it has a "Chapter N" line (or Prologue/Epilogue/Part/Interlude) near the top, an optional title under it, and then a tall blank band before the text starts. That's a common layout; a book that opens chapters differently may need `detect_chapter_opening` adjusted. Text before the first chapter heading becomes a "Continued" chapter.

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
- Record in 1080p rather than 4K: OCR works just as well and files are much smaller.

Rough timing on an M3 Pro: OCR takes about 0.75× the video's length, and the voice takes about 1/6 of the audio's length (an hour of audio in ~10 minutes).

## How it works

1. `ocr.swift` runs Apple's Vision framework on the full image, on each page cropped at the detected spine, and as a "fast" pass that catches short lines the accurate pass misses. Vision handles one image at a time per process, so `book2audio.py` runs several OCR processes in parallel (video frames are split every-Nth-frame between them).
2. `book2audio.py`:
   - splits each spread into pages and removes running heads and page numbers
   - drops text outside the page (keyboards, other books) and blurry garbage
   - orders lines on curved pages and rebuilds paragraphs from indents
   - detects chapter openings and scene breaks by layout
3. Multiple views of the same page are matched by their text, and the best one is kept. Page numbers are decided by vote, with evidence from the facing page. A read page number is ignored unless another page has a number close to it, which filters misreads like "867" while still allowing recordings of separate parts of the book.
4. Optional: Claude proofreads the transcript (`--cleanup`, above).
5. Kokoro speaks each chapter, and ffmpeg packages everything as an AAC `.m4b` with a chapter track.

## Troubleshooting

- **"book2audio isn't fully set up yet"** or a Python error after moving the folder: run `./setup.sh` again.
- **"Couldn't compile the OCR helper":** run `xcode-select --install`, then `./setup.sh`.
- **Pages missing or out of order:** re-shoot the pages the run lists, slower, and pass both recordings in one run.

## Credits and third-party software

book2audio doesn't include any of these in the repository; `setup.sh` downloads them onto your Mac, each under its own license.

| Component | Used for | License |
|---|---|---|
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) model and voices | Speech | Apache-2.0 |
| [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) | Running Kokoro | MIT |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | Running the model | MIT |
| [espeak-ng](https://github.com/espeak-ng/espeak-ng) (via espeakng-loader) | Pronunciation | GPL-3.0 |
| [FFmpeg](https://ffmpeg.org) (via Homebrew) | Encoding the `.m4b` | LGPL/GPL |
| NumPy, soundfile, Anthropic Python SDK | Audio arrays, WAV files, optional API proofreading | BSD-3-Clause / BSD-3-Clause / Apache-2.0 |

Apple, Mac, macOS, iPhone, Apple Books and CarPlay are trademarks of Apple Inc. Claude and Claude Code are trademarks of Anthropic. BookPlayer is a third-party app. book2audio is an independent hobby project. It isn't affiliated with, sponsored by, or endorsed by any of these companies or projects.

## License

The book2audio code is MIT-licensed. See [LICENSE](LICENSE). The license covers this code only, not any book you use it on or anything it produces from one.

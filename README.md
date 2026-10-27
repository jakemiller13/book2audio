# book2audio

Turn photos, screenshots, or a video of book pages into an audiobook, with chapter markers, that you can listen to on an iPhone.

## Setup (once)

```sh
cd ~/Documents/book2audio
./setup.sh
```

This creates a Python environment in `.venv`, downloads the Kokoro voice model (~340 MB) into `models/`, installs ffmpeg with Homebrew if it's missing, and builds the OCR helper.

Keep the folder at a normal-length path such as `~/Documents/book2audio`. Kokoro's pronunciation library can't load from a very deeply nested folder (paths over ~100 characters before `.venv`), and the tool tells you if that happens.

## Usage

```sh
# A video of you flipping pages (pause ~1-2 s on each spread)
./book2audio ~/Downloads/IMG_1168.MOV --cleanup -o "output/Chapter 20-21.m4b"

# Photos or a folder of screenshots
./book2audio ~/Desktop/chapter32/ --cleanup -o "output/Chapter 32.m4b"

# Several recordings at once, in any order; they're stitched by page number
./book2audio ~/Downloads/IMG_1170.MOV ~/Downloads/IMG_1171.MOV --cleanup -o "output/Chapters 10-13.m4b"
```

If pages are missing between or within recordings, the run prints which page numbers to re-shoot.

Each run writes the audiobook (`.m4b`) and a transcript (`.md`). With `--cleanup`, it also keeps the raw OCR text as `.ocr.md`.

| Option | What it does |
|---|---|
| `--cleanup` | Has Claude fix OCR errors before speaking. Uses your Claude Code login (the `claude` command); no separate API key. |
| `--voice NAME` | Kokoro voice. Default `am_michael`; listen to the samples in `voice_samples/`. |
| `--speed X` | Speaking speed, e.g. `0.9` or `1.15` |
| `--engine say` | Use the built-in macOS voices instead of Kokoro (`--voice "Ava (Premium)"` etc.) |
| `--text-only` | Write the transcript only, with no audio |
| `--video-every S` | Seconds between sampled video frames (default 0.5) |

OCR results are cached in `~/Library/Caches/book2audio`, so re-rendering with a different voice or speed skips the OCR step.

### Voices

Kokoro voices start with a code: `am_` American male, `af_` American female, `bm_`/`bf_` British male/female. Good narrators to try: `am_michael`, `am_fenrir`, `am_onyx`, `bm_george`, `bm_fable`, `bm_lewis`, `af_heart`, `bf_emma`. Kokoro renders at about 6× real time on an M3 Pro (a 25-minute chapter takes about 4 minutes).

### Chapters

A page is treated as a chapter opening when it has a "Chapter N" line (or Prologue/Epilogue/Part/Interlude) near the top, an optional title under it, and then a tall blank band before the text starts. That's the layout this book uses. Text before the first chapter heading becomes a "Continued" chapter. Chapters show up in Apple Books, BookPlayer and CarPlay.

A blank gap in the middle of a page (a scene break) is not a chapter. It becomes a 1.5-second pause in the audio and a `* * *` line in the transcript. A scene break that falls exactly at a page boundary can't be seen and is read straight through.

## Getting it onto your iPhone

- **Best for the car:** a free audiobook player such as BookPlayer. AirDrop the `.m4b` or save it to iCloud Drive, then import it from Files. The player remembers your place, shows chapters, does variable speed, and works with CarPlay.
- **Apple Books:** open the `.m4b` in Books on the Mac, then sync it to the phone through Finder (iPhone → Audiobooks).

## Tips for good captures

- Light the pages evenly and avoid glare. Keep fingers off the text.
- Hold the book open as flat as you can. Lines right at the spine are the hardest.
- Video is fine and quick; blurry frames during page turns are skipped automatically. Pause about a second on each spread. Frames are sampled every half second, so flipping faster than once per second can skip pages.
- Some shake and pages slipping mid-turn are fine; frames that can't be read cleanly are skipped as long as other frames show the same pages.
- Record in 1080p rather than 4K: OCR works just as well and files are much smaller.

Rough timing on an M3 Pro: OCR takes about 0.75× the video's length, and the voice takes about 1/6 of the audio's length (an hour of audio in ~10 minutes).

## How it works

1. `ocr.swift` runs Apple Vision on the full image, on each page cropped at the detected spine, and as a "fast" pass that catches short lines the accurate pass misses. Vision handles one image at a time per process, so `book2audio.py` runs several OCR processes in parallel (video frames are split every-Nth-frame between them).
2. `book2audio.py`:
   - splits each spread into pages and removes running heads and page numbers
   - drops text outside the page (keyboards, other books) and blurry garbage
   - orders lines on curved pages and rebuilds paragraphs from indents
   - detects chapter openings and scene breaks by layout
3. Multiple views of the same page are matched by their text, and the best one is kept. Page numbers are decided by vote, with evidence from the facing page. A read page number is ignored unless another page has a number close to it, which filters misreads like "867" while still allowing recordings of separate parts of the book.
4. Optional: `claude -p` proofreads the transcript in ~3,000-word chunks. A chunk whose length changes by more than 10% is rejected and the OCR text is kept.
5. Kokoro (or `say`) speaks each chapter, and ffmpeg packages everything as an AAC `.m4b` with a chapter track.

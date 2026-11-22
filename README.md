# book2audio

Record a video of yourself turning a book's pages, and book2audio turns it into an audiobook with chapter markers to listen to on your phone. Photos and screenshots of pages work too. It also writes a transcript, and if you like, a one-paragraph summary of each chapter, so you can catch up after a few days away from the book. An optional, more expressive reading puts some emotion into the dialogue and the dramatic moments.

## Use it responsibly

book2audio is a personal, non-commercial hobby project for listening to books you could otherwise read yourself. It's free, it isn't a product or a service, and nothing about it is for sale.

- **Only use it on books you have the right to copy for yourself:** books you own, books in the public domain, or your own writing.
- **Keep what it makes to yourself.** The audiobook, transcript and summaries are copies of the book. Don't share, upload, post, or sell them.
- It only works from recordings and images of pages: video, photos or screenshots. It doesn't open e-book, PDF or audiobook files, and it doesn't remove copy protection from anything.
- Text recognition and the voices run on your Mac. Proofreading, summaries and the expressive reading are optional and off unless you ask for them (`--proofread`, `--summary`, `--emotion`); they send the book's text to Claude (see [Proofreading and summaries](#proofreading-and-summaries) and [Expressive reading](#expressive-reading)). Without them, everything stays on your Mac.

This isn't legal advice. Copyright rules differ between countries, and you're responsible for how you use the tool.

## Requirements

- A Mac (tested on macOS 27 with Apple silicon; recent versions should work). Text recognition uses Apple's Vision framework, so it doesn't run on Windows or Linux.
- Xcode Command Line Tools: run `xcode-select --install` if you don't have them.
- Python 3.12 or 3.13 (the voice packages don't support 3.14 yet). If you don't have one: `brew install python@3.13`. `setup.sh` finds it even if your default `python3` is a different version.
- [Homebrew](https://brew.sh), or ffmpeg already installed.
- About 1 GB of free disk space (the voice model is ~340 MB). The optional expressive voice for `--emotion` needs about 4.5 GB more, but only if you choose to install it (see below).
- Optional, for proofreading, summaries and the expressive reading: [Claude Code](https://claude.com/claude-code) (the `claude` command), logged in to your Claude account. Everything else works without it.

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

> [!IMPORTANT]
> **The expressive voice is a separate 4.5 GB download, and it's never installed unless you ask for it.** Plain `./setup.sh` leaves it out, and everything except `--emotion` works without it. Running `--emotion` without it doesn't download anything; it stops and tells you how to install it.
>
> To install it, run `./setup.sh --emotion` once. Before downloading anything, it lists what it will download and asks you to confirm:
>
> | What | Where | Size |
> |---|---|---|
> | PyTorch and Chatterbox, in a separate Python environment | `.venv-emotion/` | about 1.3 GB |
> | The Chatterbox voice and Whisper line-check models | `models/emotion/` | about 3.2 GB |
>
> Everything stays inside the book2audio folder. If there's less than 6 GB free, it stops before downloading. When it reads a book, the expressive voice runs offline and never downloads anything. Use `./setup.sh --emotion --yes` to skip the question in a script, and `./setup.sh --remove-emotion` to delete it all again.

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
| `<Book> - Ch 20-21.summary.md` | A one-paragraph summary of each chapter (with `--summary`) |
| `<Book> - Ch 20-21.ocr.md` | The transcript before proofreading (with `--proofread`) |

## Usage

```sh
# A video of you turning pages (pause about a second on each spread)
./book2audio ~/Downloads/IMG_1234.MOV

# Photos, or a folder of screenshots, with a different voice
./book2audio ~/Desktop/chapter32/ --voice george

# Several recordings at once, in any order; they're put together by page number
./book2audio ~/Downloads/IMG_1240.MOV ~/Downloads/IMG_1241.MOV -o "output/Chapters 10-13.m4b"

# Have Claude fix text recognition errors, and summarize each chapter: the summaries are
# saved in a file and each one is read aloud after its chapter, as its own skippable chapter
./book2audio ~/Downloads/IMG_1234.MOV --proofread --summary

# Just the chapter summaries for a transcript you already have, e.g. for a book club
./book2audio "output/My Book - Ch 20-21.md" --summary --text-only

# The recording starts with the end of chapter 25, which you've already heard
./book2audio ~/Downloads/IMG_1234.MOV --from-chapter 26

# A book without chapters: start a new chapter about every 10 pages
./book2audio ~/Downloads/IMG_1234.MOV --pages-per-chapter 10

# Say the book's invented names your way (see Pronunciations below)
./book2audio ~/Downloads/IMG_1234.MOV --pronunciations ~/Books/my-book.txt

# Make the audiobook again from a transcript, e.g. after changing the list or the voice
./book2audio "output/My Book - Ch 20-21.md" --pronunciations ~/Books/my-book.txt

# A more expressive reading of the dialogue and dramatic moments (needs ./setup.sh --emotion)
./book2audio "output/My Book - Ch 20-21.md" --emotion hybrid --pronunciations ~/Books/my-book.txt
```

If pages are missing between or within recordings, the run prints which page numbers to re-shoot. It also lists lines with a hole in them, where something (usually a thumb) covered the words in every frame, so you can retake that page while the book is still open, or type the missing words into the transcript and make the audio again. Run `./book2audio --help` for the full list of options.

| Option | What it does |
|---|---|
| `--voice NAME` | Narrator: one voice, e.g. `am_michael` or just `michael`, or a mix such as `lewis:70+onyx:30` (see [Voices](#voices)). Default `lewis:70+onyx:30` |
| `--accent american\|british` | Pronunciation rules. Default: American with the default voice, otherwise the accent of the main `--voice` |
| `--speed N` | Speaking speed, e.g. `1` for normal, `0.9` for slower. Default `0.9` |
| `--list-voices` | Lists every voice |
| `--voice-samples` | Writes a short clip of each suggested voice to `voice_samples/` and opens the folder |
| `--pronunciations FILE` | How to say particular words (see [Pronunciations](#pronunciations)). Give it more than once to combine lists; a later list wins |
| `--proofread` | Has Claude fix text recognition errors before the text is read aloud (see [Proofreading and summaries](#proofreading-and-summaries)) |
| `--summary` | Writes a one-paragraph summary of each chapter (`.summary.md`) and reads each one aloud as a separate "Chapter 20 – Summary" chapter right after the chapter it covers |
| `--emotion hybrid\|full` | A more expressive reading (see [Expressive reading](#expressive-reading)). `hybrid`: the expressive voice reads dialogue and intense paragraphs, the usual voice the rest. `full`: the expressive voice reads everything. Needs `./setup.sh --emotion`; much slower |
| `--from-chapter N` | Starts at chapter N, leaving out the end of the chapter before it, e.g. when your last recording already covered it |
| `--pages-per-chapter N` | For a book without chapters: starts a new chapter about every N pages, named "Pages 11-20" (see [Chapters](#chapters)) |
| `-o FILE` | Output file. By default it's named from the contents, e.g. `output/<Book> - Ch 20-21.m4b` |
| `--title`, `--author` | Book title and author tags. By default they're read from the running heads: usually the author on left-hand pages and the title on right-hand pages, and the other way round when only the left-hand head reads like a title (it has small words such as "the" or "of", and the right-hand one has none). Use these for a single photo or if the run gets them wrong |
| `--text-only` | Writes the transcript (and summaries, with `--summary`), with no audio |
| `INPUT.md` | A transcript from an earlier run: works from it again without redoing text recognition. Edit it first if you like. Only the chapters whose text, voice, pronunciations or `--emotion` setting changed are read again, so fixing a line takes seconds to a minute. Give the `.ocr.md` with `--proofread` to proofread it again, e.g. after fixing something by hand |
| `--stay-awake` | Keeps the Mac from going to sleep on its own while the audio is made, so a long run (such as `--emotion`) can be left unattended. Off unless you ask for it; it ends with the run. Closing the lid still puts the Mac to sleep |
| `--video-every S` | Seconds between sampled video frames (default 0.5) |

Text recognition results are cached in `~/Library/Caches/book2audio`, so running the same recording again (with a different voice, say) skips that step. Each chapter's audio is cached there too (about 80 MB per hour of audio), so making an audiobook again reuses every chapter that hasn't changed; with `--emotion`, so is each paragraph the expressive voice reads, so a run that stops partway carries on from the last finished paragraph. So are Claude's answers, so proofreading or summarizing the same text again is instant and uses nothing from your Claude plan. Anything not used for 30 days is deleted. Delete the folder to clear the cache.

### Recording a whole book

A long book usually takes several recordings, each a batch of chapters. This is a routine that works well, using every option above:

```sh
# 1. Make the transcript and summaries for the new recording, starting at the chapter where
#    the last one ended (it ended partway through chapter 26, so start at the top of 26)
./book2audio ~/Downloads/IMG_1234.MOV --from-chapter 26 --proofread --summary \
    --pronunciations ~/Books/my-book.txt --text-only

# 2. The run lists names that aren't in your pronunciation list yet. Add the ones the
#    voice might get wrong to ~/Books/my-book.txt, and fix anything in the transcript.

# 3. Make the audiobook from the transcript (the summaries come from the cache)
./book2audio "output/My Book - Ch 26-36.md" --summary --pronunciations ~/Books/my-book.txt
```

Splitting it in two like this means you can check the names before any audio is made. If you'd rather, step 1 without `--text-only` does everything in one go. The voice options work the same way at step 3: try a different `--voice`, `--accent` or `--speed` to suit the book (see [Voices](#voices)).

### Proofreading and summaries

Both steps are optional and off unless you ask for them. They use Claude through the `claude` command (Claude Code) and your existing login, with no extra setup, and they send the book's text to Anthropic.

- **Proofreading (`--proofread`):** text recognition makes small mistakes, such as `rn` read as `m` or a dropped quotation mark, and the voice would read them aloud. Each chapter goes to Claude (a long one in parts of about 4,000 words), with instructions to fix recognition errors only, without rewording. If a part comes back more than 10% longer or shorter, the original text is kept and the run names the chapter so you can check it: Claude may have dropped something, or rightly removed junk such as a duplicated page. If Claude leaves out a chapter's heading line, it's put back. The unproofread text is saved as `.ocr.md`.
- **Summaries (`--summary`):** a short paragraph (under about 90 words) per chapter, covering only the text that was read, saved as `.summary.md`. Each summary is also read aloud as its own chapter right after the chapter it covers, so you can skip it, or jump back to it before you carry on with the book. A chapter the recording started partway through is summarized from the part that was read, and sections too short to summarize (under about 80 words) are left out. With `--text-only`, you get just the summaries file, handy for a book club.

Both use your Claude plan's usage. Proofreading uses the most, since the whole text comes back; summaries send the whole text but get only a paragraph per chapter back. For a recording of about 40,000 words, proofreading and summaries take about 5–10 minutes.

Claude's answers are saved as they arrive. If a run stops partway, for example because your plan's usage limit was reached, it says so and keeps what was done: run the same command again (after the limit resets) to carry on from there. If `claude` isn't installed or isn't logged in, `--proofread` and `--summary` stop with a message.

### Expressive reading

The usual narrator reads everything in the same even tone, so a scream and a quiet aside sound much alike. `--emotion` gives a more expressive reading. It's optional, needs a one-time `./setup.sh --emotion` (a 4.5 GB download that asks before it starts; see [Install](#install)), and is many times slower than the usual reading (see the timing below), so try it on a chapter first.

- **Claude marks the text:** each paragraph is split into lines, and each line gets who is speaking (the narrator or a character), how intense it is, whether it's whispered or shouted, and how long a pause follows it. This sends the book's text to Claude, like `--proofread`.
- **An expressive voice reads it:** [Chatterbox](https://github.com/resemble-ai/chatterbox), a larger voice model that can be made more or less intense line by line. It imitates your narrator from a short clip read by the usual voice, so it sounds like the same person, though not identical: it follows `--voice`, `--accent` and `--speed`.
- **`--emotion hybrid`:** the expressive voice reads only the paragraphs with dialogue or strong emotion; the usual voice reads the calm narration. It switches only between paragraphs, and the two are matched in loudness. How much of a book the expressive voice reads depends on the book: about 80% of a tense, dialogue-heavy chapter, much less of a quiet one. The run says how much before it starts.
- **`--emotion full`:** the expressive voice reads everything, including chapter titles and summaries.

Some care goes into keeping the expressive voice reliable:

- A very short line, like a one-word shout, is read together with the words around it (`"Run!" she screamed.`), since on its own it can come out garbled.
- The voice is kept just below the top of its intensity range, where it's least stable.
- Each line is checked by a small speech-to-text model. If it didn't come out as written (garbled, skipped or rambling on), it's made again, up to 3 times. Lines that still don't pass are listed at the end of the run, so you can listen to those spots.
- Long silences inside a line are shortened, and each line ends with the pause Claude marked.
- It reads plain text rather than exact sounds, so a [pronunciation list](#pronunciations) reaches it as plain spellings: a name listed as `KAIR` is given to it as "Kare". That's nearly always right, but not as exact as with the usual voice.

Timing on an M3 Pro with 18 GB of memory: the usual voice reads an hour of audio in about 10 minutes. With `hybrid`, two chapters where the expressive voice read 62% of the words (29 minutes of audio) took 49 minutes, about 1.7 times their length. `full` is slower, roughly 2.5 times (an estimate from that run). So `hybrid` suits a few chapters at a time, best left running with `--stay-awake`, and `full` a chapter or a scene. The expressive voice uses about 5 GB of memory while it runs. Only one voice model runs at a time, so the Mac stays usable, but it works hard, and background work such as a virus scan slows it down a lot. The audio the expressive voice makes carries an inaudible watermark marking it as machine-made (part of Chatterbox), which is another reason to keep what book2audio makes to yourself.

### Voices

The default narrator is a blend of two voices, `lewis:70+onyx:30`, with an American accent at speed 0.9. It's only a starting point: the voice options are among the most flexible in book2audio, so it's worth trying a few voices, mixes, accents and speeds to find what suits each book. Run `./book2audio --voice-samples` to hear the suggested narrators, or `./book2audio --list-voices` for all of them. Kokoro voice names start with a code: `am_` American male, `af_` American female, `bm_`/`bf_` British male/female. Good narrators to try: `am_michael`, `am_fenrir`, `am_onyx`, `bm_george`, `bm_fable`, `bm_lewis`, `af_heart`, `bf_emma`. You can give just the name part, e.g. `--voice fenrir`.

You can also mix voices. `--voice lewis:70+onyx:30` blends the two voices' sound, 70% to 30% (the numbers are relative, so `7+3` means the same; leave them out for equal parts). The voice and the accent are separate: the accent comes from the pronunciation rules, which follow the main voice unless you pass `--accent`. So `--voice lewis --accent american` keeps that voice but with American pronunciation.

`--speed` changes how the narrator speaks, not just the playback rate: at `0.9` the narrator takes their time over each word, which can suit a grand style. Your audiobook player's speed control is still the better way to simply go faster or slower. Kokoro renders at about 6× real time on an M3 Pro (a 25-minute chapter takes about 4 minutes).

### Pronunciations

The voice works out unfamiliar words from their spelling, so invented names and words in other languages can come out wrong. A pronunciation list is a text file with one word per line, `word = how it sounds`:

```text
# My book
Zarathel = ZAR-uh-thel
Ysolde = ee-ZOHLD-uh
Kaer = KAIR
Vhaelin = /vˈeɪlɪn/
HQ = letters
```

- **Respelling** (the easy way): syllables split by hyphens, the stressed syllable in CAPITALS (with none in capitals, the first syllable is stressed). Spell the sounds the way they're commonly respelled: `ay` as in say, `ah` as in father, `ee` as in see, `eh` as in bed, `ih` as in sit, `oh` as in go, `oo` as in food, `uh` as in cup, `ow` as in cow, `oy` as in boy, `eye` or `ye` as in eye, `air` as in hair, `ar` as in car, `or` as in for, `er` as in her; `sh`, `ch`, `th`, `zh` (as in vision), `kh` (as in loch), `j` as in jam, `g` always hard. A single vowel letter is short (`a` as in cat, `e` as in bed, `i` as in sit, `o` as in hot, `u` as in cup), and a final `e` makes it long, as in `vice`, `ace`, `rope`.
- **IPA** between slashes, for exact control: `/vˈeɪlɪn/`, with `ˈ` before the stressed vowel.
- **`letters`** to spell a word out letter by letter.

With `--emotion`, the expressive voice gets each listed word as a plain spelling made from its sounds (see [Expressive reading](#expressive-reading)).

Words match whatever their capitals, and the possessive and plural come along (`Zarathel's`, `Zarathels`). Lines starting with `#` are notes. The run says how many of the listed words appear in the text, so a typo in a name shows up as a lower count. It also lists names in the text that aren't in your list yet (capitalized words that aren't in the word list that comes with macOS), so you can decide which ones to add. Keep one list per book and pass it with every recording. To try a change, give the transcript from `output/` as the input instead of the video: only the chapters with a changed word are read again.

### Chapters

A page is treated as a chapter opening when it has a "Chapter N" line (or Prologue/Epilogue/Part/Interlude, or a Roman numeral such as "IV" on its own) near the top, an optional title under it, and then a tall blank band before the text starts. In video, the numeral is small and often read in only one of a page's frames; that's enough. Chapters that start on a right-hand page facing a blank page work too, and the blank page isn't reported as missing. That's a common layout; a book that opens chapters differently may need `detect_chapter_opening` in `src/book2audio/layout.py` adjusted.

A recording usually starts partway through a chapter. That opening text gets its own chapter, named from the next chapter's number, e.g. "Chapter 31 (continued)", or "Previous chapter (continued)" if no numbered chapter follows.

A blank gap in the middle of a page (a scene break) is not a chapter. It becomes a 1.5-second pause in the audio and a `* * *` line in the transcript. A scene break that falls exactly at a page boundary can't be seen and is read straight through.

**A book without chapters:** with no chapter headings, a recording is one long chapter, which is hard to find your place in. `--pages-per-chapter 10` starts a new chapter about every 10 pages instead (the run suggests it when it finds no chapter headings). The cuts go near pages 11, 21, 31 and so on of the book's own numbering, so separate recordings of the same book line up. Each cut moves to the nearest paragraph start (or a scene break, if there's one within a page), so no sentence is cut in two. The chapters are named "Pages 11-20". The name isn't read aloud: the story carries on after a short pause, as it does in the book. With `--summary`, each summary is announced as "Summary of pages 11 to 20". A piece of less than half the chosen length at the start or end of a recording joins its neighbour, so a recording of pages 36-101 gives "Pages 36-40", "Pages 41-50", … "Pages 91-101". With this option the run doesn't look for chapter headings at all, so don't use it on a book that has them. Use it on the photos or video, not on a transcript: the transcript has no page numbers, but the chapters it was made with are kept in it.

Page numbers are read from the top of the page, or from the foot of the page in books that print them there. In a video, a page number at the foot is often read in only a few frames; the pages in between are numbered from the facing pages and the spreads around them.

## Listening on your phone

The `.m4b` file is a standard audiobook file with chapter markers.

- **Easiest:** a free audiobook player such as BookPlayer. AirDrop the `.m4b` to your iPhone (it lands in Files), then in the player tap **+ → Import files** and pick it. The player remembers your place, shows chapters, does variable speed and works with CarPlay.
- **Apple Books:** connect the iPhone to the Mac, select it in the Finder sidebar, open the Audiobooks tab, drag the `.m4b` in and sync. Apple Books on the iPhone can't import audiobooks from Files directly.

## Tips for good captures

- Light the pages evenly and avoid glare. Keep fingers off the text.
- Keep other text out of the frame, such as a screen or papers behind the book. It can be mistaken for the book's page headers or chapter headings.
- Hold the book open as flat as you can. Lines right at the spine are the hardest.
- Video is fine and quick; blurry frames during page turns are skipped automatically. Pause about a second on each spread. Frames are sampled every half second, so flipping faster than once per second can skip pages.
- Some shake and pages slipping mid-turn are fine; frames that can't be read cleanly are skipped as long as other frames show the same pages.
- Record in 1080p rather than 4K: text recognition works just as well and files are much smaller.

Rough timing on an M3 Pro: text recognition takes about 0.75× the video's length, and the voice takes about 1/6 of the audio's length (an hour of audio in ~10 minutes). Proofreading and summaries add about 5–10 minutes for a recording of about 40,000 words (a couple of minutes for a short one). `--emotion` is much slower (see [Expressive reading](#expressive-reading)). A Mac that goes to sleep pauses the run. For a long run left unattended, add `--stay-awake`; closing the lid still puts the Mac to sleep, so leave it open (and plugged in).

## How it works

The code is in `src/book2audio/`:

| Module | Step |
|---|---|
| `capture.py`, `ocr.swift` | Runs Apple's Vision framework on each photo or video frame: on the full image, on each page cropped at the detected spine, and as a "fast" pass that catches short lines the accurate pass misses. A photo taken sideways or upside down is turned upright first. Vision handles one image at a time per process, so several helper processes run in parallel |
| `layout.py` | Splits each spread into pages; removes running heads, page numbers, text outside the page and blurry frames (and frames caught mid page-turn); orders lines on curved pages; finds paragraph indents, scene breaks and chapter openings |
| `ordering.py` | Matches the many views of each page by their text and keeps the clearest (adding a short last line the clearest one missed, if most of the others read it). Page numbers are decided by vote, with evidence from the facing page. A read page number is ignored unless another page has a number close to it, which filters misreads like "867" while still allowing recordings of separate parts of the book. In a video, a spread with no number read follows the spread before it |
| `text.py` | Rebuilds paragraphs, cuts a book without chapters every N pages (`--pages-per-chapter`), and writes the Markdown transcript |
| `claude.py` | Proofreading and summaries, through `claude -p` |
| `pronounce.py` | Reads `--pronunciations` lists and turns respellings into the sounds the voice speaks (and, for `--emotion`, sounds back into plain spellings) |
| `speech.py` | Kokoro speaks each chapter, and ffmpeg packages everything as an AAC `.m4b` with a chapter track |
| `emotion.py` | `--emotion`: Claude's marks, choosing which paragraphs the expressive voice reads, checking each line, and evening out silences and loudness |
| `emotion_worker.py` | Runs the expressive voice and the line check in their own environment (`.venv-emotion`), one request at a time |
| `cli.py` | The command line, tying the steps together |

## Troubleshooting

- **"book2audio isn't fully set up yet"**, or a Python error after moving the folder or upgrading Python: run `./setup.sh` again.
- **"Python 3.12 or 3.13 is needed":** run `brew install python@3.13`, then `./setup.sh`.
- **"Couldn't compile the OCR helper":** run `xcode-select --install`, then `./setup.sh`.
- **Pages missing or out of order:** re-shoot the pages the run lists, slower, and pass both recordings in one run.
- **"--proofread and --summary need the `claude` command":** install Claude Code and run `claude` once to log in.
- **"Claude couldn't finish the proofreading/summaries/mood marks":** usually your Claude plan's usage limit. What was done is saved; run the same command again later to carry on.
- **"--emotion needs the optional expressive voice":** it isn't installed, and book2audio won't download it on its own. If you want it, run `./setup.sh --emotion` once (about 4.5 GB; it asks first). Otherwise, leave out `--emotion`.
- **"The expressive voice couldn't run":** the message shows the end of its log. Run `./setup.sh --emotion` again, then the same command: finished chapters and paragraphs are kept.
- **"N line(s) may sound unclear" after an `--emotion` run:** listen to those lines. If one is wrong, run the same command again: only that paragraph is read again if you change a word in the transcript, or change nothing and delete `~/Library/Caches/book2audio/audio/emotion` to read every expressive paragraph again.

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
| [libsndfile](https://github.com/libsndfile/libsndfile) (bundled with soundfile) | Reading and writing WAV files | LGPL-2.1 |
| [Chatterbox](https://github.com/resemble-ai/chatterbox) model and code (only with `./setup.sh --emotion`) | The expressive voice | MIT |
| [Perth](https://github.com/resemble-ai/Perth) (bundled with Chatterbox) | Watermarking the expressive voice's audio | MIT |
| [PyTorch](https://pytorch.org), torchaudio, librosa (with `--emotion`) | Running the expressive voice | BSD-3-Clause, ISC |
| [Transformers](https://github.com/huggingface/transformers) and the [Whisper base.en](https://huggingface.co/openai/whisper-base.en) model (with `--emotion`) | Checking each expressive line | Apache-2.0 |

Apple, Mac, macOS, iPhone, Apple Books and CarPlay are trademarks of Apple Inc. Claude and Claude Code are trademarks of Anthropic. BookPlayer is a third-party app. book2audio is an independent hobby project. It isn't affiliated with, sponsored by, or endorsed by any of these companies or projects.

## License

The book2audio code is MIT-licensed. See [LICENSE](LICENSE). The license covers this code only, not any book you use it on or anything it produces from one.

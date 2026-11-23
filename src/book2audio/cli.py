"""The book2audio command line."""
import argparse
import re
import shutil
import sys
from pathlib import Path

from . import ROOT, capture, claude, emotion, layout, ordering, pronounce, speech, text

USAGE = """book2audio INPUT [INPUT ...] [options]
       book2audio --list-voices | --voice-samples"""

EPILOG = f"""\
INPUT is usually a video (.mov/.mp4) of you turning the pages. Photos, screenshots
and folders of them work too. Each run writes to output/: the audiobook (.m4b) and
the transcript (.md) it was read from.

Give a transcript (.md) as INPUT instead to work from it again without redoing the
text recognition: after fixing a line in it, to try other pronunciations or another
voice, or to proofread or summarize it. Only the chapters that changed are read
aloud again.

--proofread, --summary and --emotion send the book's text to Claude through your
`claude` login (Claude Code). Without them, everything stays on your Mac.

--emotion needs the optional expressive voice: run  ./setup.sh --emotion  once (about
4.5 GB). It is much slower than the usual reading: "hybrid" takes about 1.7 times the
audio's length for a dialogue-heavy chapter, "full" roughly 2.5 times, so try a chapter first.

examples:
  ./book2audio ~/Downloads/IMG_1234.MOV
  ./book2audio ~/Downloads/IMG_1234.MOV --proofread --summary --pronunciations my-book.txt
  ./book2audio ~/Downloads/IMG_1234.MOV --voice george --speed 1
  ./book2audio ~/Downloads/IMG_1234.MOV --pages-per-chapter 10
  ./book2audio "output/My Book - Ch 20-21.md" --summary --text-only
  ./book2audio "output/My Book - Ch 20-21.md" --pronunciations my-book.txt
  ./book2audio "output/My Book - Ch 20-21.md" --emotion hybrid --pronunciations my-book.txt
"""


def parse_args():
    ap = argparse.ArgumentParser(prog="book2audio", usage=USAGE, epilog=EPILOG,
                                 description="Turn a video of you turning a book's pages (or photos of them) into an audiobook.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="*", metavar="INPUT", help=argparse.SUPPRESS)
    ap.add_argument("-o", "--output", metavar="FILE",
                    help="audiobook file to write (default: output/<Book> - <Ch 20-21>.m4b)")
    ap.add_argument("--title", help="book title tag (default: read from the running heads)")
    ap.add_argument("--author", help="author tag (default: read from the running heads)")
    ap.add_argument("--from-chapter", type=int, metavar="N",
                    help="start at chapter N, leaving out the end of the chapter before it "
                         "(e.g. when the last recording already covered it)")
    ap.add_argument("--pages-per-chapter", type=int, metavar="N",
                    help="for a book without chapters: start a new chapter about every N pages "
                         "(e.g. 10), at a paragraph break near pages 11, 21, 31, ..., named "
                         "\"Pages 11-20\"")

    g = ap.add_argument_group("voice")
    g.add_argument("--voice", metavar="NAME",
                   help=f"narrator: one voice, e.g. am_michael or just michael, or a mix with "
                        f"weights, e.g. lewis:70+onyx:30 (default {speech.DEFAULT_VOICE})")
    g.add_argument("--accent", choices=["american", "british"],
                   help="pronunciation (default: american with the default voice, otherwise the "
                        "accent of the main --voice)")
    g.add_argument("--speed", type=float, metavar="N",
                   help=f"speaking speed, e.g. 1 for normal or 0.9 for slower and more measured "
                        f"(default {speech.DEFAULT_SPEED})")
    g.add_argument("--list-voices", action="store_true", help="list the voices and exit")
    g.add_argument("--voice-samples", action="store_true",
                   help="write a short clip of every voice (and the default) to voice_samples/ and exit")
    g.add_argument("--pronunciations", metavar="FILE", action="append",
                   help="how to say particular words, one per line:  Zarathel = ZAR-uh-thel  "
                        "(see README; give it more than once to combine lists)")

    g = ap.add_argument_group("Claude (optional; sends the book's text to Anthropic)")
    g.add_argument("--proofread", action="store_true",
                   help="have Claude fix text recognition errors before the text is read aloud")
    g.add_argument("--summary", action="store_true",
                   help="write a one-paragraph summary of each chapter (.summary.md) and read "
                        "each one aloud after its chapter, as its own chapter you can skip")

    g.add_argument("--emotion", choices=emotion.MODES, metavar="hybrid|full",
                   help="a more expressive reading: Claude marks who is speaking and how "
                        "intensely, and an expressive copy of the narrator's voice reads it. "
                        "hybrid: only dialogue and intense paragraphs (the rest as usual); "
                        "full: everything (much slower). Needs ./setup.sh --emotion")

    g = ap.add_argument_group("other")
    g.add_argument("--text-only", action="store_true",
                   help="stop after the transcript (and summaries), without making audio")
    g.add_argument("--stay-awake", action="store_true",
                   help="keep the Mac from going to sleep on its own while the audio is made, "
                        "for a long run left unattended (closing the lid still puts it to sleep)")
    g.add_argument("--video-every", type=float, default=0.5, metavar="SECONDS",
                   help="time between sampled video frames (default 0.5)")
    args = ap.parse_args()

    if not (args.inputs or args.list_voices or args.voice_samples):
        ap.error("give at least one photo, folder or video (see --help)")
    args.transcript = any(Path(a).suffix.lower() == ".md" for a in args.inputs)
    if args.transcript:
        if len(args.inputs) > 1:
            ap.error("give a transcript (.md) on its own, without photos or videos")
        if not Path(args.inputs[0]).expanduser().is_file():
            ap.error(f"not found: {args.inputs[0]}")
    if args.pages_per_chapter is not None:
        if args.pages_per_chapter < 1:
            ap.error("--pages-per-chapter needs a number of pages, e.g. 10")
        if args.transcript:
            ap.error("--pages-per-chapter works from the photos or video (a transcript has no page "
                     "numbers); its chapters are already in the transcript")
        if args.from_chapter is not None:
            ap.error("--from-chapter and --pages-per-chapter don't go together: with "
                     "--pages-per-chapter the book has no numbered chapters")
    return args


def report_cut_off(pages):
    """Lines that look covered or cut off in every frame: worth a retake while the book is open."""
    hits = [(p["number"], r["text"]) for p in pages for r in p["body"] if r.get("cut_off")]
    if not hits:
        return
    print(f"Text may be missing on {len(hits)} line(s), covered or cut off in every frame:", file=sys.stderr)
    for num, line in hits:
        where = f"page {num}" if num is not None else "a page without a number"
        print(f"  {where}: ...{line[-60:]}", file=sys.stderr)
    print("  Fix the line in the transcript (.md) and make the audio from it again, or record "
          "that page again.", file=sys.stderr)


def main():
    args = parse_args()
    if not speech.installed() or not shutil.which("ffmpeg"):
        sys.exit("book2audio isn't fully set up yet: run ./setup.sh")
    if args.list_voices:
        return speech.list_voices()
    if args.voice_samples:
        return speech.write_voice_samples()

    voice = speech.resolve_voice(args.voice, args.accent, args.speed)   # check before the slow steps
    pron = pronounce.load(args.pronunciations)
    if (args.proofread or args.summary or args.emotion) and not claude.available():
        sys.exit("--proofread, --summary and --emotion need the `claude` command (Claude Code), "
                 "logged in. Leave them out to do without.")
    if args.emotion and not args.text_only and not emotion.installed():
        sys.exit("--emotion needs the optional expressive voice, which isn't installed yet. "
                 "Run  ./setup.sh --emotion  (about 4.5 GB), then the same command again.")

    if args.transcript:
        blocks, out, tags = read_transcript(args)
    else:
        blocks, out, tags = read_pages(args)
    blocks = from_chapter(blocks, args.from_chapter)
    out.parent.mkdir(parents=True, exist_ok=True)

    md_text = text.blocks_to_markdown(blocks)
    md = out.with_suffix(".md")
    if args.proofread:
        if not args.transcript:
            out.with_suffix(".ocr.md").write_text(md_text)
        print("Proofreading with Claude...", file=sys.stderr)
        md_text = claude.proofread(md_text)
        blocks = text.markdown_to_blocks(md_text)
    if not (args.transcript and md.resolve() == Path(args.inputs[0]).expanduser().resolve()
            and md.read_text() == md_text):
        md.write_text(md_text)
        print(f"Transcript: {md}", file=sys.stderr)

    summaries = None
    if args.summary:
        print("Summarizing chapters with Claude...", file=sys.stderr)
        summaries = claude.summarize(blocks)
        summary_md = out.with_suffix(".summary.md")
        summary_md.write_text(claude.summaries_to_markdown(summaries, out.stem))
        print(f"Summaries: {summary_md}", file=sys.stderr)

    report_new_names(md_text, pron)
    if args.text_only:
        return
    expressive = None
    if args.emotion:
        print("Marking who speaks and how intensely, with Claude...", file=sys.stderr)
        marks = emotion.mark(blocks)
        expressive = emotion.Expressive(args.emotion, marks, voice, pron, speech.AUDIO_CACHE)
        report_emotion(blocks, expressive)
    narrate(blocks, out, voice, tags, summaries, pron, expressive, args.stay_awake)


def read_pages(args):
    """Text recognition on the photos and videos: (blocks, audiobook path, tags)."""
    capture.ensure_ocr_binary()
    paths = capture.collect_inputs(args.inputs)
    print(f"OCR on {len(paths)} file(s)...", file=sys.stderr)
    records = capture.run_ocr(paths, args.video_every)

    pages = layout.assemble_spreads(records)
    if not pages:
        sys.exit("No readable book pages were found in the input.")
    heads = layout.detect_running_heads(pages)
    by_pages = args.pages_per_chapter
    pages = [layout.analyze_page(p, heads, chapters=not by_pages) for p in pages]
    title, author = layout.book_tags(pages, heads)
    title, author = args.title or title, args.author or author
    pages = ordering.order_pages(pages)
    nums = [p["number"] for p in pages if p["number"] is not None]
    if nums:
        print(f"Pages {min(nums)}-{max(nums)} ({len(pages)} pages)", file=sys.stderr)
    report_cut_off(pages)

    if by_pages:
        cuts = text.cut_pages(pages, by_pages)
        print(f"{len(cuts)} chapter(s) of about {by_pages} pages: {', '.join(cuts)}", file=sys.stderr)
    blocks = text.build_text(pages)
    if not by_pages and len(pages) >= 30 and not any(kind == "chapter" for kind, _ in blocks):
        print("No chapter headings were found, so this is all one chapter. If the book has no "
              "chapters, --pages-per-chapter 10 starts a new one about every 10 pages.", file=sys.stderr)
    blocks = text.label_continued(blocks)
    contents = text.describe_contents(from_chapter(blocks, args.from_chapter), pages)
    if args.output:
        out = Path(args.output).expanduser()
    else:
        name = re.sub(r'[/:\\]', "-", f"{title} - {contents}" if title else contents)
        out = ROOT / "output" / f"{name}.m4b"
    if title or author:
        print(f"Book: {title or '?'} by {author or '?'}", file=sys.stderr)
    tags = {"title": f"{title} - {contents}" if title else out.stem, "album": title or out.stem,
            "artist": author, "album_artist": author}
    return blocks, out, tags


def read_transcript(args):
    """A transcript (.md) from an earlier run, e.g. after fixing a line in it: (blocks,
    audiobook path, tags). 'X.ocr.md' (the unproofread text) makes 'X.md' and 'X.m4b'."""
    md = Path(args.inputs[0]).expanduser()
    stem = re.sub(r"\.ocr$", "", md.stem)
    out = Path(args.output).expanduser() if args.output else md.with_name(stem + ".m4b")
    title = args.title or (stem.split(" - ")[0] if " - " in stem else stem)
    tags = {"title": stem, "album": title, "artist": args.author, "album_artist": args.author}
    return text.markdown_to_blocks(md.read_text()), out, tags


def from_chapter(blocks, n):
    """--from-chapter N: the blocks from chapter N's heading on."""
    if n is None:
        return blocks
    i = next((i for i, (kind, t) in enumerate(blocks)
              if kind == "chapter" and text.chapter_number(t) == n and not text.is_continued(t)), None)
    if i is None:
        found = [t for kind, t in blocks if kind == "chapter"]
        sys.exit(f"--from-chapter {n}: no chapter {n} heading in this text "
                 f"(it has: {', '.join(found) or 'no chapter headings'})")
    return blocks[i:]


def report_new_names(md_text, pron):
    """Names the voice may say wrongly: capitalized words that aren't ordinary English words
    and aren't in the --pronunciations lists yet."""
    names = pronounce.new_names(md_text, pron)
    if names:
        print(f"Names that aren't in a pronunciation list yet ({len(names)}), worth checking how "
              f"they're said:\n  " + ", ".join(f"{w} ({n})" for w, n in names), file=sys.stderr)


def report_emotion(blocks, expressive):
    paras = [t for kind, t in blocks if kind == "para"]
    mine = [p for p in paras if expressive.wants(p)]
    share = sum(len(p.split()) for p in mine) / max(1, sum(len(p.split()) for p in paras))
    print(f"  The expressive voice reads {len(mine)} of {len(paras)} paragraphs ({share:.0%} of the "
          f"words); expect this to take much longer than usual", file=sys.stderr)


def narrate(blocks, out, voice, tags, summaries, pron, expressive=None, awake=False):
    print(f"Synthesizing speech ({voice})...", file=sys.stderr)
    if pron:
        used = pron.used_in(text.blocks_to_markdown(blocks))
        print(f"Pronunciations: {len(used)} of the {len(pron.entries)} listed words are in this text",
              file=sys.stderr)
    chapters = speech.synthesize(blocks, out, voice, tags, summaries, pron, expressive, awake)
    print(f"Audio: {out}  ({chapters[-1][2] / 60:.0f} min)", file=sys.stderr)
    for name, start, _ in chapters:
        print(f"  {int(start // 60):3d}:{int(start % 60):02d}  {name}", file=sys.stderr)

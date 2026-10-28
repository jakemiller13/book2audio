"""The book2audio command line."""
import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

from . import ROOT, capture, claude, layout, ordering, paper, pronounce, science, speech, text

USAGE = """book2audio INPUT [INPUT ...] [options]
       book2audio PAPER.pdf [options]
       book2audio --summarize TRANSCRIPT.md
       book2audio --list-voices | --voice-samples"""

EPILOG = """\
INPUT is usually a video (.mov/.mp4) of you turning the pages. Photos, screenshots
and folders of them work too. By default each run writes to output/: the audiobook
(.m4b), the transcript (.md) and one-paragraph chapter summaries (.summary.md).

Proofreading and summaries send the book's text to Claude through your `claude`
login (Claude Code). Use --no-cleanup --no-summary to keep everything on your Mac.

A scientific paper (beta): give its PDF on its own. Claude reads every page and
describes the figures and tables (this always uses Claude, page images included),
and you get output/<Paper title>.m4b and .md. The run prints the Claude tokens used.

examples:
  ./book2audio ~/Downloads/IMG_1234.MOV
  ./book2audio ~/Desktop/chapter32/ --voice george --speak-summary
  ./book2audio --summarize "output/My Book - Ch 20-21.md"
  ./book2audio ~/Downloads/paper.pdf --text-only
"""


def parse_args():
    ap = argparse.ArgumentParser(prog="book2audio", usage=USAGE, epilog=EPILOG,
                                 description="Turn a video of you turning a book's pages (or photos of them), "
                                             "or a scientific paper's PDF, into an audiobook.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="*", metavar="INPUT", help=argparse.SUPPRESS)
    ap.add_argument("-o", "--output", metavar="FILE",
                    help="audiobook file to write (default: output/<Book> - <Ch 20-21>.m4b, "
                         "or output/<Paper title>.m4b)")
    ap.add_argument("--title", help="title tag (default: a book's running heads, or the paper's title)")
    ap.add_argument("--author", help="author tag (default: a book's running heads, or the PDF's author)")

    g = ap.add_argument_group("voice")
    g.add_argument("--voice", metavar="NAME",
                   help=f"narrator, e.g. am_michael or just michael (default {speech.DEFAULT_VOICE})")
    g.add_argument("--list-voices", action="store_true", help="list the voices and exit")
    g.add_argument("--voice-samples", action="store_true",
                   help="write a short clip of each suggested voice to voice_samples/ and exit")

    g = ap.add_argument_group("Claude (proofreading and summaries)")
    g.add_argument("--no-cleanup", action="store_true",
                   help="don't have Claude fix OCR errors before speaking")
    g.add_argument("--no-summary", action="store_true", help="don't write chapter summaries")
    g.add_argument("--speak-summary", action="store_true",
                   help="also read each summary aloud, as its own chapter after the chapter it recaps")
    g.add_argument("--summarize", metavar="TRANSCRIPT",
                   help="write summaries for an existing transcript (.md) and exit")

    g = ap.add_argument_group("other")
    g.add_argument("--text-only", action="store_true", help="stop after the transcript and summaries")
    g.add_argument("--video-every", type=float, default=0.5, metavar="SECONDS",
                   help="time between sampled video frames (default 0.5)")
    args = ap.parse_args()

    if args.speak_summary and args.no_summary:
        ap.error("--speak-summary and --no-summary contradict each other")
    if args.summarize and args.inputs:
        ap.error("--summarize takes a transcript on its own, without other inputs")
    if not (args.inputs or args.summarize or args.list_voices or args.voice_samples):
        ap.error("give at least one photo, folder or video, or a paper's PDF (see --help)")
    args.paper = None
    if any(Path(a).suffix.lower() == ".pdf" for a in args.inputs):
        if len(args.inputs) > 1:
            ap.error("give a paper (.pdf) on its own: one paper per run, without photos or videos")
        if args.no_cleanup:
            ap.error("papers need Claude to read the pages, so --no-cleanup doesn't apply to them")
        if args.speak_summary:
            ap.error("papers have no chapter summaries (the abstract is read instead)")
        args.paper = Path(args.inputs[0]).expanduser()
        if not args.paper.is_file():
            ap.error(f"not found: {args.inputs[0]}")
    return args


def summarize_transcript(md_path):
    """--summarize: summaries for a transcript from an earlier run."""
    md = Path(md_path).expanduser()
    if not md.is_file():
        sys.exit(f"Not found: {md_path}")
    if not claude.available():
        sys.exit("Summaries need the `claude` command (Claude Code). Install it and log in first.")
    stem = re.sub(r"\.ocr$", "", md.stem)
    out = md.with_name(stem + ".summary.md")
    print("Summarizing chapters with Claude...", file=sys.stderr)
    summaries = claude.summarize(text.markdown_to_blocks(md.read_text()))
    if not summaries:
        sys.exit("No summaries were written (the transcript may be too short).")
    out.write_text(claude.summaries_to_markdown(summaries, stem))
    print(f"Summaries: {out}", file=sys.stderr)


def main():
    args = parse_args()
    if args.summarize:
        return summarize_transcript(args.summarize)
    if not speech.installed() or not shutil.which("ffmpeg"):
        sys.exit("book2audio isn't fully set up yet: run ./setup.sh")
    if args.list_voices:
        return speech.list_voices()
    if args.voice_samples:
        return speech.write_voice_samples()

    voice = speech.resolve_voice(args.voice)   # check before the slow steps
    if args.paper:
        return paper_audiobook(args, voice)
    use_claude = not (args.no_cleanup and args.no_summary)
    if use_claude and not claude.available():
        if args.speak_summary:
            sys.exit("--speak-summary needs the `claude` command (Claude Code).")
        print("Note: the `claude` command (Claude Code) isn't installed, so there will be no "
              "proofreading or summaries.\n      Pass --no-cleanup --no-summary to hide this note.",
              file=sys.stderr)
        args.no_cleanup = args.no_summary = True

    capture.ensure_ocr_binary()
    paths = capture.collect_inputs(args.inputs)
    print(f"OCR on {len(paths)} file(s)...", file=sys.stderr)
    records = capture.run_ocr(paths, args.video_every)

    pages = layout.assemble_spreads(records)
    if not pages:
        sys.exit("No readable book pages were found in the input.")
    heads = layout.detect_running_heads(pages)
    pages = [layout.analyze_page(p, heads) for p in pages]
    title, author = layout.book_tags(pages, heads)
    title, author = args.title or title, args.author or author
    pages = ordering.order_pages(pages)
    nums = [p["number"] for p in pages if p["number"] is not None]
    if nums:
        print(f"Pages {min(nums)}-{max(nums)} ({len(pages)} pages)", file=sys.stderr)

    blocks = text.label_continued(text.build_text(pages))
    contents = text.describe_contents(blocks, pages)
    out = output_path(args, f"{title} - {contents}" if title else contents)
    if title or author:
        print(f"Book: {title or '?'} by {author or '?'}", file=sys.stderr)

    md_text = text.blocks_to_markdown(blocks)
    if not args.no_cleanup:
        out.with_suffix(".ocr.md").write_text(md_text)
        print("Proofreading with Claude...", file=sys.stderr)
        md_text = claude.proofread(md_text)
        blocks = text.markdown_to_blocks(md_text)
    md = out.with_suffix(".md")
    md.write_text(md_text)
    print(f"Transcript: {md}", file=sys.stderr)

    summaries = None
    if not args.no_summary:
        print("Summarizing chapters with Claude...", file=sys.stderr)
        summaries = claude.summarize(blocks)
        if summaries:
            summary_md = out.with_suffix(".summary.md")
            summary_md.write_text(claude.summaries_to_markdown(summaries, out.stem))
            print(f"Summaries: {summary_md}", file=sys.stderr)
    if args.text_only:
        return

    print(f"Synthesizing speech ({voice})...", file=sys.stderr)
    tags = {"title": f"{title} - {contents}" if title else out.stem, "album": title or out.stem,
            "artist": author, "album_artist": author}
    chapters = speech.synthesize(blocks, out, voice, tags, summaries if args.speak_summary else None)
    report_audio(out, chapters)


def paper_audiobook(args, voice):
    """A scientific paper (PDF): Claude rebuilds each page and describes the figures and tables,
    then the voice reads it with the pronunciation rules for scientific notation."""
    if not claude.available():
        sys.exit("Papers need the `claude` command (Claude Code), which rebuilds each page and "
                 "describes the figures. Install it and log in first.")
    with tempfile.TemporaryDirectory() as tmp:   # page images, needed only while Claude reads them
        title, author, blocks = paper.read_paper(args.paper, Path(tmp))
    title, author = args.title or title, args.author or author
    out = output_path(args, title)
    print(f"Paper: {title}", file=sys.stderr)
    md = out.with_suffix(".md")
    md.write_text(text.blocks_to_markdown(blocks))
    print(f"Transcript: {md}", file=sys.stderr)
    if args.text_only:
        print(claude.usage_report(), file=sys.stderr)
        return

    print("Working out how to say the paper's technical words with Claude...", file=sys.stderr)
    pron = pronounce.for_paper(paper.cache_dir(args.paper), title, blocks)
    review = out.with_suffix(".pronunciations.md")
    review.write_text(pron.review(title, text.blocks_to_markdown(blocks)))
    print(f"Pronunciations: {review}", file=sys.stderr)
    print(claude.usage_report(), file=sys.stderr)

    print(f"Synthesizing speech ({voice})...", file=sys.stderr)
    tags = {"title": title, "album": title, "artist": author, "album_artist": author}
    chapters = speech.synthesize(blocks, out, voice, tags, normalize=science.speakable_science, pronouncer=pron)
    report_audio(out, chapters)


def output_path(args, name):
    """-o, or output/<name>.m4b (shortened at a word if very long, as paper titles can be)."""
    if args.output:
        out = Path(args.output).expanduser()
    else:
        name = re.sub(r'[/:\\]', "-", name).strip()
        if len(name) > 120:
            name = name[:120].rsplit(" ", 1)[0].rstrip(" ,;-")
        out = ROOT / "output" / f"{name}.m4b"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def report_audio(out, chapters):
    print(f"Audio: {out}  ({chapters[-1][2] / 60:.0f} min)", file=sys.stderr)
    for name, start, _ in chapters:
        print(f"  {int(start // 60):3d}:{int(start % 60):02d}  {name}", file=sys.stderr)

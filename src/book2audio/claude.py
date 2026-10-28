"""The steps that use Claude, through the `claude` command (Claude Code) and the user's
existing login: proofreading the OCR text, short chapter summaries, and, for papers, rebuilding
pages and describing figures (paper.py) and listing how technical words are said (pronounce.py).
All of them send the text to Anthropic."""
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .text import SCENE_BREAK, sections

PARALLEL = 4   # claude calls at once

PROOFREAD_PROMPT = """You are proofreading OCR output from photos of a printed book. The text will be
read aloud by text-to-speech, so recognition errors would be heard.

Fix only OCR errors:
- misrecognized letters or words ("I he" -> "The", "tour guards" -> "four guards", "talse" -> "false")
- broken or missing quotation marks and apostrophes
- stray characters or fragments from the page edge, a finger, or the facing page ("ber That" -> "That")
- words split by a line-end hyphen that were not rejoined, or real hyphens/dashes that were lost
  (use an em dash "—" where the book clearly had one)

Do not reword, modernize, summarize, or "improve" the author's prose. Keep invented names and
words exactly as the author would spell them, using their most common spelling in the text.
Keep every paragraph, in order, separated by a blank line, and keep "## " heading lines as they
are, and "* * *" scene-break lines as they are. If a passage is garbled beyond repair, leave it as it is.

Return only the corrected text, with no commentary."""

SUMMARY_PROMPT = """You write short chapter recaps for a reader who comes back to a book after a
few days away. You get the text of one chapter, or of the part of it that was read (it may start
or end partway through).

Write a single short paragraph, at most 90 words in 3 or 4 plain sentences, in the present tense:
the main events, who is involved, and how the passage ends. Use the book's own names. Leave out
minor detail; this is a reminder, not a retelling.
Cover only this text; don't guess at what comes next or bring in anything from outside it.
No heading, no bullet points, no Markdown, no commentary. Return only the paragraph."""


USAGE = {"calls": 0, "tokens": 0}   # running total for this run, for the end-of-run report
_usage_lock = threading.Lock()


def available():
    return shutil.which("claude") is not None


def ask(system_prompt, text, images=()):
    """One `claude -p` call: no tools, no MCP servers, nothing saved to session history.
    Images (PNG paths) are sent inline with the text, so Claude needs no file access."""
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                            "data": base64.b64encode(Path(p).read_bytes()).decode()}}
               for p in images]
    content.append({"type": "text", "text": text})
    msg = {"type": "user", "message": {"role": "user", "content": content}}
    with tempfile.TemporaryDirectory() as tmp:  # neutral cwd: no project CLAUDE.md
        r = subprocess.run(
            ["claude", "-p", "--model", "opus", "--effort", "low",
             "--system-prompt", system_prompt, "--tools", "", "--strict-mcp-config",
             "--no-session-persistence", "--input-format", "stream-json",
             "--output-format", "stream-json", "--verbose"],
            input=json.dumps(msg) + "\n", capture_output=True, text=True, cwd=tmp, timeout=900)
    result = None
    for line in r.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "result":
            result = event
    if r.returncode != 0 or result is None or result.get("is_error"):
        detail = (result or {}).get("result") or r.stderr.strip() or f"exit code {r.returncode}"
        raise RuntimeError(f"claude failed: {str(detail)[:200]}")
    usage = result.get("usage") or {}
    with _usage_lock:
        USAGE["calls"] += 1
        USAGE["tokens"] += sum(usage.get(k) or 0 for k in ("input_tokens", "output_tokens",
                               "cache_creation_input_tokens", "cache_read_input_tokens"))
    return (result.get("result") or "").strip()


def usage_report():
    return f"Claude: {USAGE['calls']} call(s), about {USAGE['tokens']:,} tokens"


def proofread(md_text):
    """Fix OCR errors in the Markdown transcript, ~3,000 words per call. A part that fails
    or comes back more than 10% longer or shorter is kept as OCR'd."""
    chunks, cur, words = [], [], 0
    for para in md_text.strip().split("\n\n"):
        cur.append(para)
        words += len(para.split())
        if words > 3000:
            chunks.append("\n\n".join(cur))
            cur, words = [], 0
    if cur:
        chunks.append("\n\n".join(cur))

    def one(numbered):
        i, chunk = numbered
        try:
            text = ask(PROOFREAD_PROMPT, chunk)
        except Exception as e:  # keep going with the OCR text rather than lose the run
            print(f"  part {i}: {e}; keeping OCR text", file=sys.stderr)
            return chunk
        ratio = len(text.split()) / max(1, len(chunk.split()))
        if not text or not 0.9 <= ratio <= 1.1:
            print(f"  part {i}: length changed {ratio:.0%}; keeping OCR text", file=sys.stderr)
            return chunk
        return text

    print(f"  {len(chunks)} part(s)...", file=sys.stderr)
    with ThreadPoolExecutor(PARALLEL) as pool:
        out = list(pool.map(one, enumerate(chunks, 1)))
    return "\n\n".join(out) + "\n"


def summarize(blocks):
    """A one-paragraph recap per chapter: [(heading, summary)]. Very short sections and
    failed calls are left out."""
    secs = [(title, "\n\n".join(p for p in paras if p != SCENE_BREAK))
            for title, paras in sections(blocks)]

    def one(sec):
        title, text = sec
        if len(text.split()) < 80:
            return None
        try:
            summary = " ".join(ask(SUMMARY_PROMPT, f"{title}\n\n{text}").split())
        except Exception as e:
            print(f"  {title}: {e}; no summary", file=sys.stderr)
            return None
        if not summary or len(summary.split()) > 200:
            print(f"  {title}: unusable summary; skipped", file=sys.stderr)
            return None
        return title, summary

    with ThreadPoolExecutor(PARALLEL) as pool:
        return [s for s in pool.map(one, secs) if s]


def summaries_to_markdown(summaries, book_label):
    out = [f"# {book_label}: chapter summaries"]
    for title, summary in summaries:
        out += [f"## {title}", summary]
    return "\n\n".join(out) + "\n"

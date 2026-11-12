"""The optional steps that use Claude, through the `claude` command (Claude Code) and the
user's existing login: proofreading the OCR text, and short chapter summaries.
Either way, the book's text is sent to Anthropic."""
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .text import SCENE_BREAK, sections

PARALLEL = 4        # claude calls at once
PART_WORDS = 4000   # a longer chapter is proofread in parts of about this many words
CACHE = Path.home() / "Library" / "Caches" / "book2audio" / "claude"
MODEL = "opus"
# What `claude -p` prints instead of an answer when the plan's usage limit is reached
REFUSAL = re.compile(r"usage limit|hit your limit|limit reached|rate limit|credit balance|"
                     r"please log ?in|not logged in|invalid api key", re.I)


class ClaudeError(Exception):
    """Claude couldn't answer (not installed, logged out, usage limit, ...)."""


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


def available():
    return shutil.which("claude") is not None


def ask(system_prompt, text):
    """One `claude -p` call: no tools, no MCP servers, nothing saved to session history.
    Replies are kept in the cache, so a run that stops partway picks up where it left off."""
    key = hashlib.sha256("\0".join([MODEL, system_prompt, text]).encode()).hexdigest()[:32]
    cached = CACHE / f"{key}.txt"
    if cached.is_file():
        cached.touch()   # recently used, so prune_cache keeps it
        return cached.read_text()
    with tempfile.TemporaryDirectory() as tmp:  # neutral cwd: no project CLAUDE.md
        try:
            r = subprocess.run(
                ["claude", "-p", "--model", MODEL, "--effort", "low",
                 "--system-prompt", system_prompt, "--tools", "", "--strict-mcp-config",
                 "--no-session-persistence", "--output-format", "text"],
                input=text, capture_output=True, text=True, cwd=tmp, timeout=900)
        except subprocess.TimeoutExpired:
            raise ClaudeError("no answer after 15 minutes")
    reply = r.stdout.strip()
    if r.returncode != 0:
        raise ClaudeError((r.stderr.strip() or reply)[:300] or f"claude exited {r.returncode}")
    if not reply or (len(reply.split()) < 60 and REFUSAL.search(reply)):
        raise ClaudeError(reply[:300] or "an empty answer")
    CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(reply)
    return reply


def prune_cache(days=30):
    """Answers not used for a month go."""
    cutoff = time.time() - days * 86400
    for f in CACHE.glob("*.txt"):
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)


def run_all(jobs, what):
    """Run (label, function) jobs a few at a time. If any fail, finish the others (their answers
    are cached) and stop with a message, so running the same command again resumes."""
    def one(job):
        label, fn = job
        try:
            return fn(), None
        except ClaudeError as e:
            return None, f"{label}: {e}"

    with ThreadPoolExecutor(PARALLEL) as pool:
        results = list(pool.map(one, jobs))
    failed = [err for _, err in results if err]
    if failed:
        done = len(jobs) - len(failed)
        sys.exit(f"Claude couldn't finish the {what} ({done} of {len(jobs)} done and saved):\n  "
                 + "\n  ".join(failed[:5]) +
                 "\nRun the same command again later to carry on from here (for a usage limit, "
                 "after it resets).")
    prune_cache()
    return [out for out, _ in results]


def split_chapters(md_text):
    """The transcript in chapters: each starts at a '## ' heading (text before the first one
    is its own piece)."""
    return [c.strip() for c in re.split(r"(?m)^(?=## )", md_text) if c.strip()]


def parts_of(chapter):
    """A long chapter in pieces of about PART_WORDS words, split between paragraphs."""
    parts, cur, words = [], [], 0
    for para in chapter.split("\n\n"):
        if cur and words + len(para.split()) > PART_WORDS:
            parts.append("\n\n".join(cur))
            cur, words = [], 0
        cur.append(para)
        words += len(para.split())
    if cur:
        parts.append("\n\n".join(cur))
    return parts


def proofread(md_text):
    """Fix OCR errors in the Markdown transcript, chapter by chapter. A part that comes back more
    than 10% longer or shorter is kept as it was, with a warning naming the chapter."""
    jobs, where = [], []
    for chapter in split_chapters(md_text):
        heading = chapter.splitlines()[0].lstrip("# ").strip()
        for part in parts_of(chapter):
            jobs.append((heading, lambda part=part: ask(PROOFREAD_PROMPT, part)))
            where.append((heading, part))
    print(f"  {len(jobs)} part(s) in {len(split_chapters(md_text))} chapter(s)...", file=sys.stderr)
    out, kept = [], []
    for (heading, part), fixed in zip(where, run_all(jobs, "proofreading")):
        ratio = len(fixed.split()) / max(1, len(part.split()))
        if not 0.9 <= ratio <= 1.1:
            kept.append(f"{heading} (proofread text was {ratio:.0%} of the original)")
            fixed = part
        out.append(fixed.strip())
    if kept:
        print("Kept the unproofread text, since proofreading changed its length a lot, in:\n  "
              + "\n  ".join(kept) + "\n  Claude may have dropped text, or rightly removed junk "
              "such as a duplicated page. Check that part of the transcript (.md).", file=sys.stderr)
    return "\n\n".join(out) + "\n"


def summarize(blocks):
    """A one-paragraph recap per chapter: [(heading, summary)]. Very short sections are
    left out."""
    secs = [(title, "\n\n".join(p for p in paras if p != SCENE_BREAK))
            for title, paras in sections(blocks)]
    secs = [(title, body) for title, body in secs if len(body.split()) >= 80]
    jobs = [(title, lambda t=title, b=body: ask(SUMMARY_PROMPT, f"{t}\n\n{b}")) for title, body in secs]
    out = []
    for (title, _), summary in zip(secs, run_all(jobs, "summaries")):
        summary = " ".join(summary.split())
        if len(summary.split()) > 200:
            print(f"  {title}: the summary came back far too long; left out", file=sys.stderr)
            continue
        out.append((title, summary))
    return out


def summaries_to_markdown(summaries, book_label):
    out = [f"# {book_label}: chapter summaries"]
    for title, summary in summaries:
        out += [f"## {title}", summary]
    return "\n\n".join(out) + "\n"

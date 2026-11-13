"""--emotion: a more expressive reading.

Claude marks each paragraph's lines: who is speaking, how intensely, and how long to pause
after each. An expressive voice (Chatterbox, in its own environment; see emotion_worker.py)
then reads them, imitating the Kokoro narrator from a short clip of it, so it sounds like the
same person.

  hybrid  Kokoro reads calm narration; the expressive voice reads paragraphs with dialogue
          or strong emotion. It switches only between paragraphs.
  full    the expressive voice reads everything (several times slower).

Each line the expressive voice speaks is checked by a small speech-to-text model and made
again (up to TRIES times) if it didn't come out as written. Very short lines are read with
their neighbor, since alone they can come out garbled, and the voice is kept just below the
top of its range, where it is least stable."""
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from . import ROOT, claude
from .text import SCENE_BREAK, sections

MODES = ("hybrid", "full")
VENV_PYTHON = ROOT / ".venv-emotion" / "bin" / "python"
WORKER = Path(__file__).with_name("emotion_worker.py")
READY = ROOT / "models" / "emotion" / ".ready"   # written when setup has fetched the models

PART_WORDS = 1200      # paragraphs are marked in pieces of about this many words
MIN_WORDS = 3          # a shorter line is read together with its neighbor
LONG_WORDS = 60        # a longer line is split at sentence ends (the voice drifts on long ones)
MAX_INTENSITY = 0.8    # the voice is least stable at the very top of its range
EXPRESSIVE_AT = 0.6    # hybrid: a paragraph this intense, or with dialogue, gets the expressive voice
TRIES = 3              # attempts at a line that the checker doesn't hear as written
SLOWER = 30            # the expressive voice takes roughly this many times as long as Kokoro
GAIN = {"whisper": 0.6, "normal": 1.0, "loud": 1.15}
REF_TEXT = ("The road wound north through the hills, and the travelers followed it in silence for "
            "most of the day. By evening they had reached the river, where the old ferry still "
            "crossed to the far bank.")

MARK_PROMPT = """You direct the narrator of an audiobook. You get some paragraphs of a book, each
starting with its number in square brackets, like [3].

Split each paragraph into segments: a sentence, or a few sentences that share one delivery. Put
a line of dialogue in its own segment, apart from the narration around it, except a short line
(under five words), which stays together with its speaker tag, e.g. `"Run!" she screamed.`
The segments of a paragraph, joined with single spaces, must reproduce the paragraph exactly,
character for character: don't change, add or drop anything.

For each segment give:
- "p": the paragraph's number
- "text": its exact text
- "who": "narrator", or the speaker's name if the segment is mainly dialogue
- "mood": one or two words
- "intensity": 0.0 (calm, flat) to 1.0 (screaming, peak emotion). Most narration is 0.2 to 0.5;
  keep 0.8 and above for true peaks.
- "volume": "whisper", "normal" or "loud"
- "pause": seconds of silence after the segment, 0.15 to 1.2 (longer at dramatic beats)

Return only a JSON array of segments: no prose, no code fence."""


def installed():
    return VENV_PYTHON.is_file() and READY.is_file()


# --- Marking (Claude)

def mark(blocks):
    """{paragraph: [segment, ...]} for the book's paragraphs. A paragraph whose marks don't
    match its text is left out (hybrid: Kokoro reads it; full: read in an even voice)."""
    jobs, parts = [], []
    for title, paras in sections(blocks):
        for part in chunks([p for p in paras if p != SCENE_BREAK]):
            numbered = "\n\n".join(f"[{i}] {p}" for i, p in enumerate(part, 1))
            jobs.append((title, lambda t=numbered: claude.ask(MARK_PROMPT, t)))
            parts.append(part)
    print(f"  {len(jobs)} part(s)...", file=sys.stderr)
    marks, missed = {}, 0
    for part, reply in zip(parts, claude.run_all(jobs, "mood marks")):
        segs = parse(reply)
        for i, para in enumerate(part, 1):
            mine = [s for s in segs if s["p"] == i]
            if mine and " ".join(" ".join(s["text"] for s in mine).split()) == " ".join(para.split()):
                marks[para] = tidy(mine)
            else:
                missed += 1
    if missed:
        print(f"  {missed} paragraph(s) came back unmarked or altered, so they're read without "
              f"marks", file=sys.stderr)
    return marks


def chunks(paras):
    part, words = [], 0
    for p in paras:
        if part and words + len(p.split()) > PART_WORDS:
            yield part
            part, words = [], 0
        part.append(p)
        words += len(p.split())
    if part:
        yield part


def parse(reply):
    """Claude's JSON segments, cleaned up; [] if the reply isn't usable."""
    try:
        data = json.loads(reply[reply.index("["):reply.rindex("]") + 1])
    except ValueError:
        return []
    segs = []
    for s in data if isinstance(data, list) else []:
        if not (isinstance(s, dict) and isinstance(s.get("text"), str) and s["text"].strip()):
            continue
        num = lambda k, d: float(s[k]) if isinstance(s.get(k), (int, float)) else d
        segs.append({"p": s.get("p"), "text": " ".join(s["text"].split()),
                     "who": str(s.get("who") or "narrator").strip().lower() or "narrator",
                     "intensity": min(1.0, max(0.0, num("intensity", 0.3))),
                     "volume": s.get("volume") if s.get("volume") in GAIN else "normal",
                     "pause": min(1.2, max(0.15, num("pause", 0.3)))})
    return segs


def merge(a, b):
    louder = a if a["intensity"] >= b["intensity"] else b
    return {"text": a["text"] + " " + b["text"],
            "who": a["who"] if a["who"] != "narrator" else b["who"],
            "intensity": louder["intensity"], "volume": louder["volume"], "pause": b["pause"]}


def tidy(segs):
    """Short lines joined to a neighbor, long ones split at sentence ends."""
    out = []
    for s in ({k: v for k, v in s.items() if k != "p"} for s in segs):
        if out and len(out[-1]["text"].split()) < MIN_WORDS:
            out[-1] = merge(out[-1], s)
        else:
            out.append(s)
    if len(out) > 1 and len(out[-1]["text"].split()) < MIN_WORDS:
        out[-2:] = [merge(out[-2], out[-1])]
    split = []
    for s in out:
        pieces = sentences(s["text"]) if len(s["text"].split()) > LONG_WORDS else [s["text"]]
        for k, piece in enumerate(pieces):
            split.append(dict(s, text=piece, pause=s["pause"] if k == len(pieces) - 1 else 0.25))
    return split


def sentences(text, most=LONG_WORDS // 2):
    """The text in runs of whole sentences of up to about `most` words."""
    out = []
    for sent in re.split(r'(?:(?<=[.!?…])|(?<=[.!?…]["\')\]]))\s+(?=["\'(\[]?[A-Z])', text):
        if out and len(out[-1].split()) + len(sent.split()) <= most:
            out[-1] += " " + sent
        else:
            out.append(sent)
    return out


def even(text):
    """Segments for unmarked text: an even, calm reading."""
    return [{"text": t, "who": "narrator", "intensity": 0.3, "volume": "normal", "pause": 0.25}
            for t in sentences(" ".join(text.split()))]


# --- Reading

class Expressive:
    """Reads paragraphs with the expressive voice, for speech.synthesize."""

    def __init__(self, mode, marks, voice, pronouncer, cache):
        self.mode, self.marks, self.voice, self.pron = mode, marks, voice, pronouncer
        self.cache = cache / "emotion"
        self.kokoro = None          # set by speech.synthesize: reads the narrator clip
        self.proc = None
        self.rate = 24000
        self.unclear = []           # lines still unclear after every try

    def wants(self, para):
        """Whether the expressive voice reads this paragraph (or heading, or summary)."""
        if self.mode == "full":
            return True
        segs = self.marks.get(para)
        return bool(segs) and any(s["who"] != "narrator" or s["intensity"] >= EXPRESSIVE_AT for s in segs)

    def cost(self, para):
        return len(para.split()) * (SLOWER if self.wants(para) else 1)

    def fingerprint(self, paras):
        """What changes how these paragraphs sound, for the chapter cache."""
        return [self.mode, code_version(), [self.marks.get(p) for p in paras if self.wants(p)]]

    def speak(self, para):
        """The paragraph's audio (no pause after it). Kept in the cache, so a run that stops
        partway picks up from the last finished paragraph."""
        segs = self.marks.get(para) or even(para)
        sounds = {w: self.pron.entries[w] for w in self.pron.used_in(para)} if self.pron else {}
        key = hashlib.sha1(json.dumps([code_version(), self.voice_key(), segs, sounds],
                                      ensure_ascii=False).encode()).hexdigest()
        f = self.cache / f"{key}.flac"
        if f.is_file():
            os.utime(f)
            audio, self.rate = sf.read(str(f), dtype="float32")
            return audio
        self.start()
        out = []
        for k, s in enumerate(segs):
            out.append(self.line(s) * GAIN[s["volume"]])
            if k < len(segs) - 1:
                out.append(np.zeros(int(self.rate * s["pause"]), np.float32))
        audio = np.clip(np.concatenate(out), -1, 1)
        part = f.with_suffix(".part.flac")
        sf.write(str(part), audio, self.rate)
        part.replace(f)
        return audio

    def line(self, seg):
        """One line, checked: the best of up to TRIES attempts."""
        from .speech import speakable
        text = speakable(self.pron.respell(seg["text"]) if self.pron else seg["text"])
        it = min(seg["intensity"], MAX_INTENSITY)
        best, error = None, None
        for attempt in range(TRIES):
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "line.npy"
                r = self.ask({"cmd": "say", "text": text, "exaggeration": round(0.3 + 0.7 * it, 2),
                              "cfg": round(0.55 - 0.3 * it, 2), "out": str(out),
                              "seed": int(hashlib.sha1(text.encode()).hexdigest()[:7], 16) + attempt})
                if not r.get("ok"):
                    error = r.get("error")
                    continue
                wav = trim_silences(np.load(out), self.rate)
            score = heard_right(text, r.get("heard", ""), len(wav) / self.rate)
            if score < 1:
                with open(self.cache / "checks.log", "a") as log:
                    log.write(f"try {attempt + 1}  score {score:.2f}  {len(wav) / self.rate:.1f}s\n"
                              f"  wrote: {text}\n  heard: {r.get('heard', '')}\n")
            if best is None or score > best[0]:
                best = (score, wav)
            if score >= 1:
                break
        if best is None:   # the voice failed outright: Kokoro reads the line instead
            print(f"\n  The expressive voice failed on a line ({error}); Kokoro read it instead.",
                  file=sys.stderr)
            return self.kokoro.speak(seg["text"])
        if best[0] < 1:
            self.unclear.append(seg["text"])
        return level(best[1], self.target, self.rate)

    # --- the worker process

    def voice_key(self):
        v = self.voice
        return [v.parts, v.lang, v.speed]

    def start(self):
        if self.proc:
            return
        self.cache.mkdir(parents=True, exist_ok=True)
        ref = self.cache / ("narrator-" + hashlib.sha1(json.dumps(self.voice_key()).encode()).hexdigest()[:12] + ".wav")
        clip = self.kokoro.speak(REF_TEXT)
        sf.write(str(ref), clip, self.kokoro.rate)
        self.target = speech_level(clip, self.kokoro.rate)
        self.log = open(self.cache / "voice.log", "w")
        print("\n  Starting the expressive voice...", file=sys.stderr)
        self.proc = subprocess.Popen([str(VENV_PYTHON), str(WORKER)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1)
        self.rate = self.read()["rate"]
        r = self.ask({"cmd": "voice", "path": str(ref)})
        if not r.get("ok"):
            self.fail(r.get("error"))

    def ask(self, req):
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        return self.read()

    def read(self):
        line = self.proc.stdout.readline()
        if not line:
            self.fail("it stopped unexpectedly")
        return json.loads(line)

    def fail(self, why):
        self.close()
        tail = (self.cache / "voice.log").read_text().strip().splitlines()[-5:]
        sys.exit(f"The expressive voice couldn't run: {why}\n  " + "\n  ".join(tail) +
                 "\nIf it isn't installed properly, run  ./setup.sh --emotion  again. "
                 "Finished chapters and paragraphs are saved, so the same command carries on.")

    def close(self):
        if self.proc:
            self.proc.stdin.close()
            self.proc.wait(timeout=30)
            self.proc = None
            self.log.close()

    def report(self):
        if self.unclear:
            print(f"{len(self.unclear)} line(s) may sound unclear (the check didn't hear them as "
                  f"written, even after {TRIES} tries):", file=sys.stderr)
            for t in self.unclear[:10]:
                print(f"  {t[:70]}{'...' if len(t) > 70 else ''}", file=sys.stderr)


def code_version():
    here = Path(__file__).parent
    return hashlib.sha1(b"".join((here / f).read_bytes() for f in ("emotion.py", "emotion_worker.py"))).hexdigest()[:12]


# --- Audio clean-up

def frame_levels(wav, rate, ms=10):
    n = int(rate * ms / 1000)
    frames = wav[:len(wav) // n * n].reshape(-1, n)
    return np.sqrt((frames ** 2).mean(axis=1) + 1e-12), n


def trim_silences(wav, rate, longest=0.5, keep=0.3, edge=0.03):
    """Trim silence from both ends and shorten any long silence inside the line."""
    lv, n = frame_levels(wav, rate)
    voiced = lv > lv.max() * 10 ** (-35 / 20)
    if not voiced.any():
        return wav
    first, last = np.argmax(voiced), len(voiced) - np.argmax(voiced[::-1])
    out, run = [], []
    for k in range(first, last):
        frame = wav[k * n:(k + 1) * n]
        if voiced[k]:
            if len(run) * n > longest * rate:
                run = run[:int(keep * rate / n)]
            out += run + [frame]
            run = []
        else:
            run.append(frame)
    pad = np.zeros(int(edge * rate), np.float32)
    return np.concatenate([pad] + out + [pad]).astype(np.float32)


def speech_level(wav, rate):
    lv, _ = frame_levels(wav, rate)
    return float(np.median(lv[lv > lv.max() * 0.1]))


def level(wav, target, rate):
    """Match the Kokoro narrator's loudness, so switching voices doesn't jump in volume."""
    return np.clip(wav * (target / max(speech_level(wav, rate), 1e-4)), -1, 1).astype(np.float32)


def words(text):
    return re.findall(r"[a-z0-9]+", text.lower().replace("’", "'").replace("'", ""))


def heard_right(text, heard, seconds):
    """1 when the checker heard the line as written, at a sane speed; otherwise how close it
    came, 0 to 1. Letters are compared rather than whole words, since the checker spells
    invented names its own way ('Garrett' for Garet): a near miss on a name passes, while a
    garbled or skipped line doesn't."""
    want, got = words(text), words(heard)
    if not want:
        return 1.0
    if len(want) >= 3 and not 0.12 <= seconds / len(want) <= 1.5:
        return 0.0
    close = difflib.SequenceMatcher(None, " ".join(want), " ".join(got), autojunk=False).ratio()
    return 1.0 if close >= (0.85 if len(want) > 3 else 0.75) else close * 0.99

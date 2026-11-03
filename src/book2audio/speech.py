"""Narration: Kokoro speaks each chapter, and ffmpeg packages the audio as an .m4b
audiobook with chapter markers."""
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from . import ROOT
from .text import SCENE_BREAK, is_continued, sections, short_label

MODELS = ROOT / "models"
MODEL_FILE = MODELS / "kokoro-v1.0.onnx"
VOICES_FILE = MODELS / "voices-v1.0.bin"
DEFAULT_VOICE = "am_michael"
PARAGRAPH_PAUSE = 0.45   # seconds of silence between paragraphs
CHAPTER_PAUSE = 1.5      # before and after a chapter title
SCENE_PAUSE = 1.5        # at a scene break (blank gap mid-chapter)
AUDIO_CACHE = Path.home() / "Library" / "Caches" / "book2audio" / "audio"

SUGGESTED_VOICES = ["am_michael", "am_fenrir", "am_onyx", "bm_george", "bm_fable", "bm_lewis",
                    "af_heart", "bf_emma"]
ACCENTS = {"a": "American", "b": "British"}
GENDERS = {"m": "male", "f": "female"}
SAMPLE_TEXT = ("Hello. This is the {name} voice. If you choose me, I'll read your book aloud, "
               "pausing between paragraphs and at every new chapter.")


def installed():
    import importlib.util
    return (importlib.util.find_spec("kokoro_onnx") is not None
            and MODEL_FILE.exists() and VOICES_FILE.exists())


def all_voices():
    """Kokoro's English voices, e.g. 'am_michael' (American male), 'bf_emma' (British female)."""
    with np.load(VOICES_FILE) as v:
        return sorted(n for n in v.files if n[:1] in ACCENTS and n[1:2] in GENDERS)


class Voice:
    """A narrator: one Kokoro voice, or a mix of several (their style vectors averaged by
    weight), with an accent (which pronunciation rules espeak uses) and a speaking speed."""
    def __init__(self, parts, lang, speed=1.0):
        self.parts, self.lang, self.speed = parts, lang, speed

    def __str__(self):
        name = " + ".join(f"{v} {w:.0%}" for v, w in self.parts) if len(self.parts) > 1 else self.parts[0][0]
        extras = [("American" if self.lang == "en-us" else "British") + " accent" if self.lang != default_lang(self.parts) else "",
                  f"speed {self.speed:g}" if self.speed != 1 else ""]
        return ", ".join([name] + [e for e in extras if e])


def default_lang(parts):
    """The accent of the most heavily weighted voice in the mix."""
    return "en-gb" if max(parts, key=lambda p: p[1])[0][:1] == "b" else "en-us"


def resolve_voice(spec, accent=None, speed=None):
    """--voice: 'am_michael', just 'michael', or a mix like 'lewis:70+onyx:30' (weights are
    relative, so 7+3 or 0.7+0.3 mean the same; leave them out for equal parts)."""
    voices = all_voices()
    parts = []
    for item in (spec or DEFAULT_VOICE).lower().replace(" ", "").split("+"):
        name, _, weight = item.partition(":")
        try:
            w = float(weight.rstrip("%")) if weight else 1.0
        except ValueError:
            sys.exit(f"In --voice {spec!r}: {weight!r} isn't a number (e.g. lewis:70+onyx:30)")
        if w <= 0:
            sys.exit(f"In --voice {spec!r}: each weight must be more than 0")
        parts.append((voice_name(name, voices), w))
    total = sum(w for _, w in parts)
    parts = [(v, w / total) for v, w in parts]
    lang = {"american": "en-us", "british": "en-gb", None: default_lang(parts)}[accent]
    if speed is not None and not 0.5 <= speed <= 2:
        sys.exit("--speed must be between 0.5 and 2")
    return Voice(parts, lang, speed or 1.0)


def voice_name(name, voices):
    """Accept the full name ('am_michael') or just the given name ('michael')."""
    if name in voices:
        return name
    matches = [v for v in voices if v.split("_", 1)[1] == name]
    if len(matches) == 1:
        return matches[0]
    if matches:
        sys.exit(f"{name!r} could mean {' or '.join(matches)}; give the full name.")
    sys.exit(f"Unknown voice {name!r}. See the choices with:  ./book2audio --list-voices")


def describe_voice(v):
    return f"{ACCENTS[v[0]]} {GENDERS[v[1]]}"


def list_voices():
    voices = all_voices()
    print("Suggested narrators (hear them with --voice-samples):")
    for v in SUGGESTED_VOICES:
        default = "  (default)" if v == DEFAULT_VOICE else ""
        print(f"  {v:<14} {describe_voice(v)}{default}")
    print("\nAll voices:")
    for v in voices:
        print(f"  {v:<14} {describe_voice(v)}")
    print("\nUse one with --voice, e.g.  --voice bm_george  (or just  --voice george)."
          "\nMix voices with weights, e.g.  --voice lewis:70+onyx:30 --accent american")


def speakable(text):
    """Small fixes that make neural TTS read more naturally."""
    t = re.sub(r"\s*\.\s*\.\s*\.", "…", text)                 # ". . ." -> ellipsis
    # ALL-CAPS names (ZEUS) are otherwise spelled out letter by letter
    t = re.sub(r"\b([A-Z]{3,})\b", lambda m: m.group(1) if re.fullmatch(r"[IVXLC]+", m.group(1))
               else m.group(1).capitalize(), t)
    return t


@contextlib.contextmanager
def quiet_stderr():
    """Silence output that native libraries write straight to the stderr file descriptor."""
    sys.stderr.flush()
    saved = os.dup(2)
    try:
        with open(os.devnull, "w") as null:
            os.dup2(null.fileno(), 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)


class KokoroVoice:
    def __init__(self, voice, pronouncer=None):
        import espeakng_loader
        if len(espeakng_loader.get_data_path()) >= 160:  # espeak-ng's fixed path buffer
            sys.exit(f"Kokoro can't load its pronunciation data because the folder path is too long:\n"
                     f"  {ROOT}\nMove the book2audio folder somewhere shorter (e.g. ~/book2audio) "
                     f"and run ./setup.sh again.")
        with quiet_stderr():   # onnxruntime logs a harmless telemetry warning on import
            import onnxruntime
        onnxruntime.set_default_logger_severity(3)   # errors only
        from kokoro_onnx import Kokoro
        self.k = Kokoro(str(MODEL_FILE), str(VOICES_FILE))
        if isinstance(voice, str):
            voice = Voice([(voice, 1.0)], default_lang([(voice, 1.0)]))
        self.voice = (voice.parts[0][0] if len(voice.parts) == 1
                      else sum(w * self.k.get_voice_style(v) for v, w in voice.parts))
        self.lang, self.speed = voice.lang, voice.speed
        self.pronouncer = pronouncer   # --pronunciations: exact sounds for listed words (pronounce.py)
        self.rate = 24000

    def speak(self, text):
        text = speakable(text)
        opts = dict(voice=self.voice, lang=self.lang, speed=self.speed)
        if self.pronouncer:
            phonemes = self.pronouncer.phonemes(text, self.k.tokenizer, self.lang)
            if not phonemes.strip():
                return self.silence(0.1)
            samples, self.rate = self.k.create(phonemes, is_phonemes=True, **opts)
        else:
            samples, self.rate = self.k.create(text, **opts)
        return np.asarray(samples, dtype=np.float32)

    def silence(self, seconds):
        return np.zeros(int(self.rate * seconds), dtype=np.float32)


def render_section(engine, heading, paragraphs, wav_path, progress):
    """Speak one chapter (its heading, if given, then the paragraphs) into a WAV file and
    return its length in seconds."""
    chunks = []
    if heading:
        chunks += [engine.silence(CHAPTER_PAUSE), engine.speak(heading + "."), engine.silence(CHAPTER_PAUSE)]
    for para in paragraphs:
        if para == SCENE_BREAK:
            chunks.append(engine.silence(SCENE_PAUSE))
            continue
        chunks += [engine.speak(para), engine.silence(PARAGRAPH_PAUSE)]
        progress(len(para.split()))
    audio = np.concatenate(chunks) if chunks else engine.silence(PARAGRAPH_PAUSE)
    sf.write(str(wav_path), audio, engine.rate)
    return len(audio) / engine.rate


def narration_plan(blocks, summaries):
    """The audiobook's chapters, in order: [(chapter title, spoken heading or None, paragraphs)].
    With summaries, each chapter is followed by its own 'Chapter 33 – Summary' chapter,
    which a listener can skip like any other."""
    summaries = dict(summaries or [])
    plan = []
    for title, paras in sections(blocks):
        plan.append((title, None if is_continued(title) else title, paras))
        if title in summaries:
            label = short_label(title)
            plan.append((f"{label} – Summary", f"{label}, summary", [summaries[title]]))
    return plan


def synthesize(blocks, out_path, voice, tags, summaries=None, pronouncer=None):
    """Narrate the blocks into an .m4b at out_path. Returns [(chapter title, start, end)].
    Each chapter's audio is kept in the cache, so making the audiobook again (say, after
    fixing a line in the transcript) only re-reads the chapters that changed."""
    if isinstance(voice, str):
        voice = Voice([(voice, 1.0)], default_lang([(voice, 1.0)]))
    plan = narration_plan(blocks, summaries)
    AUDIO_CACHE.mkdir(parents=True, exist_ok=True)
    cached = [AUDIO_CACHE / f"{section_key(voice, pronouncer, *s)}.flac" for s in plan]
    todo = [i for i, f in enumerate(cached) if not f.is_file()]
    if len(todo) < len(plan):
        print(f"  Reusing {len(plan) - len(todo)} of {len(plan)} chapters from an earlier run",
              file=sys.stderr)
    total_words = sum(len(p.split()) for i in todo for p in plan[i][2])
    done, started = [0], time.time()

    def progress(words):
        before = done[0] / max(1, total_words)
        done[0] += words
        frac = done[0] / max(1, total_words)
        if not sys.stderr.isatty() and int(frac * 10) == int(before * 10):
            return  # in a log, only report every 10%
        eta = (time.time() - started) * (1 - frac) / max(frac, 1e-6)
        print(f"\r  {frac:4.0%} spoken, about {eta / 60:.0f} min left   ", end="", file=sys.stderr, flush=True)

    engine = KokoroVoice(voice, pronouncer) if todo else None
    with tempfile.TemporaryDirectory() as tmp:
        chapters, t = [], 0.0
        for i, ((title, heading, paras), f) in enumerate(zip(plan, cached)):
            if i in todo:
                part = f.with_suffix(".part.flac")   # renamed when complete, so a stopped run leaves no half file
                render_section(engine, heading, paras, part, progress)
                part.replace(f)
            else:
                os.utime(f)   # recently used, so prune_audio_cache keeps it
            dur = sf.info(str(f)).duration
            chapters.append((title, t, t + dur))
            t += dur
        if todo:
            print(file=sys.stderr)
        encode_with_chapters(cached, chapters, out_path, tags, Path(tmp))
    prune_audio_cache()
    return chapters


def section_key(voice, pronouncer, title, heading, paras):
    """Everything that changes how a chapter sounds: its text, the voice and the
    pronunciations of the listed words it contains."""
    text = "\n".join([heading or ""] + paras)
    words = pronouncer.used_in(text) if pronouncer else []
    sounds = {w: pronouncer.entries[w] for w in words} if pronouncer else {}
    key = [narration_code(), MODEL_FILE.stat().st_size, voice.parts, voice.lang, voice.speed,
           pronouncer is not None, text, sounds]
    return hashlib.sha1(json.dumps(key, ensure_ascii=False, default=str).encode()).hexdigest()


def narration_code():
    """A fingerprint of the code that turns text into sound, so an update to it (a new
    speaking rule, different pauses) re-reads cached chapters instead of reusing them."""
    here = Path(__file__).parent
    return hashlib.sha1(b"".join((here / f).read_bytes() for f in ("speech.py", "pronounce.py"))).hexdigest()


def prune_audio_cache(days=30):
    """Chapter audio not used for a month goes (about 80 MB per hour of audio)."""
    cutoff = time.time() - days * 86400
    for f in AUDIO_CACHE.glob("*.flac"):
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)


def write_voice_samples():
    """A short clip per suggested narrator in voice_samples/, to pick a --voice by ear."""
    out = ROOT / "voice_samples"
    out.mkdir(exist_ok=True)
    for v in SUGGESTED_VOICES:
        engine = KokoroVoice(v)
        f = out / f"{v}.wav"
        sf.write(str(f), engine.speak(SAMPLE_TEXT.format(name=v.split("_")[1].capitalize())), engine.rate)
        print(f"  {f}  ({describe_voice(v)})", file=sys.stderr)
    subprocess.run(["open", str(out)])


def encode_with_chapters(files, chapters, out_path, tags, tmp):
    """Concatenate the section files and encode an AAC audiobook with chapter markers
    (QuickTime chapter track, which Apple Books, BookPlayer and CarPlay show)."""
    listing = tmp / "files.txt"
    listing.write_text("".join(f"file '{f}'\n" for f in files))
    meta = tmp / "chapters.txt"
    esc = lambda v: re.sub(r"([=;#\\\n])", r"\\\1", v)
    lines = [";FFMETADATA1", "genre=Audiobook"] + [f"{k}={esc(v)}" for k, v in tags.items() if v]
    for name, start, end in chapters:
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(start * 1000)}",
                  f"END={int(end * 1000)}", f"title={esc(name)}"]
    meta.write_text("\n".join(lines) + "\n")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-i", str(meta), "-map", "0:a", "-map_metadata", "1", "-map_chapters", "1",
                    "-c:a", "aac", "-b:a", "64k", "-ac", "1",
                    "-f", "ipod" if out_path.suffix.lower() == ".m4b" else "mp4",
                    str(out_path)], check=True)

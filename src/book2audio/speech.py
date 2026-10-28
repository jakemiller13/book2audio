"""Narration: Kokoro speaks each chapter, and ffmpeg packages the audio as an .m4b
audiobook with chapter markers."""
import contextlib
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
from .text import SCENE_BREAK, SUBHEADING, is_continued, sections, short_label

MODELS = ROOT / "models"
MODEL_FILE = MODELS / "kokoro-v1.0.onnx"
VOICES_FILE = MODELS / "voices-v1.0.bin"
DEFAULT_VOICE = "am_michael"
PARAGRAPH_PAUSE = 0.45   # seconds of silence between paragraphs
CHAPTER_PAUSE = 1.5      # before and after a chapter title
SCENE_PAUSE = 1.5        # at a scene break (blank gap mid-chapter)

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


def resolve_voice(name):
    """Accept the full name ('am_michael') or just the given name ('michael')."""
    name = (name or DEFAULT_VOICE).strip().lower()
    voices = all_voices()
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
    print("\nUse one with --voice, e.g.  --voice bm_george  (or just  --voice george)")


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
    def __init__(self, voice, normalize=None):
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
        self.voice = voice
        self.normalize = normalize or speakable
        self.lang = "en-gb" if voice[:1] == "b" else "en-us"
        self.rate = 24000

    def speak(self, text):
        samples, self.rate = self.k.create(self.normalize(text), voice=self.voice, lang=self.lang)
        return np.asarray(samples, dtype=np.float32)

    def silence(self, seconds):
        return np.zeros(int(self.rate * seconds), dtype=np.float32)


def as_heading(text):
    """A heading read as its own sentence: 'Model domain' -> 'Model domain.'"""
    return text.rstrip(" .:") + "."


def render_section(engine, heading, paragraphs, wav_path, progress):
    """Speak one chapter (its heading, if given, then the paragraphs) into a WAV file and
    return its length in seconds."""
    chunks = []
    if heading:
        chunks += [engine.silence(CHAPTER_PAUSE), engine.speak(as_heading(heading)), engine.silence(CHAPTER_PAUSE)]
    for para in paragraphs:
        if para == SCENE_BREAK:
            chunks.append(engine.silence(SCENE_PAUSE))
            continue
        if para.startswith(SUBHEADING):
            chunks += [engine.silence(SCENE_PAUSE), engine.speak(as_heading(para[len(SUBHEADING):])),
                       engine.silence(PARAGRAPH_PAUSE)]
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


def synthesize(blocks, out_path, voice, tags, summaries=None, normalize=None):
    """Narrate the blocks into an .m4b at out_path. Returns [(chapter title, start, end)]."""
    plan = narration_plan(blocks, summaries)
    total_words = sum(len(p.split()) for _, _, paras in plan for p in paras)
    done, started = [0], time.time()

    def progress(words):
        before = done[0] / max(1, total_words)
        done[0] += words
        frac = done[0] / max(1, total_words)
        if not sys.stderr.isatty() and int(frac * 10) == int(before * 10):
            return  # in a log, only report every 10%
        eta = (time.time() - started) * (1 - frac) / max(frac, 1e-6)
        print(f"\r  {frac:4.0%} spoken, about {eta / 60:.0f} min left   ", end="", file=sys.stderr, flush=True)

    engine = KokoroVoice(voice, normalize)
    with tempfile.TemporaryDirectory() as tmp:
        files, chapters, t = [], [], 0.0
        for i, (title, heading, paras) in enumerate(plan):
            f = Path(tmp) / f"s{i:03d}.wav"
            dur = render_section(engine, heading, paras, f, progress)
            files.append(f)
            chapters.append((title, t, t + dur))
            t += dur
        print(file=sys.stderr)
        encode_with_chapters(files, chapters, out_path, tags, Path(tmp))
    return chapters


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

"""Inputs and OCR: collect the videos and photos, run the Apple Vision helper over them in
parallel, and cache the results."""
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import ROOT

OCR_SRC = Path(__file__).with_name("ocr.swift")
OCR_BIN = ROOT / "ocr"
CACHE_DIR = Path.home() / "Library" / "Caches" / "book2audio"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".tif", ".tiff"}
VIDEO_EXT = {".mov", ".mp4", ".m4v"}


def collect_inputs(args):
    """Expand folders into the images and videos inside them, in name order."""
    paths = []
    for a in args:
        p = Path(a).expanduser()
        if p.is_dir():
            found = sorted(q for q in p.iterdir() if q.suffix.lower() in IMAGE_EXT | VIDEO_EXT)
            if not found:
                sys.exit(f"No images or videos in {a}")
            paths += found
        elif not p.exists():
            sys.exit(f"Not found: {a}")
        elif p.suffix.lower() not in IMAGE_EXT | VIDEO_EXT:
            sys.exit(f"Not an image or video book2audio can read: {a}\n"
                     f"(it reads {', '.join(sorted(IMAGE_EXT | VIDEO_EXT))})")
        else:
            paths.append(p)
    return paths


def ensure_ocr_binary():
    """Compile the Swift OCR helper if it's missing or older than its source."""
    if not OCR_BIN.exists() or OCR_BIN.stat().st_mtime < OCR_SRC.stat().st_mtime:
        print("Compiling OCR helper...", file=sys.stderr)
        if subprocess.run(["swiftc", "-O", "-suppress-warnings", str(OCR_SRC), "-o", str(OCR_BIN)]).returncode:
            sys.exit("Couldn't compile the OCR helper. Install the Xcode Command Line Tools "
                     "(xcode-select --install) and run ./setup.sh again.")


def run_ocr(paths, video_every):
    """OCR every input, caching results so re-runs (a different voice, say) skip straight
    to the audio. Vision works one image at a time per process, so the work is spread over
    several OCR processes: photos one per process, videos split by frame (every Nth)."""
    workers = max(1, min(8, (os.cpu_count() or 4) - 2))

    def cache_path(path):
        st = path.stat()
        every = video_every if path.suffix.lower() in VIDEO_EXT else ""
        key = f"{path.resolve()}|{st.st_size}|{st.st_mtime}|{every}|{OCR_BIN.stat().st_mtime}"
        return CACHE_DIR / (hashlib.sha1(key.encode()).hexdigest() + ".jsonl")

    def ocr(job):
        path, cmd = job
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"OCR failed on {path}: {r.stderr.strip()[:300] or f'exit code {r.returncode}'}")
        return r.stdout

    jobs = []   # (path, command)
    for path in paths:
        if cache_path(path).exists():
            continue
        if path.suffix.lower() in VIDEO_EXT:
            jobs += [(path, [str(OCR_BIN), "--video", str(path), "--every", str(video_every),
                             "--shard", f"{k}/{workers}"]) for k in range(workers)]
        else:
            jobs.append((path, [str(OCR_BIN), str(path)]))
    with ThreadPoolExecutor(workers) as pool:
        outs = list(pool.map(ocr, jobs))
    by_path = {}
    for (path, _), out in zip(jobs, outs):
        by_path.setdefault(path, []).extend(json.loads(l) for l in out.splitlines() if l.strip())
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for path, recs in by_path.items():
        recs.sort(key=lambda r: r.get("time") or 0)  # stable: keeps pass order per frame
        cache_path(path).write_text("".join(json.dumps(r) + "\n" for r in recs))

    records = []
    for path in paths:
        records += [json.loads(l) for l in cache_path(path).read_text().splitlines() if l.strip()]
    return records

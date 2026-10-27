#!/usr/bin/env python3
"""book2audio: photos / screenshots / video of book pages -> audiobook for iPhone.

Pipeline: Apple Vision OCR (ocr helper) -> split two-page spreads -> drop running
heads and page numbers -> rebuild paragraphs -> macOS `say` -> .m4b audiobook.

Usage:
  book2audio.py INPUT [INPUT ...] -o "Chapter 32.m4b" [--voice Samantha] [--rate 185]
INPUT can be image files, a folder of images, or video files (.mov/.mp4).
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
OCR_BIN = HERE / "ocr"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".tif", ".tiff"}
VIDEO_EXT = {".mov", ".mp4", ".m4v"}
SCENE_BREAK = "* * *"
_WORDS = None


def word_ratio(text):
    """Fraction of tokens that are dictionary words. Motion blur produces 'rh¢ loSE 5unl<55'."""
    global _WORDS
    if _WORDS is None:
        try:
            _WORDS = {w.strip().lower() for w in open("/usr/share/dict/words")}
        except OSError:
            _WORDS = set()
    if not _WORDS:
        return 1.0
    toks = [re.sub(r"^[^A-Za-z]+|[^A-Za-z]+$", "", t) for t in text.split()]
    toks = [t.lower() for t in toks if t]
    if not toks:
        return 0.0

    def known(w):
        if w in _WORDS:
            return True
        for suf in ("'s", "s", "es", "ed", "d", "ing", "ly", "n't", "'ll", "'re", "'ve", "'d"):
            if w.endswith(suf) and (w[:-len(suf)] in _WORDS or w[:-len(suf)] + "e" in _WORDS):
                return True
        return False
    return sum(known(t) for t in toks) / len(toks)


def legible(line):
    return len(line["text"].split()) < 3 or word_ratio(line["text"]) >= 0.3


# ---------------------------------------------------------------- OCR

def ensure_ocr_binary():
    src = HERE / "ocr.swift"
    if not OCR_BIN.exists() or OCR_BIN.stat().st_mtime < src.stat().st_mtime:
        print("Compiling OCR helper...", file=sys.stderr)
        subprocess.run(["swiftc", "-O", str(src), "-o", str(OCR_BIN)], check=True,
                       stderr=subprocess.DEVNULL)


CACHE_DIR = Path.home() / "Library" / "Caches" / "book2audio"


def run_ocr(paths, video_every):
    """OCR every input, caching results so re-runs (a different voice, say) skip straight
    to the audio. Vision works one image at a time per process, so the work is spread over
    several OCR processes: photos one per process, videos split by frame (every Nth)."""
    import hashlib
    from concurrent.futures import ThreadPoolExecutor
    workers = max(1, min(8, (os.cpu_count() or 4) - 2))

    def cache_path(path):
        st = path.stat()
        every = video_every if path.suffix.lower() in VIDEO_EXT else ""
        key = f"{path.resolve()}|{st.st_size}|{st.st_mtime}|{every}|{OCR_BIN.stat().st_mtime}"
        return CACHE_DIR / (hashlib.sha1(key.encode()).hexdigest() + ".jsonl")

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
        outs = list(pool.map(lambda j: subprocess.run(j[1], check=True, capture_output=True,
                                                      text=True).stdout, jobs))
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


# ---------------------------------------------------------------- layout

def find_gutter(lines):
    """x position of the book's spine: the vertical strip crossed by the fewest text lines,
    with real text on both sides (so the empty desk beside the book doesn't count).
    A line only "crosses" if it reaches 1% past the strip on both sides: lines curling
    into the spine often overshoot it slightly. Same rule as findGutter in ocr.swift.
    Returns None for a single page."""
    long = [l for l in lines if len(l["text"]) > 15]
    if len(long) < 6:
        return None
    counts = []
    for i in range(20, 81):
        x = i / 100
        left = sum(1 for l in long if (l["x0"] + l["x1"]) / 2 < x)
        if left < 3 or len(long) - left < 3:
            continue
        counts.append((x, sum(1 for l in lines if l["x0"] < x - 0.01 and x + 0.01 < l["x1"])))
    if not counts:
        return None
    min_n = min(n for _, n in counts)
    if min_n > max(1, len(lines) // 20):
        return None
    xs = [x for x, n in counts if n == min_n]
    return xs[len(xs) // 2]


def looks_two_column(lines):
    """Most lines span only about half the text width: two pages side by side."""
    long = [l for l in lines if len(l["text"]) > 25]
    if len(long) < 10:
        return False
    span = max(l["x1"] for l in long) - min(l["x0"] for l in long)
    return statistics.median(l["x1"] - l["x0"] for l in long) < 0.6 * span


def in_text_column(lines):
    """Drop text outside the page's column: a keyboard, another book, the desk."""
    long = [l for l in lines if len(l["text"]) > 25]
    if len(long) < 5:
        return lines
    x0s = sorted(l["x0"] for l in long)
    x1s = sorted(l["x1"] for l in long)
    left, right = x0s[len(x0s) // 10], x1s[len(x1s) * 9 // 10]
    return [l for l in lines if l["x0"] >= left - 0.02 and l["x1"] <= right + 0.02]


def cut_by_crop(line, rec):
    """True if a one-page crop sliced this line at the crop boundary."""
    if not rec.get("crop"):
        return False
    a, b = rec["crop"]
    return (a > 0 and line["x0"] - a < 0.006) or (b < 1 and b - line["x1"] < 0.006)


def assemble_spreads(records):
    """Group OCR passes of the same photo/frame and pick the best pass for each page."""
    frames = {}
    for r in records:
        frames.setdefault((r["source"], r.get("time")), {})[r.get("pass", "full")] = r
    pages = []
    for (source, time), passes in frames.items():
        full = [l for l in passes["full"]["lines"] if l["text"].strip()]
        gutter = find_gutter(full)
        if gutter is None and time is not None and looks_two_column(full):
            continue  # a spread we couldn't split (camera shake, page mid-turn); other frames cover it
        sides = [(0, None, 1.0)] if gutter is None else [(0, 0.0, gutter), (1, gutter, 1.0)]
        for side, lo, hi in sides:
            lo = 0.0 if lo is None else lo
            best = None
            for name, rec in passes.items():
                if gutter is None and name != "full":
                    continue
                if name == "fast" or name == "left" and side == 1 or name == "right" and side == 0:
                    continue
                cand = [l for l in rec["lines"] if l["text"].strip() and legible(l)
                        and lo <= (l["x0"] + l["x1"]) / 2 < hi and not cut_by_crop(l, rec)]
                score = sum(len(l["text"]) * l["conf"] for l in cand)
                if best is None or score > best[0]:
                    best = (score, cand)
            if not best:
                continue
            best = (best[0], drop_overlaps(in_text_column(best[1])))
            if len(best[1]) < 3:
                continue
            ratios = [word_ratio(l["text"]) for l in best[1] if len(l["text"].split()) >= 4]
            legibility = statistics.mean(ratios) if ratios else 1.0
            if legibility < 0.75:
                continue  # blurry frame (page turning, camera moving)
            # Fast-pass text is noisier, so only take gap fillers that read as real words.
            lines = best[1] + [f for f in gap_fillers(passes.get("fast"), best[1], lo, hi)
                               if word_ratio(f["text"]) >= 0.7]
            ys = sorted(l["ly"] for l in lines)
            pages.append({"source": source, "time": time, "side": side, "lines": lines,
                          "legibility": legibility,
                          "top": min(l["y0"] for l in lines),
                          "spacing": statistics.median(b - a for a, b in zip(ys, ys[1:]))})
    return pages


def drop_overlaps(lines):
    """Vision occasionally reads one printed line twice (the full line plus a garbled
    fragment on top of it). Keep the stronger read."""
    if len(lines) < 3:
        return lines
    ys = sorted(l["ly"] for l in lines)
    spacing = statistics.median(b - a for a, b in zip(ys, ys[1:]))
    strength = lambda l: l["conf"] * len(l["text"])
    keep = []
    for l in lines:
        dup = False
        if l["conf"] >= 1.0:   # only second-guess reads Vision itself was unsure of
            keep.append(l)
            continue
        for o in lines:
            if o is l or strength(o) <= strength(l):
                continue
            lo, hi = max(l["lx"], o["lx"]), min(l["rx"], o["rx"])
            if hi - lo > 0.5 * (l["rx"] - l["lx"]) and abs(y_at(o, (lo + hi) / 2) - y_at(l, (lo + hi) / 2)) < 0.4 * spacing:
                dup = True
                break
        if not dup:
            keep.append(l)
    return keep


def gap_fillers(fast, lines, lo, hi):
    """Lines only the fast recognizer found (e.g. a lone "gods." the accurate pass skipped).
    Fast-pass coordinates are offset from the accurate ones, so we match lines by text and
    slot each unmatched fast line between the accurate lines its neighbours matched."""
    import difflib
    import functools
    if not fast or len(lines) < 5:
        return []
    side = [f for f in fast["lines"] if lo <= (f["x0"] + f["x1"]) / 2 < hi]
    side.sort(key=functools.cmp_to_key(above))

    def match(f):
        t = f["text"].lower()
        best, best_cover = None, 0.0
        for l in lines:
            m = difflib.SequenceMatcher(None, t, l["text"].lower(), autojunk=False)
            cover = sum(b.size for b in m.get_matching_blocks() if b.size >= 3) / max(1, len(t))
            if cover > best_cover:
                best, best_cover = l, cover
        return best if best_cover >= 0.6 else None

    ys = sorted(l["ly"] for l in lines)
    spacing = statistics.median(b - a for a, b in zip(ys, ys[1:]))
    matched = [match(f) for f in side]
    reading_order = sorted(lines, key=functools.cmp_to_key(above))
    rank = {id(l): i for i, l in enumerate(reading_order)}
    extra = []
    for i, f in enumerate(side):
        if matched[i] is not None or f["conf"] < 0.5 or sum(c.isalpha() for c in f["text"]) < 3:
            continue
        prev = next((matched[j] for j in range(i - 1, -1, -1) if matched[j]), None)
        nxt = next((matched[j] for j in range(i + 1, len(side)) if matched[j]), None)
        if prev is None or nxt is None or prev is nxt or min(len(prev["text"]), len(nxt["text"])) < 25:
            continue  # only fill gaps between ordinary body lines (not headings)
        if rank[id(nxt)] - rank[id(prev)] > 1:
            continue  # the accurate pass has something in between (e.g. the line split in two)
        x = (f["lx"] + f["rx"]) / 2
        if y_at(nxt, x) - y_at(prev, x) < 1.6 * spacing:
            continue  # no empty line slot here: it's a fragment of a line we already have
        dy = (nxt["ly"] - prev["ly"]) / 2
        extra.append({**f, "text": f["text"].rstrip(",;"),
                      "ly": prev["ly"] + dy, "ry": prev["ry"] + dy,
                      "y0": prev["y0"] + dy, "y1": prev["y1"] + dy,
                      "lx": f["lx"], "rx": f["rx"]})
    return extra


def y_at(line, x):
    """Height of a (possibly tilted) line at horizontal position x."""
    if line["rx"] - line["lx"] < 1e-6:
        return line["ly"]
    t = (x - line["lx"]) / (line["rx"] - line["lx"])
    return line["ly"] + t * (line["ry"] - line["ly"])


def above(a, b):
    """Compare two lines at a shared x position, so page curl near the spine doesn't
    reorder them. Returns -1 if a is above b."""
    lo, hi = max(a["lx"], b["lx"]), min(a["rx"], b["rx"])
    x = (lo + hi) / 2 if lo < hi else (a["lx"] + b["lx"]) / 2
    return -1 if y_at(a, x) < y_at(b, x) else 1


def group_visual_rows(lines):
    """Order lines top-to-bottom, merging fragments Vision split off the same printed line."""
    import functools
    lines = sorted(lines, key=lambda l: l["ly"])
    tol = 0.009
    rows = []
    for l in lines:
        for row in reversed(rows[-4:]):
            tail, head = row["parts"][-1], row["parts"][0]
            if l["lx"] >= tail["rx"] - 0.012 and abs(y_at(tail, l["lx"]) - l["ly"]) < tol:
                row["parts"].append(l)
                break
            if l["rx"] <= head["lx"] + 0.012 and abs(y_at(head, l["rx"]) - l["ry"]) < tol:
                row["parts"].insert(0, l)
                break
        else:
            rows.append({"parts": [l]})
    out = []
    for row in rows:
        parts = row["parts"]
        out.append({
            "text": " ".join(p["text"].strip() for p in parts),
            "conf": min(p["conf"] for p in parts),
            "lx": parts[0]["lx"], "ly": parts[0]["ly"],
            "rx": parts[-1]["rx"], "ry": parts[-1]["ry"],
        })
    out.sort(key=functools.cmp_to_key(above))
    return out


def robust_fit(ys, xs, deg=2):
    """Fit x = f(y) ignoring outliers (indented lines). Handles page curvature."""
    import numpy as np
    ys, xs = np.asarray(ys), np.asarray(xs)
    keep = np.ones(len(xs), bool)
    for _ in range(4):
        if keep.sum() <= deg + 1:
            break
        coef = np.polyfit(ys[keep], xs[keep], deg)
        resid = xs - np.polyval(coef, ys)
        keep = np.abs(resid) < max(0.004, 2.5 * np.median(np.abs(resid[keep])))
    if keep.sum() <= deg + 1:
        return lambda y: float(np.median(xs))
    coef = np.polyfit(ys[keep], xs[keep], deg)
    return lambda y: float(np.polyval(coef, y))


def analyze_page(page, running_heads):
    """Pull out page number / running head / chapter heading; mark paragraph starts."""
    page["number"] = None
    page["chapter"], heading_lines = detect_chapter_opening(page)
    lines = []
    for l in page["lines"]:
        if any(l is h for h in heading_lines):
            continue
        t = l["text"].strip()
        if in_header(l, page) and re.fullmatch(r"\d{1,4}", t):
            page["number"] = int(t)
        elif in_header(l, page) and is_running_head(t, running_heads):
            pass
        else:
            lines.append(l)

    # Drop specks outside the text block (show-through, fingers, page edges).
    long_lines = [l for l in lines if len(l["text"]) > 25]
    if len(long_lines) >= 5:
        left = robust_fit([l["ly"] for l in long_lines], [l["lx"] for l in long_lines])
        right = robust_fit([l["ry"] for l in long_lines], [l["rx"] for l in long_lines])
        lines = [l for l in lines
                 if l["rx"] > left(l["ly"]) + 0.01 and l["lx"] < right(l["ry"]) - 0.01]
    body = group_visual_rows(lines)

    if page["chapter"] and body:
        body[0]["para_start"] = True

    if len(body) >= 4:
        left = robust_fit([r["ly"] for r in body], [r["lx"] for r in body])
        right = robust_fit([r["ry"] for r in body], [r["rx"] for r in body])
        spacing = statistics.median(b["ly"] - a["ly"] for a, b in zip(body, body[1:]))
        indent = max(0.006, 0.4 * spacing)     # a paragraph indent is roughly 1 em
        for r in body:
            # A row starting left of the margin usually has a speck glued on ("ber", "©")
            if left(r["ly"]) - r["lx"] > indent and re.match(r"^\S{1,3}\s+[\"'A-Z]", r["text"].strip()):
                r["text"] = re.sub(r"^\S{1,3}\s+", "", r["text"].strip())
                r["lx"] = left(r["ly"])
        resid = [r["lx"] - left(r["ly"]) for r in body]
        for i, r in enumerate(body):
            # Compare against nearby lines too: the margin fit drifts where the page curls.
            near = sorted(resid[max(0, i - 3):i] + resid[i + 1:i + 4])
            local = near[1] if len(near) > 2 else (near[0] if near else 0.0)
            r["indented"] = resid[i] - max(min(local, 0.0), local) > indent and resid[i] > indent / 2
            r["short"] = right(r["ry"]) - r["rx"] > max(0.03, 3 * indent)
            if r["indented"]:
                r["para_start"] = True
            elif i > 0 and body[i - 1]["short"]:
                r["para_start"] = True
    # A tall blank gap mid-page is a scene break, unless what follows is a lone speck
    # far below the text (a finger, the next page's edge).
    for i in range(len(body) - 1, 0, -1):
        gap = (body[i]["ly"] - body[i - 1]["ly"]) / page["spacing"]
        if gap > 2.5:
            if i == len(body) - 1 and len(body[i]["text"]) < 15:
                body.pop()
            else:
                body[i]["scene_break"] = body[i]["para_start"] = True
    page["body"] = body
    page["quality"] = sum(len(r["text"]) * r["conf"] for r in body) * page["legibility"] ** 2
    return page


CHAPTER_RE = re.compile(r"(?i)^\s*(chapter|prologue|epilogue|interlude|part)\b")


def detect_chapter_opening(page):
    """A chapter-opening page: a "Chapter N" line near the top, an optional title block,
    then a tall blank band before the body text. Returns (title or None, heading lines)."""
    lines = sorted(page["lines"], key=lambda l: l["ly"])
    sp = page["spacing"]
    start = next((i for i, l in enumerate(lines[:3]) if CHAPTER_RE.match(l["text"])), None)
    if start is None:
        return None, []
    block = [lines[start]]
    for l in lines[start + 1:]:
        if l["ly"] - block[-1]["ly"] > 3 * sp or len(block) > 4:
            break
        block.append(l)
    rest = lines[start + len(block):]
    if not rest or rest[0]["ly"] - block[-1]["ly"] < 4 * sp:
        return None, []  # no blank band: just a sentence that starts with "Chapter"
    label = smart_title(block[0]["text"].strip())
    title = smart_title(" ".join(l["text"].strip() for l in block[1:]))
    return (f"{label}: {title}" if title else label), block


def in_header(line, page):
    """Running heads and page numbers sit in the first line or two at the top of the page."""
    return line["y0"] < page["top"] + 2 * page["spacing"]


def smart_title(s):
    small = {"and", "of", "the", "a", "an", "in", "on", "to", "for"}
    words = s.lower().split()
    return " ".join(w if (i and w in small) else w.capitalize() for i, w in enumerate(words))


def norm(t):
    return re.sub(r"[^a-z]", "", t.lower())


def is_running_head(text, heads):
    """Exact or close match: OCR misreads heads ("Christopner noocchio")."""
    import difflib
    n = norm(text)
    return n in heads or any(difflib.SequenceMatcher(None, n, h).ratio() > 0.75 for h in heads)


def detect_running_heads(pages):
    """Text repeated in the header zone of *different* pages (book title, author).
    Video has many frames of each page, so first group the views into pages by their
    text, then require a candidate (allowing OCR variants) to top at least two pages."""
    import difflib
    page_ids, sigs = [], []
    for p in pages:
        words = re.findall(r"[a-z]+", " ".join(l["text"] for l in p["lines"]).lower())
        sig = {tuple(words[i:i + 3]) for i in range(len(words) - 2)}
        pid = next((j for j, s in enumerate(sigs)
                    if sig and s and len(sig & s) / min(len(sig), len(s)) > 0.4), None)
        if pid is None:
            sigs.append(set(sig))
            pid = len(sigs) - 1
        else:
            sigs[pid] |= sig
        page_ids.append(pid)

    groups = []   # [{variants}, {page ids}]
    for p, pid in zip(pages, page_ids):
        for r in p["lines"]:
            t = r["text"].strip()
            n = norm(t)
            if not in_header(r, p) or re.fullmatch(r"\d{1,4}", t) or not n or CHAPTER_RE.match(t):
                continue
            g = next((g for g in groups if any(difflib.SequenceMatcher(None, n, v).ratio() > 0.75
                                               for v in g[0])), None)
            if g is None:
                groups.append([{n}, {pid}])
            else:
                g[0].add(n)
                g[1].add(pid)
    return {v for variants, pids in groups if len(pids) >= 2 for v in variants}


# ---------------------------------------------------------------- ordering / dedupe

def shingles(page):
    words = re.findall(r"[a-z]+", " ".join(r["text"] for r in page["body"]).lower())
    return {tuple(words[i:i + 3]) for i in range(len(words) - 2)}


def order_pages(pages):
    """Group views of the same page (several photos / video frames) by shared text,
    keep the best view of each, and sort by page number (inferred where unreadable)."""
    # 1. Cluster by text: two views are the same page if they share most word triples.
    clusters = []
    for idx, p in enumerate(pages):
        p["shingles"] = shingles(p)
        if len(p["shingles"]) < 3:
            continue  # a sliver of a page mid-turn
        best, best_score = None, 0.0
        for c in clusters:
            score = len(p["shingles"] & c["shingles"]) / min(len(p["shingles"]), len(c["shingles"]))
            if score > best_score:
                best, best_score = c, score
        if best is not None and best_score > 0.4:
            best["members"].append(p)
            best["shingles"] |= p["shingles"]
        else:
            clusters.append({"members": [p], "shingles": set(p["shingles"]), "first": idx})
    for c in clusters:
        for m in c["members"]:
            m["cluster"] = c

    # Misread page numbers ("867" for 267) would scramble the order. A real number has
    # support: some *other* page nearby in the numbering. (Not "close to the median":
    # several recordings can cover separate stretches of the book.)
    read = [(p["number"], id(p["cluster"])) for p in pages if p.get("cluster") and p["number"] is not None]
    if len({cid for _, cid in read}) > 1:
        for p in pages:
            if p.get("cluster") and p["number"] is not None and not any(
                    cid != id(p["cluster"]) and 0 < abs(n - p["number"]) <= 4 for n, cid in read):
                p["number"] = None

    # 2. Vote on each page's number: its own readings, plus the facing page's readings
    #    +/- 1 (a frame showing 276|277 is evidence for both).
    votes = {id(c): Counter() for c in clusters}
    for p in pages:
        if p.get("cluster") and p["number"] is not None:
            votes[id(p["cluster"])][p["number"]] += 1
    frames = {}
    for p in pages:
        if p.get("cluster"):
            frames.setdefault((p["source"], p["time"]), []).append(p)
    for spread in frames.values():
        if len(spread) != 2:
            continue
        l, r = sorted(spread, key=lambda p: p["side"])
        if l["cluster"] is r["cluster"]:
            continue
        if r["number"] is not None:
            votes[id(l["cluster"])][r["number"] - 1] += 1
        if l["number"] is not None:
            votes[id(r["cluster"])][l["number"] + 1] += 1
    for c in clusters:
        v = votes[id(c)]
        c["number"] = v.most_common(1)[0][0] if v else None

    # 3. Anything still unnumbered follows whatever came just before it in the input.
    clusters.sort(key=lambda c: c["first"])
    if any(c["number"] is not None for c in clusters):
        for prev, c in zip(clusters, clusters[1:]):
            if c["number"] is None and prev["number"] is not None:
                c["number"] = prev["number"] + 1
        first_known = next(i for i, c in enumerate(clusters) if c["number"] is not None)
        for i in range(first_known - 1, -1, -1):
            clusters[i]["number"] = clusters[i + 1]["number"] - 1
        by_number = {}
        for c in clusters:
            other = by_number.get(c["number"])
            if other is None or best_view(c)["quality"] > best_view(other)["quality"]:
                by_number[c["number"]] = c
        clusters = [by_number[n] for n in sorted(by_number)]
        nums = sorted(by_number)
        gaps = [f"{a + 1}-{b - 1}" if b - a > 2 else str(a + 1)
                for a, b in zip(nums, nums[1:]) if b - a > 1]
        if gaps:
            print(f"Warning: missing page(s) {', '.join(gaps)}", file=sys.stderr)

    ordered = []
    for c in clusters:
        view = best_view(c)
        view["number"] = c["number"]
        ordered.append(view)
    return ordered


def best_view(cluster):
    return max(cluster["members"], key=lambda p: p["quality"])


# ---------------------------------------------------------------- text

def clean_line(t):
    t = t.strip()
    t = t.replace("’", "'").replace("‘", "'")
    t = re.sub("[“”«»„]", '"', t)
    t = re.sub(r"[©®*#•■]", "", t)
    t = re.sub(r'(^|\s)[iạ](?="[A-Z])', r"\1", t)   # OCR'd speck glued to an opening quote
    t = re.sub(r"\s+", " ", t)
    return t


def sentence_case_lead_in(t):
    """Chapters often open in small caps: 'THEY FORCED ME TO my knees' -> 'They forced me to my knees',
    '"HOLD ON!" OTAVIA GROWLED, slamming' -> '"Hold on!" otavia growled, slamming'."""
    t = t.strip()
    m = re.match(r"""^((?:["']?[A-Z][A-Z'!?,.;:]*["']?\s+)*["']?[A-Z][A-Z'!?,.;:]+["']?)(?=\s+[a-z])""", t)
    if not m or sum(c.isalpha() for c in m.group(1)) < 4:
        return t
    lead = re.sub(r"\bi\b", "I", m.group(1).lower())
    lead = re.sub(r"^([\"']?)([a-z])", lambda x: x.group(1) + x.group(2).upper(), lead)
    lead = re.sub(r"([.!?][\"']?\s+[\"']?)([a-z])", lambda x: x.group(1) + x.group(2).upper(), lead)
    return lead + t[m.end():]


def build_text(pages):
    """Return list of (kind, text) blocks: ('chapter', title) or ('para', text)."""
    blocks = []
    para = ""

    def flush():
        nonlocal para
        if para.strip():
            blocks.append(("para", para.strip()))
        para = ""

    prev_short = False
    for p in pages:
        if p["chapter"]:
            flush()
            blocks.append(("chapter", p["chapter"]))
            if p["body"]:
                p["body"][0]["text"] = sentence_case_lead_in(clean_line(p["body"][0]["text"]))
        for i, r in enumerate(p["body"]):
            t = clean_line(r["text"])
            if r.get("para_start") or (i == 0 and prev_short):
                flush()
            if r.get("scene_break"):
                blocks.append(("break", ""))
            if para.endswith("-") and t[:1].islower():
                para = para[:-1] + t          # re-join hyphenated word
            elif para:
                para += " " + t
            else:
                para = t
        if p["body"]:
            prev_short = p["body"][-1].get("short", False)
    flush()
    return blocks


def blocks_to_markdown(blocks):
    out = {"chapter": "## {}", "break": SCENE_BREAK, "para": "{}"}
    return "\n\n".join(out[k].format(t) for k, t in blocks) + "\n"


# ---------------------------------------------------------------- AI proofreading (optional)

CLEANUP_MODEL = "claude-opus-5-5"
CLEANUP_PROMPT = """You are proofreading OCR output from photos of a printed book. The text will be
read aloud by text-to-speech, so recognition errors are audible.

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


def cleanup_markdown(md_text):
    """Proofread the transcript with Claude, a chunk at a time. Uses the `claude` command
    (your Claude Code login) when installed, otherwise ANTHROPIC_API_KEY. Any chunk that
    fails or changes length suspiciously is left as OCR'd."""
    import shutil
    proofread = proofread_with_cli if shutil.which("claude") else proofread_with_api
    chunks, cur, words = [], [], 0
    for para in md_text.strip().split("\n\n"):
        cur.append(para)
        words += len(para.split())
        if words > 3000:
            chunks.append("\n\n".join(cur))
            cur, words = [], 0
    if cur:
        chunks.append("\n\n".join(cur))

    out = []
    for i, chunk in enumerate(chunks, 1):
        print(f"  proofreading part {i}/{len(chunks)}...", file=sys.stderr)
        try:
            text = proofread(chunk)
        except Exception as e:  # keep going with the OCR text rather than lose the run
            print(f"  part {i}: {e}; keeping OCR text", file=sys.stderr)
            out.append(chunk)
            continue
        ratio = len(text.split()) / max(1, len(chunk.split()))
        if not text or not 0.9 <= ratio <= 1.1:
            print(f"  part {i}: length changed {ratio:.0%}; keeping OCR text", file=sys.stderr)
            out.append(chunk)
            continue
        out.append(text)
    return "\n\n".join(out) + "\n"


def proofread_with_cli(chunk):
    """One `claude -p` call: no tools, no MCP servers, nothing saved to session history."""
    with tempfile.TemporaryDirectory() as tmp:  # neutral cwd: no project CLAUDE.md
        r = subprocess.run(
            ["claude", "-p", "--model", "opus", "--effort", "low",
             "--system-prompt", CLEANUP_PROMPT, "--tools", "", "--strict-mcp-config",
             "--no-session-persistence", "--output-format", "text"],
            input=chunk, capture_output=True, text=True, cwd=tmp, timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"claude exited {r.returncode}: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def proofread_with_api(chunk):
    import anthropic
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise RuntimeError("no `claude` command and no ANTHROPIC_API_KEY")
    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model=CLEANUP_MODEL,
        max_tokens=64000,
        system=CLEANUP_PROMPT,
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": chunk}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason != "end_turn":
        raise RuntimeError(f"stopped ({msg.stop_reason})")
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def markdown_to_blocks(md_text):
    blocks = []
    for para in md_text.strip().split("\n\n"):
        para = para.strip()
        if para.startswith("## "):
            blocks.append(("chapter", para[3:].strip()))
        elif para == SCENE_BREAK:
            blocks.append(("break", ""))
        elif para:
            blocks.append(("para", " ".join(para.split())))
    return blocks


# ---------------------------------------------------------------- audio

MODELS = HERE / "models"
PARAGRAPH_PAUSE = 0.45   # seconds of silence between paragraphs
CHAPTER_PAUSE = 1.5      # before and after a chapter title
SCENE_PAUSE = 1.5        # at a scene break (blank gap mid-chapter)


def sections(blocks):
    """Split into chapter sections: [(title, [paragraph, ...])]. Text before the first
    chapter heading (the end of the previous chapter) gets its own section."""
    out = []
    for kind, text in blocks:
        if kind == "chapter":
            out.append((text, [text + "."]))
        else:
            if not out:
                out.append(("Continued", []))
            out[-1][1].append(SCENE_BREAK if kind == "break" else text)
    return out


def speakable(text):
    """Small fixes that make neural TTS read more naturally."""
    t = re.sub(r"\s*\.\s*\.\s*\.", "…", text)                 # ". . ." -> ellipsis
    # ALL-CAPS names (MINOS) are otherwise spelled out letter by letter
    t = re.sub(r"\b([A-Z]{3,})\b", lambda m: m.group(1) if re.fullmatch(r"[IVXLC]+", m.group(1))
               else m.group(1).capitalize(), t)
    return t


class KokoroVoice:
    def __init__(self, voice, speed):
        import espeakng_loader
        if len(espeakng_loader.get_data_path()) >= 160:  # espeak-ng's fixed path buffer
            sys.exit(f"Kokoro can't load its pronunciation data because the folder path is too long:\n"
                     f"  {HERE}\nMove the book2audio folder somewhere shorter (e.g. ~/Documents) and run ./setup.sh again.")
        from kokoro_onnx import Kokoro
        self.k = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))
        if voice not in self.k.get_voices():
            sys.exit(f"Unknown Kokoro voice {voice!r}. Try one of: {', '.join(sorted(self.k.get_voices()))}")
        self.voice, self.speed = voice, speed
        self.lang = "en-gb" if voice[:1] == "b" else "en-us"

    def speak(self, text):
        import numpy as np
        samples, sr = self.k.create(speakable(text), voice=self.voice, speed=self.speed, lang=self.lang)
        self.rate = sr
        return np.asarray(samples, dtype=np.float32)


def render_section_kokoro(engine, paragraphs, wav_path, is_chapter, progress):
    import numpy as np
    import soundfile as sf
    chunks = []
    for i, para in enumerate(paragraphs):
        if para == SCENE_BREAK:
            chunks.append(np.zeros(int(engine.rate * SCENE_PAUSE), dtype=np.float32))
            continue
        audio = engine.speak(para)
        pause = CHAPTER_PAUSE if (is_chapter and i == 0) else PARAGRAPH_PAUSE
        chunks += [audio, np.zeros(int(engine.rate * pause), dtype=np.float32)]
        progress(len(para.split()))
    if is_chapter:
        chunks.insert(0, np.zeros(int(engine.rate * CHAPTER_PAUSE), dtype=np.float32))
    audio = np.concatenate(chunks)
    sf.write(str(wav_path), audio, engine.rate)
    return len(audio) / engine.rate


def render_section_say(voice, speed, paragraphs, aiff_path, is_chapter, progress):
    lead = f"[[slnc {int(CHAPTER_PAUSE * 1000)}]] " if is_chapter else ""
    paragraphs = [f"[[slnc {int(SCENE_PAUSE * 1000)}]]" if p == SCENE_BREAK else p for p in paragraphs]
    text = lead + f" [[slnc {int(PARAGRAPH_PAUSE * 1000)}]]\n".join(paragraphs)
    txt = aiff_path.with_suffix(".txt")
    txt.write_text(text)
    cmd = ["say", "-f", str(txt), "-o", str(aiff_path), "-r", str(int(185 * speed))]
    if voice:
        cmd += ["-v", voice]
    subprocess.run(cmd, check=True)
    progress(sum(len(p.split()) for p in paragraphs))
    info = subprocess.run(["afinfo", str(aiff_path)], capture_output=True, text=True).stdout
    return float(re.search(r"estimated duration: ([\d.]+)", info).group(1))


def synthesize(blocks, out_path, engine_name, voice, speed, tags):
    import shutil
    import time
    secs = sections(blocks)
    total_words = sum(len(p.split()) for _, paras in secs for p in paras)
    done, started = [0], time.time()

    def progress(words):
        before = done[0] / max(1, total_words)
        done[0] += words
        frac = done[0] / max(1, total_words)
        if not sys.stderr.isatty() and int(frac * 10) == int(before * 10):
            return  # in a log, only report every 10%
        eta = (time.time() - started) * (1 - frac) / max(frac, 1e-6)
        print(f"\r  {frac:4.0%} spoken, about {eta / 60:.0f} min left   ", end="", file=sys.stderr, flush=True)

    if engine_name == "kokoro":
        engine = KokoroVoice(voice or "am_michael", speed)
    with tempfile.TemporaryDirectory() as tmp:
        files, chapters, t = [], [], 0.0
        for i, (name, paras) in enumerate(secs):
            is_chapter = name != "Continued"
            if engine_name == "kokoro":
                f = Path(tmp) / f"s{i:03d}.wav"
                dur = render_section_kokoro(engine, paras, f, is_chapter, progress)
            else:
                f = Path(tmp) / f"s{i:03d}.aiff"
                dur = render_section_say(voice, speed, paras, f, is_chapter, progress)
            files.append(f)
            chapters.append((name, t, t + dur))
            t += dur
        print(file=sys.stderr)

        if shutil.which("ffmpeg"):
            encode_with_chapters(files, chapters, out_path, tags, Path(tmp))
        else:
            if len(chapters) > 1:
                print("Note: install ffmpeg (brew install ffmpeg) for chapter markers.", file=sys.stderr)
            src = files[0] if len(files) == 1 else concat_without_ffmpeg(files, Path(tmp))
            fmt = "m4bf" if out_path.suffix.lower() == ".m4b" else "m4af"
            subprocess.run(["afconvert", "-f", fmt, "-d", "aac", "-b", "64000", str(src), str(out_path)], check=True)
    return chapters


def concat_without_ffmpeg(files, tmp):
    import numpy as np
    import soundfile as sf
    data, rate = [], None
    for f in files:
        d, rate = sf.read(str(f), dtype="float32")
        data.append(d)
    out = tmp / "all.wav"
    sf.write(str(out), np.concatenate(data), rate)
    return out


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


# ---------------------------------------------------------------- main

def book_tags(pages, heads):
    """Book title and author from the running heads. By convention the author heads the
    left-hand pages and the title the right-hand ones (as in many books)."""
    by_side = {0: Counter(), 1: Counter()}
    for p in pages:
        for l in p["lines"]:
            t = " ".join(l["text"].split())
            if in_header(l, p) and not re.fullmatch(r"\d{1,4}", t) and is_running_head(t, heads):
                by_side[p["side"]][t] += 1
    pick = lambda c: c.most_common(1)[0][0] if c else None
    return pick(by_side[1]), pick(by_side[0])   # (title, author)


def describe_contents(blocks, pages):
    """Short label for what this audiobook covers: 'Ch 20-21', 'Ch 32', or 'p266-277'."""
    nums = [int(m.group(1)) for k, t in blocks if k == "chapter"
            for m in [re.search(r"(?i)chapter\s+(\d+)", t)] if m]
    if nums:
        return f"Ch {min(nums)}" if min(nums) == max(nums) else f"Ch {min(nums)}-{max(nums)}"
    pnums = [p["number"] for p in pages if p["number"] is not None]
    return f"p{min(pnums)}-{max(pnums)}" if pnums else time_label()


def time_label():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H%M")


def kokoro_available():
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        return False
    return (MODELS / "kokoro-v1.0.onnx").exists() and (MODELS / "voices-v1.0.bin").exists()


def collect_inputs(args):
    paths = []
    for a in args:
        p = Path(a).expanduser()
        if p.is_dir():
            paths += sorted(q for q in p.iterdir() if q.suffix.lower() in IMAGE_EXT | VIDEO_EXT)
        elif p.exists():
            paths.append(p)
        else:
            sys.exit(f"Not found: {a}")
    return paths


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="images, folders of images, or videos")
    ap.add_argument("-o", "--output", default=None,
                    help='output .m4b (default: output/<Book> - <Ch 20-21>.m4b, named from the contents)')
    ap.add_argument("--title", default=None, help="book title tag (default: read from the running heads)")
    ap.add_argument("--author", default=None, help="author tag (default: read from the running heads)")
    ap.add_argument("--engine", choices=["kokoro", "say"], default=None,
                    help="kokoro: natural neural voice (default when installed); say: built-in macOS voices")
    ap.add_argument("--voice", default=None,
                    help="Kokoro voice (default am_michael; samples in voice_samples/) or macOS voice for --engine say")
    ap.add_argument("--speed", type=float, default=1.0, help="speaking speed, e.g. 0.9 or 1.15 (default 1.0)")
    ap.add_argument("--video-every", type=float, default=0.5, help="seconds between sampled video frames")
    ap.add_argument("--text-only", action="store_true", help="stop after writing the .md transcript")
    ap.add_argument("--cleanup", action="store_true",
                    help="proofread OCR errors with Claude before speaking (uses your `claude` login)")
    args = ap.parse_args()

    import shutil
    if args.cleanup and not shutil.which("claude") and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("--cleanup needs the `claude` command (Claude Code) or an ANTHROPIC_API_KEY.")
    if args.engine is None:
        args.engine = "kokoro" if kokoro_available() else "say"
    elif args.engine == "kokoro" and not kokoro_available():
        sys.exit("Kokoro isn't set up: run ./setup.sh")
    ensure_ocr_binary()
    paths = collect_inputs(args.inputs)
    print(f"OCR on {len(paths)} file(s)...", file=sys.stderr)
    records = run_ocr(paths, args.video_every)

    pages = assemble_spreads(records)
    heads = detect_running_heads(pages)
    pages = [analyze_page(p, heads) for p in pages]
    title, author = book_tags(pages, heads)
    title, author = args.title or title, args.author or author
    pages = order_pages(pages)
    nums = [p["number"] for p in pages if p["number"] is not None]
    if nums:
        print(f"Pages {min(nums)}-{max(nums)} ({len(pages)} pages)", file=sys.stderr)

    blocks = build_text(pages)
    contents = describe_contents(blocks, pages)
    if args.output:
        out = Path(args.output).expanduser()
    else:
        name = re.sub(r'[/:\\]', "-", f"{title} - {contents}" if title else contents)
        out = HERE / "output" / f"{name}.m4b"
    out.parent.mkdir(parents=True, exist_ok=True)
    if title or author:
        print(f"Book: {title or '?'} by {author or '?'}", file=sys.stderr)
    md = out.with_suffix(".md")
    md_text = blocks_to_markdown(blocks)
    if args.cleanup:
        out.with_suffix(".ocr.md").write_text(md_text)
        print("Proofreading with Claude...", file=sys.stderr)
        md_text = cleanup_markdown(md_text)
        blocks = markdown_to_blocks(md_text)
    md.write_text(md_text)
    print(f"Transcript: {md}", file=sys.stderr)
    if args.text_only:
        return
    print(f"Synthesizing speech ({args.engine})...", file=sys.stderr)
    tags = {"title": f"{title} - {contents}" if title else out.stem, "album": title or out.stem,
            "artist": author, "album_artist": author}
    chapters = synthesize(blocks, out, args.engine, args.voice, args.speed, tags)
    print(f"Audio: {out}  ({chapters[-1][2] / 60:.0f} min)", file=sys.stderr)
    for name, start, _ in chapters:
        print(f"  {int(start // 60):3d}:{int(start % 60):02d}  {name}", file=sys.stderr)


if __name__ == "__main__":
    main()

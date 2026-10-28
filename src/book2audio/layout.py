"""Reading the layout of photographed book pages: split two-page spreads at the spine,
drop running heads, page numbers and stray text, put curved lines in reading order, and
find paragraph starts, scene breaks and chapter openings."""
import difflib
import functools
import re
import statistics
from collections import Counter

import numpy as np

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
    """Lines only the fast recognizer found (e.g. a lone "end." the accurate pass skipped).
    Fast-pass coordinates are offset from the accurate ones, so we match lines by text and
    slot each unmatched fast line between the accurate lines its neighbours matched."""
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
    """Exact or close match: OCR misreads heads ("Moby Dlck" for "Moby Dick")."""
    n = norm(text)
    return n in heads or any(difflib.SequenceMatcher(None, n, h).ratio() > 0.75 for h in heads)


def detect_running_heads(pages):
    """Text repeated in the header zone of *different* pages (book title, author).
    Video has many frames of each page, so first group the views into pages by their
    text, then require a candidate (allowing OCR variants) to top at least two pages."""
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

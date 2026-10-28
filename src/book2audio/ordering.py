"""Put pages in book order: match the many views of each page (video frames, repeat
photos), keep the clearest one, and settle each page's number by vote."""
import re
import sys
from collections import Counter


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



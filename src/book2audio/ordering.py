"""Put pages in book order: match the many views of each page (video frames, repeat
photos), keep the clearest one, and settle each page's number by vote."""
import difflib
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
    clusters = drop_garbled_copies(clusters)
    for c in clusters:
        for m in c["members"]:
            m["cluster"] = c
    clusters = drop_turning_leaves(clusters, pages)

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
    for p in pages:   # a page facing a blank one: the blank page's number, +/- 1
        if p.get("cluster") and p.get("facing_number") is not None:
            votes[id(p["cluster"])][p["facing_number"] + (1 if p["side"] == 1 else -1)] += 1
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

    # 3. Anything still unnumbered: in a video, a spread follows the spread before it (its left
    #    page one after that spread's right page); anything else follows whatever came just
    #    before it in the input. Books that print the page number at the foot of the page get
    #    it read in only a few frames, so most spreads are numbered this way.
    clusters.sort(key=lambda c: c["first"])
    mates = spread_partners(frames)
    if any(c["number"] is not None for c in clusters):
        last_spread = {}   # video -> the right-hand page number of the latest spread
        for prev, c in zip([None] + clusters, clusters):
            mate, side = mates.get(id(c), (None, None))
            if c["number"] is None and mate is not None:
                if mate["number"] is not None:
                    c["number"] = mate["number"] + (1 if side == 1 else -1)
                elif c["members"][0]["source"] in last_spread:
                    c["number"] = last_spread[c["members"][0]["source"]] + (1 if side == 0 else 2)
            if c["number"] is None and prev is not None and prev["number"] is not None:
                # A chapter page facing a blank one: the blank page has a number too.
                c["number"] = prev["number"] + (2 if best_view(c).get("blank_facing") else 1)
            if mate is not None and c["number"] is not None:
                last_spread[c["members"][0]["source"]] = c["number"] + (1 if side == 0 else 0)
        # Before the first numbered page, count down from the lowest number that follows (a
        # spread's right page is often read first: page 1 comes before 2, not before 3).
        # Count in spread order, left page before right: the order pages first appear on
        # screen isn't enough, as the right page can flash past alone mid-turn before the
        # spread settles.
        first_known = next(i for i, c in enumerate(clusters) if c["number"] is not None)
        lowest = min(c["number"] for c in clusters[first_known:first_known + 4] if c["number"] is not None)
        def spread_order(c):
            mate, side = mates.get(id(c), (None, None))
            if mate is None or mate["first"] >= clusters[first_known]["first"]:
                return c["first"], 0
            return min(c["first"], mate["first"]), side
        before = sorted(clusters[:first_known], key=spread_order)
        for c in reversed(before):
            lowest -= 1
            c["number"] = lowest
        clusters[:first_known] = before
        # Two pages can end up with one number. Usually one is a frame or two glimpsed in
        # motion (e.g. while paging back), numbered from a misread facing page: keep the
        # page whose own frames show that number most often, then the one seen in more
        # frames, then the clearer one. Pages each seen in several frames are both real
        # (two views of the same page would have been grouped by their text), so the
        # numbering is what's wrong: keep both, in the order they were first seen.
        def claim(c):
            reads = sum(1 for m in c["members"] if m["number"] == c["number"])
            return reads, len(c["members"]), best_view(c)["quality"]
        by_number = {}
        for c in clusters:
            by_number.setdefault(c["number"], []).append(c)
        for n, same in by_number.items():
            seen = [c for c in same if len(c["members"]) >= 3]
            by_number[n] = sorted(seen, key=lambda c: c["first"]) if len(seen) > 1 else [max(same, key=claim)]
        clusters = [c for n in sorted(by_number) for c in by_number[n]]
        # The blank page facing a chapter opening isn't missing.
        blank = {c["number"] + (-1 if best_view(c)["side"] == 1 else 1)
                 for c in clusters if best_view(c).get("blank_facing")}
        nums = sorted(set(by_number) | blank)
        gaps = [f"{a + 1}-{b - 1}" if b - a > 2 else str(a + 1)
                for a, b in zip(nums, nums[1:]) if b - a > 1]
        if gaps:
            print(f"Warning: missing page(s) {', '.join(gaps)}", file=sys.stderr)

    ordered = []
    for c in clusters:
        view = best_view(c)
        view["number"] = c["number"]
        restore_last_line(view, c["members"])
        ordered.append(view)
    return ordered


def restore_last_line(view, members):
    """Even the clearest view of a page can miss a short last line (one word ending a
    sentence) that most of the page's other clear views read: add it back."""
    if not view["body"] or view["body"][-1].get("short"):
        return   # the page ends with the end of a paragraph: nothing missing

    def key(text):
        return re.sub(r"[^a-z]", "", text.lower())[-12:]

    last = key(view["body"][-1]["text"])
    good = [m for m in members if m is not view and m["body"] and m["quality"] >= 0.8 * view["quality"]]
    longer = [m for m in good if len(m["body"]) >= 2 and key(m["body"][-2]["text"]) == last]
    if last and good and len(longer) > len(good) / 2:
        best = max(longer, key=lambda m: m["quality"])
        view["body"] = view["body"] + [dict(best["body"][-1])]


def spread_partners(frames):
    """In a video, the two pages of a spread are seen side by side in many frames; a page
    glimpsed mid-turn, in one or two. {id(cluster): (facing cluster, 0 for the left page or
    1 for the right)} for pages that are each other's most frequent neighbour in at least
    three frames."""
    pairs, clusters = Counter(), {}
    for views in frames.values():
        if len(views) != 2:
            continue
        l, r = sorted(views, key=lambda p: p["side"])
        if l["side"] == r["side"] or l["cluster"] is r["cluster"]:
            continue
        pairs[id(l["cluster"]), id(r["cluster"])] += 1
        clusters[id(l["cluster"])], clusters[id(r["cluster"])] = l["cluster"], r["cluster"]
    best = {}
    for (l, r), n in pairs.items():
        for me, other, side in ((l, r, 0), (r, l, 1)):
            if n > best.get(me, (None, None, 0))[2]:
                best[me] = (other, side, n)
    return {me: (clusters[other], side) for me, (other, side, n) in best.items()
            if n >= 3 and best.get(other, (None,))[0] == me}


def bigrams(page):
    words = re.findall(r"[a-z]+", " ".join(r["text"] for r in page["body"]).lower())
    return {tuple(words[i:i + 2]) for i in range(len(words) - 1)}


def drop_garbled_copies(clusters):
    """A page glimpsed in a frame or two while moving (mid-turn, camera shake) can be read so
    badly that it doesn't join the clear views of the same page; often with a misread page
    number too, so it lands in the wrong place as a garbled extra page. Drop such a cluster
    when much of its text is in a far clearer one."""
    keep = []
    for c in clusters:
        q = best_view(c)["quality"]
        mine = set().union(*(bigrams(m) for m in c["members"]))
        def copy_of(d):
            if d is c or best_view(d)["quality"] <= q:
                return False
            shared = len(mine & set().union(*(bigrams(m) for m in d["members"]))) / len(mine)
            return shared > 0.5 or (shared > 0.25 and best_view(d)["quality"] >= 2 * q)
        garbled = len(c["members"]) <= 2 and len(mine) >= 10 and any(copy_of(d) for d in clusters)
        if not garbled and not blurred_frame(c, clusters):
            keep.append(c)
    return keep


def drop_turning_leaves(clusters, pages):
    """A page seen in a single video frame, beside a page that is seen in many frames with a
    different facing page, isn't a page of its own: it's the leaf being turned, caught in
    motion and read as garble. Drop it."""
    frames = {}
    for p in pages:
        if p.get("cluster") and p.get("time") is not None:
            frames.setdefault((p["source"], p["time"]), []).append(p)
    mates = spread_partners(frames)
    leaves = set()
    for c in clusters:
        me = c["members"][0]
        if len(c["members"]) != 1 or me.get("time") is None:
            continue
        for other in frames.get((me["source"], me["time"]), []):
            mate = mates.get(id(other["cluster"]))
            if other["side"] != me["side"] and mate is not None and mate[0] is not c:
                leaves.add(id(c))
    for c in clusters:
        if id(c) in leaves:
            c["members"][0]["cluster"] = None
    return [c for c in clusters if id(c) not in leaves]


def unclear_lines(view, most=4):
    """Lines of a page that its video frames read differently (blurred or covered in most of
    them), each with the other frames' readings: [(line, [other readings])]. Proofreading
    can often piece the printed words together from several bad readings."""
    def norm(t):
        return " ".join(t.split()).lower()
    others = sorted((m for m in view["cluster"]["members"]
                     if m is not view and m["quality"] >= 0.5 * view["quality"]),
                    key=lambda m: -m["quality"])[:8]
    out = []
    for i, row in enumerate(view["body"]):
        line = row["text"].strip()
        if len(line) < 20:
            continue
        readings = []
        for m in others:
            near = [r["text"].strip() for r in m["body"][max(0, i - 3):i + 4]]
            scored = [(difflib.SequenceMatcher(None, norm(line), norm(t)).ratio(), t) for t in near
                      if difflib.SequenceMatcher(None, norm(line), norm(t)).real_quick_ratio() >= 0.5]
            if scored:
                ratio, text = max(scored)
                if ratio >= 0.5:
                    readings.append((ratio, text))
        if len(readings) >= 2 and sum(r for r, _ in readings) / len(readings) < 0.85:
            alts = Counter(t for _, t in readings if norm(t) != norm(line))
            if alts:
                out.append((line, [t for t, _ in alts.most_common(most)]))
    return out


def blurred_frame(c, clusters):
    """A single video frame of a page in motion, read too badly to match its clear views
    by text: it's a copy if the page it shares most words with is shown, clearly and in
    several frames, on the same side of the screen within a second of it."""
    if len(c["members"]) != 1:
        return False
    me = c["members"][0]
    mine = bigrams(me)
    if me.get("time") is None or len(mine) < 10:
        return False
    def shared(d):
        return len(mine & set().union(*(bigrams(m) for m in d["members"]))) / len(mine)
    others = [d for d in clusters if d is not c and len(d["members"]) >= 3]
    if not others:
        return False
    d = max(others, key=shared)
    return shared(d) >= 0.2 and best_view(d)["quality"] > me["quality"] and any(
        m["source"] == me["source"] and m["side"] == me["side"] and abs(m["time"] - me["time"]) <= 1
        for m in d["members"])


def best_view(cluster):
    """The clearest view of the page. A small chapter numeral is often read in only one or two
    of a page's frames, so if any frame found a chapter heading the page keeps it: from the
    best of those frames if it is nearly as clear, otherwise carried over to the clearest one."""
    members = cluster["members"]
    view = max(members, key=lambda p: p["quality"])
    opened = [p for p in members if p.get("chapter")]
    if opened and not view.get("chapter"):
        label = Counter(p["chapter"] for p in opened).most_common(1)[0][0]
        best_opened = max(opened, key=lambda p: p["quality"])
        if best_opened["quality"] >= 0.8 * view["quality"]:
            view = best_opened
        view["chapter"] = label
    return view

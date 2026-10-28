"""The book's text as a list of blocks, and the Markdown transcript made from them.

A block is (kind, text): ("chapter", heading), ("para", paragraph), ("break", "") for a
scene break, or ("subheading", heading) for a heading inside a chapter (papers)."""
import datetime
import re

SCENE_BREAK = "* * *"
SUBHEADING = "### "   # marks a subheading among a section's paragraphs
CONTINUED = "(continued)"


def clean_line(t):
    t = t.strip()
    t = t.replace("’", "'").replace("‘", "'")
    t = re.sub("[“”«»„]", '"', t)
    t = re.sub(r"[©®*#•■]", "", t)
    t = re.sub(r'(^|\s)[iạ](?="[A-Z])', r"\1", t)   # OCR'd speck glued to an opening quote
    t = re.sub(r"\s+", " ", t)
    return t


def sentence_case_lead_in(t):
    """Chapters often open in small caps: 'IT WAS THE BEST of times' -> 'It was the best of times',
    '"HOLD ON!" ALICE CRIED, running' -> '"Hold on!" alice cried, running' (names lose their capital)."""
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


def label_continued(blocks):
    """A recording usually starts partway through a chapter. Give that opening text a
    heading, "Chapter 32 (continued)", worked out from the next chapter's number."""
    if not blocks or blocks[0][0] == "chapter":
        return blocks
    nums = [chapter_number(t) for k, t in blocks if k == "chapter"]
    n = next((n for n in nums if n is not None), None)
    title = f"Chapter {n - 1} {CONTINUED}" if n and n > 1 else f"Previous chapter {CONTINUED}"
    return [("chapter", title)] + blocks


def chapter_number(title):
    m = re.match(r"(?i)chapter\s+(\d+)\b", title)
    return int(m.group(1)) if m else None


def is_continued(title):
    return title.endswith(CONTINUED)


def short_label(title):
    """'Chapter 33: Living the Lie' -> 'Chapter 33'."""
    return title if is_continued(title) else title.split(":")[0].strip()


def sections(blocks):
    """Group blocks by chapter: [(heading, [paragraph, SCENE_BREAK or "### subheading", ...])]."""
    out = []
    for kind, text in blocks:
        if kind == "chapter":
            out.append((text, []))
        else:
            if not out:
                out.append((f"Previous chapter {CONTINUED}", []))
            out[-1][1].append(SCENE_BREAK if kind == "break" else
                              SUBHEADING + text if kind == "subheading" else text)
    return out


def blocks_to_markdown(blocks):
    out = {"chapter": "## {}", "subheading": SUBHEADING + "{}", "break": SCENE_BREAK, "para": "{}"}
    return "\n\n".join(out[k].format(t) for k, t in blocks) + "\n"


def markdown_to_blocks(md_text):
    blocks = []
    for para in md_text.strip().split("\n\n"):
        para = para.strip()
        if para.startswith(SUBHEADING):
            blocks.append(("subheading", para[len(SUBHEADING):].strip()))
        elif para.startswith("## "):
            blocks.append(("chapter", para[3:].strip()))
        elif para == SCENE_BREAK:
            blocks.append(("break", ""))
        elif para:
            blocks.append(("para", " ".join(para.split())))
    return blocks


def describe_contents(blocks, pages):
    """Short label for what a run covers: 'Ch 20-21', 'Ch 32', or 'p266-277'."""
    nums = [n for k, t in blocks if k == "chapter" for n in [chapter_number(t)] if n is not None]
    if nums:
        return f"Ch {min(nums)}" if min(nums) == max(nums) else f"Ch {min(nums)}-{max(nums)}"
    pnums = [p["number"] for p in pages if p["number"] is not None]
    if pnums:
        return f"p{min(pnums)}-{max(pnums)}"
    return datetime.datetime.now().strftime("%Y-%m-%d %H%M")

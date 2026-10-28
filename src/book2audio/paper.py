"""Scientific papers (PDF): rebuild each page as clean Markdown with Claude, from the page's
image and its embedded text layer, and check the result against the text layer so that
nothing is silently dropped."""
import difflib
import hashlib
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from . import claude
from .capture import CACHE_DIR

PAGE_PROMPT = """You turn one page of a scientific paper into clean text for an audiobook narrator.

You get an image of the page and the page's embedded PDF text layer. The text layer has the
author's exact words but can lose ligatures ("identi ed" for "identified"), spaces, superscripts
and math; the image shows the true layout. Use the text layer's wording, corrected against the image.

Write the page as Markdown, in the order a person reads it (columns left to right, top to bottom):
- "# " for the paper's title, "## " for section headings (Abstract, Introduction, Results,
  Discussion, Methods, Acknowledgements, ...), "### " for subsection headings. Bold run-in
  headings ("Model domain.") stay at the start of their paragraph as plain text. If the abstract
  or the main text starts without a heading, put "## Abstract" or "## Introduction" before it.
- Keep every sentence of the body text. When unsure whether something is page furniture, keep it.
- Leave out running heads, page numbers, line numbers, journal banners, copyright/licence notices,
  download stamps, "Check for updates" marks, email addresses, URLs and DOIs.
- Author affiliations go in one paragraph starting "Affiliations: ". Footnotes go at the end of the
  page, each as its own paragraph starting "Footnote: ".
- Rejoin words hyphenated at line ends. One paragraph per line, blank line between paragraphs.
- Remove citation markers: superscript reference numbers, bracketed numbers like [12] or [3-5],
  and author-year citations like (Smith et al. 2020; Lee 2019). If a citation is part of the
  sentence ("as calculated in [27]", "following Smith et al. (2019)"), keep the sentence whole by
  writing "reference 27" or "Smith and colleagues". A citation cut off at the start or end of the
  page is still a citation: remove only the citation fragment, keep the words and punctuation
  around it ("al. 2018). Taken together" becomes ". Taken together"). Keep figure and table
  references such as (Fig 2B) or (Table 1).
- Leave out the reference list (References, Literature Cited, Bibliography): write one line
  <<REFERENCES>> where it starts, and carry on with anything that follows it on the page.
- A displayed equation becomes one line: [Equation 3: one plain-English sentence on what it
  expresses, e.g. "the number of protofibrils grows with the square of the fiber diameter"]
  ("[Equation: ...]" if it has no number). Give its meaning, not a symbol-by-symbol reading, and
  don't list the entries of vectors or matrices. Inline math stays inline, written as text
  (e.g. "n = 46").
- Figures and tables: don't transcribe text inside them (axis labels, panel letters, table cells).
  For each figure or table caption on this page write one line, using the label as printed
  (1, 2, S1, ...):
  <<FIGURE 2: the full caption, exactly as printed>>  or  <<TABLE 1: the full caption>>
  For a figure or table on this page whose caption is NOT on this page, write <<FIGURE 2 IMAGE>>
  or <<TABLE 1 IMAGE>> (work out its number from the captions and context if it isn't printed on it).
- If the page starts or ends mid-sentence, leave it that way; pages are joined later. You also get
  the last lines of the previous page and the first lines of the next page, only so you can tell
  how a sentence or citation cut at the page edge continues. Never write that context out.
- Do not summarize, reword or add anything. Return only the Markdown."""

PROMPT_VERSION = hashlib.sha1(PAGE_PROMPT.encode()).hexdigest()[:8]

REF_START = re.compile(r"(?i)^(references|references and notes|literature cited|cited literature"
                       r"|bibliography|works cited)\s*:?$")
REF_END = re.compile(r"(?i)^(acknowledge?ments?|author contributions?|contributions|funding|"
                     r"competing interests|conflicts? of interest|declaration of interests|"
                     r"data availability( statement)?|code availability|additional information|"
                     r"supporting information|supplementary (information|materials?|data)|"
                     r"(materials and |online )?methods|figure legends|figures|tables|appendix\b.*|"
                     r"ethics declarations|abbreviations)\s*:?$"
                     r"|^(fig\.?|figure|table)\s*s?\d+\b")
REF_LIKE = re.compile(r"\b(?:19|20)\d\d[a-z]?\b|\bet al\b|\bdoi\b|\d+\s*[–-]\s*\d+")   # years, page ranges
CITATION = re.compile(r"\((?=[^()]*\b(?:19|20)\d\d[a-z]?\b)(?=[^()]*[A-Z][a-z])[^()]{0,400}\)"
                      r"|\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]")
LIGATURES = ("", "fi", "fl", "ff", "ffi", "ffl")
MATH_GARBLE = re.compile(r"[¼½¾ÞðÐþ�]")     # how PDF math fonts often come out as text
LICENCE = re.compile(r"(?i)copyright|©|open access|creative commons|licen[cs]e|permits unrestricted")
EMAIL_URL = re.compile(r"\S+@\S+|https?://\S+|www\.\S+|\bdoi:\s*\S+")
TABULAR = re.compile(r"\([^()]{1,6}\)|\d")       # unit headers like (µM), numbers


def letters(text):
    return re.sub(r"[^a-z]", "", text.lower())


def letter_cover(line, out_letters):
    """Fraction of a line's letters found, in order and in runs of 5 or more, in the output:
    tolerant of lost spaces ("50mMTris/HCl,137mMNaCl") and reworded citations."""
    a = letters(line)
    if not a:
        return 1.0
    blocks = difflib.SequenceMatcher(None, a, out_letters, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocks if b.size >= 5) / len(a)


def words(text):
    return re.findall(r"[a-z]{2,}", text.lower())


def norm_line(t):
    return re.sub(r"[^a-z]+", " ", t.lower()).strip()


def furniture(pages):
    """Lines repeated on three or more pages: banners, running heads, journal footers."""
    seen = Counter()
    for p in pages:
        for n in {norm_line(l) for l in p["text"].splitlines()}:
            if len(n) >= 8:
                seen[n] += 1
    return {n for n, count in seen.items() if count >= 3}


def mark_references(pages, furn):
    """For each page, its text-layer lines that are worth checking (not furniture, not in the
    reference list), and whether the page is nothing but reference list. A page after the list
    whose lines don't look like references (a full-page figure's labels, say) still gets read."""
    in_refs = False
    for p in pages:
        keep, ref_lines = [], []
        for line in p["text"].splitlines():
            s = line.strip()
            if not s or norm_line(s) in furn or re.fullmatch(r"[\d\s.]+", s):
                continue
            if REF_START.match(s):
                in_refs = True
            elif in_refs and REF_END.match(s):
                in_refs = False
            (ref_lines if in_refs else keep).append(s)
        p["check_lines"] = keep
        p["only_references"] = (bool(ref_lines) and not keep
                                and sum(bool(REF_LIKE.search(l)) for l in ref_lines) >= 0.2 * len(ref_lines))


def missing_lines(check_lines, output, threshold=0.5):
    """Text-layer lines whose word pairs mostly don't appear in Claude's output: likely
    dropped text. Citations are stripped first, and words broken by lost ligatures or
    line-end hyphens are repaired against the output's vocabulary."""
    out_words = words(output)
    vocab, out_pairs = set(out_words), set(zip(out_words, out_words[1:]))
    out_letters, has_table = letters(output), "<<TABLE" in output
    text = EMAIL_URL.sub(" ", CITATION.sub(" ", "\n".join(check_lines)))
    text = re.sub(r"([a-z])-\n([a-z]+)", r"\1\2\n", text)
    lines = text.splitlines()
    near_licence = {j for i, l in enumerate(lines) if LICENCE.search(l) for j in range(i - 3, i + 4)}
    flagged = []
    for i, line in enumerate(lines):
        if i in near_licence or MATH_GARBLE.search(line):
            continue   # notices the prompt leaves out on purpose; equations become gists
        ws = words(line)
        fixed, i = [], 0
        while i < len(ws):
            w = ws[i]
            if w not in vocab and i + 1 < len(ws):
                joined = next((w + lig + ws[i + 1] for lig in LIGATURES if w + lig + ws[i + 1] in vocab), None)
                if joined:
                    fixed.append(joined)
                    i += 2
                    continue
            if w not in vocab:
                w = next((c for lig in LIGATURES[1:] for c in (lig + w, w + lig) if c in vocab), w)
            fixed.append(w)
            i += 1
        if len(fixed) < 6:
            continue   # labels, figure text, equation fragments
        pairs = list(zip(fixed, fixed[1:]))
        if sum(pr in out_pairs for pr in pairs) / len(pairs) >= threshold:
            continue
        if has_table and len(TABULAR.findall(line)) >= 2:
            continue   # a table's header or row: tables are described, not read
        if letter_cover(line, out_letters) < 0.6:
            flagged.append(line.strip())
    return flagged


def page_request(page, prev, nxt, note=""):
    tail = "\n".join(prev["check_lines"][-6:]) if prev else "(none: first page)"
    head = "\n".join(nxt["check_lines"][:6]) if nxt else "(none: last page)"
    layer = page["text"].strip() or "(none: this page is a scanned image; transcribe it from the image)"
    return (f"End of the previous page (context only):\n{tail}\n\n"
            f"Start of the next page (context only):\n{head}\n\n"
            f"Text layer of THIS page:\n\n{layer}{note}")


def cache_dir(pdf_path):
    """Where a paper's rebuilt pages and figure descriptions are kept, named by the PDF's content."""
    cache = CACHE_DIR / "papers" / hashlib.sha1(pdf_path.read_bytes()).hexdigest()[:16]
    cache.mkdir(parents=True, exist_ok=True)
    return cache


PAGE_META = re.compile(r"(?i)^(i can(no|')t|i won't|i will not|i'm sorry|sorry,|as an ai|i am unable|i'm unable|"
                       r"here is the|here's the)\b")


def usable_page(text):
    """Claude's page, without a code fence if it added one. An empty or chatty reply is an error,
    so it is retried rather than cached."""
    text = re.sub(r"^```\w*\n|\n?```$", "", text.strip()).strip()
    if not text or PAGE_META.match(text):
        raise RuntimeError(f"unusable reply: {text[:80]!r}")
    return text


def raw_text(page):
    """A page Claude couldn't rebuild: its text layer as one paragraph, with line-end hyphens
    rejoined and citation markers removed (a pause after every printed line would be worse)."""
    text = re.sub(r"([a-z])-\n([a-z])", r"\1\2", "\n".join(page["check_lines"]))
    return re.sub(r"\s+([.,;:])", r"\1", " ".join(CITATION.sub("", text).split()))


def rebuild_pages(cache, pages):
    """Rebuild every page (except pure reference-list pages) as Markdown. Returns
    {page number: markdown}; also warns about lines that may still be missing."""
    furn = furniture(pages)
    mark_references(pages, furn)
    todo = [i for i, p in enumerate(pages) if not p["only_references"]]
    page_file = lambda page: cache / f"p{page['page']:03d}-{PROMPT_VERSION}.md"
    saved = sum(page_file(pages[i]).exists() for i in todo)
    skipped = len(pages) - len(todo)
    print(f"  {len(todo) - saved} page(s) to rebuild" + (f", {saved} saved from an earlier run" if saved else "")
          + (f", {skipped} reference-list page(s) skipped" if skipped else ""), file=sys.stderr)

    def one(i):
        page = pages[i]
        prev = pages[i - 1] if i > 0 else None
        nxt = pages[i + 1] if i + 1 < len(pages) else None
        check = (lambda text: missing_lines(page["check_lines"], text)) if page["text"].strip() else (lambda text: [])
        cached = page_file(page)
        if cached.exists():
            out = cached.read_text()
            return page["page"], out, check(out)

        def ask(note=""):
            for attempt in range(2):
                try:
                    return usable_page(claude.ask(PAGE_PROMPT, page_request(page, prev, nxt, note), [page["image"]]))
                except Exception as e:
                    error = e
            raise error

        try:
            out = ask()
        except Exception as e:
            print(f"  page {page['page']}: Claude failed ({e}); using the raw text layer", file=sys.stderr)
            return page["page"], raw_text(page), []
        gaps = check(out)
        if gaps:
            note = ("\n\nNote: an earlier version of this page seemed to leave out these lines of the text "
                    "layer. Include them unless they are part of a figure, table, equation, page furniture "
                    "or the reference list:\n" + "\n".join(f"- {g}" for g in gaps))
            try:
                retry = ask(note)
                retry_gaps = check(retry)
                if len(retry_gaps) <= len(gaps):
                    out, gaps = retry, retry_gaps
            except Exception:
                pass   # keep the first version
        cached.write_text(out)
        return page["page"], out, gaps

    done = {}
    with ThreadPoolExecutor(claude.PARALLEL) as pool:
        for n, out, gaps in pool.map(one, todo):
            done[n] = out
            for g in gaps:
                print(f"  page {n}: this line may be missing: {g[:100]}", file=sys.stderr)
    return done


# ---------------------------------------------------------------- joining pages into chapters

MARKER = re.compile(r"^<<(FIGURE|TABLE)\s+(.+?)\s*(?::\s*(.*?))?\s*>>$")
MENTION = re.compile(r"\b(Supplementary\s+|Suppl\.\s+|Extended\s+Data\s+)?(Fig(?:ure)?s?\.?|Tables?)\s*"
                     r"((?:S?\d+[A-Za-z]?(?:\s*(?:,|–|—|-|&|and)\s*(?=S?\d))?)+)")
OPEN_END = re.compile(r"""[.!?:]["”’')\]]*$""")
CAPTION_LABEL = re.compile(r"^(?:(?:Supplementary|Suppl\.|Extended\s+Data)\s+)?(?:Fig(?:ure)?\.?|Table)\s*"
                           r"[A-Z]?\d+[A-Za-z]?\s*[.:|]?\s*")   # "Figure 2.", "Extended Data Fig. 3 |"


def figure_key(kind, label):
    """"Figure 2", "Table 1", "Figure S1", "Extended Data Figure 4"."""
    m = re.match(r"(?i)extended data\s+(.+)", label)
    return f"Extended Data {kind.title()} {m.group(1)}" if m else f"{kind.title()} {label}"


def mentions(text):
    """Figure and table keys mentioned in a paragraph, in order: "Figs. 2–4" -> Figures 2, 3, 4."""
    keys = []
    for m in MENTION.finditer(text):
        prefix = (m.group(1) or "").strip().lower()
        kind = "TABLE" if m.group(2).lower().startswith("table") else "FIGURE"
        for part in re.split(r"\s*(?:,|&|\band\b)\s*", m.group(3)):
            r = re.match(r"(S?)(\d+)[A-Za-z]?\s*[–—-]\s*S?(\d+)", part)
            nums = ([f"{r.group(1)}{n}" for n in range(int(r.group(2)), int(r.group(3)) + 1)] if r
                    else re.findall(r"S?\d+", part)[:1])
            for n in nums:
                if prefix.startswith("extended"):
                    n = f"Extended Data {n}"
                elif prefix.startswith("suppl") and not n.startswith("S"):
                    n = f"S{n}"
                keys.append(figure_key(kind, n))
    return keys


def join_pages(page_md):
    """The rebuilt pages, in order, as one flow of items {"type", "text", "page"} with figure
    and table markers pulled out. A paragraph cut by a page break (even across a page that is
    only a figure) is rejoined, and footnotes wait until the paragraph they interrupt ends.
    Returns (flow, figures)."""
    flow, figures, pending_notes = [], {}, []
    everything = "\n".join(page_md.values())

    def keeps_hyphen(left, right):
        """'self-' + 'assembly' across a page break stays 'self-assembly' if the paper writes it
        that way elsewhere; otherwise it's a word broken at the line end ('identi-' + 'fied')."""
        w = lambda s: re.search(rf"\b{s}\b", everything)
        return bool(w(re.escape(f"{left}-{right}")) and not w(re.escape(left + right)))

    def last_para():
        return next((it for it in reversed(flow) if it["type"] not in ("marker", "front")), None)

    for page_no in sorted(page_md):
        first_text = True
        for line in page_md[page_no].splitlines():
            line = line.strip()
            if not line:
                continue
            m = MARKER.match(line)
            if m:
                kind, label, caption = m.group(1), m.group(2), m.group(3)
                image = label.upper().endswith(" IMAGE")
                key = figure_key(kind, label[:-6].strip() if image else label)
                fig = figures.setdefault(key, {"key": key, "caption": None, "caption_page": None,
                                               "image_pages": [], "caption_at_top": False})
                if image:
                    fig["image_pages"].append(page_no)
                elif caption:
                    fig["caption"] = CAPTION_LABEL.sub("", caption).strip()
                    fig["caption_page"] = page_no
                    fig["caption_at_top"] = first_text
                    flow.append({"type": "marker", "text": key, "page": page_no})
                continue
            if line == "<<REFERENCES>>":
                kind, text = "refs", ""
            elif line.startswith("# "):
                kind, text = "title", line[2:].strip()
            elif line.startswith("## "):
                kind, text = "section", line[3:].strip()
            elif line.startswith("### "):
                kind, text = "subsection", line[4:].strip()
            elif line.startswith("#"):
                kind, text = "subheading", line.lstrip("#").strip()
            elif line.startswith("Affiliations: "):
                flow.append({"type": "front", "text": line, "page": page_no})
                continue
            elif line.startswith("Footnote: "):
                pending_notes.append({"type": "para", "text": line, "page": page_no})
                continue
            else:
                kind, text = "para", re.sub(r"^\[(Equation[^\]]*)\]$", r"\1", line)
            prev = last_para()
            if first_text and kind == "para" and prev and prev["type"] == "para" and prev["page"] != page_no:
                p = prev["text"]
                if re.search(r"[a-z]-$", p) and text[:1].islower():
                    left, right = re.search(r"(\w+)-$", p).group(1), re.match(r"\w+", text).group()
                    prev["text"], text = (p if keeps_hyphen(left, right) else p[:-1]) + text, None
                elif not OPEN_END.search(p) or text[:1].islower():
                    prev["text"], text = p + " " + text, None
                if text is None:
                    prev["page"] = page_no
                    first_text = False
                    continue
            first_text = False
            flow += pending_notes
            pending_notes = []
            flow.append({"type": kind, "text": text, "page": page_no})
    return flow + pending_notes, figures


def build_chapters(flow, figures, fallback_title):
    """Chapters for narration: (title, [(kind, text)], [figure keys to follow it]) where kind is
    "para" or "subheading". Top-level sections are chapters. A section that has figures is split
    at its subsections, so each figure follows the part of the paper that first mentions it
    (or, if nothing mentions it, the part where its caption is printed)."""
    title, in_refs = fallback_title, False
    front = {"title": None, "parts": [{"title": None, "items": []}]}
    sections = [front]
    for it in flow:
        t = it["type"]
        if t == "front":                   # affiliations: always with the title and authors
            front["parts"][0]["items"].append({**it, "type": "para"})
        elif t == "title":
            title = it["text"]
        elif t == "section":
            in_refs = False
            sections.append({"title": it["text"], "parts": [{"title": None, "items": []}]})
        elif t == "refs":
            in_refs = True
        elif in_refs and t != "marker":
            continue                       # stray reference-list text (figure legends after it still count)
        elif t == "subsection":
            sections[-1]["parts"].append({"title": it["text"], "items": []})
        else:
            sections[-1]["parts"][-1]["items"].append(it)

    describable = {k for k, f in figures.items() if f["caption"]}
    placed, order = {}, {}                 # key -> (section, part); key -> first position
    for pass_ in ("mention", "caption"):
        pos = 0
        for s_i, sec in enumerate(sections):
            for p_i, part in enumerate(sec["parts"]):
                for it in part["items"]:
                    pos += 1
                    if pass_ == "mention" and it["type"] == "para":
                        keys = mentions(it["text"])
                    elif pass_ == "caption" and it["type"] == "marker":
                        keys = [it["text"]]
                    else:
                        continue
                    for key in keys:
                        if key in describable and key not in placed:
                            placed[key], order[key] = (s_i, p_i), pos

    def content(part):
        return [("subheading" if it["type"] == "subheading" else "para", it["text"])
                for it in part["items"] if it["type"] != "marker"]

    chapters = []
    for s_i, sec in enumerate(sections):
        name = title if s_i == 0 else sec["title"]
        if not any(s == s_i for s, _ in placed.values()):
            items = []
            for part in sec["parts"]:
                if part["title"]:
                    items.append(("subheading", part["title"]))
                items += content(part)
            if items or s_i == 0:
                chapters.append((name, items, []))
            continue
        for p_i, part in enumerate(sec["parts"]):
            items = content(part)
            figs = sorted((k for k, where in placed.items() if where == (s_i, p_i)), key=order.get)
            if not items and not figs and part["title"] is None and p_i + 1 < len(sec["parts"]):
                continue                   # a section heading followed straight by its first subsection
            label = name if part["title"] is None else f"{name}: {part['title']}"
            chapters.append((label, items, figs))
    unplaced = sorted(set(figures) - set(placed))
    return title, chapters, unplaced


# ---------------------------------------------------------------- figures and tables

FIGURE_PROMPT = """You describe one figure or table from a scientific paper for someone listening to
the paper as an audiobook, who can't see it. They will already have heard its caption.

You get its caption and images of the page or pages where it is printed. The figure may be on a
different page from its caption, and the image may not carry its number, so match it to the
caption by content and panel letters.

Describe what it shows. For a figure: panel by panel if it has panels, say what kind of chart or
image it is, what is plotted against what or what is pictured, and the main pattern or result.
For a table: what the rows and columns are, and the main comparison. Enough to picture it and get
its point; leave out individual data points, labels and cells, and give at most one or two key
numbers. Don't restate the caption, and don't claim anything the image and caption don't support.
Plain sentences, no Markdown, no heading, 100 to 150 words. Return only the description."""

FIGURE_VERSION = hashlib.sha1(FIGURE_PROMPT.encode()).hexdigest()[:8]
META_REPLY = re.compile(r"(?i)^(understood|sure|okay|ok|certainly|i can(no|')t|i won't|i will not|i'm sorry|sorry|"
                        r"as an ai|i am unable|i'm unable|here is|here's)\b")


def figure_images(fig, pages):
    """The page images to show for a figure: pages marked as holding it, plus its caption's page.
    If nothing is marked and the caption opens its page, the figure is probably on the page before."""
    by_no = {p["page"]: p for p in pages}
    nums = set(fig["image_pages"]) | {fig["caption_page"]}
    if not fig["image_pages"] and fig["caption_at_top"] and fig["caption_page"] - 1 in by_no:
        sizes = sorted(len(p["text"].split()) for p in pages)
        prev = by_no[fig["caption_page"] - 1]
        if len(prev["text"].split()) < 0.4 * sizes[len(sizes) // 2]:
            nums.add(prev["page"])
    return [by_no[n]["image"] for n in sorted(nums) if n in by_no]


def describe_figures(cache, figures, pages, keys):
    """{key: description} for the given figures and tables; a failed one is left out."""
    def one(key):
        fig = figures[key]
        cached = cache / f"{re.sub(r'[^A-Za-z0-9]+', '-', key)}-{FIGURE_VERSION}.txt"
        if cached.exists():
            return key, cached.read_text()
        for attempt in range(2):
            try:
                text = " ".join(claude.ask(FIGURE_PROMPT, f"{key}. Caption: {fig['caption']}",
                                           figure_images(fig, pages)).split())
            except Exception as e:
                text, problem = None, str(e)
                continue
            if 50 <= len(text.split()) <= 400 and not META_REPLY.match(text):
                cached.write_text(text)
                return key, text
            problem = f"unusable reply: {text[:80]!r}"
        print(f"  {key}: {problem}; no description", file=sys.stderr)
        return key, None

    with ThreadPoolExecutor(claude.PARALLEL) as pool:
        return {k: d for k, d in pool.map(one, keys) if d}


# ---------------------------------------------------------------- the whole paper

def read_paper(pdf_path, image_dir):
    """PDF -> (title, author, blocks) ready for narration, with figure and table chapters."""
    from .pdf import read_pdf
    info, pages = read_pdf(pdf_path, image_dir, dpi=110)
    print(f"Reading {len(pages)} page(s) with Claude...", file=sys.stderr)
    cache = cache_dir(pdf_path)
    page_md = rebuild_pages(cache, pages)
    flow, figures = join_pages(page_md)
    title, chapters, unplaced = build_chapters(flow, figures, info["title"] or pdf_path.stem)
    for key in unplaced:
        print(f"  {key}: no caption found, so it isn't described", file=sys.stderr)
    keys = [k for _, _, figs in chapters for k in figs]
    if keys:
        print(f"Describing {len(keys)} figure(s) and table(s)...", file=sys.stderr)
    descriptions = describe_figures(cache, figures, pages, keys)
    blocks = []
    for name, items, figs in chapters:
        blocks.append(("chapter", name))
        blocks += items
        for key in figs:
            blocks += [("chapter", key), ("para", f"Caption: {figures[key]['caption']}")]
            if key in descriptions:
                blocks.append(("para", f"Description: {descriptions[key]}"))
    return title, info.get("author") or None, blocks

"""How unusual words in a paper are said. The voice's pronunciation step knows everyday English
but guesses other words from their spelling ("fibrin" as "FIB-rin"). For each paper, Claude
writes how its technical words, names and abbreviations are said, once (cached), and the voice
gets exact sounds for those words. Entries in pronunciations.txt (and pronunciations.local.txt)
win over Claude's list."""
import hashlib
import re
import sys

from . import ROOT, claude
from .science import LETTER_NAMES, speakable_science, spell_out

PROMPT = """You help a text-to-speech voice read a scientific paper aloud. Its pronunciation engine
(espeak-ng, American English) knows everyday English, but it guesses unfamiliar words from their
spelling and often gets them wrong. That includes many ordinary-looking academic words, not
just jargon: a Greek "ch" said as in "church", "-ysis" endings said "-eez", the wrong vowel or
stress in short technical words. When in doubt, include the word; an entry the engine didn't need
does no harm.

You get the paper's title and abstract, for context, and then every distinct word in the paper.
Return the words a scientist in this field would say differently from how such an engine would
probably guess: technical terms, names of genes, proteins, chemicals, organisms and software,
acronyms and abbreviations, and people's names. Leave out everyday words, numbers and units.

For each word give its pronunciation in IPA, American English, the way a scientist in this field
says it, with ˈ right before the vowel of the stressed syllable and ˌ before a secondary stress.
For an abbreviation said letter by letter, write LETTERS instead of IPA (the voice spells it out
itself, plural "s" included); an abbreviation said as a word gets that word's IPA (NASA: nˈæsə).
Examples of the format:
meiosis	maɪˈoʊsɪs
kinase	kˈaɪneɪs
DNA	LETTERS
NASA	nˈæsə

One word per line: the word exactly as written, a tab, the IPA. Return only the list."""

VERSION = hashlib.sha1(PROMPT.encode()).hexdigest()[:8]
OVERRIDE_FILES = [ROOT / "pronunciations.txt", ROOT / "pronunciations.local.txt"]   # the later one wins
VOWELS = set("aeiouæɐɑɒɔəɚɛɜɪʊʌᵻɨʏøœɤɯAIOWYQᵊ")
WORD = r"[A-Za-z][A-Za-z0-9']*"


def kokoro_ipa(ipa, known):
    """IPA in the voice's own symbols, with each stress mark right before its vowel (where the
    voice expects it) rather than at the start of the syllable."""
    for a, b in (("g", "ɡ"), ("r", "ɹ"), ("ɝ", "ɜːɹ"), ("ɫ", "l"), ("ʧ", "tʃ"), ("ʤ", "dʒ"), (".", ""),
                 ("ˑ", ""), ("ʍ", "w"), ("'", "ˈ"), ("/", "")):
        ipa = ipa.replace(a, b)
    out, pending = [], ""
    for ch in ipa.strip():
        if ch in "ˈˌ":
            pending = ch
            continue
        if pending and ch in VOWELS:
            out.append(pending)
            pending = ""
        out.append(ch)
    return known("".join(out))


def read_overrides():
    """[(word, sounds, is_prefix)] from pronunciations.txt and pronunciations.local.txt."""
    entries = {}
    for path in OVERRIDE_FILES:
        if not path.exists():
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            word, sep, sounds = (s.strip() for s in line.partition("="))
            if not sep or not re.fullmatch(WORD + r"\*?", word) or not sounds:
                print(f"  {path.name} line {n}: expected  word = how it sounds", file=sys.stderr)
                continue
            entries[word.rstrip("*").lower() if word.islower() else word.rstrip("*")] = (sounds, word.endswith("*"))
    return [(w, s, p) for w, (s, p) in entries.items()]


class Pronouncer:
    """Turns a paragraph of (already speakable) text into the voice's phonemes, using exact sounds
    for listed words and the voice's own pronunciation step for everything else."""

    def __init__(self, claude_list, overrides):
        letters = lambda m: spell_out(m.group())
        self.ipa, self.source, mine, claudes = {}, {}, [], []
        for word, sounds in claude_list.items():
            if sounds.upper() == "LETTERS":
                claudes.append((word, False, letters))
            else:
                self.ipa[word] = sounds
            self.source[word] = ("Claude", sounds)
        for word, sounds, prefix in sorted(overrides, key=lambda e: -len(e[0])):   # longest first:
            shadowed = [w for w in claude_list if (w.lower().startswith(word.lower()) if prefix else w.lower() == word.lower())]
            for w in shadowed:                 # Claude's entries your rule replaces       (deacetylase* before deacetyl*)
                self.ipa.pop(w, None)
                self.source.pop(w, None)
                claudes = [c for c in claudes if c[0] != w]
            self.source[word + ("*" if prefix else "")] = ("your list", sounds)
            if sounds.startswith("/"):
                self.ipa[word] = sounds.strip("/")
            else:
                mine.append((word, prefix, letters if sounds.lower() == "letters" else self.respell(sounds, prefix)))
        self.text_rules = mine + claudes       # your respellings first, so Claude's entries can't undo them
        self.compiled, self.ipa_pattern, self.ipa_ready = None, None, None

    @staticmethod
    def respell(sounds, prefix):
        said = sounds.rstrip("*")

        def swap(m):
            out = said + (m.group(2) if prefix else "")
            return out[:1].upper() + out[1:] if m.group(1)[:1].isupper() else out
        return swap

    def patterns(self):
        if self.compiled is None:
            self.compiled = []
            for word, prefix, swap in self.text_rules:
                flags = re.I if word.islower() else 0
                tail = r"([A-Za-z0-9']*)" if prefix else r"()"
                self.compiled.append((re.compile(rf"(?<![A-Za-z0-9])({re.escape(word)}){tail}(?![A-Za-z0-9])", flags), swap))
        return self.compiled

    def prepare(self, text):
        """Your respellings, and abbreviations spelled out letter by letter."""
        for pattern, swap in self.patterns():
            text = pattern.sub(swap, text)
        return text

    def phonemes(self, text, tokenizer, lang):
        """The voice's phonemes for the text: exact sounds for listed words, its own guess elsewhere."""
        text = self.prepare(text)
        if self.ipa_ready is None:
            ready = {w: kokoro_ipa(p, tokenizer.known) for w, p in self.ipa.items()}
            ready = {w: p for w, p in ready.items() if p}
            for w in list(ready):                       # "fibrin" also covers "Fibrin" at a sentence start
                if w.islower():
                    ready.setdefault(w.capitalize(), ready[w])
            self.ipa_ready = ready
            keys = sorted(ready, key=len, reverse=True)
            self.ipa_pattern = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(map(re.escape, keys)) + r")(?![A-Za-z0-9])") \
                if keys else None
        if not self.ipa_pattern:
            return tokenizer.phonemize(text, lang)
        parts, last = [], 0
        for m in self.ipa_pattern.finditer(text):
            between = text[last:m.start()]
            parts += [tokenizer.phonemize(between, lang) if re.search(r"[A-Za-z0-9]", between) else between.strip(),
                      self.ipa_ready[m.group(1)]]
            last = m.end()
        tail = text[last:]
        parts.append(tokenizer.phonemize(tail, lang) if re.search(r"[A-Za-z0-9]", tail) else tail.strip())
        return re.sub(r"\s+([,.;:!?)])", r"\1", " ".join(p for p in parts if p and p != "-"))

    def review(self, title, used_in):
        """A list of the words this paper's audio says in a special way, for checking."""
        rows = [(w, *self.source[w]) for w in sorted(self.source, key=str.lower)
                if re.search(rf"(?<![A-Za-z0-9]){re.escape(w.rstrip('*'))}", used_in, re.I if w.islower() else 0)]
        out = [f"# How unusual words are said: {title}", "",
               "Claude wrote the sounds for this paper; entries from pronunciations.txt (\"your list\") win.",
               "Sounds between slashes are IPA. To change a word, add a line such as  fibrin = fye-brin  to",
               "pronunciations.local.txt in the book2audio folder, then make the audiobook again.", "",
               "| Word | Said as | From |", "|---|---|---|"]
        out += [f"| {w} | {'letter by letter' if s.upper() == 'LETTERS' else s if src == 'your list' else '/' + s + '/'} | {src} |"
                for w, src, s in rows]
        return "\n".join(out) + "\n"


def word_list(texts, overrides):
    """The distinct words of the paper as it will be spoken, minus ones your list already covers
    and everyday short words."""
    covered = [(w, p) for w, _, p in overrides]

    def overridden(tok):
        low = tok.lower()
        return any(low.startswith(w.lower()) if p else low == w.lower() for w, p in covered)
    skip = set(LETTER_NAMES.values())
    return sorted({w for t in texts for w in re.findall(WORD, t)
                   if not (w.islower() and len(w) <= 3) and w.lower() not in skip and not overridden(w)})


def for_paper(cache, title, blocks):
    """A Pronouncer for this paper: Claude's list (asked once, then cached) plus your overrides.
    If Claude fails, the paper is read with your overrides only."""
    overrides = read_overrides()
    cached = cache / f"pronunciations-{VERSION}.tsv"
    if cached.exists():
        reply = cached.read_text()
    else:
        texts = [speakable_science(t) for k, t in blocks if k != "break"]
        abstract = next((nxt[1] for cur, nxt in zip(blocks, blocks[1:])
                         if cur[0] == "chapter" and cur[1].lower() == "abstract"), "(none)")
        words = word_list(texts, overrides)
        try:
            reply = claude.ask(PROMPT, f"Title: {title}\n\nAbstract: {abstract}\n\nWords:\n" + "\n".join(words))
            cached.write_text(reply)
        except Exception as e:
            print(f"  Claude couldn't list pronunciations ({e}); using pronunciations.txt only", file=sys.stderr)
            reply = ""
    claude_list = {}
    for line in reply.splitlines():
        word, _, sounds = (s.strip() for s in line.partition("\t"))
        if re.fullmatch(WORD, word) and sounds:
            claude_list[word] = sounds
    return Pronouncer(claude_list, overrides)

"""How particular words are said. The voice guesses unfamiliar words (invented names, words in
other languages) from their spelling; a pronunciation list (--pronunciations FILE) tells it
exactly. Each line is  word = how it sounds,  written as a respelling (SEAL-sin), as IPA
between slashes, or as "letters"."""
import re
import sys
from pathlib import Path

WORD = r"[A-Za-z][A-Za-z0-9'’-]*"
VOWELS = set("aeiouæɐɑɒɔəɚɛɜɪʊʌᵻɨ")

# The respelling key, longest spellings first. A syllable in CAPITALS is stressed.
RESPELL_VOWELS = [
    ("eye", "aɪ"), ("igh", "aɪ"), ("air", "ɛɹ"), ("are", "ɛɹ"), ("ear", "ɪɹ"), ("ire", "aɪɹ"),
    ("ore", "ɔːɹ"),
    ("ay", "eɪ"), ("ai", "eɪ"), ("ah", "ɑː"), ("aw", "ɔː"), ("ow", "aʊ"), ("oy", "ɔɪ"), ("oi", "ɔɪ"),
    ("oo", "uː"), ("ew", "juː"), ("eu", "juː"), ("ee", "iː"), ("ea", "iː"), ("oh", "oʊ"), ("oa", "oʊ"),
    ("eh", "ɛ"), ("uh", "ʌ"), ("ih", "ɪ"), ("ye", "aɪ"), ("ar", "ɑːɹ"), ("or", "ɔːɹ"),
    ("er", "ɜːɹ"), ("ur", "ɜːɹ"), ("ir", "ɜːɹ"),
    ("a", "æ"), ("e", "ɛ"), ("i", "ɪ"), ("o", "ɑː"), ("u", "ʌ"),
]
MAGIC_E = {"a": "eɪ", "e": "iː", "i": "aɪ", "o": "oʊ", "u": "uː"}   # "vice", "ace", "rope"
RESPELL_CONSONANTS = [
    ("tch", "tʃ"), ("sh", "ʃ"), ("ch", "tʃ"), ("th", "θ"), ("dh", "ð"), ("zh", "ʒ"), ("kh", "x"),
    ("ng", "ŋ"), ("ph", "f"), ("wh", "w"), ("qu", "kw"), ("ck", "k"),
    ("j", "dʒ"), ("g", "ɡ"), ("c", "k"), ("x", "ks"), ("y", "j"), ("r", "ɹ"),
]


def respelling_to_ipa(said):
    """'SEAL-sin' -> 'sˈiːlsɪn'. Syllables are split by hyphens; a syllable in capitals is
    stressed (the first one most), and with none in capitals the first syllable is."""
    syllables = [s for s in said.split("-") if s]
    stressed = [i for i, s in enumerate(syllables) if len(s) > 1 and s.isupper()] or [0]
    out = []
    for i, syl in enumerate(syllables):
        mark = "ˈ" if i == stressed[0] else "ˌ" if i in stressed else ""
        out.append(syllable_ipa(syl.lower(), mark))
    return "".join(out)


def syllable_ipa(s, mark):
    """One respelled syllable as IPA, with the stress mark (if any) before its first vowel."""
    s = re.sub(r"([b-df-hj-np-tv-z])\1", r"\1", s)                     # "nn" is one n
    s = re.sub(r"c(?=[eiy])", "s", s)                                   # "ace", "cinder"
    magic = None
    if re.fullmatch(r"[^aeiouy]*[aeiou][^aeiouyr]e", s):                # magic e: "vice", "ace"
        s = s[:-1]
        magic = re.search(r"[aeiou]", s).start()
    if s.endswith("y") and not re.search(r"[aeiou]", s[:-1]):         # "fly", "my"
        s = s[:-1] + "ye"
    out, i, marked = [], 0, False
    while i < len(s):
        if i == magic:
            out.append(mark + MAGIC_E[s[i]])
            marked, i = True, i + 1
            continue
        for spelling, ipa in (RESPELL_VOWELS if s[i] in "aeiou" or s.startswith("ye", i) and i else []):
            if s.startswith(spelling, i):
                if spelling == "e" and s[i + 1:i + 2] in tuple("aiou"):  # "meoo": mee-oo
                    ipa = "iː"
                elif spelling == "uh" and not mark:
                    ipa = "ə"
                elif spelling in ("er", "ur", "ir") and not mark:
                    ipa = "ɚ"
                if mark and not marked:
                    ipa, marked = mark + ipa, True
                out.append(ipa)
                i += len(spelling)
                break
        else:
            for spelling, ipa in RESPELL_CONSONANTS:
                if s.startswith(spelling, i):
                    out.append(ipa)
                    i += len(spelling)
                    break
            else:
                out.append(s[i])
                i += 1
    return "".join(out)


def plural(ipa):
    """The sound of a possessive or plural ending after a word: s, z or iz."""
    last = ipa.rstrip("ː")[-1:]
    if ipa.endswith(("s", "z", "ʃ", "ʒ", "tʃ", "dʒ")):
        return "ɪz"
    return "s" if last in ("p", "t", "k", "f", "θ") else "z"


def read_list(path):
    """{word: sounds} from a pronunciation list. Sounds are IPA, or 'LETTERS'."""
    entries = {}
    for n, line in enumerate(Path(path).expanduser().read_text().splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        word, sep, said = (s.strip() for s in line.partition("="))
        if not sep or not re.fullmatch(WORD, word) or not said:
            print(f"  {Path(path).name} line {n}: expected  word = how it sounds", file=sys.stderr)
            continue
        if said.lower() == "letters":
            entries[word.lower()] = "LETTERS"
        elif said.startswith("/"):
            entries[word.lower()] = said.strip("/ ").replace("'", "ˈ")
        elif re.fullmatch(r"[A-Za-z]+(-[A-Za-z]+)*", said):
            entries[word.lower()] = respelling_to_ipa(said)
        else:
            print(f"  {Path(path).name} line {n}: write the sounds with letters and hyphens "
                  f"(SEAL-sin), or as IPA between slashes", file=sys.stderr)
    return entries


class Pronouncer:
    """Turns a paragraph into the voice's phonemes: the listed sounds for listed words (and their
    possessives and plurals), the voice's own guess for everything else."""

    def __init__(self, entries):
        self.entries = entries
        self.ready = None
        keys = sorted(entries, key=len, reverse=True)
        self.pattern = re.compile(r"(?<![A-Za-z0-9'’-])(" + "|".join(map(re.escape, keys)) +
                                  r")(['’]s|s)?(?![A-Za-z0-9])", re.I) if keys else None

    def sounds(self, word, tokenizer):
        if self.ready is None:
            self.ready = {w: "".join(c for c in stress_before_vowel(p) if c in tokenizer.vocab) if p != "LETTERS" else p
                          for w, p in self.entries.items()}
        return self.ready[word.lower()]

    def phonemes(self, text, tokenizer, lang):
        if not self.pattern:
            return tokenizer.phonemize(text, lang)
        parts, last = [], 0
        for m in self.pattern.finditer(text):
            parts.append(guess(text[last:m.start()], tokenizer, lang))
            ipa = self.sounds(m.group(1), tokenizer)
            if ipa == "LETTERS":
                ipa = tokenizer.phonemize(" ".join(m.group(1).upper()), lang).strip(" .")
            if m.group(2):
                ipa += plural(ipa)
            parts.append(ipa)
            last = m.end()
        parts.append(guess(text[last:], tokenizer, lang))
        return re.sub(r"\s+([,.;:!?…)])", r"\1", " ".join(p for p in parts if p and p != "-"))

    def used_in(self, text):
        """The listed words that appear in the text."""
        return sorted({m.group(1).lower() for m in self.pattern.finditer(text)}) if self.pattern else []


def guess(text, tokenizer, lang):
    """The voice's own phonemes for a stretch of text (bare punctuation is kept as is)."""
    return tokenizer.phonemize(text, lang) if re.search(r"[A-Za-z0-9]", text) else text.strip()


def stress_before_vowel(ipa):
    """IPA in the voice's own symbols, with each stress mark right before its vowel (where the
    voice expects it) rather than at the start of the syllable: /ˈsiːl/ -> sˈiːl."""
    for a, b in (("g", "ɡ"), ("r", "ɹ"), ("ɝ", "ɜːɹ"), ("ɫ", "l"), ("ʧ", "tʃ"), ("ʤ", "dʒ"),
                 (".", ""), ("ˑ", ""), ("ʍ", "w")):
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
    return "".join(out)


def load(paths):
    """A Pronouncer from the --pronunciations files (a later file wins), or None."""
    entries = {}
    for path in paths or []:
        if not Path(path).expanduser().is_file():
            sys.exit(f"Not found: {path}")
        entries.update(read_list(path))
    return Pronouncer(entries) if entries else None

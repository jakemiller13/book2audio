"""Text fixes so the voice reads scientific notation the way a person would: units, comparison
signs, ranges and common abbreviations. Used for papers instead of the book rules."""
import re

SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
MICRO = "[µμu]"
UNITS = [                                   # (pattern after a number, spoken form)
    (rf"{MICRO}m", "micrometers"), (r"nm", "nanometers"), (r"mm", "millimeters"),
    (rf"{MICRO}M", "micromolar"), (r"mM", "millimolar"), (r"nM", "nanomolar"), (r"pM", "picomolar"),
    (rf"{MICRO}[lL]", "microliters"), (r"m[lL]", "milliliters"), (r"n[lL]", "nanoliters"),
    (rf"{MICRO}g", "micrograms"), (r"mg", "milligrams"), (r"ng", "nanograms"),
    (r"kDa", "kilodaltons"), (r"Da", "daltons"), (r"kb", "kilobases"), (r"bp", "base pairs"),
    (r"nt", "nucleotides"), (r"hrs?|h", "hours"), (r"min", "minutes"), (r"ms", "milliseconds"),
]
ABBREVIATIONS = [
    (r"\bvs\.", "versus"), (r"\be\.g\.,?", "for example,"), (r"\bi\.e\.,?", "that is,"),
    (r"\bFigs\.", "Figures"), (r"\bFig\.?(?=\s*\d)", "Figure"), (r"\bEq\.(?=\s*\d)", "Equation"),
    (r"\b(IC|EC|LD|ED|CC)50\b", lambda m: " ".join(m.group(1)) + " 50"),
    (r"\bkcat\b", "k cat"),
]


def speakable_science(text):
    t = text.translate(SUPERSCRIPTS)
    t = re.sub(r"(?<=\d)\s*[sS]\s*[−-]\s*1\b", " per second", t)                 # 5 s−1
    t = re.sub(r"(?<=\d)\s*[hH]\s*[−-]\s*1\b", " per hour", t)
    t = re.sub(r"(?<![\w.])[−-](?=\d)", "minus ", t)                             # −80 °C
    t = re.sub(r"\s*<\s*(?=[\d.])", " less than ", t)
    t = re.sub(r"\s*>\s*(?=[\d.])", " greater than ", t)
    t = re.sub(r"~\s*(?=\d)", "about ", t)
    t = re.sub(r"(?<=\d)([a-zA-Z]?)\s*[–—]\s*(?=\d)", r"\1 to ", t)             # 10–22, 1a–1g
    for pattern, spoken in UNITS:
        t = re.sub(rf"(?<=\d)\s*(?:{pattern})(?![A-Za-z])", " " + spoken, t)
    t = re.sub(r"(?<=\d)\s+M(?![A-Za-z])", " molar", t)          # single letters need a space:
    t = re.sub(r"(?<=\d)\s+s(?![A-Za-z])", " seconds", t)        # "1990s" stays as it is
    for pattern, spoken in ABBREVIATIONS:
        t = re.sub(pattern, spoken, t)
    return re.sub(r"\s+", " ", t).strip()

"""Text fixes so the voice reads scientific notation the way a person would: units, comparison
signs, ranges and common abbreviations. Used for papers instead of the book rules."""
import re

SUP_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"   # not one Unicode range: ¹²³ live apart from the rest
SUPERSCRIPTS = str.maketrans(SUP_DIGITS + "⁺⁻", "0123456789+-")
MICRO = "[µμu]"
UNITS = [                                   # (pattern after a number, spoken form)
    (rf"{MICRO}m", "micrometers"), (r"nm", "nanometers"), (r"mm", "millimeters"),
    (rf"{MICRO}M", "micromolar"), (r"mM", "millimolar"), (r"nM", "nanomolar"), (r"pM", "picomolar"),
    (rf"{MICRO}[lL]", "microliters"), (r"m[lL]", "milliliters"), (r"n[lL]", "nanoliters"),
    (rf"{MICRO}g", "micrograms"), (r"mg", "milligrams"), (r"ng", "nanograms"),
    (r"kDa", "kilodaltons"), (r"Da", "daltons"), (r"kb", "kilobases"), (r"bp", "base pairs"),
    (r"nt", "nucleotides"), (r"hrs?|h", "hours"), (r"min", "minutes"), (r"ms", "milliseconds"),
    (r"Å", "angstroms"),
]
# single letters need a space after the number: "1990s" stays as it is
SPACED_UNITS = [(r"M", "molar"), (r"s", "seconds"), (r"L", "liters")]
PER = {"M": "molar", "s": "second", "h": "hour", "min": "minute"}       # s−1 -> per second
MICRO_ALONE = {"g": "micrograms", "l": "microliters", "L": "microliters", "m": "micrometers",
               "M": "micromolar", "s": "microseconds"}                 # a unit with no number: (μg/mL)
SLASH_UNITS = [(r"m[lL]", "milliliter"), (rf"{MICRO}[lL]", "microliter"), (r"L", "liter"),   # mg/mL
               (r"min", "minute"), (r"h", "hour"), (r"s", "second"), (r"kg", "kilogram"), (r"g", "gram")]
GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "Δ": "delta", "ε": "epsilon",
         "θ": "theta", "κ": "kappa", "λ": "lambda", "μ": "mu", "π": "pi", "σ": "sigma", "τ": "tau",
         "φ": "phi", "ϕ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega", "Ω": "omega"}
IONS = {"Zn": "zinc", "Mg": "magnesium", "Mn": "manganese", "Ca": "calcium", "Fe": "iron",
        "Cu": "copper", "Co": "cobalt", "Ni": "nickel", "Cd": "cadmium", "Na": "sodium",
        "K": "potassium", "Li": "lithium"}
ABBREVIATIONS = [
    (r"\bvs\.", "versus"), (r"\be\.g\.,?", "for example,"), (r"\bi\.e\.,?", "that is,"),
    (r"\bFigs\.", "Figures"), (r"\bFig\.?(?=\s*\d)", "Figure"), (r"\bEq\.(?=\s*\d)", "Equation"),
    (r"\b(IC|EC|LD|ED|CC)50\b", lambda m: " ".join(m.group(1)) + " 50"),
    (r"\bkcat\s*/\s*K[Mm]\b", "k cat over K M"), (r"\bkcat\b", "k cat"), (r"\bK[Mm]\b", "K M"),
    (r"\b([A-Z])\.\s+(?=[a-z]{3,})", r"\1 "), (r"\bspp?\.", "species"),          # E. coli, sp.
    (r"\b(NADP?)\+", r"\1 plus"), (r"\(w/v\)", "(weight per volume)"), (r"\(v/v\)", "(volume per volume)"),
]
READ_AS = [                                 # how a written form is meant to be read; how individual
    (r"ac\(et\)yl", "acetyl"),              # words sound is up to pronounce.py and pronunciations.txt
    (r"([A-Z]{2,})acyl", r"\1 acyl"),       # a peptide named for its acyl group: ABCacyl
]


ROMAN_AFTER = re.compile(r"\b((?i:(?:sub)?(?:types?|class(?:es)?|groups?|complex(?:es)?|phases?|stages?|grades?|"
                         r"famil(?:y|ies)|clusters?|clades?|domains?|parts?|regions?|sites?|motifs?|loops?|"
                         r"generations?|levels?|categor(?:y|ies)|forms?|(?:pro|meta|ana|telo)phase|meiosis|ligases?|"
                         r"polymerases?|pol|topoisomerases?|photosystems?|chromosomes?))\s+)([IVX]+)(?=[a-z]?(?![A-Za-z]))")
ROMAN_SUBTYPE = re.compile(r"(?<![\w-])([IVX]+)(?=-[A-Z]\d?\b)")      # IV-C, III-B: a subtype, whatever precedes
ROMAN_MORE = re.compile(r"(\d(?:-[A-Z]\d?)?[a-z]?(?:\s*,\s*|\s+and\s+|\s+or\s+|\s*/\s*)(?:and\s+|or\s+)?)"
                        r"([IVX]+)(?=[a-z]?(?![A-Za-z]))")


# Letters as words the voice always says as that letter ("A" alone is read as "uh", "ay" as "eye")
LETTER_NAMES = dict(A="eigh", B="bee", C="see", D="dee", E="ee", F="ef", G="gee", H="aitch", I="eye", J="jay",
                    K="kay", L="ell", M="em", N="en", O="oh", P="pee", Q="cue", R="ar", S="ess", T="tee", U="you",
                    V="vee", W="double-you", X="ex", Y="why", Z="zee")
PLURAL_ES = {"H", "S", "X"}


def padded(m, words):
    """Letter names set apart from digits they touch: 'R15HL1488' -> 'R15 aitch ell 1488'."""
    s = m.string
    return ((" " if m.start() and s[m.start() - 1].isdigit() else "") + words
            + (" " if m.end() < len(s) and s[m.end()].isdigit() else ""))


def spell_out(token):
    """'EM' -> 'ee em', 'MGEs' -> 'em gee ees', 'crRNA' -> 'see ar ar en eigh', 'BRCA1' -> 'bee ar see eigh 1'."""
    base, plural = (token[:-1], True) if re.search(r"[A-Z0-9]s$", token) else (token, False)
    parts = re.findall(r"\d+|[A-Za-z]", base)
    words = [p if p.isdigit() else LETTER_NAMES[p.upper()] for p in parts]
    if plural and words:
        words[-1] += "es" if parts[-1].upper() in PLURAL_ES else "s"
    return " ".join(words)


# Two capitals are almost always said letter by letter, but "EM" on its own, or at the end of
# "cryo-EM", can come out as the word "em"; letter names are said the same way every time.
SPELL_TWO = re.compile(r"(?<![A-Za-z'])(?!(?:OF|IN|ON|AT|TO|BY|OR|AN|AS|IS|IT|IF|NO|SO|UP|US|WE|BE|DO|GO|HE|"
                       r"ME|MY|OK|II|IV|VI|IX|XI|XV|XX)(?![A-Za-z]))([A-Z])([A-Z])(?![A-Za-z])")


def roman(s):
    """'IV' -> 4; None if it isn't a well-formed Roman numeral."""
    values = {"I": 1, "V": 5, "X": 10}
    n = sum(-values[a] if values[a] < values[b] else values[a] for a, b in zip(s, s[1:] + "I"))
    return n if 0 < n < 40 and re.fullmatch(r"X{0,3}(IX|IV|V?I{0,3})", s) else None


def arabic_numerals(t):
    """'type IV-C' -> 'type 4-C', 'class IIa' -> 'class 2a', 'types I and III' -> 'types 1 and 3'.
    The voice would say 'roman four', or 'eye' for I."""
    swap = lambda m: m.group(1) + str(roman(m.group(2)) or m.group(2))
    t = ROMAN_AFTER.sub(swap, t)
    t = ROMAN_SUBTYPE.sub(lambda m: str(roman(m.group(1)) or m.group(1)), t)
    for _ in range(4):                     # lists: "types I, II and III"
        t = ROMAN_MORE.sub(swap, t)
    return t


def say_unit(t, pattern, spoken, space=r"\s*"):
    """'5 mM' -> '5 millimolar'; singular after 1 or an article: '1 hour', 'a 3 kilobase fragment'."""
    def say(m):
        one = m.group("n") == "1" or m.group("art")
        word = spoken[:-1] if one and spoken.endswith("s") else spoken
        return f"{m.group('art') or ''}{m.group('n')} {word}"
    return re.sub(rf"(?P<art>\b(?:[Aa]n?|[Tt]he) )?(?P<n>(?<![\w.])\d[\d.,]*?){space}(?:{pattern})(?![A-Za-z])",
                  say, t)


def speakable_science(text):
    t = arabic_numerals(text)                                                          # type IV-C
    t = re.sub(r"(?<![\w.])(\d{1,2})\s?['′’](?![A-Za-z]{2})", r"\1 prime ", t)              # 5′ end, 3’-OH
    t = re.sub(r"(?<=\d)\s*[×x]\s*10(?=[⁻^" + SUP_DIGITS + "])", " times 10", t)     # 3.1 × 10³
    power = lambda m: "10 to the " + m.group(1).translate(SUPERSCRIPTS).replace("-", "minus ").replace("−", "minus ")
    t = re.sub(rf"\b10(⁻?[{SUP_DIGITS}]+)", power, t)
    t = re.sub(r"\b10\^([−-]?\d+)", power, t)                                           # 10^-3
    t = re.sub(r"(?<=[A-Za-zÅ])²(?![⁺⁻])", " squared", t)                               # R², Å²
    t = re.sub(r"(?<=[A-Za-z])³(?![⁺⁻])", " cubed", t)
    t = t.translate(SUPERSCRIPTS)
    for _ in range(3):                                                                  # M−1 s−1
        t = re.sub(r"(\d|per \w+)\s*(M|s|h|min)\s*\^?\s*[−-]\s*1\b", lambda m: f"{m.group(1)} per {PER[m.group(2)]}", t)
    t = re.sub(r"\b(" + "|".join(IONS) + r")\d?\+(?![\w+])", lambda m: IONS[m.group(1)], t)   # Zn2+
    t = re.sub(r"\s*±\s*", " plus or minus ", t)
    t = re.sub(r"(?<=\d)×-(?=[A-Za-z])", "-", t)                                        # 6×-His
    t = re.sub(r"\s*×\s*", " times ", t)                                                # 4000 × g
    t = re.sub(r"\s*°\s*C\b", " degrees Celsius", t)
    t = re.sub(r"(?<=\d)\s*°", " degrees", t)
    t = re.sub(r"(?<![\w.])[−-](?=\d)", "minus ", t)                             # −80 °C
    t = re.sub(r"\s*≤\s*", " less than or equal to ", t)
    t = re.sub(r"\s*≥\s*", " greater than or equal to ", t)
    t = re.sub(r"\s*≈\s*", " approximately ", t)
    t = re.sub(r"\s*<\s*(?=[\d.])", " less than ", t)
    t = re.sub(r"\s*>\s*(?=[\d.])", " greater than ", t)
    t = re.sub(r"[~∼]\s*(?=\d)", "about ", t)
    t = re.sub(r"\s*=\s*", " equals ", t)                                       # n = 3
    t = re.sub(r"\s*•\s*", " with ", t)                                         # enzyme•inhibitor
    t = re.sub(r"(?<=\d)([a-zA-Z]?)\s*[–—‒-]\s*(?=\d)", r"\1 to ", t)           # 10–22, 10-20, 1a–1g
    t = re.sub(r"(?<=\d[a-z])\s*[–—-]\s*(?=[a-z]\b)", " to ", t)                # Fig. 1a–c
    for pattern, spoken in UNITS:
        t = say_unit(t, pattern, spoken)
    for pattern, spoken in SPACED_UNITS:
        t = say_unit(t, pattern, spoken, space=r"\s+")
    t = re.sub(r"(?<!\w)[µμ](g|[lL]|m|M|s)\b", lambda m: MICRO_ALONE[m.group(1)], t)   # (μg/mL)
    for pattern, spoken in SLASH_UNITS:
        t = re.sub(rf"(?<=[a-z])\s*/\s*(?:{pattern})(?![A-Za-z])", " per " + spoken, t)
    for pattern, spoken in ABBREVIATIONS + READ_AS:
        t = re.sub(pattern, spoken, t)
    # Decimals written out: the pronunciation step can take a decimal point for a full stop
    # ("3.1" -> "three. one", with a pause), depending on the rest of the sentence.
    t = re.sub(r"(?<![\d.])\d+(?:\.\d+){2,}(?!\d|\.\d)", lambda m: " point ".join(m.group().split(".")), t)  # 9.5.1
    t = re.sub(r"(?<![\d.])(\d*)\.(\d+)(?!\d|\.\d)",
               lambda m: (m.group(1) + " " if m.group(1) else "") + "point " + " ".join(m.group(2)), t)
    t = re.sub(f"([A-Za-z])?([{''.join(GREEK)}])([A-Za-z0-9])?",                         # β-sheet, TNFα, Gαq
               lambda m: " ".join(s for s in (m.group(1), GREEK[m.group(2)], m.group(3)) if s), t)
    t = t.replace("−", "-")                                                     # a minus sign between labels
    t = re.sub(r"(?<=\w)_(?=\w)", " ", t)                                     # k_deg -> "k deg"
    t = SPELL_TWO.sub(lambda m: padded(m, spell_out(m.group())), t)             # cryo-EM -> cryo-ee em
    # Capitals glued into a name after a digit are letters of the name, not a Roman numeral:
    # Cas10IVc -> "Cas10 eye vee c" (a standalone "type IV" became "type 4" above)
    t = re.sub(r"\b([A-Za-z]+\d+)([A-Z]{2,})(?=[a-z]?\d*(?![A-Za-z]))", lambda m: f"{m.group(1)} {spell_out(m.group(2))} ", t)
    return re.sub(r"\s+", " ", t).strip()

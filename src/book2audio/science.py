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
         "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega", "Ω": "omega"}
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
RESPELLINGS = [                             # words the voice says wrong, spelled the way they sound
    (r"ac\(et\)yl", "acetyl"),              # deac(et)ylase: read every letter
    (r"([A-Z]{2,})acyl", r"\1 ace-il"),     # a peptide named for its acyl group: ABCacyl
    (r"\b([Dd])eacetylase", r"\1e-a-setta-lace"),   # "dee-uh-SET-uh-lay-sus"
    (r"\b([Dd])eacetyl", r"\1e-a-setta-l"),         # deacetylation, deacetylated
    (r"\b([Ee])ukaryot(?=e|ic)", lambda m: ("Y" if m.group(1) == "E" else "y") + "ou-carry-ot"),  # "YOU-carry-otes"
    (r"\b([Pp])rokaryot(?=e|ic)", r"\1ro-carry-ot"),                                              # "PRO-carry-otes"
    (r"\b([Dd])e(?=a(?:cyl|cetyl|mid|min)|lactyl)", r"\1e-"),   # deacylase: "dee-ACE...", not "dee-sil..."
    (r"(?<=cyl|tyl)ase", "ace"),            # -ases with an s sound: "dee-ace-il-ay-sus"
    (r"\b([Aa])cyl", lambda m: m.group(1) + "ce-il"),   # acyl: "ACE-il", not "a-KILL"
]


def say_unit(t, pattern, spoken, space=r"\s*"):
    """'5 mM' -> '5 millimolar'; singular after 1 or an article: '1 hour', 'a 3 kilobase fragment'."""
    def say(m):
        one = m.group("n") == "1" or m.group("art")
        word = spoken[:-1] if one and spoken.endswith("s") else spoken
        return f"{m.group('art') or ''}{m.group('n')} {word}"
    return re.sub(rf"(?P<art>\b(?:[Aa]n?|[Tt]he) )?(?P<n>(?<![\w.])\d[\d.,]*?){space}(?:{pattern})(?![A-Za-z])",
                  say, t)


def speakable_science(text):
    t = re.sub(r"(?<=\d)\s*[×x]\s*10(?=[⁻^" + SUP_DIGITS + "])", " times 10", text)  # 3.1 × 10³
    power = lambda m: "10 to the " + m.group(1).translate(SUPERSCRIPTS).replace("-", "minus ").replace("−", "minus ")
    t = re.sub(rf"\b10(⁻?[{SUP_DIGITS}]+)", power, t)
    t = re.sub(r"\b10\^([−-]?\d+)", power, t)                                           # 10^-3
    t = re.sub(r"(?<=[A-Za-zÅ])²(?![⁺⁻])", " squared", t)                               # R², Å²
    t = re.sub(r"(?<=[A-Za-z])³(?![⁺⁻])", " cubed", t)
    t = t.translate(SUPERSCRIPTS)
    for _ in range(3):                                                                  # M−1 s−1
        t = re.sub(r"(\d|per \w+)\s*(M|s|h|min)\s*[−-]\s*1\b", lambda m: f"{m.group(1)} per {PER[m.group(2)]}", t)
    t = re.sub(r"\b(" + "|".join(IONS) + r")\d?\+(?![\w+])", lambda m: IONS[m.group(1)], t)   # Zn2+
    t = re.sub(r"\s*±\s*", " plus or minus ", t)
    t = re.sub(r"(?<=\d)×-(?=[A-Za-z])", "-", t)                                        # 6×-His
    t = re.sub(r"\s*×\s*", " times ", t)                                                # 4000 × g
    t = re.sub(r"\s*°\s*C\b", " degrees Celsius", t)
    t = re.sub(r"(?<=\d)\s*°", " degrees", t)
    t = re.sub(r"(?<![\w.])[−-](?=\d)", "minus ", t)                             # −80 °C
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
    for pattern, spoken in ABBREVIATIONS + RESPELLINGS:
        t = re.sub(pattern, spoken, t)
    # Decimals written out: the pronunciation step can take a decimal point for a full stop
    # ("3.1" -> "three. one", with a pause), depending on the rest of the sentence.
    t = re.sub(r"(?<![\d.])\d+(?:\.\d+){2,}(?!\d|\.\d)", lambda m: " point ".join(m.group().split(".")), t)  # 9.5.1
    t = re.sub(r"(?<![\d.])(\d*)\.(\d+)(?!\d|\.\d)",
               lambda m: (m.group(1) + " " if m.group(1) else "") + "point " + " ".join(m.group(2)), t)
    t = re.sub(f"([A-Za-z])?([{''.join(GREEK)}])([A-Za-z0-9])?",                         # β-sheet, TNFα, Gαq
               lambda m: " ".join(s for s in (m.group(1), GREEK[m.group(2)], m.group(3)) if s), t)
    t = t.replace("−", "-")                                                     # a minus sign between labels
    return re.sub(r"\s+", " ", t).strip()

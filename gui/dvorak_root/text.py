"""TLatex-safe text. No ROOT import, so the GUI can use it too.

Interactive ROOT on macOS draws a string one byte at a time in Mac Roman,
so the UTF-8 bytes of an em dash (E2 80 94) show up as ‚Äî and a micro
sign as ¬µ. JSROOT draws the same string as Unicode, so the two windows
disagree. Every label therefore leaves here as ASCII: symbols become TLatex
tokens (#mu, ^{2}, #Delta) that both renderers draw, accented letters become
TLatex accents (#ddot{a}), and anything else falls back to its plain letter.
"""

from __future__ import annotations

import unicodedata

# Destinations stay free of quotes and backslashes so they can be applied to a
# JSON document or a C string literal without breaking its structure. Each one
# was checked in both JSROOT and interactive ROOT.
_SYMBOLS: dict[str, str] = {
    # Units and math
    "µ": "#mu",
    "°": "^{#circ}",
    "±": "#pm",
    "∓": "#mp",
    "×": "#times",
    "÷": "#divide",
    "·": "#upoint",
    "⋅": "#upoint",
    "•": "#bullet",
    "∘": "#circ",
    "≈": "#approx",
    "≠": "#neq",
    "≡": "#equiv",
    "≤": "#leq",
    "≥": "#geq",
    "≪": "<<",
    "≫": ">>",
    "∼": "~",
    "∝": "#propto",
    "∞": "#infty",
    "∂": "#partial",
    "∇": "#nabla",
    "∫": "#int",
    "∑": "#sum",
    "∏": "#prod",
    "√": "#surd",
    "∈": "#in ",
    "∉": "#notin",
    "∩": "#cap",
    "∪": "#cup",
    "∧": "#wedge",
    "∨": "#vee",
    "⊕": "#oplus",
    "⊗": "#otimes",
    "∠": "#angle",
    "⊥": "#perp",
    "∆": "#Delta",
    "Ω": "#Omega",
    "Å": "#AA",
    "ℏ": "#hbar",
    "ℓ": "l",
    "←": "#leftarrow",
    "→": "#rightarrow",
    "↑": "#uparrow",
    "↓": "#downarrow",
    "↔": "#leftrightarrow",
    "⇐": "#Leftarrow",
    "⇒": "#Rightarrow",
    "‰": " per mil",
    # Typography
    "—": "-",
    "–": "-",
    "−": "-",
    "‐": "-",
    "‑": "-",
    "…": "...",
    "’": "'",
    "‘": "'",
    "′": "'",
    "″": "''",
    "“": "'",
    "”": "'",
    "«": "<<",
    "⟨": "<",
    "⟩": ">",
    "»": ">>",
    " ": " ",
    " ": " ",
    " ": " ",
    "​": "",
    "ß": "ss",
    "æ": "ae",
    "Æ": "AE",
    "œ": "oe",
    "Œ": "OE",
    "ø": "o",
    "Ø": "O",
    "đ": "d",
    "Đ": "D",
    "ł": "l",
    "Ł": "L",
    "ı": "i",
}

_GREEK = (
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
    "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron", "pi", "rho",
    "varsigma", "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega",
)
for _offset, _name in enumerate(_GREEK):
    _SYMBOLS[chr(0x03B1 + _offset)] = "#" + _name
    if _name != "varsigma":
        _SYMBOLS[chr(0x0391 + _offset)] = "#" + _name[0].upper() + _name[1:]
_SYMBOLS["ϑ"] = "#vartheta"
_SYMBOLS["ϕ"] = "#varphi"
_SYMBOLS["ϵ"] = "#varepsilon"

_SUPERSCRIPTS = dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ", "0123456789+-=()n"))
_SUBSCRIPTS = dict(zip("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎", "0123456789+-=()"))

# Combining marks with a TLatex accent. Others (ring, cedilla, ogonek…) drop
# to the bare letter.
_ACCENTS = {
    "̀": "#grave",
    "́": "#acute",
    "̂": "#hat",
    "̃": "#tilde",
    "̄": "#bar",
    "̇": "#dot",
    "̈": "#ddot",
    "̋": "#ddot",
    "̌": "#check",
}


def root_text(text: object) -> str:
    """Rewrite one label as ASCII TLatex that JSROOT and Legacy ROOT draw alike."""
    value = str(text)
    if value.isascii():
        return value
    out: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char.isascii():
            out.append(char)
            index += 1
            continue
        for table, token in ((_SUPERSCRIPTS, "^"), (_SUBSCRIPTS, "_")):
            if char in table:
                end = index
                run = []
                while end < len(value) and value[end] in table:
                    run.append(table[value[end]])
                    end += 1
                out.append(f"{token}{{{''.join(run)}}}")
                index = end
                break
        else:
            out.append(_char_text(char))
            index += 1
    return "".join(out)


def _char_text(char: str) -> str:
    mapped = _SYMBOLS.get(char)
    if mapped is not None:
        return mapped
    decomposed = unicodedata.normalize("NFD", char)
    base = "".join(part for part in decomposed if not unicodedata.combining(part))
    marks = [part for part in decomposed if unicodedata.combining(part)]
    if base.isascii() and base:
        accent = next((_ACCENTS[mark] for mark in marks if mark in _ACCENTS), "")
        return f"{accent}{{{base}}}" if accent else base
    if base in _SYMBOLS:
        return _SYMBOLS[base]
    folded = unicodedata.normalize("NFKD", char)
    folded = "".join(part for part in folded if part.isascii() and not unicodedata.combining(part))
    if folded:
        return folded
    if unicodedata.category(char).startswith(("Z", "C")):
        return " "
    return "?"


def root_strings(node: object, *, skip: frozenset[str] = frozenset({"_typename"})) -> object:
    """Apply :func:`root_text` to every string in a parsed JSON tree, in place."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in skip:
                continue
            if isinstance(value, str):
                node[key] = root_text(value)
            else:
                root_strings(value, skip=skip)
    elif isinstance(node, list):
        for position, value in enumerate(node):
            if isinstance(value, str):
                node[position] = root_text(value)
            else:
                root_strings(value, skip=skip)
    return node


def ascii_source(source: str) -> str:
    """Make a generated ROOT macro pure ASCII by rewriting any stray character."""
    if source.isascii():
        return source
    return "".join(char if char.isascii() else root_text(char) for char in source)

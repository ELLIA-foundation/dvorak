"""TLatex-safe text. No ROOT import, so the GUI can use it too.

ROOT's interactive fonts draw a title as Latin-1. A UTF-8 em dash
(E2 80 94) therefore shows up as â, and a micro sign as Âµ. JSROOT
draws the same string as Unicode, so the two windows disagree.
TLatex tokens (#mu, ^{2}, #alpha) render in both.
"""

from __future__ import annotations

# Destinations stay free of raw quotes and backslashes so they can be
# applied to a JSON document without breaking its structure.
_CHARACTERS = (
    ("µ", "#mu"),
    ("μ", "#mu"),
    ("—", "-"),
    ("–", "-"),
    ("−", "-"),
    ("²", "^{2}"),
    ("³", "^{3}"),
    ("…", "..."),
    ("±", "#pm"),
    ("×", "#times"),
    ("α", "#alpha"),
    ("β", "#beta"),
    ("γ", "#gamma"),
    ("σ", "#sigma"),
    ("°", "#circ"),
    ("’", "'"),
    ("‘", "'"),
    ("“", "'"),
    ("”", "'"),
    ("\u00a0", " "),
)

# Same characters when a JSON writer escaped them instead of emitting UTF-8.
_ESCAPES = (
    ("\\u2014", "-"),
    ("\\u2013", "-"),
    ("\\u2212", "-"),
    ("\\u00b5", "#mu"),
    ("\\u03bc", "#mu"),
    ("\\u00b2", "^{2}"),
    ("\\u00b3", "^{3}"),
    ("\\u2026", "..."),
    ("\\u00b1", "#pm"),
    ("\\u00d7", "#times"),
    ("\\u03b1", "#alpha"),
    ("\\u03b2", "#beta"),
    ("\\u03b3", "#gamma"),
    ("\\u03c3", "#sigma"),
    ("\\u00b0", "#circ"),
    ("\\u2019", "'"),
    ("\\u2018", "'"),
    ("\\u201c", "'"),
    ("\\u201d", "'"),
    ("\\u00a0", " "),
)


def root_text(text: str) -> str:
    """Rewrite one label so Legacy ROOT shows the same glyphs as JSROOT."""
    value = str(text)
    for src, dst in _CHARACTERS:
        if src in value:
            value = value.replace(src, dst)
    return value


def sanitize_root_json(payload: str) -> str:
    """Apply :func:`root_text` to a TBufferJSON document without reparsing it.

    Re-dumping the document reorders keys and breaks ``ConvertFromJSON``.
    """
    value = str(payload)
    for src, dst in _ESCAPES:
        if src in value:
            value = value.replace(src, dst)
    return root_text(value)

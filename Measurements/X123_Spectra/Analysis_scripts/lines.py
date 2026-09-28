"""X-ray line markers for Amptek Si-PIN spectra (keV)."""

from __future__ import annotations

# Energies from standard K/L characteristic tables, rounded to 0.001 keV.
U_L_LINES: tuple[tuple[str, float], ...] = (
    ("U Lα", 13.614),
    ("U Lβ", 17.220),
    ("U Lγ", 20.167),
)

COMMON_K_LINES: tuple[tuple[str, float], ...] = (
    ("Fe Kα", 6.404),
    ("Cu Kα", 8.048),
    ("Pb Lα", 10.551),
    ("Pb Lβ", 12.614),
)


def all_lines(*, uranium: bool = True, common: bool = True) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    if uranium:
        rows.extend(U_L_LINES)
    if common:
        rows.extend(COMMON_K_LINES)
    return rows

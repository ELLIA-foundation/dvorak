"""X-ray line markers for Amptek Si-PIN spectra (keV)."""

from __future__ import annotations

# Energies from standard K/L characteristic tables, rounded to 0.001 keV.
U_L_LINES: tuple[tuple[str, float], ...] = (
    ("U Lα", 13.614),
    ("U Lβ", 17.220),
    ("U Lγ", 20.167),
)

# L lines labelled on the U-containing glass spectrum (10–18 keV).
# Th Lα1 (12.969) and Bi Lβ1 (13.023) are unresolved at Si-PIN resolution;
# that peak is marked "Th+Bi Lα Lβ" on the reference plot.
MATERIAL_L_LINES: tuple[tuple[str, float], ...] = (
    ("Bi Lα", 10.839),
    ("Th Lα", 12.969),
    ("Bi Lβ", 13.023),
    ("Ra Lβ", 15.235),
    ("Th Lβ", 16.202),
)

COMMON_K_LINES: tuple[tuple[str, float], ...] = (
    ("Fe Kα", 6.404),
    ("Cu Kα", 8.048),
    ("Pb Lα", 10.551),
    ("Pb Lβ", 12.614),
)

_LABEL_LEVELS = (0.92, 0.74, 0.56)
# For crowded plots (many markers from the X-ray lines pane).
DENSE_LABEL_LEVELS = (0.92, 0.83, 0.74, 0.65, 0.56, 0.47)


def all_lines(
    *,
    uranium: bool = True,
    common: bool = True,
    material: bool = True,
) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    if uranium:
        rows.extend(U_L_LINES)
    if material:
        rows.extend(MATERIAL_L_LINES)
    if common:
        rows.extend(COMMON_K_LINES)
    return rows


def label_positions(
    lines: list[tuple[str, float]],
    *,
    gap_kev: float = 0.7,
    levels: tuple[float, ...] = _LABEL_LEVELS,
) -> list[float]:
    """Fraction along a vertical marker, staggered when neighbours would overlap."""
    slots = [0] * len(lines)
    previous_energy: float | None = None
    level = 0
    for index in sorted(range(len(lines)), key=lambda i: lines[i][1]):
        energy = lines[index][1]
        if previous_energy is not None and energy - previous_energy < gap_kev:
            level = (level + 1) % len(levels)
        else:
            level = 0
        slots[index] = level
        previous_energy = energy
    return [levels[level] for level in slots]

"""Regenerate ``xray_lines.json``, the X-ray line database used by ``linedb.py``.

Needs xraylib (``pip install xraylib``) only when rebuilding; the GUI reads the
JSON and never imports xraylib.

    python3 Measurements/X123_Spectra/Analysis_scripts/build_line_db.py

Every radiative K, L1-3 and M1-5 transition for Z = 4..98 is kept when its
energy is 0.1-150 keV and its relative intensity is at least ``--min-rel`` %.
Relative intensity is the Kissel cascade XRF cross section at 1.5x the
family's deepest edge (K, L1 or M1), normalised so the strongest line of each
element's K, L or M family is 100. It is a guide to which lines of a family
should appear together, not a quantitative prediction for a given source.

A short list of low-energy gamma lines from common check sources and the
U/Th decay chains is appended (``family = "gamma"``, ``rel`` = emission
probability in %). Check those against NNDC before quoting them.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import xraylib as xrl

OUT = Path(__file__).with_name("xray_lines.json")
Z_MIN, Z_MAX = 4, 98
E_MIN_KEV, E_MAX_KEV = 0.1, 150.0

_IUPAC = re.compile(r"^(K|L[1-3]|M[1-5])([K-Q])([1-7])_LINE$")
_SHELL_ORDER = "KLMNOPQ"

# IUPAC transition -> Siegbahn name, for the lines that have one.
SIEGBAHN = {
    "KL3": "Kα1", "KL2": "Kα2", "KL1": "Kα3",
    "KM3": "Kβ1", "KM2": "Kβ3", "KN3": "Kβ2", "KN2": "Kβ2'",
    "KM5": "Kβ5", "KM4": "Kβ5'", "KN5": "Kβ4", "KN4": "Kβ4'",
    "L3M5": "Lα1", "L3M4": "Lα2", "L2M4": "Lβ1", "L3N5": "Lβ2",
    "L3N4": "Lβ15", "L1M3": "Lβ3", "L1M2": "Lβ4", "L3O4": "Lβ5",
    "L3O5": "Lβ5'", "L3N1": "Lβ6", "L3O1": "Lβ7", "L1M5": "Lβ9",
    "L1M4": "Lβ10", "L2M3": "Lβ17", "L2N4": "Lγ1", "L1N2": "Lγ2",
    "L1N3": "Lγ3", "L1O3": "Lγ4", "L1O2": "Lγ4'", "L2N1": "Lγ5",
    "L2O4": "Lγ6", "L2O1": "Lγ8", "L3M1": "Ll", "L2M1": "Lη",
    "L3M3": "Ls", "L3M2": "Lt", "L2N6": "Lν", "L3N6": "Lu",
    "M5N7": "Mα1", "M5N6": "Mα2", "M4N6": "Mβ", "M3N5": "Mγ",
    "M5N3": "Mζ1", "M4N2": "Mζ2", "M3N1": "Mγ'", "M2N4": "Mδ",
}

# (nuclide, energy keV, emission probability %). Gamma and nuclear lines only;
# the X-rays that follow decay are already in the element table.
GAMMA_LINES = (
    ("Am-241", 26.3446, 2.31),
    ("Am-241", 59.5409, 35.9),
    ("Th-231", 25.64, 14.1),
    ("Th-231", 84.214, 6.6),
    ("Pb-210", 46.539, 4.25),
    ("U-238", 49.55, 0.064),
    ("Pb-214", 53.228, 1.08),
    ("Th-234", 63.30, 3.7),
    ("Th-230", 67.672, 0.38),
    ("Cd-109", 88.0336, 3.64),
    ("Th-234", 92.38, 2.13),
    ("Th-234", 92.80, 2.10),
    ("Co-57", 14.4129, 9.16),
    ("Co-57", 122.0607, 85.6),
    ("Co-57", 136.4736, 10.68),
    ("U-235", 143.767, 10.96),
)


def _family(shell: str) -> str:
    return shell[0]


def _deepest_edge(z: int, family: str) -> float | None:
    shell = {"K": xrl.K_SHELL, "L": xrl.L1_SHELL, "M": xrl.M1_SHELL}[family]
    try:
        return float(xrl.EdgeEnergy(z, shell))
    except ValueError:
        return None


def _transitions() -> list[tuple[str, int]]:
    rows = []
    for name in dir(xrl):
        match = _IUPAC.match(name)
        if not match:
            continue
        inner, outer = match.group(1), match.group(2)
        if _SHELL_ORDER.index(outer) <= _SHELL_ORDER.index(inner[0]):
            continue
        rows.append((f"{inner}{outer}{match.group(3)}", getattr(xrl, name)))
    return rows


def build(min_rel: float) -> dict:
    lines: list[dict] = []
    transitions = _transitions()
    for z in range(Z_MIN, Z_MAX + 1):
        symbol = xrl.AtomicNumberToSymbol(z)
        by_family: dict[str, list[dict]] = {}
        for iupac, macro in transitions:
            family = _family(iupac)
            edge = _deepest_edge(z, family)
            if edge is None:
                continue
            try:
                energy = float(xrl.LineEnergy(z, macro))
                weight = float(xrl.CS_FluorLine_Kissel_Cascade(z, macro, 1.5 * edge))
            except ValueError:
                continue
            if not (E_MIN_KEV <= energy <= E_MAX_KEV) or weight <= 0:
                continue
            by_family.setdefault(family, []).append(
                {
                    "z": z,
                    "el": symbol,
                    "family": family,
                    "iupac": f"{iupac[:-2]}-{iupac[-2:]}" if family != "K" else f"K-{iupac[1:]}",
                    "siegbahn": SIEGBAHN.get(iupac, ""),
                    "e": round(energy, 4),
                    "w": weight,
                }
            )
        for family, rows in by_family.items():
            top = max(row["w"] for row in rows)
            for row in rows:
                rel = 100.0 * row.pop("w") / top
                if rel >= min_rel:
                    row["rel"] = round(rel, 3)
                    lines.append(row)
    for nuclide, energy, prob in GAMMA_LINES:
        lines.append(
            {
                "z": 0,
                "el": nuclide,
                "family": "gamma",
                "iupac": "",
                "siegbahn": "γ",
                "e": energy,
                "rel": prob,
            }
        )
    lines.sort(key=lambda row: row["e"])
    return {
        "source": f"xraylib {getattr(xrl, '__version__', '')}".strip(),
        "notes": (
            "rel: % of the strongest line in the element's K, L or M family "
            "(Kissel cascade XRF cross section at 1.5x the K/L1/M1 edge). "
            "gamma rows: rel is emission probability in %."
        ),
        "lines": lines,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-rel", type=float, default=0.05, help="drop lines below this %%")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    data = build(args.min_rel)
    # One line per entry keeps diffs of a rebuild readable.
    rows = ",\n".join(
        "  " + json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        for row in data["lines"]
    )
    head = json.dumps(
        {k: v for k, v in data.items() if k != "lines"}, ensure_ascii=False
    )[:-1]
    args.out.write_text(f'{head}, "lines": [\n{rows}\n]}}\n', encoding="utf-8")
    print(f"Wrote {len(data['lines'])} lines to {args.out}")


if __name__ == "__main__":
    main()

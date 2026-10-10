"""X-ray line database: which characteristic lines could sit at an energy.

The table is ``xray_lines.json`` beside this file (rebuild with
``build_line_db.py``). Energies are keV. ``rel`` is the line's intensity as a
% of the strongest line in its element's K, L or M family; for ``gamma`` rows
it is the emission probability in %.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

DB_PATH = Path(__file__).with_name("xray_lines.json")
FAMILIES = ("K", "L", "M", "gamma")

# Si-PIN detector artifacts.
SI_KA_KEV = 1.740
SI_ESCAPE_MIN_KEV = 1.839  # Si K edge: no escape peak below it
ARTIFACT_WEIGHT = 0.3

# Fano-limited Si resolution: FWHM^2 = noise^2 + 2.355^2 * F * w * E.
_FANO = 0.115
_W_SI_KEV = 0.00362


def fwhm_kev(energy_kev: float | np.ndarray, noise_kev: float = 0.14) -> float | np.ndarray:
    """Expected Si-PIN peak FWHM at ``energy_kev`` for an electronic noise FWHM."""
    e = np.maximum(np.asarray(energy_kev, dtype=np.float64), 0.0)
    out = np.sqrt(noise_kev**2 + 2.355**2 * _FANO * _W_SI_KEV * e)
    return float(out) if out.ndim == 0 else out


@dataclass(frozen=True)
class Line:
    z: int
    element: str
    family: str
    iupac: str
    siegbahn: str
    energy_kev: float
    rel: float

    @property
    def short(self) -> str:
        """Line name without the element: ``Lα1``, ``L3-N6``, ``γ``."""
        return self.siegbahn or self.iupac

    @property
    def name(self) -> str:
        return f"{self.element} {self.short}"


@dataclass(frozen=True)
class Candidate:
    line: Line
    kind: str  # "line", "escape" (Si Kα escape of the line) or "pileup"
    energy_kev: float  # where this candidate would appear in the spectrum
    delta_kev: float  # query energy minus energy_kev
    score: float

    @property
    def label(self) -> str:
        if self.kind == "escape":
            return f"{self.line.name} esc"
        if self.kind == "pileup":
            return f"{self.line.name} pile-up"
        return self.line.name


class LineDatabase:
    def __init__(self, lines: list[Line], source: str = "") -> None:
        self.lines = sorted(lines, key=lambda line: line.energy_kev)
        self.energies = np.array([line.energy_kev for line in self.lines], dtype=np.float64)
        self.source = source
        self._symbols = {line.element.lower(): line.element for line in self.lines}

    @classmethod
    def from_json(cls, path: Path = DB_PATH) -> LineDatabase:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        lines = [
            Line(
                z=int(row["z"]),
                element=str(row["el"]),
                family=str(row["family"]),
                iupac=str(row.get("iupac") or ""),
                siegbahn=str(row.get("siegbahn") or ""),
                energy_kev=float(row["e"]),
                rel=float(row["rel"]),
            )
            for row in payload["lines"]
        ]
        return cls(lines, str(payload.get("source") or ""))

    def elements(self) -> list[str]:
        """Element symbols in Z order, then gamma nuclides."""
        seen: dict[str, int] = {}
        for line in self.lines:
            seen.setdefault(line.element, line.z if line.z > 0 else 1000)
        return sorted(seen, key=lambda el: (seen[el], el))

    def canonical(self, symbol: str) -> str | None:
        return self._symbols.get(symbol.strip().lower())

    def select(
        self,
        lo: float,
        hi: float,
        *,
        min_rel: float = 0.0,
        families: tuple[str, ...] | None = None,
        elements: set[str] | None = None,
    ) -> list[Line]:
        i0 = int(np.searchsorted(self.energies, lo, side="left"))
        i1 = int(np.searchsorted(self.energies, hi, side="right"))
        return [
            line
            for line in self.lines[i0:i1]
            if line.rel >= min_rel
            and (families is None or line.family in families)
            and (elements is None or line.element in elements)
        ]

    def element_lines(
        self,
        element: str,
        *,
        min_rel: float = 0.0,
        families: tuple[str, ...] | None = None,
        lo: float = 0.0,
        hi: float = float("inf"),
    ) -> list[Line]:
        return self.select(lo, hi, min_rel=min_rel, families=families, elements={element})

    def candidates(
        self,
        energy_kev: float,
        tolerance_kev: float,
        *,
        min_rel: float = 0.0,
        families: tuple[str, ...] | None = None,
        elements: set[str] | None = None,
        artifacts: bool = True,
        artifact_min_rel: float = 20.0,
    ) -> list[Candidate]:
        """Lines (and optionally escape / pile-up parents) near ``energy_kev``, best first.

        Score is a Gaussian in the offset (sigma = tolerance / 2) times the
        line's relative intensity, so a strong line slightly off beats a weak
        one right on. Escape and pile-up candidates are down-weighted.
        """
        tol = max(float(tolerance_kev), 1e-6)
        sigma = tol / 2.0
        out: list[Candidate] = []

        def add(line: Line, kind: str, at: float, weight: float) -> None:
            delta = energy_kev - at
            score = weight * float(np.exp(-0.5 * (delta / sigma) ** 2)) * max(line.rel, 0.01) / 100
            out.append(Candidate(line, kind, at, delta, score))

        for line in self.select(
            energy_kev - tol, energy_kev + tol,
            min_rel=min_rel, families=families, elements=elements,
        ):
            add(line, "line", line.energy_kev, 1.0)
        if artifacts:
            amin = max(min_rel, artifact_min_rel)
            parent = energy_kev + SI_KA_KEV
            for line in self.select(
                parent - tol, parent + tol, min_rel=amin, families=families, elements=elements
            ):
                if line.energy_kev >= SI_ESCAPE_MIN_KEV:
                    add(line, "escape", line.energy_kev - SI_KA_KEV, ARTIFACT_WEIGHT)
            half = energy_kev / 2.0
            for line in self.select(
                half - tol / 2, half + tol / 2, min_rel=amin, families=families, elements=elements
            ):
                add(line, "pileup", 2.0 * line.energy_kev, ARTIFACT_WEIGHT)
        out.sort(key=lambda c: c.score, reverse=True)
        return out


@lru_cache(maxsize=1)
def database() -> LineDatabase:
    return LineDatabase.from_json(DB_PATH)


_SPLIT = re.compile(r"[\s,;]+")


def parse_elements(text: str, db: LineDatabase | None = None) -> tuple[set[str] | None, list[str]]:
    """Parse ``"U Th, bi Pb-210"`` into canonical symbols. Empty text means every element.

    Returns (symbols or None, unknown tokens).
    """
    db = db or database()
    tokens = [t for t in _SPLIT.split(text.strip()) if t]
    if not tokens:
        return None, []
    found: set[str] = set()
    unknown: list[str] = []
    for token in tokens:
        symbol = db.canonical(token)
        if symbol is None:
            unknown.append(token)
        else:
            found.add(symbol)
    return found, unknown


def parse_energies(text: str) -> list[float]:
    """Energies in keV from ``"13.6, 17.2 20.1"``; tokens that are not numbers are skipped."""
    values: list[float] = []
    for token in _SPLIT.split(text.strip()):
        try:
            values.append(float(token))
        except ValueError:
            continue
    return values

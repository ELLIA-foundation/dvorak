"""Automatic peak search and line identification for X-123 spectra.

Pipeline (``scan``):

1. Smooth the raw counts with a Gaussian matched to the detector resolution.
2. Estimate the continuum with SNIP (log-log-sqrt transform, decreasing window).
3. Take local maxima of the net spectrum; integrate raw minus background over
   +/-0.6 FWHM and keep peaks whose net area is ``sigma`` standard deviations
   above zero (sqrt(net + 2 * background)).
4. Identify by element family (``U L``, ``Fe K``...), not line by line. A family
   is a candidate when its strongest line in range matches a peak; its score is
   the intensity-weighted fraction of its in-range lines (rel >= 10 %) that
   match peaks, times the significance it explains. Families are accepted
   greedily while they explain a peak no accepted family has explained.
5. Peaks still unexplained are checked as Si escape (E + 1.74 keV) or pile-up
   (2E, or the sum of two found peaks) of explained peaks.

Inputs must be counts (not counts/s or normalised) so the statistics hold.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from linedb import (
    SI_ESCAPE_MIN_KEV,
    SI_KA_KEV,
    Candidate,
    Line,
    LineDatabase,
    fwhm_kev,
)

FAMILY_MIN_REL = 10.0
PRIMARY_MIN_REL = 30.0
ARTIFACT_PARENT_RATIO = 10.0


@dataclass
class Peak:
    energy_kev: float
    channel: int
    net_counts: float
    background_counts: float
    significance: float
    fwhm_kev: float | None
    height: float
    labels: list[str] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    alternatives: list[Candidate] = field(default_factory=list)

    @property
    def assignment(self) -> str:
        return " + ".join(self.labels)


@dataclass
class FamilyMatch:
    element: str
    family: str
    score: float
    coverage: float
    matched: list[tuple[int, Line]]
    missing: list[Line]

    @property
    def name(self) -> str:
        return self.element if self.family == "gamma" else f"{self.element} {self.family}"


@dataclass
class ScanResult:
    peaks: list[Peak]
    background: np.ndarray
    families: list[FamilyMatch]


def snip_background(counts: np.ndarray, half_width: int) -> np.ndarray:
    """SNIP continuum under ``counts``; ``half_width`` is the largest clipping window in channels."""
    y = np.maximum(np.asarray(counts, dtype=np.float64), 0.0)
    if y.size < 3:
        return y.copy()
    v = np.log(np.log(np.sqrt(y + 1.0) + 1.0) + 1.0)
    m_max = max(1, min(int(half_width), (y.size - 1) // 2))
    for m in range(m_max, 0, -1):
        mean = 0.5 * (v[: -2 * m] + v[2 * m :])
        v[m:-m] = np.minimum(v[m:-m], mean)
    return (np.exp(np.exp(v) - 1.0) - 1.0) ** 2 - 1.0


def _gaussian_smooth(y: np.ndarray, sigma_ch: float) -> np.ndarray:
    if sigma_ch < 0.5:
        return y.copy()
    half = int(np.ceil(3 * sigma_ch))
    x = np.arange(-half, half + 1, dtype=np.float64)
    kernel = np.exp(-0.5 * (x / sigma_ch) ** 2)
    kernel /= kernel.sum()
    padded = np.pad(y, half, mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def _half_max_width(energy: np.ndarray, net: np.ndarray, i: int, reach: int) -> float | None:
    peak = net[i]
    if peak <= 0:
        return None
    half = 0.5 * peak
    lo = i
    while lo > max(0, i - reach) and net[lo] > half:
        lo -= 1
    hi = i
    while hi < min(net.size - 1, i + reach) and net[hi] > half:
        hi += 1
    if net[lo] > half or net[hi] > half:
        return None

    def cross(a: int, b: int) -> float:
        ya, yb = net[a], net[b]
        t = 0.0 if yb == ya else (half - ya) / (yb - ya)
        return float(energy[a] + t * (energy[b] - energy[a]))

    return cross(hi, hi - 1) - cross(lo, lo + 1)


def find_peaks(
    energy_kev: np.ndarray,
    counts: np.ndarray,
    *,
    sigma: float = 3.0,
    min_energy_kev: float = 1.5,
    max_energy_kev: float | None = None,
    noise_kev: float = 0.14,
    min_net_counts: float = 10.0,
) -> tuple[list[Peak], np.ndarray]:
    energy = np.asarray(energy_kev, dtype=np.float64)
    raw = np.maximum(np.asarray(counts, dtype=np.float64), 0.0)
    if energy.size < 8 or raw.size != energy.size:
        return [], np.zeros_like(raw)
    slope = float(np.median(np.diff(energy)))
    if slope <= 0:
        return [], np.zeros_like(raw)
    e_hi = float(energy[-1]) if max_energy_kev is None else float(max_energy_kev)
    ref = fwhm_kev(min(max(min_energy_kev, 5.0), e_hi), noise_kev)
    smooth = _gaussian_smooth(raw, ref / 2.355 / slope)
    wide = fwhm_kev(e_hi, noise_kev)
    background = np.maximum(snip_background(smooth, int(np.ceil(1.5 * wide / slope))), 0.0)
    net = smooth - background

    peaks: list[Peak] = []
    for i in range(1, net.size - 1):
        e = float(energy[i])
        if e < min_energy_kev or e > e_hi or net[i] <= 0:
            continue
        width = float(fwhm_kev(e, noise_kev))
        reach = max(1, int(round(0.5 * width / slope)))
        lo, hi = max(0, i - reach), min(net.size, i + reach + 1)
        if net[i] < np.max(net[lo:hi]):
            continue
        win = max(1, int(round(0.6 * width / slope)))
        a, b = max(0, i - win), min(raw.size, i + win + 1)
        bkg = float(np.sum(background[a:b]))
        area = float(np.sum(raw[a:b])) - bkg
        if area < min_net_counts:
            continue
        signif = area / np.sqrt(max(area + 2.0 * bkg, 1.0))
        if signif < sigma:
            continue
        c_win = max(1, int(round(0.5 * width / slope)))
        c0, c1 = max(0, i - c_win), min(net.size, i + c_win + 1)
        weights = np.maximum(net[c0:c1], 0.0)
        centroid = float(np.sum(energy[c0:c1] * weights) / np.sum(weights)) if weights.sum() > 0 else e
        peaks.append(
            Peak(
                energy_kev=centroid,
                channel=i,
                net_counts=area,
                background_counts=bkg,
                significance=float(signif),
                fwhm_kev=_half_max_width(energy, net, i, 4 * reach),
                height=float(net[i]),
            )
        )
    # Plateaus give twin maxima; keep the stronger of peaks closer than half a FWHM.
    peaks.sort(key=lambda p: p.energy_kev)
    merged: list[Peak] = []
    for peak in peaks:
        if merged and peak.energy_kev - merged[-1].energy_kev < 0.5 * fwhm_kev(peak.energy_kev, noise_kev):
            if peak.significance > merged[-1].significance:
                merged[-1] = peak
            continue
        merged.append(peak)
    return merged, background


def _tolerance(energy: float, noise_kev: float, factor: float, slack_kev: float) -> float:
    return factor * 0.5 * float(fwhm_kev(energy, noise_kev)) + slack_kev


def _nearest_peak(peaks: list[Peak], energy: float, tol: float) -> int | None:
    best, best_d = None, tol
    for index, peak in enumerate(peaks):
        d = abs(peak.energy_kev - energy)
        if d <= best_d:
            best, best_d = index, d
    return best


def identify(
    peaks: list[Peak],
    db: LineDatabase,
    *,
    e_lo: float,
    e_hi: float,
    noise_kev: float = 0.14,
    tolerance_factor: float = 1.0,
    slack_kev: float = 0.03,
    elements: set[str] | None = None,
    families: tuple[str, ...] = ("K", "L", "M"),
    min_coverage: float = 0.5,
    artifacts: bool = True,
) -> list[FamilyMatch]:
    """Label ``peaks`` in place and return the accepted element families, best first."""
    if not peaks:
        return []
    in_range = db.select(e_lo, e_hi, min_rel=FAMILY_MIN_REL, families=families, elements=elements)
    by_family: dict[tuple[str, str], list[Line]] = {}
    for line in in_range:
        by_family.setdefault((line.element, line.family), []).append(line)

    options: list[FamilyMatch] = []
    for (element, family), lines in by_family.items():
        primary = max(lines, key=lambda line: line.rel)
        tol = _tolerance(primary.energy_kev, noise_kev, tolerance_factor, slack_kev)
        if _nearest_peak(peaks, primary.energy_kev, tol) is None:
            continue
        matched: list[tuple[int, Line]] = []
        missing: list[Line] = []
        got = total = 0.0
        for line in lines:
            tol = _tolerance(line.energy_kev, noise_kev, tolerance_factor, slack_kev)
            index = _nearest_peak(peaks, line.energy_kev, tol)
            total += line.rel
            if index is None:
                missing.append(line)
                continue
            sigma = tol / 2.0
            closeness = float(np.exp(-0.5 * ((peaks[index].energy_kev - line.energy_kev) / sigma) ** 2))
            got += line.rel * closeness
            matched.append((index, line))
        coverage = got / total if total else 0.0
        if coverage < 0.5 * min_coverage:
            continue
        explained = {index for index, _line in matched}
        strength = sum(np.sqrt(peaks[i].significance) for i in explained)
        options.append(FamilyMatch(element, family, coverage * strength, coverage, matched, missing))

    # Greedy, re-ranked each round: once an element is identified its other
    # families (U M after U L) need half the coverage and score double.
    accepted: list[FamilyMatch] = []
    explained: set[int] = set()
    found: set[str] = set()
    remaining = list(options)
    while remaining:
        ranked = []
        for option in remaining:
            known = option.element in found
            if option.coverage < (0.5 if known else 1.0) * min_coverage:
                continue
            new = {i for i, line in option.matched if line.rel >= PRIMARY_MIN_REL} - explained
            if new:
                ranked.append((option.score * (2.0 if known else 1.0), option))
        if not ranked:
            break
        _score, option = max(ranked, key=lambda row: row[0])
        remaining.remove(option)
        accepted.append(option)
        found.add(option.element)
        for index, line in option.matched:
            peak = peaks[index]
            if line not in peak.lines:
                peak.lines.append(line)
            explained.add(index)
    for peak in peaks:
        # One label per family at a peak: the strongest of its lines there.
        best: dict[tuple[str, str], Line] = {}
        for line in peak.lines:
            key = (line.element, line.family)
            if key not in best or line.rel > best[key].rel:
                best[key] = line
        peak.labels = [line.name for line in sorted(best.values(), key=lambda l: -l.rel)]

    if artifacts:
        _label_artifacts(peaks, explained, noise_kev, tolerance_factor, slack_kev)
    for peak in peaks:
        tol = _tolerance(peak.energy_kev, noise_kev, tolerance_factor, slack_kev)
        peak.alternatives = db.candidates(
            peak.energy_kev, tol, min_rel=1.0, elements=elements, artifacts=artifacts
        )[:8]
    return accepted


def _label_artifacts(
    peaks: list[Peak],
    explained: set[int],
    noise_kev: float,
    factor: float,
    slack_kev: float,
) -> None:
    # Escape and pile-up peaks are a few % of their parent at most.
    parents = [peaks[i] for i in sorted(explained)]
    strong = ARTIFACT_PARENT_RATIO
    for index, peak in enumerate(peaks):
        if index in explained:
            continue
        tol = _tolerance(peak.energy_kev, noise_kev, factor, slack_kev)
        for parent in parents:
            if parent.energy_kev >= SI_ESCAPE_MIN_KEV and abs(
                parent.energy_kev - SI_KA_KEV - peak.energy_kev
            ) <= tol and parent.net_counts > strong * peak.net_counts:
                peak.labels.append(f"{parent.labels[0]} esc")
                break
            if (
                abs(2 * parent.energy_kev - peak.energy_kev) <= tol
                and parent.net_counts > strong * peak.net_counts
            ):
                peak.labels.append(f"{parent.labels[0]} pile-up")
                break
        if peak.labels:
            continue
        for a in parents:
            for b in parents:
                if a.energy_kev <= b.energy_kev and abs(
                    a.energy_kev + b.energy_kev - peak.energy_kev
                ) <= tol and min(a.net_counts, b.net_counts) > strong * peak.net_counts:
                    peak.labels.append(f"{a.labels[0]} + {b.labels[0]} sum")
                    break
            if peak.labels:
                break


def scan(
    energy_kev: np.ndarray,
    counts: np.ndarray,
    db: LineDatabase,
    *,
    sigma: float = 3.0,
    min_energy_kev: float = 1.5,
    max_energy_kev: float | None = None,
    noise_kev: float = 0.14,
    tolerance_factor: float = 1.0,
    elements: set[str] | None = None,
    families: tuple[str, ...] = ("K", "L", "M"),
    min_coverage: float = 0.5,
    artifacts: bool = True,
) -> ScanResult:
    energy = np.asarray(energy_kev, dtype=np.float64)
    peaks, background = find_peaks(
        energy,
        counts,
        sigma=sigma,
        min_energy_kev=min_energy_kev,
        max_energy_kev=max_energy_kev,
        noise_kev=noise_kev,
    )
    e_hi = float(energy[-1]) if max_energy_kev is None and energy.size else float(max_energy_kev or 0.0)
    matches = identify(
        peaks,
        db,
        e_lo=min_energy_kev,
        e_hi=e_hi,
        noise_kev=noise_kev,
        tolerance_factor=tolerance_factor,
        elements=elements,
        families=families,
        min_coverage=min_coverage,
        artifacts=artifacts,
    )
    return ScanResult(peaks=peaks, background=background, families=matches)

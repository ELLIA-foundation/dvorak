"""Read-only access to Pixet / OPIXE pixel-detector products.

Pixet (and its compiled ``opixe-core``) stays the engine that clusters,
calibrates, and derives spectra. Dvorak only lists the measurements it has
already processed and reads their ``derived.root`` energy histograms for
overlays. Nothing here writes into the Pixet tree.

Locations, in order of preference:

* data root: ``$OPIXE_DATA_DIR``, then ``$PIXET_DIR/Data/OPIXE Data``, then
  a ``Pixet`` checkout next to this repository.
* ``opixe-core``: ``$OPIXE_CORE``, then ``<Pixet>/OPIXE/bin/opixe-core``, then PATH.

Histogram arrays come from the ROOT renderer (``RootClient.read_hists``) so
this module never imports ROOT.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from lib.paths import repo_root

DERIVED_NAME = "derived.root"
ENERGY_MATCH = r"(^|/)hClusterEnergy(_\d+)?$"
CHIP_KEY = "chip"
_REGION_RE = re.compile(r"hClusterEnergy_(\d+)$")


def pixet_dir() -> Path:
    env = os.environ.get("PIXET_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    return repo_root().parent / "Pixet"


def default_data_root() -> Path:
    env = os.environ.get("OPIXE_DATA_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    return pixet_dir() / "Data" / "OPIXE Data"


def find_opixe_core(pixet: Path | None = None) -> Path | None:
    env = os.environ.get("OPIXE_CORE", "").strip()
    if env and Path(env).is_file():
        return Path(env)
    candidate = (pixet or pixet_dir()) / "OPIXE" / "bin" / "opixe-core"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    found = shutil.which("opixe-core")
    return Path(found) if found else None


@dataclass
class PixelMeasurement:
    """One OPIXE measurement folder, as the catalogue describes it."""

    id: str
    dir: Path
    label: str = ""
    description: str = ""
    created: str = ""
    tags: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    distance_cm: float | None = None
    live_time_s: float | None = None
    chip_id: str = ""
    family: str = "hitlist"
    status: str = ""
    derived: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def derived_path(self) -> Path:
        return self.dir / DERIVED_NAME

    @property
    def display_name(self) -> str:
        return self.label or self.id

    def search_haystack(self) -> str:
        return " ".join(
            [self.id, self.label, self.description, self.chip_id, *self.tags, *self.groups]
        ).lower()

    def metadata_dict(self) -> dict[str, Any]:
        """Fields for the metadata panel and legend; nested dicts flatten there."""
        payload: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "created": self.created,
            "tags": ", ".join(self.tags),
            "groups": ", ".join(self.groups),
            "distance_cm": self.distance_cm,
            "live_time_s": self.live_time_s,
            "chip_id": self.chip_id,
            "family": self.family,
            "status": self.status,
        }
        payload.update(self.meta)
        return {key: value for key, value in payload.items() if value not in (None, "", [])}


def _from_core(entry: dict, group_names: dict[str, str]) -> PixelMeasurement:
    setup = entry.get("setup") or {}
    meta: dict[str, Any] = {"calib_prefix": entry.get("calibPrefix") or ""}
    if entry.get("nFrames") is not None:
        meta["frames"] = entry.get("nFrames")
    if isinstance(setup, dict):
        if setup.get("tube"):
            meta["tube"] = setup["tube"]
        if "lidOn" in setup:
            meta["lid"] = "on" if setup.get("lidOn") else "off"
        attenuator = setup.get("attenuator")
        if isinstance(attenuator, dict) and attenuator.get("material"):
            meta["attenuator"] = attenuator.get("material")
            thick = attenuator.get("thicknesses_mm")
            if thick:
                meta["attenuator_thickness_mm"] = ", ".join(f"{float(t):g}" for t in thick)
    groups = [group_names.get(gid, gid) for gid in entry.get("groupIds") or []]
    return PixelMeasurement(
        id=str(entry.get("id") or Path(str(entry.get("dir"))).name),
        dir=Path(str(entry.get("dir") or "")).resolve(),
        label=str(entry.get("label") or ""),
        description=str(entry.get("description") or ""),
        created=str(entry.get("created") or ""),
        tags=[str(tag) for tag in entry.get("tags") or []],
        groups=groups,
        distance_cm=_float(entry.get("distance_cm")),
        live_time_s=_float(entry.get("liveTimeS")),
        chip_id=str(entry.get("chipId") or ""),
        family=str(entry.get("family") or "hitlist"),
        status=str(entry.get("status") or ""),
        derived=bool(entry.get("derived")),
        meta=meta,
    )


def _from_folder(folder: Path) -> PixelMeasurement | None:
    """Fallback when opixe-core is unavailable: read the config and stamp only."""
    config_path = folder / "opixe.json"
    if not config_path.is_file():
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    stamp: dict = {}
    stamp_path = folder / f"{DERIVED_NAME}.stamp.json"
    if stamp_path.is_file():
        try:
            stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stamp = {}
    derived = (folder / DERIVED_NAME).is_file()
    entry = {
        "id": folder.name,
        "dir": str(folder),
        "description": config.get("description"),
        "created": config.get("created"),
        "tags": config.get("tags"),
        "distance_cm": config.get("distance_cm"),
        "liveTimeS": stamp.get("liveTimeS"),
        "chipId": config.get("chipId"),
        "family": config.get("family"),
        "calibPrefix": config.get("calibPrefix"),
        "setup": config.get("setup"),
        "derived": derived,
        # Staleness needs opixe-core's hashes; do not guess it here.
        "status": "derived (status unknown)" if derived else "not derived",
    }
    return _from_core(entry, {})


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


@dataclass
class PixelCatalogue:
    data_root: Path
    measurements: list[PixelMeasurement]
    source: str  # "opixe-core" or "folders"
    error: str = ""


def list_measurements(data_root: Path | None = None, timeout: float = 60) -> PixelCatalogue:
    """Measurements under ``data_root`` that have a derived energy spectrum.

    Uses ``opixe-core list`` so labels, groups and up-to-date status are
    exactly what OPIXE reports. Falls back to reading the folders.
    """
    root = Path(data_root or default_data_root()).expanduser()
    if not root.is_dir():
        return PixelCatalogue(root, [], "folders", f"OPIXE data folder not found: {root}")
    core = find_opixe_core()
    error = ""
    if core is not None:
        try:
            completed = subprocess.run(
                [str(core), "list", "--data-root", str(root)],
                cwd=str(core.resolve().parent.parent.parent),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            payload = _last_json(completed.stdout)
            if payload and payload.get("ok") and isinstance(payload.get("data"), dict):
                data = payload["data"]
                names = {
                    str(group.get("id")): str(group.get("name") or group.get("id"))
                    for group in data.get("groups") or []
                }
                items = [_from_core(entry, names) for entry in data.get("measurements") or []]
                # Frame-family runs derive frames.root, not an energy histogram.
                usable = [m for m in items if m.derived and m.derived_path.is_file()]
                return PixelCatalogue(root, usable, "opixe-core")
            error = str((payload or {}).get("error") or "opixe-core list failed")
        except (OSError, subprocess.SubprocessError) as exc:
            error = str(exc)
    items = []
    for folder in sorted(root.iterdir()):
        if folder.is_dir() and not folder.name.startswith((".", "_")):
            measurement = _from_folder(folder)
            if measurement is not None and measurement.derived_path.is_file():
                items.append(measurement)
    return PixelCatalogue(root, items, "folders", error)


def _last_json(text: str) -> dict | None:
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


@dataclass
class PixelSpectrum:
    """One energy histogram from ``derived.root``: counts in variable-width bins."""

    key: str  # "chip" or "region:N"
    title: str
    edges: np.ndarray
    counts: np.ndarray
    errors: np.ndarray
    live_time_s: float | None = None
    thickness_mm: float | None = None
    mean_energy: np.ndarray | None = None  # per-bin <E> of the clusters, from pClusterEnergy
    mean_entries: np.ndarray | None = None

    @property
    def centers(self) -> np.ndarray:
        return 0.5 * (self.edges[:-1] + self.edges[1:])

    @property
    def widths(self) -> np.ndarray:
        return np.diff(self.edges)


def region_label(key: str, thickness_mm: float | None = None) -> str:
    if key == CHIP_KEY:
        return "Whole chip"
    index = key.split(":", 1)[-1]
    if thickness_mm is not None:
        return f"Region {index} ({thickness_mm:g} mm)"
    return f"Region {index}"


def spectra_from_payload(payload: dict, fallback_live: float | None = None) -> list[PixelSpectrum]:
    """Turn a ``read_hists`` reply into chip and region spectra."""
    params = payload.get("params") or {}
    live = _float(params.get("acqTimeTotal"))
    if live is None or live <= 0:
        live = fallback_live
    thickness: list = []
    try:
        config = json.loads(str(params.get("opixeConfig") or "{}"))
        thickness = list((config.get("regions") or {}).get("thickness") or [])
    except (json.JSONDecodeError, AttributeError):
        thickness = []
    profiles = {
        str(p.get("path") or ""): p for p in payload.get("profiles") or []
    }
    spectra: list[PixelSpectrum] = []
    for hist in payload.get("hists") or []:
        path = str(hist.get("path") or "")
        name = path.rsplit("/", 1)[-1]
        if name == "hClusterEnergy":
            key, thick = CHIP_KEY, None
        else:
            match = _REGION_RE.search(name)
            if match is None:
                continue
            index = int(match.group(1))
            key = f"region:{index}"
            thick = _float(thickness[index]) if index < len(thickness) else None
        edges = np.asarray(hist.get("edges") or [], dtype=np.float64)
        counts = np.asarray(hist.get("counts") or [], dtype=np.float64)
        if edges.size != counts.size + 1 or counts.size == 0:
            continue
        head, _, tail = path.rpartition("/")
        profile = profiles.get(f"{head}/p{tail[1:]}" if head else f"p{tail[1:]}")
        means = entries = None
        if profile is not None:
            means = np.asarray(profile.get("means") or [], dtype=np.float64)
            entries = np.asarray(profile.get("entries") or [], dtype=np.float64)
            if means.size != counts.size or entries.size != counts.size:
                means = entries = None
        spectra.append(
            PixelSpectrum(
                key=key,
                title=str(hist.get("title") or name),
                edges=edges,
                counts=counts,
                errors=np.asarray(hist.get("errors") or np.sqrt(counts), dtype=np.float64),
                live_time_s=live,
                thickness_mm=thick,
                mean_energy=means,
                mean_entries=entries,
            )
        )
    spectra.sort(key=lambda s: -1 if s.key == CHIP_KEY else int(s.key.split(":")[1]))
    return spectra


POINT_RULES = (
    ("center", "Bin centre"),
    ("centroid", "Weighted centroid"),
    ("lw", "Lafferty-Wyatt"),
    ("both", "Centre + Lafferty-Wyatt"),
)


def _centroid_offset(w: float) -> float:
    """Centroid offset from the low edge, as a fraction of the bin, for exp(-alpha E)."""
    if abs(w) < 1e-8:
        return 0.5 - w / 12.0
    return 1.0 / w - np.exp(-w) / (1.0 - np.exp(-w))


def _lw_offset(w: float) -> float:
    if abs(w) < 1e-8:
        return 0.5 - w / 24.0
    return float(np.log(w / (1.0 - np.exp(-w))) / w)


def _solve_slope(frac: float) -> float:
    lo, hi = -50.0, 50.0
    if frac >= _centroid_offset(lo):
        return lo
    if frac <= _centroid_offset(hi):
        return hi
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _centroid_offset(mid) > frac:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def point_abscissae(spectrum: PixelSpectrum, rule: str) -> np.ndarray:
    """Marker x per bin, as OPIXE's ``TSpectrumAbscissae`` (SCR/TSpectrumPoints.h).

    ``rule`` is ``center``, ``centroid`` or ``lw`` (Lafferty-Wyatt). Bins with no
    usable mean-energy profile entry stay at the bin centre.
    """
    edges = spectrum.edges
    x = 0.5 * (edges[:-1] + edges[1:])
    means, entries = spectrum.mean_energy, spectrum.mean_entries
    if rule == "center" or means is None or entries is None:
        return x
    x = x.copy()
    for b in range(x.size):
        lo, hi = edges[b], edges[b + 1]
        width = hi - lo
        m = means[b]
        if width <= 0 or entries[b] <= 0 or not (lo < m < hi):
            continue
        if rule == "centroid":
            x[b] = m
            continue
        frac = (m - lo) / width
        if abs(frac - 0.5) < 1e-6:
            continue
        shifted = lo + width * _lw_offset(_solve_slope(frac))
        if lo < shifted < hi:
            x[b] = shifted
    return x

"""One-shot import of Andras X-123 ODS spectra. MCA files are copied, not converted."""

from __future__ import annotations

import argparse
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np

for _parent in Path(__file__).resolve().parents:
    if (_parent / "lib" / "paths.py").is_file() and (_parent / "Measurements").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

from lib.paths import spectra_dir
from lib.spectrum import (
    SpectrumCapture,
    load_calibration,
    load_mca,
    save_spectrum,
)

_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"

MCA_CAMPAIGN = "Glass_1800s"
MATERIAL_CAMPAIGN = "Material_5min"
N_CHANNELS = 2048
EXPECTED_PRE = 19509.0
EXPECTED_POST = 24128.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Folder with .mca files and the material ODS workbook",
    )
    args = parser.parse_args(argv)
    source = args.source.expanduser().resolve()
    if not source.is_dir():
        raise SystemExit(f"Source folder not found: {source}")

    cal = load_calibration()
    _ensure_campaign(MCA_CAMPAIGN)
    glass_dir = spectra_dir() / MCA_CAMPAIGN / "Data"
    glass_dir.mkdir(parents=True, exist_ok=True)

    copied = []
    for mca in sorted(source.glob("*.mca")):
        dest = glass_dir / mca.name
        shutil.copy2(mca, dest)
        capture = load_mca(dest, cal)
        copied.append((dest.name, capture.counts.sum(), capture.live_time_s))
        print(f"copied {mca.name}  sum={capture.counts.sum():.0f}  live={capture.live_time_s}")

    material_ods = _find_ods(source, "material")
    ore_ods = _find_ods(source, "ore")
    if ore_ods is not None:
        pre, post = _ore_column_sums(ore_ods)
        print(
            f"ore ODS {ore_ods.name}: pre sum={pre:.0f} post sum={post:.0f} "
            "(duplicate of Glass_1800s MCA; skipped)"
        )
        if pre and abs(pre - EXPECTED_PRE) > 1:
            print(f"  warning: expected pre integral {EXPECTED_PRE:.0f}")
        if post and abs(post - EXPECTED_POST) > 1:
            print(f"  warning: expected post integral {EXPECTED_POST:.0f}")

    if material_ods is None:
        print("no material ODS found")
        return 0

    material_dir = spectra_dir() / MATERIAL_CAMPAIGN / "Data"
    material_dir.mkdir(parents=True, exist_ok=True)
    columns = _material_columns(material_ods)
    labels = [
        ("pre_1", "pre", 0),
        ("pre_2", "pre", 1),
        ("pre_3", "pre", 2),
        ("post_1", "post", 3),
        ("post_2", "post", 4),
        ("post_3", "post", 5),
    ]
    for stem, phase, index in labels:
        counts = columns[index][:N_CHANNELS]
        channel = np.arange(len(counts), dtype=np.int32)
        capture = SpectrumCapture(
            channel=channel,
            counts=counts,
            energy_kev=cal.energy_kev(channel),
            run_name=stem,
            phase=phase,
            calibration=cal,
            extra={
                "source_format": "ods",
                "source_file": material_ods.name,
                "source_column": index,
            },
        )
        paths = save_spectrum(capture, material_dir, stem)
        print(f"wrote {paths['npz'].name}  sum={counts.sum():.0f}")
    return 0


def _ensure_campaign(name: str) -> bool:
    path = spectra_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "Data").mkdir(exist_ok=True)
    return True


def _find_ods(source: Path, needle: str) -> Path | None:
    matches = [
        path
        for path in source.glob("*.ods")
        if needle.lower() in path.name.lower()
    ]
    return matches[0] if matches else None


def _material_columns(path: Path) -> list[np.ndarray]:
    sheet = _sheet(path, "U containing ore activation")
    rows = list(_iter_rows(sheet))
    data = rows[1:]
    columns = [[] for _ in range(6)]
    for row in data:
        for i, col in enumerate(range(2, 8)):
            value = row[col] if len(row) > col else 0.0
            columns[i].append(float(value) if isinstance(value, (int, float)) else 0.0)
    return [np.asarray(col, dtype=np.float64) for col in columns]


def _ore_column_sums(path: Path) -> tuple[float | None, float | None]:
    sheet = _sheet(path, "U containing ore activation")
    rows = list(_iter_rows(sheet))
    pre = post = 0.0
    for row in rows[1:]:
        if len(row) > 3 and isinstance(row[3], (int, float)):
            pre += float(row[3])
        if len(row) > 5 and isinstance(row[5], (int, float)):
            post += float(row[5])
    return pre, post


def _sheet(path: Path, name: str):
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("content.xml"))
    for table in root.findall(f".//{_TABLE}table"):
        if table.get(f"{_TABLE}name") == name:
            return table
    raise FileNotFoundError(f"Sheet {name!r} not in {path}")


def _cell_value(cell) -> float | str | None:
    raw = cell.get(f"{_OFFICE}value")
    if raw is not None:
        try:
            return float(raw)
        except ValueError:
            return raw
    parts = [
        "".join(paragraph.itertext())
        for paragraph in cell.findall(f".//{_TEXT}p")
    ]
    text = "\n".join(parts).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return text


def _iter_rows(table):
    for row in table.findall(f"{_TABLE}table-row"):
        cells: list[float | str | None] = []
        col = 0
        for cell in row.findall(f"{_TABLE}table-cell"):
            repeat = int(cell.get(f"{_TABLE}number-columns-repeated") or 1)
            value = _cell_value(cell)
            for _ in range(min(repeat, 16 - col)):
                cells.append(value)
                col += 1
                if col >= 16:
                    break
            if col >= 16:
                break
        yield cells


if __name__ == "__main__":
    raise SystemExit(main())

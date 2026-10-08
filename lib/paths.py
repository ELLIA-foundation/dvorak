"""Repository layout: campaign folders and path resolvers.

Scripts that are not imported as part of the repo package should put the
repository root on ``sys.path`` before importing this module:

    from pathlib import Path
    import sys

    for _parent in Path(__file__).resolve().parents:
        if (_parent / "lib" / "paths.py").is_file() and (_parent / "Measurements").is_dir():
            if str(_parent) not in sys.path:
                sys.path.insert(0, str(_parent))
            break
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterable
from pathlib import Path

CAMPAIGN_SPARK_GAP = "Spark_Gap_Traces"
CAMPAIGN_FREQUENCY_RESPONSES = "Frequency_responses"
VIDEO_CONTAINER = "Videos"
SPECTRA_CONTAINER = "X123_Spectra"
_VIDEO_SKIP_DIRS = {"Analysis_scripts"}
RECIPES_CONTAINER = "Plot_recipes"
_SPECTRA_SKIP_DIRS = {"Analysis_scripts", "calibration"}
_SESSION_SKIP_DIRS = {"plots"}
VIDEO_EXTENSIONS = {".mp4", ".mov"}
MCA_EXTENSION = ".mca"


def slug_name(value: str | None) -> str:
    """Filesystem-safe token for session folders and measurement names."""
    if value is None:
        return ""
    return re.sub(r"[^\w\-]+", "_", str(value).strip()).strip("_")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def measurements_dir() -> Path:
    return repo_root() / "Measurements"


def instruments_dir() -> Path:
    return repo_root() / "instruments"


def list_campaigns() -> list[str]:
    """Instrument campaigns under Measurements/, excluding nested containers."""
    root = measurements_dir()
    if not root.is_dir():
        return []
    skip = {VIDEO_CONTAINER, SPECTRA_CONTAINER, RECIPES_CONTAINER}
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir()
        and not path.name.startswith(".")
        and path.name not in skip
    )


def campaign_dir(name: str) -> Path:
    path = measurements_dir() / name
    if not path.is_dir():
        known = ", ".join(list_campaigns()) or "(none)"
        raise FileNotFoundError(f"Unknown campaign {name!r}. Available: {known}")
    return path


def campaign_data(name: str) -> Path:
    return campaign_dir(name) / "Data"


def campaign_plots(name: str) -> Path:
    return campaign_data(name) / "plots"


def list_sessions(campaign: str) -> list[str]:
    """Immediate session folders under Measurements/<campaign>/Data/."""
    root = campaign_data(campaign)
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir()
        and not path.name.startswith(".")
        and path.name.lower() not in _SESSION_SKIP_DIRS
    )


def campaign_session_data(campaign: str, session: str, *, create: bool = False) -> Path:
    slug = slug_name(session)
    if not slug:
        raise ValueError(f"Invalid session name {session!r}")
    path = campaign_data(campaign) / slug
    if create:
        path.mkdir(parents=True, exist_ok=True)
        return path
    if not path.is_dir():
        known = ", ".join(list_sessions(campaign)) or "(none)"
        raise FileNotFoundError(
            f"Unknown session {session!r} in {campaign}. Available: {known}"
        )
    return path


def campaign_session_plots(campaign: str, session: str, *, create: bool = False) -> Path:
    return campaign_session_data(campaign, session, create=create) / "plots"


def infer_session(path: Path) -> str | None:
    """Return the session folder if ``path`` sits under Measurements/<campaign>/Data/<session>/."""
    resolved = path.resolve()
    campaign = infer_campaign(resolved)
    if campaign is None:
        return None
    data = campaign_data(campaign).resolve()
    try:
        relative = resolved.relative_to(data)
    except ValueError:
        return None
    parts = relative.parts
    if not parts or parts[0].lower() in _SESSION_SKIP_DIRS:
        return None
    session_dir = data / parts[0]
    if session_dir.is_dir():
        return parts[0]
    return None


def campaign_scripts(name: str) -> Path:
    return campaign_dir(name) / "Analysis_scripts"


def videos_dir() -> Path:
    return measurements_dir() / VIDEO_CONTAINER


def video_scripts_dir() -> Path:
    return videos_dir() / "Analysis_scripts"


def list_video_campaigns() -> list[str]:
    """Campaign folders under Measurements/Videos/, excluding the shared scripts."""
    root = videos_dir()
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir()
        and not path.name.startswith(".")
        and path.name not in _VIDEO_SKIP_DIRS
    )


def video_campaign_dir(name: str) -> Path:
    path = videos_dir() / name
    if not path.is_dir():
        known = ", ".join(list_video_campaigns()) or "(none)"
        raise FileNotFoundError(f"Unknown video campaign {name!r}. Available: {known}")
    return path


def video_campaign_data(name: str) -> Path:
    return video_campaign_dir(name) / "data"


def video_campaign_plots(name: str) -> Path:
    return video_campaign_dir(name) / "plots"


def list_video_clips(name: str) -> list[Path]:
    """MP4 and MOV files sitting directly in a video campaign folder."""
    folder = video_campaign_dir(name)
    clips = [
        path
        for path in folder.iterdir()
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in VIDEO_EXTENSIONS
    ]
    return sorted(clips, key=lambda path: path.name.lower())


def spectra_dir() -> Path:
    return measurements_dir() / SPECTRA_CONTAINER


def spectrum_scripts_dir() -> Path:
    return spectra_dir() / "Analysis_scripts"


def plot_recipes_dir() -> Path:
    """Default home for saved plot recipes of every analysis.

    Subfolders are the user's to create; recipes record which analysis they
    belong to, so one tree serves them all.
    """
    path = measurements_dir() / RECIPES_CONTAINER
    path.mkdir(parents=True, exist_ok=True)
    return path


def spectrum_calibration_path() -> Path:
    return spectra_dir() / "calibration" / "energy.json"


def list_spectrum_campaigns() -> list[str]:
    """Campaign folders under Measurements/X123_Spectra/."""
    root = spectra_dir()
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir()
        and not path.name.startswith(".")
        and path.name not in _SPECTRA_SKIP_DIRS
    )


def spectrum_campaign_dir(name: str) -> Path:
    path = spectra_dir() / name
    if not path.is_dir():
        known = ", ".join(list_spectrum_campaigns()) or "(none)"
        raise FileNotFoundError(f"Unknown spectrum campaign {name!r}. Available: {known}")
    return path


def spectrum_campaign_data(name: str) -> Path:
    return spectrum_campaign_dir(name) / "Data"


def spectrum_campaign_plots(name: str) -> Path:
    return spectrum_campaign_data(name) / "plots"


def resolve_spectrum_session(name: str) -> str:
    """Folder name under ``X123_Spectra/`` for a new or existing session.

    An existing campaign is kept as-is (including spaces). A new name is
    slugified the same way spark-gap session folders are.
    """
    raw = (name or "").strip()
    if not raw or raw.startswith("."):
        raise ValueError(f"Invalid session name {name!r}")
    existing = list_spectrum_campaigns()
    if raw in existing:
        return raw
    by_lower = {item.lower(): item for item in existing}
    if raw.lower() in by_lower:
        return by_lower[raw.lower()]
    slug = slug_name(raw)
    if not slug or slug in _SPECTRA_SKIP_DIRS or slug.startswith("."):
        raise ValueError(f"Invalid session name {name!r}")
    matched = by_lower.get(slug.lower())
    if matched:
        return matched
    return slug


def ensure_spectrum_session(name: str) -> Path:
    """Create ``Measurements/X123_Spectra/<session>/Data/`` and return it."""
    folder = resolve_spectrum_session(name)
    data = spectra_dir() / folder / "Data"
    data.mkdir(parents=True, exist_ok=True)
    return data


def collect_mca_files(paths: Iterable[Path]) -> list[Path]:
    """``.mca`` files from ``paths``, plus ``.mca`` files sitting directly in any directory."""
    found: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        path = Path(raw)
        if path.is_file() and path.suffix.lower() == MCA_EXTENSION:
            candidates = [path]
        elif path.is_dir():
            candidates = sorted(
                (
                    child
                    for child in path.iterdir()
                    if child.is_file()
                    and not child.name.startswith(".")
                    and child.suffix.lower() == MCA_EXTENSION
                ),
                key=lambda child: child.name.lower(),
            )
        else:
            continue
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(candidate)
    return found


def plan_mca_copy(
    dest: Path,
    sources: Iterable[Path],
) -> tuple[list[tuple[Path, Path]], list[tuple[Path, Path]]]:
    """Split MCA copies into ``(new, already_named)`` pairs of ``(source, destination)``.

    A source that is already the destination file is omitted.
    """
    fresh: list[tuple[Path, Path]] = []
    collisions: list[tuple[Path, Path]] = []
    for src in collect_mca_files(sources):
        target = dest / src.name
        try:
            same = target.exists() and target.resolve() == src.resolve()
        except OSError:
            same = False
        if same:
            continue
        if target.exists():
            collisions.append((src, target))
        else:
            fresh.append((src, target))
    return fresh, collisions


def copy_mca_pairs(pairs: Iterable[tuple[Path, Path]]) -> tuple[list[Path], list[str]]:
    """Copy planned pairs. Returns ``(written paths, error messages)``."""
    written: list[Path] = []
    errors: list[str] = []
    for src, target in pairs:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
        except OSError as exc:
            errors.append(f"{src.name}: {exc}")
        else:
            written.append(target)
    return written, errors


def list_spectrum_files(name: str) -> list[Path]:
    """``.mca`` files and ``spectrum_*.npz`` in a campaign folder or its Data/."""
    folder = spectrum_campaign_dir(name)
    files: list[Path] = []
    seen: set[Path] = set()
    search = [folder]
    data = folder / "Data"
    if data.is_dir():
        search.append(data)
    for directory in search:
        for path in directory.iterdir():
            if not path.is_file() or path.name.startswith("."):
                continue
            suffix = path.suffix.lower()
            is_mca = suffix == MCA_EXTENSION
            is_npz = suffix == ".npz" and path.name.startswith("spectrum_")
            if not is_mca and not is_npz:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(path)
    return sorted(files, key=lambda path: path.name.lower())


def infer_campaign(path: Path) -> str | None:
    """Return the campaign name if ``path`` sits under Measurements/<name>/."""
    resolved = path.resolve()
    root = measurements_dir().resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return None
    parts = relative.parts
    return parts[0] if parts else None

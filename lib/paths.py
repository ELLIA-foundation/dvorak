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

from pathlib import Path

CAMPAIGN_SPARK_GAP = "Spark_Gap_Traces"
CAMPAIGN_FREQUENCY_RESPONSES = "Frequency_responses"
VIDEO_CONTAINER = "Videos"
_VIDEO_SKIP_DIRS = {"Analysis_scripts"}
VIDEO_EXTENSIONS = {".mp4", ".mov"}


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def measurements_dir() -> Path:
    return repo_root() / "Measurements"


def instruments_dir() -> Path:
    return repo_root() / "instruments"


def list_campaigns() -> list[str]:
    """Instrument campaigns under Measurements/, excluding the Videos container."""
    root = measurements_dir()
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir()
        and not path.name.startswith(".")
        and path.name != VIDEO_CONTAINER
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

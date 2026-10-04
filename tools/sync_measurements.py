"""Bring raw Measurements/ captures from origin/master onto this branch.

NPZ waveforms and JSON sidecars are tracked in git. PNG, PDF, and MP4 stay
gitignored. Analysis machines on the Analysis branch run this to pick up new
lab captures without merging the rest of master.

Data/ has two owners. This branch keeps GUI analysis artifacts under
any ``plots/`` directory in ``Data/`` (``Data/plots/`` or
``Data/<session>/plots/``); master owns the raw files beside them. The
script commits those plot artifacts first, then checks out only raw Data/
files. ``Measurements/X123_Spectra/`` stays on this branch.

By default Analysis_scripts on this branch stay put. Pass --all to update
the rest of Measurements/ (still leaving those plots/ folders, and
X123_Spectra, alone).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REF_DEFAULT = "origin/master"
COMMIT_MESSAGE = "Save analysis artifacts from GUI pipeline"
VIDEO_CONTAINER = "Videos"
SPECTRA_CONTAINER = "X123_Spectra"
CHECKOUT_BATCH = 200
_IN_PROGRESS = (
    ("MERGE_HEAD", "merge"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
)


def _looks_like_root(path: Path) -> bool:
    return (path / "lib" / "paths.py").is_file() and (path / "Measurements").is_dir()


def _repo_root() -> Path:
    git_top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )
    if git_top.returncode == 0:
        root = Path(git_top.stdout.strip())
        if _looks_like_root(root):
            return root
    for parent in Path(__file__).resolve().parents:
        if _looks_like_root(parent):
            return parent
    raise SystemExit("Could not find repository root (expected lib/paths.py and Measurements/).")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )


def _die(message: str, detail: str = "") -> None:
    print(f"error: {message}", file=sys.stderr)
    extra = (detail or "").rstrip()
    if extra:
        print(extra, file=sys.stderr)
    raise SystemExit(1)


def _require_git_repo(root: Path) -> None:
    result = _git(root, "rev-parse", "--is-inside-work-tree")
    if result.returncode != 0 or result.stdout.strip() != "true":
        _die("not a git work tree", result.stderr)


def _ref_exists(root: Path, ref: str) -> bool:
    result = _git(root, "rev-parse", "--verify", ref)
    return result.returncode == 0


def _is_spectra_path(path: str) -> bool:
    parts = Path(path).parts
    return (
        len(parts) >= 2
        and parts[0] == "Measurements"
        and parts[1] == SPECTRA_CONTAINER
    )


def _plots_part_index(parts: tuple[str, ...]) -> int | None:
    """Index of a ``plots`` folder under ``Measurements/<campaign>/Data/``."""
    if len(parts) < 4 or parts[0] != "Measurements" or parts[2] != "Data":
        return None
    if parts[1] in {VIDEO_CONTAINER, SPECTRA_CONTAINER}:
        return None
    for index, part in enumerate(parts[3:], start=3):
        if part == "plots":
            return index
    return None


def _is_plots_path(path: str) -> bool:
    return _plots_part_index(Path(path).parts) is not None


def _is_raw_data_path(path: str) -> bool:
    parts = Path(path).parts
    return (
        len(parts) >= 4
        and parts[0] == "Measurements"
        and parts[2] == "Data"
        and parts[1] not in {VIDEO_CONTAINER, SPECTRA_CONTAINER}
        and _plots_part_index(parts) is None
    )


def _data_pathspecs(root: Path, ref: str) -> list[str]:
    result = _git(root, "ls-tree", "-r", "-d", "--name-only", ref, "Measurements")
    if result.returncode != 0:
        _die(f"could not list Measurements/ on {ref}", result.stderr)
    paths: list[str] = []
    for line in result.stdout.splitlines():
        parts = Path(line).parts
        if len(parts) == 3 and parts[0] == "Measurements" and parts[2] == "Data":
            paths.append(line)
    return paths


def _iter_campaign_dirs(root: Path) -> list[Path]:
    measurements = root / "Measurements"
    if not measurements.is_dir():
        return []
    return [
        campaign
        for campaign in sorted(measurements.iterdir())
        if campaign.is_dir()
        and not campaign.name.startswith(".")
        and campaign.name not in {VIDEO_CONTAINER, SPECTRA_CONTAINER}
    ]


def _local_data_dirs(root: Path) -> list[str]:
    return [
        str((campaign / "Data").relative_to(root))
        for campaign in _iter_campaign_dirs(root)
        if (campaign / "Data").is_dir()
    ]


def _local_plots_dirs(root: Path) -> list[str]:
    found: list[str] = []
    for campaign in _iter_campaign_dirs(root):
        data = campaign / "Data"
        if not data.is_dir():
            continue
        for plots in sorted(data.rglob("plots")):
            if not plots.is_dir():
                continue
            relative = plots.relative_to(data)
            if "plots" in relative.parts[:-1]:
                continue
            found.append(str(plots.relative_to(root)))
    return found


def _nul_paths(text: str) -> set[str]:
    return {item for item in text.split("\0") if item}


def _name_only(root: Path, *args: str) -> list[str]:
    result = _git(root, *args)
    if result.returncode != 0:
        _die(f"git {args[0]} failed", result.stderr or result.stdout)
    return [line for line in result.stdout.splitlines() if line.strip()]


def _local_changes(root: Path, *pathspecs: str) -> list[str]:
    if not pathspecs:
        return []
    names: set[str] = set()
    for diff_args in (
        ("diff", "--name-only", "HEAD", "--"),
        ("diff", "--name-only", "--cached", "--"),
        ("diff", "--name-only", "--"),
    ):
        names.update(_name_only(root, *diff_args, *pathspecs))
    others = _git(root, "ls-files", "-o", "--exclude-standard", "-z", "--", *pathspecs)
    if others.returncode != 0:
        _die("git ls-files failed", others.stderr)
    names.update(_nul_paths(others.stdout))
    return sorted(names)


def _ref_files(root: Path, ref: str, *pathspecs: str) -> list[str]:
    if not pathspecs:
        return []
    result = _git(root, "ls-tree", "-r", "--name-only", "-z", ref, "--", *pathspecs)
    if result.returncode != 0:
        _die(f"could not list files on {ref}", result.stderr)
    return sorted(_nul_paths(result.stdout))


def _checkout_files(root: Path, ref: str, all_tree: bool) -> list[str]:
    if all_tree:
        specs = ["Measurements"]
        files = _ref_files(root, ref, *specs)
        if not files:
            _die(f"no Measurements/ on {ref}")
    else:
        specs = _data_pathspecs(root, ref)
        if not specs:
            _die(f"no Measurements/<campaign>/Data folders on {ref}")
        files = _ref_files(root, ref, *specs)
    return [path for path in files if not _is_plots_path(path) and not _is_spectra_path(path)]


def _checkout_ref_files(root: Path, ref: str, files: list[str]) -> None:
    for start in range(0, len(files), CHECKOUT_BATCH):
        chunk = files[start : start + CHECKOUT_BATCH]
        result = _git(root, "checkout", ref, "--", *chunk)
        if result.returncode != 0:
            _die(f"git checkout {ref} -- Measurements/ failed", result.stderr or result.stdout)


def _git_path(root: Path, name: str) -> Path:
    result = _git(root, "rev-parse", "--git-path", name)
    if result.returncode != 0:
        _die("git rev-parse --git-path failed", result.stderr)
    path = Path(result.stdout.strip())
    return path if path.is_absolute() else root / path


def _sequencer_in_progress(root: Path) -> str | None:
    for name, label in _IN_PROGRESS:
        if _git_path(root, name).exists():
            return label
    return None


def _file_on_ref(root: Path, ref: str, path: str) -> bool:
    return _git(root, "cat-file", "-e", f"{ref}:{path}").returncode == 0


def _worktree_matches_ref(root: Path, ref: str, path: str) -> bool:
    if not _file_on_ref(root, ref, path):
        return False
    return _git(root, "diff", "--quiet", ref, "--", path).returncode == 0


def _plot_changes(root: Path) -> list[str]:
    return [path for path in _local_changes(root, *_local_plots_dirs(root)) if _is_plots_path(path)]


def _raw_changes(root: Path) -> list[str]:
    data_dirs = _local_data_dirs(root)
    if not data_dirs:
        return []
    return [path for path in _local_changes(root, *data_dirs) if _is_raw_data_path(path)]


def _conflicting_raw(root: Path, ref: str) -> list[str]:
    return [path for path in _raw_changes(root) if not _worktree_matches_ref(root, ref, path)]


def _commit_plots(root: Path, paths: list[str]) -> str:
    busy = _sequencer_in_progress(root)
    if busy:
        _die(f"cannot commit analysis artifacts during {busy}; finish it first")
    added = _git(root, "add", "--", *paths)
    if added.returncode != 0:
        _die("git add of analysis artifacts failed", added.stderr or added.stdout)
    cached = _git(root, "diff", "--cached", "--quiet", "--", *paths)
    if cached.returncode == 0:
        return ""
    if cached.returncode != 1:
        _die("git diff --cached failed", cached.stderr or cached.stdout)
    committed = _git(root, "commit", "-m", COMMIT_MESSAGE, "--", *paths)
    if committed.returncode != 0:
        _die("git commit of analysis artifacts failed", committed.stderr or committed.stdout)
    rev = _git(root, "rev-parse", "--short", "HEAD")
    return rev.stdout.strip() or "HEAD"


def _extra_paths(root: Path, ref: str, pathspecs: list[str]) -> list[str]:
    listed = _git(root, "ls-files", "-z", "--", *pathspecs)
    if listed.returncode != 0:
        _die("git ls-files failed", listed.stderr)
    on_ref = _git(root, "ls-tree", "-r", "--name-only", "-z", ref, "--", *pathspecs)
    if on_ref.returncode != 0:
        _die(f"git ls-tree {ref} failed", on_ref.stderr)
    extras = _nul_paths(listed.stdout) - _nul_paths(on_ref.stdout)
    return sorted(
        path
        for path in extras
        if not _is_plots_path(path) and not _is_spectra_path(path)
    )


def _remove_paths_missing_from_ref(root: Path, ref: str, pathspecs: list[str]) -> int:
    extras = _extra_paths(root, ref, pathspecs)
    if not extras:
        return 0
    removed = _git(root, "rm", "-q", "--", *extras)
    if removed.returncode != 0:
        _die("could not remove paths missing from the ref", removed.stderr)
    return len(extras)


def _print_paths(title: str, paths: list[str]) -> None:
    print(f"{title} {len(paths)} path(s):")
    for path in paths:
        print(f"  {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch origin, save GUI analysis artifacts under Data/ plots folders, "
            "then check out raw campaign Data/ from origin/master. "
            "NPZ/JSON are tracked; PNG/PDF/MP4 stay gitignored."
        )
    )
    parser.add_argument(
        "--from",
        dest="ref",
        default=REF_DEFAULT,
        help=f"Git ref to copy raw Measurements/ from (default: {REF_DEFAULT})",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Also update Analysis_scripts and the rest of Measurements/, except plots/ under Data/ and X123_Spectra/",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip git fetch (use the ref as it already exists locally)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the analysis commit and Measurements/ diff without changing files",
    )
    parser.add_argument(
        "--no-commit",
        action="store_true",
        help="Do not commit Data/ plots folders; still check out raw captures only",
    )
    args = parser.parse_args(argv)

    root = _repo_root()
    _require_git_repo(root)

    if not args.offline:
        fetch = _git(root, "fetch", "origin")
        if fetch.returncode != 0:
            _die("git fetch origin failed", fetch.stderr or fetch.stdout)

    if not _ref_exists(root, args.ref):
        _die(f"missing ref {args.ref!r} (fetch origin, or pass --from)")

    plots = _plot_changes(root)
    checkout_files = _checkout_files(root, args.ref, all_tree=args.all)
    extra_specs = ["Measurements"] if args.all else _data_pathspecs(root, args.ref)
    extras = _extra_paths(root, args.ref, extra_specs) if args.all else []

    if args.dry_run:
        if args.no_commit:
            print("Would skip committing analysis artifacts (--no-commit).")
        elif plots:
            _print_paths("Would commit", plots)
        else:
            print("No analysis artifacts to commit.")
        conflicts = _conflicting_raw(root, args.ref)
        if conflicts:
            _die(
                "raw Measurements/ Data files have local changes that differ from "
                f"{args.ref}; commit, stash, or discard them first",
                "\n".join(conflicts),
            )
        _print_paths(f"Would check out from {args.ref}", checkout_files)
        if extras:
            print(f"Would remove {len(extras)} path(s) not on {args.ref}.")
        if checkout_files:
            diff = _git(root, "diff", "--stat", "HEAD", args.ref, "--", *checkout_files)
            if diff.stdout.strip():
                print()
                print(diff.stdout.rstrip())
            else:
                print("Raw Data/ already matches this ref.")
        else:
            print("No raw files to check out.")
        return 0

    if plots and not args.no_commit:
        rev = _commit_plots(root, plots)
        if rev:
            print(f"Committed analysis artifacts as {rev}:")
            for path in plots:
                print(f"  {path}")
        else:
            print("No analysis artifacts to commit.")
    elif args.no_commit and plots:
        print(f"Left {len(plots)} analysis artifact(s) uncommitted (--no-commit).")
    else:
        print("No analysis artifacts to commit.")

    conflicts = _conflicting_raw(root, args.ref)
    if conflicts:
        _die(
            "raw Measurements/ Data files have local changes that differ from "
            f"{args.ref}; commit, stash, or discard them first",
            "\n".join(conflicts),
        )

    _checkout_ref_files(root, args.ref, checkout_files)

    removed = 0
    if args.all:
        removed = _remove_paths_missing_from_ref(root, args.ref, extra_specs)

    _print_paths(f"Checked out from {args.ref}", checkout_files)
    if removed:
        print(f"Removed {removed} path(s) not on {args.ref}.")
    print("New captures are staged; commit them when you want.")
    print("NPZ and JSON are tracked; PNG, PDF, and MP4 stay gitignored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

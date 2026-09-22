"""Bring Measurements/ data from origin/master onto this branch.

NPZ waveforms and JSON sidecars are tracked in git. PNG, PDF, and MP4 stay
gitignored. Analysis machines on the Analysis branch run this to pick up new
lab captures without merging the rest of master.

By default only each campaign's Data/ folder is updated, so Analysis_scripts
on this branch stay put. Pass --all to replace the entire Measurements/ tree.

Fails if the paths that would be overwritten have local changes.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REF_DEFAULT = "origin/master"


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


def _porcelain(root: Path, *pathspecs: str) -> list[str]:
    result = _git(root, "status", "--porcelain", "--", *pathspecs)
    if result.returncode != 0:
        _die("git status failed", result.stderr)
    return [line for line in result.stdout.splitlines() if line.strip()]


def _ref_exists(root: Path, ref: str) -> bool:
    result = _git(root, "rev-parse", "--verify", ref)
    return result.returncode == 0


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


def _pathspecs(root: Path, ref: str, all_tree: bool) -> list[str]:
    if all_tree:
        result = _git(root, "ls-tree", "--name-only", ref, "Measurements")
        if result.returncode != 0:
            _die(f"could not list Measurements/ on {ref}", result.stderr)
        if not result.stdout.strip():
            _die(f"no Measurements/ on {ref}")
        return ["Measurements"]
    paths = _data_pathspecs(root, ref)
    if not paths:
        _die(f"no Measurements/<campaign>/Data folders on {ref}")
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch origin and check out Measurements/ from origin/master. "
            "NPZ/JSON are tracked; PNG/PDF/MP4 stay gitignored."
        )
    )
    parser.add_argument(
        "--from",
        dest="ref",
        default=REF_DEFAULT,
        help=f"Git ref to copy Measurements/ from (default: {REF_DEFAULT})",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Replace the entire Measurements/ tree, including Analysis_scripts",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip git fetch (use the ref as it already exists locally)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the Measurements/ diff and exit without changing files",
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

    paths = _pathspecs(root, args.ref, all_tree=args.all)

    dirty = _porcelain(root, *paths)
    if dirty:
        _die(
            "Measurements/ has local changes; commit, stash, or discard them first",
            "\n".join(dirty),
        )

    if args.dry_run:
        diff = _git(root, "diff", "--stat", "HEAD", args.ref, "--", *paths)
        print(f"Would check out {len(paths)} path(s) from {args.ref}:")
        for path in paths:
            print(f"  {path}")
        if args.all:
            extras = _extra_paths(root, args.ref, paths)
            if extras:
                print(f"Would remove {len(extras)} path(s) not on {args.ref}.")
        if diff.stdout.strip():
            print()
            print(diff.stdout.rstrip())
        else:
            print("Already matches this ref.")
        return 0

    checkout = _git(root, "checkout", args.ref, "--", *paths)
    if checkout.returncode != 0:
        _die(f"git checkout {args.ref} -- Measurements/ failed", checkout.stderr or checkout.stdout)

    removed = 0
    if args.all:
        removed = _remove_paths_missing_from_ref(root, args.ref, paths)

    print(f"Checked out {len(paths)} path(s) from {args.ref}:")
    for path in paths:
        print(f"  {path}")
    if removed:
        print(f"Removed {removed} path(s) not on {args.ref}.")
    print("NPZ and JSON are tracked; PNG, PDF, and MP4 stay gitignored.")
    return 0


def _nul_paths(text: str) -> set[str]:
    return {item for item in text.split("\0") if item}


def _extra_paths(root: Path, ref: str, pathspecs: list[str]) -> list[str]:
    listed = _git(root, "ls-files", "-z", "--", *pathspecs)
    if listed.returncode != 0:
        _die("git ls-files failed", listed.stderr)
    on_ref = _git(root, "ls-tree", "-r", "--name-only", "-z", ref, "--", *pathspecs)
    if on_ref.returncode != 0:
        _die(f"git ls-tree {ref} failed", on_ref.stderr)
    return sorted(_nul_paths(listed.stdout) - _nul_paths(on_ref.stdout))


def _remove_paths_missing_from_ref(root: Path, ref: str, pathspecs: list[str]) -> int:
    extras = _extra_paths(root, ref, pathspecs)
    if not extras:
        return 0
    removed = _git(root, "rm", "-q", "--", *extras)
    if removed.returncode != 0:
        _die("could not remove paths missing from the ref", removed.stderr)
    return len(extras)


if __name__ == "__main__":
    raise SystemExit(main())

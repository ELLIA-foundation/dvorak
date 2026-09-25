#!/usr/bin/env python3
"""Extract a per-frame intensity chronograph from campaign clips.

Point this at a campaign folder, the Videos folder, or one clip:

    python3 Measurements/Videos/Analysis_scripts/extract_brightness.py Measurements/Videos/<campaign>
    python3 Measurements/Videos/Analysis_scripts/extract_brightness.py Measurements/Videos/<campaign>/<stem>.mp4
    python3 Measurements/Videos/Analysis_scripts/extract_brightness.py Measurements/Videos/<campaign> --force

Each clip becomes ``<campaign>/data/<stem>.csv`` (time and mean intensity) plus
a local JSON sidecar with fps, frame size, file fingerprint, and ``t1_s`` (the
detected rising edge). An existing cache is reused unless ``--force`` is passed.
This step does not assign tube or current on/off times.

Needs ffmpeg and ffprobe on PATH, and numpy.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np

from dataset import (
    EXTRACTOR_ID,
    cache_is_fresh,
    campaign_name_for,
    find_videos,
    load_meta,
    parse_video_name,
    rel_to_root,
    video_fingerprint,
    video_to_cache,
    write_meta,
)


def _require_ffmpeg() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise SystemExit(f"{', '.join(missing)} must be on PATH")


def probe_video(path: Path) -> tuple[int, int, float, int]:
    """Return width, height, fps, and an estimated frame count (0 if unknown)."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,nb_frames,duration",
        "-of",
        "default=noprint_wrappers=1:nokey=0",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffprobe must be on PATH") from exc
    width = height = None
    fps = None
    nb_frames = 0
    duration_s = 0.0
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        if key == "width":
            width = int(value)
        elif key == "height":
            height = int(value)
        elif key == "r_frame_rate":
            num, _, den = value.partition("/")
            fps = float(num) / float(den) if den else float(num)
        elif key == "nb_frames" and value.isdigit():
            nb_frames = int(value)
        elif key == "duration":
            try:
                duration_s = float(value)
            except ValueError:
                duration_s = 0.0
    if width is None or height is None or fps is None:
        raise RuntimeError(f"Could not probe {path}: {result.stdout}")
    expected = nb_frames
    if expected <= 0 and duration_s > 0 and fps > 0:
        expected = int(round(duration_s * fps))
    return width, height, fps, max(expected, 0)


def rising_edge_time(t: np.ndarray, y: np.ndarray) -> float:
    """First time y exceeds baseline + 0.25*(ymax - baseline)."""
    n = int(y.size)
    if n < 2:
        return 0.0
    ymax = float(y.max())
    nbase = max(30, min(n // 10, n))
    baseline = float(np.median(y[:nbase]))
    thresh = baseline + 0.25 * (ymax - baseline)
    hits = np.flatnonzero(y > thresh)
    if hits.size == 0:
        return 0.0
    return float(t[int(hits[0])])


def load_brightness_csv(csv_path: Path) -> tuple[np.ndarray, np.ndarray]:
    t: list[float] = []
    y: list[float] = []
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise RuntimeError(f"Empty CSV: {csv_path}")
        if "mean_Y" in reader.fieldnames:
            y_key = "mean_Y"
        elif "mean_rgb" in reader.fieldnames:
            y_key = "mean_rgb"
        else:
            raise RuntimeError(f"No intensity column in {csv_path}")
        for row in reader:
            t.append(float(row["t_s"]))
            y.append(float(row[y_key]))
    if not t:
        raise RuntimeError(f"No rows in {csv_path}")
    return np.asarray(t), np.asarray(y)


def _meta_dict(
    path: Path,
    *,
    width: int,
    height: int,
    fps: float,
    n_frames: int,
    t1_s: float,
) -> dict:
    size, mtime_ns = video_fingerprint(path)
    return {
        "source": rel_to_root(path),
        "size": size,
        "mtime_ns": mtime_ns,
        "width": width,
        "height": height,
        "fps": fps,
        "n_frames": n_frames,
        "t1_s": t1_s,
        "extractor": EXTRACTOR_ID,
    }


def extract_video(
    path: Path,
    out_csv: Path,
    meta_path: Path | None = None,
    on_frame: Callable[[int, int], None] | None = None,
) -> dict:
    """Decode full-frame RGB, write the chronograph CSV and sidecar.

    ``on_frame(done, expected)`` is called during the decode. ``expected`` is 0
    when the container does not advertise a frame count.
    """
    path = path.resolve()
    seq, current = parse_video_name(path)
    width, height, fps, expected = probe_video(path)
    nbytes = width * height * 3
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg must be on PATH") from exc
    if proc.stdout is None:
        raise RuntimeError("ffmpeg stdout pipe was not created")

    n_frames = 0
    times: list[float] = []
    means: list[float] = []
    if on_frame is not None:
        on_frame(0, expected)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    current_s = "" if np.isnan(current) else f"{current:.6g}"
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "frame",
                "t_s",
                "seq",
                "current_A",
                "mean_rgb",
                "mean_Y",
                "mean_R",
                "mean_G",
                "mean_B",
            ]
        )
        while True:
            buf = proc.stdout.read(nbytes)
            if len(buf) < nbytes:
                break
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
            mean_r, mean_g, mean_b = arr.mean(axis=(0, 1), dtype=np.float64)
            mean_rgb = (mean_r + mean_g + mean_b) / 3.0
            mean_y = 0.2126 * mean_r + 0.7152 * mean_g + 0.0722 * mean_b
            t_s = n_frames / fps
            times.append(t_s)
            means.append(mean_y)
            writer.writerow(
                [
                    n_frames,
                    f"{t_s:.6f}",
                    seq,
                    current_s,
                    f"{mean_rgb:.6f}",
                    f"{mean_y:.6f}",
                    f"{mean_r:.6f}",
                    f"{mean_g:.6f}",
                    f"{mean_b:.6f}",
                ]
            )
            n_frames += 1
            if n_frames % 25 == 0 and on_frame is not None:
                on_frame(n_frames, expected)
            if n_frames % 100 == 0:
                print(f"  {n_frames} frames", flush=True)

    stderr = proc.stderr.read() if proc.stderr is not None else b""
    ret = proc.wait()
    if ret != 0:
        raise RuntimeError(
            f"ffmpeg failed on {path.name} (exit {ret}): {stderr.decode(errors='replace')}"
        )
    if n_frames == 0:
        raise RuntimeError(f"No frames from {path}")
    if on_frame is not None:
        on_frame(n_frames, n_frames)

    t1 = rising_edge_time(np.asarray(times), np.asarray(means))
    if meta_path is None:
        _, meta_path = video_to_cache(path)
    meta = _meta_dict(
        path,
        width=width,
        height=height,
        fps=fps,
        n_frames=n_frames,
        t1_s=t1,
    )
    write_meta(meta_path, meta)
    print(
        f"{path.name}: {n_frames} frames, {width}x{height} @ {fps:g} fps "
        f"t1={t1:.4g} s -> {out_csv}"
    )
    return meta


def backfill_meta(video: Path, csv_path: Path, meta_path: Path) -> dict:
    """Write a sidecar for an existing CSV without re-decoding the clip."""
    video = video.resolve()
    width, height, fps, _expected = probe_video(video)
    t, y = load_brightness_csv(csv_path)
    meta = _meta_dict(
        video,
        width=width,
        height=height,
        fps=fps,
        n_frames=int(t.size),
        t1_s=rising_edge_time(t, y),
    )
    write_meta(meta_path, meta)
    print(f"backfill sidecar {video.name} -> {meta_path}")
    return meta


def ensure_cache(
    video: Path,
    force: bool = False,
    on_frame: Callable[[int, int], None] | None = None,
) -> tuple[Path, Path, dict]:
    """Return (csv, meta_path, meta), extracting only when the cache is stale."""
    video = video.resolve()
    if not video.is_file():
        raise FileNotFoundError(f"No such file: {video}")
    csv_path, meta_path = video_to_cache(video)
    if not force and cache_is_fresh(video, csv_path, meta_path):
        print(f"cache hit {video.name} -> {csv_path}")
        return csv_path, meta_path, load_meta(meta_path)
    if not force and csv_path.is_file() and not meta_path.is_file():
        meta = backfill_meta(video, csv_path, meta_path)
        return csv_path, meta_path, meta
    print(f"Reading {video.name} ...", flush=True)
    meta = extract_video(video, csv_path, meta_path, on_frame=on_frame)
    return csv_path, meta_path, meta


def resolve_targets(target: Path) -> list[Path]:
    target = target.resolve()
    if target.is_file():
        if target.suffix.lower() not in {".mp4", ".mov"}:
            raise FileNotFoundError(f"Not an MP4 or MOV file: {target}")
        return [target]
    if target.is_dir():
        videos = find_videos(target)
        if videos:
            return videos
        raise FileNotFoundError(f"No MP4 or MOV files found in {target}")
    raise FileNotFoundError(f"No such file or directory: {target}")


def _extract_one(video: Path, data_dir: Path | None, force: bool) -> None:
    if data_dir is None:
        ensure_cache(video, force=force)
        return
    out_csv = data_dir / f"{video.stem}.csv"
    meta_path = out_csv.with_suffix(".json")
    if not force and cache_is_fresh(video, out_csv, meta_path):
        print(f"cache hit {video.name} -> {out_csv}")
        return
    if not force and out_csv.is_file() and not meta_path.is_file():
        backfill_meta(video, out_csv, meta_path)
        return
    print(f"Reading {video.name} ...", flush=True)
    extract_video(video, out_csv, meta_path)


def main(argv: list[str] | None = None) -> int:
    _require_ffmpeg()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "measurement",
        type=Path,
        help="Campaign folder, the Videos folder, or a single MP4/MOV",
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=None,
        help="CSV directory (default: <campaign>/data next to each clip)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-extract even when the cache is fresh",
    )
    args = parser.parse_args(argv)

    try:
        videos = resolve_targets(args.measurement)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    campaigns = sorted({campaign_name_for(video) for video in videos})
    print(f"{len(videos)} clip(s) in {', '.join(campaigns)}")
    try:
        for video in videos:
            _extract_one(video, args.data, args.force)
    except (RuntimeError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

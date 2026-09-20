"""Record a clip from the configured camera into a campaign Data/ folder."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    if (_parent / "lib" / "paths.py").is_file() and (_parent / "Measurements").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break
else:
    raise SystemExit("Could not find repository root (expected lib/paths.py and Measurements/).")

from instruments.registry import DEFAULT_CAMERA, list_cameras, open_camera
from lib.paths import campaign_data, list_campaigns
from lib.video import save_video, video_stem


def _resolve_output_dir(campaign: str | None, output_dir: Path | None) -> Path:
    if output_dir is not None:
        return output_dir
    if campaign:
        try:
            return campaign_data(campaign)
        except FileNotFoundError as exc:
            raise SystemExit(str(exc)) from exc
    known = ", ".join(list_campaigns()) or "(none)"
    raise SystemExit(
        "Specify --campaign (writes to Measurements/<name>/Data/) or --output-dir. "
        f"Available campaigns: {known}"
    )


def main() -> None:
    registered = list_cameras()
    parser = argparse.ArgumentParser(
        description="Record a video clip from the bench camera into a campaign Data/ folder."
    )
    parser.add_argument(
        "--campaign",
        type=str,
        default=None,
        help="Measurement campaign name (saves under Measurements/<name>/Data/)",
    )
    parser.add_argument(
        "--camera",
        type=str,
        default=None,
        choices=registered or None,
        help=(
            "Camera model id "
            f"(default: {DEFAULT_CAMERA}). "
            f"Registered: {', '.join(registered) or '(none)'}"
        ),
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=3.0,
        help="Recording length in seconds (default: 3)",
    )
    parser.add_argument(
        "--quality",
        choices=("preview", "full"),
        default="preview",
        help="NDI proxy stream (preview, default) or full-bandwidth 4K (full)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override save directory (default: Measurements/<campaign>/Data/)",
    )
    parser.add_argument(
        "--no-preview",
        action="store_true",
        help="Do not extract a first-frame PNG under Data/plots/",
    )
    args = parser.parse_args()

    output_dir = _resolve_output_dir(args.campaign, args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    video_path = output_dir / f"{video_stem()}.mp4"
    print(f"Opening camera {args.camera or DEFAULT_CAMERA} ...", flush=True)

    camera = open_camera(args.camera)
    try:
        print(f"Connected: {camera.identify()}", flush=True)
        print(f"Model:     {camera.model_id}", flush=True)
        print(f"Recording: {args.duration:.2f} s -> {video_path}", flush=True)
        capture = camera.record(args.duration, video_path, quality=args.quality)
        paths = save_video(capture, write_preview=not args.no_preview)
        extra = capture.extra
        print(f"\nBackend:   {extra.get('backend', '?')}", flush=True)
        if extra.get("ndi_source"):
            print(f"NDI:       {extra['ndi_source']}", flush=True)
        print(f"Frames:    {capture.frame_count}", flush=True)
        print(f"Size:      {capture.width}x{capture.height} @ {capture.frame_rate_hz:.3f} fps", flush=True)
        print(f"Duration:  {capture.duration_s:.3f} s", flush=True)
        print("\nSaved:", flush=True)
        for label, path in paths.items():
            print(f"  {label}: {path}", flush=True)
    finally:
        camera.close()
    # NDI Runtime leaves native worker threads that block a normal CPython exit.
    os._exit(0)


if __name__ == "__main__":
    main()

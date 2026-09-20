"""Identity-check the instruments configured in instruments/lab.json."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    if (_parent / "lib" / "paths.py").is_file() and (_parent / "Measurements").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break
else:
    raise SystemExit("Could not find repository root (expected lib/paths.py and Measurements/).")

from instruments.registry import load_lab, open_generator, open_oscilloscope


def _check_oscilloscope(model_id: str | None) -> None:
    scope = open_oscilloscope(model_id)
    try:
        print("Oscilloscope")
        print(f"  model: {scope.model_id}")
        print(f"  IDN:   {scope.identify()}")
    finally:
        scope.close()


def _check_generator(model_id: str | None) -> None:
    try:
        generator = open_generator(model_id)
    except NotImplementedError as exc:
        print("Generator")
        print(f"  skipped: {exc}")
        return
    try:
        print("Generator")
        print(f"  model: {generator.model_id}")
        print(f"  IDN:   {generator.identify()}")
    except NotImplementedError as exc:
        print("Generator")
        print(f"  skipped: {exc}")
    finally:
        generator.close()


def main() -> None:
    lab = load_lab()
    parser = argparse.ArgumentParser(
        description="Query *IDN? on the oscilloscope and generator configured in lab.json."
    )
    parser.add_argument(
        "--scope",
        type=str,
        default=None,
        help="Oscilloscope model id override",
    )
    parser.add_argument(
        "--generator",
        type=str,
        default=None,
        help="Generator model id override",
    )
    parser.add_argument(
        "--skip-generator",
        action="store_true",
        help="Do not probe the generator role",
    )
    args = parser.parse_args()

    print(f"lab.json roles: {lab.get('roles', {})}")
    _check_oscilloscope(args.scope)
    if not args.skip_generator:
        _check_generator(args.generator)


if __name__ == "__main__":
    main()

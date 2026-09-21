"""Import plugin modules so they register with the launcher."""

from __future__ import annotations


def load_plugins() -> None:
    from . import measurement, spark_gap, trace  # noqa: F401

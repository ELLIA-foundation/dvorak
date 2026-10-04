"""Dvorak desktop analysis front end.

A thin PySide6 shell over campaign analysis libraries. This package must not
import ``instruments`` or PyVISA — analysis machines do not talk to the bench.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# PySide is already imported by the time a figure pack loads. Without this,
# matplotlib selects the Qt backend and aborts when that first import happens
# on a worker thread.
os.environ.setdefault("MPLBACKEND", "Agg")

__version__ = "0.1.0"
APP_NAME = "Dvorak"

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

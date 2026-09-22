"""Load a campaign Analysis_scripts module without treating the folder as a package.

Campaign folders are not installable packages (and some CLIs import instruments).
Import the library module by file path so the GUI never pulls in PyVISA.
"""

from __future__ import annotations

import importlib.util
import sys
from types import ModuleType


def load_campaign_module(campaign: str, module: str) -> ModuleType:
    from lib.paths import campaign_scripts

    path = campaign_scripts(campaign) / f"{module}.py"
    if not path.is_file():
        raise FileNotFoundError(f"No {module}.py in {campaign} Analysis_scripts ({path})")
    key = f"dvorak_campaign_{campaign}_{module}"
    existing = sys.modules.get(key)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(key, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load {path}")
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[key] = loaded
    spec.loader.exec_module(loaded)
    return loaded

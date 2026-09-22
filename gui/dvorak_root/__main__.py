"""Long-lived ROOT renderer. Protocol on the original stdout; ROOT chatter on stderr.

Each request is one JSON line. Each response is one line prefixed with ``DVORAK ``.
"""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
from pathlib import Path


def _protocol_stream():
    """Point C/Python stdout at stderr, and keep the parent's pipe for replies."""
    proto = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return proto


def _reply(proto, payload: dict) -> None:
    proto.write("DVORAK " + json.dumps(payload, separators=(",", ":")) + "\n")
    proto.flush()


def _safe_stem(name: str) -> str:
    """File stem that is also a legal ROOT macro function name."""
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")
    if not stem or stem[0].isdigit():
        stem = "fig_" + stem
    return stem or "figure"


def _render(request: dict) -> dict:
    import ROOT

    from .figures import render_spec

    ROOT.gROOT.SetBatch(True)
    spec = request.get("spec") or {}
    canvas = render_spec(spec)
    outputs = set(request.get("outputs") or ["json"])
    result: dict = {}
    if "json" in outputs:
        encoded = ROOT.TBufferJSON.ToJSON(canvas, 3)
        result["json"] = str(encoded)
    stem = _safe_stem(str(spec.get("name") or "figure"))
    if "pdf" in outputs:
        pdf = request.get("pdf_path") or str(Path(request.get("dir") or ".") / f"{stem}.pdf")
        Path(pdf).parent.mkdir(parents=True, exist_ok=True)
        canvas.SaveAs(str(pdf))
        result["pdf"] = str(pdf)
    if "macro" in outputs:
        requested = request.get("macro_path")
        folder = Path(requested).parent if requested else Path(request.get("dir") or ".")
        macro = folder / f"{stem}.C"
        macro.parent.mkdir(parents=True, exist_ok=True)
        canvas.SaveAs(str(macro))
        result["macro"] = str(macro)
    return result


def main() -> int:
    proto = _protocol_stream()
    import ROOT

    ROOT.gROOT.SetBatch(True)
    ROOT.gErrorIgnoreLevel = ROOT.kWarning

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _reply(proto, {"ok": False, "error": f"invalid request: {exc}"})
            continue
        ident = request.get("id")
        if request.get("op") == "quit":
            _reply(proto, {"id": ident, "ok": True})
            return 0
        try:
            payload = _render(request)
        except Exception:
            _reply(proto, {"id": ident, "ok": False, "error": traceback.format_exc()})
            continue
        payload["id"] = ident
        payload["ok"] = True
        _reply(proto, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

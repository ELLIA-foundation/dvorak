"""Long-lived ROOT renderer. Protocol on the original stdout; ROOT chatter on stderr.

Each request is one JSON line. Each response is one line prefixed with ``DVORAK ``.

``op`` is ``render`` (a figure spec to JSON, PDF, or macro), ``legacy`` (a
canvas exported from the JSROOT page to a macro), or ``quit``.
"""

from __future__ import annotations

import json
import os
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


def _size(request: dict) -> tuple[int, int] | None:
    size = request.get("size")
    if isinstance(size, (list, tuple)) and len(size) == 2:
        try:
            width, height = int(size[0]), int(size[1])
        except (TypeError, ValueError):
            return None
        if width > 0 and height > 0:
            return width, height
    return None


def _macro_path(request: dict, stem: str) -> Path:
    from .layout import safe_stem

    requested = request.get("macro_path")
    folder = Path(requested).parent if requested else Path(request.get("dir") or ".")
    return folder / f"{safe_stem(stem)}.C"


def _render(request: dict) -> dict:
    import ROOT

    from .figures import render_spec
    from .layout import safe_stem
    from .macro import write_macro
    from .text import root_text

    ROOT.gROOT.SetBatch(True)
    spec = request.get("spec") or {}
    size = _size(request)
    canvas = render_spec(spec, size=size)
    outputs = set(request.get("outputs") or ["json"])
    result: dict = {}
    if "json" in outputs:
        encoded = ROOT.TBufferJSON.ToJSON(canvas, 3)
        result["json"] = str(encoded)
    stem = safe_stem(str(spec.get("name") or "figure"))
    if "pdf" in outputs:
        pdf = request.get("pdf_path") or str(Path(request.get("dir") or ".") / f"{stem}.pdf")
        Path(pdf).parent.mkdir(parents=True, exist_ok=True)
        canvas.SaveAs(str(pdf))
        result["pdf"] = str(pdf)
    if "macro" in outputs:
        title = root_text(spec.get("label") or spec.get("title") or stem)
        macro = write_macro(canvas, _macro_path(request, stem), title=title, size=size)
        result["macro"] = str(macro)
    return result


def _legacy(request: dict) -> dict:
    """Rebuild a canvas exported from the JSROOT page; write a macro and/or PDF."""
    import ROOT

    from .macro import canvas_from_json, write_macro
    from .style import apply_root_style
    from .text import root_text

    ROOT.gROOT.SetBatch(True)
    apply_root_style()
    stem = str(request.get("name") or "figure")
    canvas = canvas_from_json(str(request.get("canvas") or ""), request.get("view") or {})
    outputs = set(request.get("outputs") or ["macro"])
    result: dict = {}
    if "pdf" in outputs:
        pdf = str(request.get("pdf_path") or Path(request.get("dir") or ".") / f"{stem}.pdf")
        Path(pdf).parent.mkdir(parents=True, exist_ok=True)
        canvas.SaveAs(pdf)
        result["pdf"] = pdf
    if "macro" in outputs:
        title = root_text(request.get("title") or stem)
        macro = write_macro(canvas, _macro_path(request, stem), title=title, size=_size(request))
        result["macro"] = str(macro)
    return result


def main() -> int:
    proto = _protocol_stream()
    import ROOT

    from .style import keep_colors_with_canvas

    ROOT.gROOT.SetBatch(True)
    ROOT.gErrorIgnoreLevel = ROOT.kWarning
    keep_colors_with_canvas()

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
        op = request.get("op") or "render"
        if op == "quit":
            _reply(proto, {"id": ident, "ok": True})
            return 0
        try:
            payload = _legacy(request) if op == "legacy" else _render(request)
        except Exception:
            _reply(proto, {"id": ident, "ok": False, "error": traceback.format_exc()})
            continue
        payload["id"] = ident
        payload["ok"] = True
        _reply(proto, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

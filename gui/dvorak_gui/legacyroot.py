"""Launch the interactive ROOT GUI on a saved canvas macro (.C)."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QMessageBox, QWidget


def _safe_stem(name: str) -> str:
    """File stem that is also a legal ROOT macro function name."""
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")
    if not stem or stem[0].isdigit():
        stem = "fig_" + stem
    return stem or "figure"


def _align_draw_options(node) -> None:
    """Give every primitive a draw option.

    JSROOT's export can append a color table to ``arr`` and leave ``opt`` one
    entry short. ``TBufferJSON`` then indexes off the end of that array and
    the interactive session aborts before the canvas opens.
    """
    if isinstance(node, dict):
        arr = node.get("arr")
        opt = node.get("opt")
        if isinstance(arr, list) and isinstance(opt, list):
            for index, value in enumerate(opt):
                if value is None:
                    opt[index] = ""
            if len(opt) < len(arr):
                opt.extend("" for _ in range(len(arr) - len(opt)))
        for value in node.values():
            _align_draw_options(value)
    elif isinstance(node, list):
        for value in node:
            _align_draw_options(value)


def _prepare_canvas_json(canvas_json: str) -> str:
    try:
        data = json.loads(canvas_json)
    except json.JSONDecodeError:
        return canvas_json
    _align_draw_options(data)
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def write_canvas_macro(directory: Path, name: str, canvas_json: str) -> Path:
    """Write a macro that rebuilds a canvas from TBufferJSON.

    ``SaveAs(.C)`` puts title bytes into a C string. The interactive ROOT
    session then draws those bytes as Latin-1, so characters that JSROOT
    showed correctly come out as â. Loading the JSON that JSROOT already
    parsed keeps the canvas, including titles and colors edited in the page.
    """
    stem = _safe_stem(name)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{stem}.json"
    macro = directory / f"{stem}.C"
    json_path.write_text(_prepare_canvas_json(canvas_json), encoding="utf-8")
    c_path = str(json_path.resolve()).replace("\\", "\\\\").replace('"', '\\"')
    macro.write_text(
        f"""#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include "TBufferJSON.h"
#include "TCanvas.h"
#include "TColor.h"
#include "TObjArray.h"
#include "TROOT.h"

void {stem}() {{
  std::ifstream in("{c_path}");
  if (!in) {{
    printf("Could not open canvas JSON\\n");
    return;
  }}
  std::stringstream buffer;
  buffer << in.rdbuf();
  std::string text = buffer.str();
  TObject *obj = TBufferJSON::ConvertFromJSON(text.c_str());
  if (!obj) {{
    printf("Could not read the canvas JSON\\n");
    return;
  }}
  if (auto *canvas = dynamic_cast<TCanvas*>(obj)) {{
    // Custom colors travel in a ListOfColors primitive. Reading the JSON
    // creates the objects; registering them makes the fill indices drawable.
    if (auto *prims = canvas->GetListOfPrimitives()) {{
      TIter next(prims);
      while (auto *item = next()) {{
        if (std::strcmp(item->GetName(), "ListOfColors") != 0) continue;
        auto *colors = dynamic_cast<TObjArray*>(item);
        if (!colors) continue;
        for (int i = 0; i < colors->GetEntriesFast(); ++i) {{
          auto *col = dynamic_cast<TColor*>(colors->At(i));
          if (!col || col->GetNumber() < 1000 || gROOT->GetColor(col->GetNumber()))
            continue;
          new TColor(col->GetNumber(), col->GetRed(), col->GetGreen(), col->GetBlue(),
                     col->GetName(), col->GetAlpha());
        }}
      }}
    }}
    // JSON from the batch renderer has no pad painter. Draw() then crashes
    // in TCanvas::Build(). A real painter has to exist before the window opens.
    canvas->GetCanvasPainter();
    canvas->SetBatch(kFALSE);
    unsigned ww = canvas->GetWindowWidth();
    unsigned wh = canvas->GetWindowHeight();
    if (ww < 2) ww = canvas->GetWw() > 2 ? canvas->GetWw() : 800;
    if (wh < 2) wh = canvas->GetWh() > 2 ? canvas->GetWh() : 600;
    canvas->SetWindowSize(ww, wh);
    canvas->Draw();
    return;
  }}
  obj->Draw();
}}
""",
        encoding="utf-8",
    )
    return macro


def find_root_binary(rootsys: str | None = None) -> Path | None:
    """Locate the interactive ``root`` executable."""
    candidates: list[Path] = []
    if rootsys:
        candidates.append(Path(rootsys) / "bin" / "root")
    env = os.environ.get("ROOTSYS", "")
    if env:
        candidates.append(Path(env) / "bin" / "root")
    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return cand
    which = shutil.which("root")
    return Path(which) if which else None


def open_legacy_root(
    macro_path: str | Path,
    *,
    rootsys: str | None = None,
    parent: QWidget | None = None,
) -> bool:
    """Detach-launch ``root -l macro.C``. Returns True when the process started."""
    path = Path(macro_path)
    if not path.is_file():
        QMessageBox.critical(parent, "Legacy ROOT", f"Macro not found:\n{path}")
        return False

    binary = find_root_binary(rootsys)
    if binary is None:
        QMessageBox.critical(
            parent,
            "Legacy ROOT",
            "Cannot find the interactive ROOT executable.\n\n"
            "Set ROOTSYS (or put `root` on PATH) and restart Dvorak.",
        )
        return False

    ok = QProcess.startDetached(str(binary), ["-l", str(path.resolve())])
    if not ok:
        QMessageBox.critical(
            parent,
            "Legacy ROOT",
            f"Failed to launch:\n{binary} -l {path}",
        )
        return False
    return True

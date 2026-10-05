"""A JSROOT canvas in a web view.

The renderer serialises each TCanvas with TBufferJSON. JSROOT draws that
object, so pan, zoom, and hover follow the ROOT canvas rather than a picture
of it. The bundle is a local file from the ROOT installation.

The renderer lays a figure out for the pixel size it will be shown at, so the
owner asks for ``canvas_size()`` before rendering and renders again when
``resized`` fires. A figure taller than the pane scrolls instead of shrinking.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QTemporaryDir, QTimer, QUrl, Signal
from PySide6.QtWidgets import QLabel, QStackedWidget, QVBoxLayout, QWidget

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView

    WEBENGINE_AVAILABLE = True
except ImportError:
    WEBENGINE_AVAILABLE = False


SHELL_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  html, body { margin: 0; padding: 0; height: 100%; background: #ffffff; overflow: hidden; }
  #plot { width: 100%; height: 100%; }
  #msg {
    font: 13px -apple-system, Helvetica, Arial, sans-serif;
    color: #888; padding: 16px;
  }
</style>
<script src="__JSROOT_URL__"></script>
</head>
<body>
<div id="plot"></div>
<div id="msg">Loading JSROOT...</div>
<script>
(function() {
  var dom = document.getElementById('plot');
  var msg = document.getElementById('msg');
  if (typeof JSROOT === 'undefined') {
    msg.textContent = 'JSROOT could not be loaded.';
    return;
  }
  msg.style.display = 'none';
  // gStyle belongs to the ROOT session, not the canvas, so none of it
  // survives TBufferJSON. Restore what rootlogon.C sets.
  JSROOT.gStyle.fOptStat = 0;
  JSROOT.gStyle.fOptTitle = 0;
  if (typeof JSROOT.gStyle.fPadTickY !== 'undefined') JSROOT.gStyle.fPadTickY = 1;
  if (typeof JSROOT.gStyle.fTextFont !== 'undefined') JSROOT.gStyle.fTextFont = 42;
  if (JSROOT.settings) {
    JSROOT.settings.MoveResize = true;
    JSROOT.settings.DragAndDrop = true;
  }
  var pending = Promise.resolve();
  var dvorakPainter = null;
  var dvorakHeight = 0;
  window.dvorakSource = '';

  // Labels arrive as ASCII TLatex so Legacy ROOT can draw them. JSROOT draws
  // #ddot{a} and friends poorly, so show the real letter here; the export
  // turns it back into TLatex on the way to ROOT.
  var ACCENTS = {
    ddot: '\\u0308', acute: '\\u0301', grave: '\\u0300', hat: '\\u0302',
    tilde: '\\u0303', check: '\\u030c', dot: '\\u0307'
  };
  var ACCENT_RE = /#(ddot|acute|grave|hat|tilde|check|dot)\\{([A-Za-z])\\}/g;
  function dvorakAccents(node, seen) {
    if (!node || typeof node !== 'object' || seen.indexOf(node) >= 0) return;
    seen.push(node);
    var keys = Object.keys(node);
    for (var i = 0; i < keys.length; i++) {
      var key = keys[i];
      if (key.charAt(0) === '_' || key.charAt(0) === '$') continue;
      var value = node[key];
      if (typeof value === 'string') {
        if (value.indexOf('#') >= 0)
          node[key] = value.replace(ACCENT_RE, function(_m, name, letter) {
            return (letter + ACCENTS[name]).normalize('NFC');
          });
      } else if (value && typeof value === 'object') {
        dvorakAccents(value, seen);
      }
    }
  }

  // A figure laid out taller than the pane scrolls. The wheel then scrolls
  // the page; drag a box on an axis to zoom.
  function dvorakFit() {
    var tall = dvorakHeight > window.innerHeight + 1;
    dom.style.height = tall ? (dvorakHeight + 'px') : '100%';
    document.documentElement.style.overflowY = tall ? 'auto' : 'hidden';
    document.body.style.overflowY = 'visible';
    if (JSROOT.settings) JSROOT.settings.ZoomWheel = !tall;
  }

  window.dvorakDraw = function(text, height) {
    window.dvorakSource = text || '';
    dvorakHeight = Number(height) || 0;
    pending = pending.catch(function() {}).then(function() {
      var obj = JSROOT.parse(text);
      dvorakAccents(obj, []);
      dvorakPainter = null;
      window.dvorakPainter = null;
      JSROOT.cleanup(dom);
      dvorakFit();
      return JSROOT.draw(dom, obj, '').then(function(painter) {
        dvorakPainter = painter;
        window.dvorakPainter = painter;
      });
    });
  };

  function dvorakColorRecord(index, spec) {
    if (typeof spec !== 'string') return null;
    var r = 0, g = 0, b = 0, a = 1, hex = '';
    if (spec.charAt(0) === '#' && spec.length >= 7) {
      hex = spec.slice(0, 7);
      r = parseInt(hex.slice(1, 3), 16) / 255;
      g = parseInt(hex.slice(3, 5), 16) / 255;
      b = parseInt(hex.slice(5, 7), 16) / 255;
      if (spec.length >= 9) a = parseInt(spec.slice(7, 9), 16) / 255;
    } else {
      var match = spec.match(/rgba?\\(\\s*([\\d.]+)\\s*,\\s*([\\d.]+)\\s*,\\s*([\\d.]+)(?:\\s*,\\s*([\\d.]+))?/);
      if (!match) return null;
      r = Number(match[1]) / 255;
      g = Number(match[2]) / 255;
      b = Number(match[3]) / 255;
      if (match[4] !== undefined) a = Number(match[4]);
      hex = '#' + [r, g, b].map(function(value) {
        var byte = Math.max(0, Math.min(255, Math.round(value * 255))).toString(16);
        return byte.length < 2 ? '0' + byte : byte;
      }).join('');
    }
    if (!isFinite(r) || !isFinite(g) || !isFinite(b) || !isFinite(a)) return null;
    return {
      _typename: 'TColor', fUniqueID: 0, fBits: 0,
      fName: hex, fTitle: hex, fNumber: index,
      fRed: r, fGreen: g, fBlue: b,
      fHue: 0, fLight: 0, fSaturation: 0, fAlpha: a
    };
  }
  function dvorakWalk(node, seen, visit) {
    if (!node || typeof node !== 'object') return;
    if (seen.indexOf(node) >= 0) return;
    seen.push(node);
    visit(node);
    var keys = Object.keys(node);
    for (var i = 0; i < keys.length; i++) {
      if (keys[i].charAt(0) === '$') continue;
      dvorakWalk(node[keys[i]], seen, visit);
    }
  }
  function dvorakPlainColor(col) {
    if (!col || col._typename !== 'TColor' || typeof col.fNumber !== 'number') return null;
    return {
      _typename: 'TColor', fUniqueID: 0, fBits: 0,
      fName: col.fName || '', fTitle: col.fTitle || col.fName || '',
      fNumber: col.fNumber,
      fRed: col.fRed || 0, fGreen: col.fGreen || 0, fBlue: col.fBlue || 0,
      fHue: col.fHue || 0, fLight: col.fLight || 0, fSaturation: col.fSaturation || 0,
      fAlpha: (col.fAlpha == null) ? 1 : col.fAlpha
    };
  }
  function dvorakDropNamed(list, name) {
    var arr = list && list.arr;
    if (!arr) return;
    for (var i = arr.length - 1; i >= 0; i--) {
      if (!arr[i] || arr[i].name !== name) continue;
      arr.splice(i, 1);
      if (Array.isArray(list.opt) && list.opt.length > i) list.opt.splice(i, 1);
    }
  }
  // Keep a color record for every index the canvas uses, including colors
  // picked from the JSROOT menus, which exist only in this page.
  function dvorakColorTable(canvas) {
    var used = {};
    var defined = {};
    dvorakWalk(canvas, [], function(node) {
      Object.keys(node).forEach(function(key) {
        var value = node[key];
        if (/Color$/.test(key) && typeof value === 'number' && value > 0) used[value] = true;
      });
      var plain = dvorakPlainColor(node);
      if (plain) defined[plain.fNumber] = plain;
    });
    var records = [];
    Object.keys(used).forEach(function(key) {
      var index = Number(key);
      var record = defined[index];
      if (!record && dvorakPainter && typeof dvorakPainter.getColor === 'function')
        record = dvorakColorRecord(index, dvorakPainter.getColor(index));
      if (record) records.push(record);
    });
    return records;
  }

  function dvorakRange(fp, axis) {
    function num(v) { return (typeof v === 'number' && isFinite(v)) ? v : null; }
    var lo = num(fp['zoom_' + axis + 'min']);
    var hi = num(fp['zoom_' + axis + 'max']);
    if (lo === null || hi === null || !(hi > lo)) {
      lo = num(fp['scale_' + axis + 'min']);
      hi = num(fp['scale_' + axis + 'max']);
    }
    return (lo === null || hi === null || !(hi > lo)) ? null : [lo, hi];
  }

  // What the user is looking at but the canvas object does not hold: the
  // visible range of each pad, and the size the canvas is drawn at.
  function dvorakViewState() {
    var pads = {};
    if (dvorakPainter && typeof dvorakPainter.forEachPainterInPad === 'function') {
      dvorakPainter.forEachPainterInPad(function(pp) {
        var fp = (typeof pp.getFramePainter === 'function') ? pp.getFramePainter() : null;
        var pad = (typeof pp.getRootPad === 'function') ? pp.getRootPad() : null;
        if (!fp || !pad || !pad.fName) return;
        pads[pad.fName] = { x: dvorakRange(fp, 'x'), y: dvorakRange(fp, 'y') };
      }, 'pads');
    }
    var rect = dom.getBoundingClientRect();
    return { width: Math.round(rect.width), height: Math.round(rect.height), pads: pads };
  }

  window.dvorakExport = function() {
    return pending.then(function() {
      var obj = null;
      if (dvorakPainter && typeof dvorakPainter.getObject === 'function')
        obj = dvorakPainter.getObject();
      if (!obj || typeof JSROOT.toJSON !== 'function')
        return '';
      var list = obj.fPrimitives;
      var records = dvorakColorTable(obj);
      var saved = null;
      if (list)
        saved = { arr: list.arr.slice(), opt: Array.isArray(list.opt) ? list.opt.slice() : list.opt };
      if (list) {
        dvorakDropNamed(list, 'ListOfColors');
        dvorakDropNamed(list, 'CurrentColorPalette');
        if (records.length) {
          list.arr.push({ _typename: 'TObjArray', name: 'ListOfColors', arr: records,
                          opt: records.map(function() { return ''; }) });
          // TBufferJSON reads one option per primitive.
          if (Array.isArray(list.opt)) list.opt.push('');
        }
      }
      var canvas = '';
      try {
        canvas = JSROOT.toJSON(obj) || '';
      } finally {
        // Put the page's own lists back in place; painters hold on to them.
        if (saved) {
          list.arr.length = 0;
          Array.prototype.push.apply(list.arr, saved.arr);
          if (Array.isArray(list.opt) && Array.isArray(saved.opt)) {
            list.opt.length = 0;
            Array.prototype.push.apply(list.opt, saved.opt);
          }
        }
      }
      if (!canvas) return '';
      return JSON.stringify({ canvas: canvas, view: dvorakViewState() });
    }).catch(function() { return ''; });
  };

  window.addEventListener('resize', function() {
    dvorakFit();
    JSROOT.resize(dom);
  });
})();
</script>
</body>
</html>
"""


class JsRootView(QWidget):
    """One JSROOT canvas. ``draw`` takes a TBufferJSON payload."""

    # Debounced (width, height) after the pane settles on a new size.
    resized = Signal(int, int)

    def __init__(self, jsroot_path: str = "", parent=None) -> None:
        super().__init__(parent)
        self.jsroot_path = ""
        self._page_ready = False
        self._pending: tuple[str, int] | None = None
        self._export_token: object | None = None
        self._source_json = ""
        self._tmp = QTemporaryDir()
        self._last_size: tuple[int, int] | None = None
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(250)
        self._resize_timer.timeout.connect(self._emit_resized)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        self.placeholder = QLabel("Looking for a ROOT installation…")
        self.placeholder.setWordWrap(True)
        self.placeholder.setMargin(16)
        self.placeholder.setStyleSheet("color: #777;")
        self.stack.addWidget(self.placeholder)

        self.view = None
        if WEBENGINE_AVAILABLE:
            self.view = QWebEngineView()
            self.view.loadFinished.connect(self._on_load_finished)
            self.stack.addWidget(self.view)
        if jsroot_path:
            self.set_bundle(jsroot_path)
        elif not WEBENGINE_AVAILABLE:
            self.show_message(
                "PySide6 is installed without Qt WebEngine, so interactive "
                "ROOT plots are unavailable. Legacy ROOT and PDF export still work."
            )

    @property
    def usable(self) -> bool:
        return WEBENGINE_AVAILABLE and bool(self.jsroot_path) and self.view is not None

    def canvas_size(self) -> tuple[int, int]:
        """Pixel size a canvas would be drawn at, in CSS pixels."""
        size = self.stack.size()
        return max(1, size.width()), max(1, size.height())

    def show_message(self, text: str) -> None:
        self.placeholder.setText(text)
        self.stack.setCurrentWidget(self.placeholder)

    def set_bundle(self, jsroot_path: str) -> None:
        if not jsroot_path or jsroot_path == self.jsroot_path:
            return
        self.jsroot_path = jsroot_path
        self._page_ready = False
        if self.view is None:
            self.show_message(
                "PySide6 is installed without Qt WebEngine, so interactive "
                "ROOT plots are unavailable."
            )
            return
        self._load_shell()

    @property
    def source_json(self) -> str:
        """Renderer JSON last handed to ``draw``, color table included."""
        return self._source_json

    def draw(self, payload: str, height: int = 0) -> None:
        """Draw a canvas. ``height`` is the pixel height it was laid out for."""
        self._source_json = payload or ""
        if not self.view or not self.usable:
            return
        self.stack.setCurrentWidget(self.view)
        if not self._page_ready:
            self._pending = (payload, int(height))
            return
        self.view.page().runJavaScript(
            f"window.dvorakDraw({json.dumps(payload)}, {int(height)});"
        )

    def export_canvas(self, callback) -> None:
        """Hand back the drawn canvas as it looks now.

        ``callback`` receives ``{"canvas": <TBufferJSON text>, "view": {...}}``
        or ``None``. The canvas carries edits made from the JSROOT menus; the
        view carries each pad's visible range and the drawn size. Qt does not
        unwrap the Promise returned by the page, so the script stores the
        text and this method polls for it.
        """
        if self.view is None or not self.usable or not self._page_ready:
            callback(None)
            return
        token = object()
        self._export_token = token
        page = self.view.page()

        def finish(text: str) -> None:
            try:
                data = json.loads(text) if text else None
            except json.JSONDecodeError:
                data = None
            if not isinstance(data, dict) or not str(data.get("canvas") or "").startswith("{"):
                callback(None)
                return
            if not isinstance(data.get("view"), dict):
                data["view"] = {}
            callback(data)

        def started(_result: object) -> None:
            self._poll_export(token, finish, 0)

        page.runJavaScript(
            "window.dvorakExportResult = null;"
            "window.dvorakExport().then(function(text) {"
            "  window.dvorakExportResult = (typeof text === 'string') ? text : '';"
            "}).catch(function() { window.dvorakExportResult = ''; });",
            started,
        )

    def _poll_export(self, token: object, callback, attempt: int) -> None:
        if token is not self._export_token or self.view is None:
            return
        if attempt > 300:
            callback("")
            return

        def got(result: object) -> None:
            if token is not self._export_token:
                return
            if isinstance(result, str) and result != "__PENDING__":
                callback(result)
                return
            QTimer.singleShot(50, lambda: self._poll_export(token, callback, attempt + 1))

        self.view.page().runJavaScript(
            "window.dvorakExportResult === null ? '__PENDING__' : window.dvorakExportResult",
            got,
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._resize_timer.start()

    def _emit_resized(self) -> None:
        size = self.canvas_size()
        if size == self._last_size:
            return
        self._last_size = size
        self.resized.emit(*size)

    def _load_shell(self) -> None:
        if not self.view or not self._tmp.isValid():
            return
        root = Path(self._tmp.path())
        shell = root / "shell.html"
        shell.write_text(
            SHELL_HTML.replace(
                "__JSROOT_URL__", QUrl.fromLocalFile(self.jsroot_path).toString()
            ),
            encoding="utf-8",
        )
        self.view.load(QUrl.fromLocalFile(str(shell)))

    def _on_load_finished(self, ok: bool) -> None:
        self._page_ready = bool(ok)
        if not ok:
            self.show_message("JSROOT page failed to load.")
            return
        if self._pending is not None:
            (payload, height), self._pending = self._pending, None
            self.draw(payload, height)

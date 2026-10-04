"""A JSROOT canvas in a web view.

The renderer serialises each TCanvas with TBufferJSON. JSROOT draws that
object, so pan, zoom, and hover follow the ROOT canvas rather than a picture
of it. The bundle is a local file from the ROOT installation.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QTemporaryDir, QTimer, QUrl
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
  html, body { margin: 0; padding: 0; height: 100%; background: #ffffff; }
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
  window.dvorakSource = '';
  window.dvorakDraw = function(text) {
    window.dvorakSource = text || '';
    pending = pending.catch(function() {}).then(function() {
      var obj = JSROOT.parse(text);
      dvorakPainter = null;
      window.dvorakPainter = null;
      JSROOT.cleanup(dom);
      return JSROOT.draw(dom, obj, '').then(function(painter) {
        dvorakPainter = painter;
        window.dvorakPainter = painter;
      });
    });
  };
  function dvorakColorRecord(index, spec) {
    if (typeof spec !== 'string' || index < 1000) return null;
    var r = 0, g = 0, b = 0, hex = '';
    if (spec.charAt(0) === '#' && spec.length >= 7) {
      hex = spec.slice(0, 7);
      r = parseInt(hex.slice(1, 3), 16) / 255;
      g = parseInt(hex.slice(3, 5), 16) / 255;
      b = parseInt(hex.slice(5, 7), 16) / 255;
    } else {
      var match = spec.match(/rgba?\\(\\s*([\\d.]+)\\s*,\\s*([\\d.]+)\\s*,\\s*([\\d.]+)/);
      if (!match) return null;
      r = Number(match[1]) / 255;
      g = Number(match[2]) / 255;
      b = Number(match[3]) / 255;
      hex = '#' + [r, g, b].map(function(value) {
        var byte = Math.max(0, Math.min(255, Math.round(value * 255))).toString(16);
        return byte.length < 2 ? '0' + byte : byte;
      }).join('');
    }
    if (!isFinite(r) || !isFinite(g) || !isFinite(b)) return null;
    return {
      _typename: 'TColor', fUniqueID: 0, fBits: 0,
      fName: hex, fTitle: hex, fNumber: index,
      fRed: r, fGreen: g, fBlue: b,
      fHue: 0, fLight: 0, fSaturation: 0, fAlpha: 1
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
  function dvorakAttachColors(canvas) {
    var list = canvas && canvas.fPrimitives;
    var prims = list && list.arr;
    if (!prims) return;
    var used = {};
    var defined = {};
    dvorakWalk(canvas, [], function(node) {
      ['fFillColor', 'fLineColor', 'fMarkerColor', 'fTextColor'].forEach(function(key) {
        var value = node[key];
        if (typeof value === 'number' && value >= 1000) used[value] = true;
      });
      var plain = dvorakPlainColor(node);
      if (plain) defined[plain.fNumber] = plain;
    });
    Object.keys(used).forEach(function(key) {
      var index = Number(key);
      if (defined[index]) return;
      var spec = (dvorakPainter && typeof dvorakPainter.getColor === 'function')
        ? dvorakPainter.getColor(index) : null;
      var record = dvorakColorRecord(index, spec);
      if (record) defined[index] = record;
    });
    var missing = Object.keys(used).some(function(key) { return !defined[Number(key)]; });
    if (missing && window.dvorakSource) {
      try {
        var source = JSROOT.parse(window.dvorakSource);
        dvorakWalk(source, [], function(node) {
          var plain = dvorakPlainColor(node);
          if (plain && used[plain.fNumber] && !defined[plain.fNumber])
            defined[plain.fNumber] = plain;
        });
      } catch (err) {}
    }
    var records = [];
    Object.keys(used).forEach(function(key) {
      var record = defined[Number(key)];
      if (record) records.push(record);
    });
    dvorakDropNamed(list, 'ListOfColors');
    if (!records.length) return;
    prims.push({
      _typename: 'TObjArray',
      name: 'ListOfColors',
      arr: records,
      opt: records.map(function() { return ''; })
    });
    // TBufferJSON reads one option per primitive. A shorter opt array aborts the load.
    if (Array.isArray(list.opt)) list.opt.push('');
  }
  window.dvorakExport = function() {
    return pending.then(function() {
      var obj = null;
      if (dvorakPainter && typeof dvorakPainter.getObject === 'function')
        obj = dvorakPainter.getObject();
      if (!obj || typeof JSROOT.toJSON !== 'function')
        return '';
      dvorakAttachColors(obj);
      var exported = JSROOT.toJSON(obj) || '';
      var prims = obj.fPrimitives && obj.fPrimitives.arr;
      if (prims && prims.length && prims[prims.length - 1] && prims[prims.length - 1].name === 'ListOfColors') {
        prims.pop();
        var opt = obj.fPrimitives.opt;
        if (Array.isArray(opt) && opt.length > prims.length) opt.pop();
      }
      return exported;
    }).catch(function() { return ''; });
  };
  window.addEventListener('resize', function() {
    JSROOT.resize(dom);
  });
})();
</script>
</body>
</html>
"""


class JsRootView(QWidget):
    """One JSROOT canvas. ``draw`` takes a TBufferJSON payload."""

    def __init__(self, jsroot_path: str = "", parent=None) -> None:
        super().__init__(parent)
        self.jsroot_path = ""
        self._page_ready = False
        self._pending_payload: str | None = None
        self._export_token: object | None = None
        self._source_json = ""
        self._tmp = QTemporaryDir()

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

    def draw(self, payload: str) -> None:
        self._source_json = payload or ""
        if not self.view or not self.usable:
            return
        self.stack.setCurrentWidget(self.view)
        if not self._page_ready:
            self._pending_payload = payload
            return
        self.view.page().runJavaScript(f"window.dvorakDraw({json.dumps(payload)});")

    def export_json(self, callback) -> None:
        """Hand the drawn canvas back as TBufferJSON, including menu edits.

        ``callback`` receives a string. It is empty when the page has no canvas.
        Qt does not unwrap the Promise returned by the page, so the script
        stores the text and this method polls for it.
        """
        if self.view is None or not self.usable or not self._page_ready:
            callback("")
            return
        token = object()
        self._export_token = token
        page = self.view.page()

        def started(_result: object) -> None:
            self._poll_export(token, callback, 0)

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
        if self._pending_payload is not None:
            payload, self._pending_payload = self._pending_payload, None
            self.draw(payload)

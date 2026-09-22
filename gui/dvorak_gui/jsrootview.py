"""A JSROOT canvas in a web view.

The renderer serialises each TCanvas with TBufferJSON. JSROOT draws that
object, so pan, zoom, and hover follow the ROOT canvas rather than a picture
of it. The bundle is a local file from the ROOT installation.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QTemporaryDir, QUrl
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
  window.dvorakDraw = function(text) {
    pending = pending.catch(function() {}).then(function() {
      var obj = JSROOT.parse(text);
      JSROOT.cleanup(dom);
      return JSROOT.draw(dom, obj, '');
    });
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

    def draw(self, payload: str) -> None:
        if not self.view or not self.usable:
            return
        self.stack.setCurrentWidget(self.view)
        if not self._page_ready:
            self._pending_payload = payload
            return
        self.view.page().runJavaScript(f"window.dvorakDraw({json.dumps(payload)});")

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

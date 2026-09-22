"""Find ROOT and talk to the renderer. This module does not import ROOT or Qt."""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

_GUI_DIR = Path(__file__).resolve().parent.parent
_JSROOT_RELATIVE = (
    "share/root/js/build/jsroot.js",
    "js/build/jsroot.js",
    "share/js/build/jsroot.js",
)
_PROBE_CODE = "import ROOT; print(ROOT.gROOT.GetVersion())"


def gui_dir() -> Path:
    return _GUI_DIR


def find_rootsys() -> str:
    env = os.environ.get("ROOTSYS", "").strip()
    if env and Path(env).is_dir():
        return str(Path(env).resolve())
    root_config = shutil.which("root-config")
    if root_config:
        try:
            prefix = subprocess.check_output(
                [root_config, "--prefix"], text=True, timeout=10
            ).strip()
        except (OSError, subprocess.SubprocessError):
            prefix = ""
        if prefix and Path(prefix).is_dir():
            return str(Path(prefix).resolve())
    root_bin = shutil.which("root")
    if root_bin:
        candidate = Path(root_bin).resolve().parent.parent
        if (candidate / "bin" / "root").is_file() or (candidate / "lib").is_dir():
            return str(candidate)
    for candidate in ("/opt/homebrew/opt/root", "/usr/local/opt/root"):
        path = Path(candidate)
        if path.is_dir():
            return str(path.resolve())
    return ""


def find_root_binary(rootsys: str) -> str:
    if rootsys:
        candidate = Path(rootsys) / "bin" / "root"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    which = shutil.which("root")
    return which or ""


def find_jsroot(rootsys: str) -> str:
    if not rootsys:
        return ""
    base = Path(rootsys)
    for relative in _JSROOT_RELATIVE:
        candidate = base / relative
        if candidate.is_file():
            return str(candidate)
    return ""


def root_env(rootsys: str) -> dict[str, str]:
    env = os.environ.copy()
    lib = str(Path(rootsys) / "lib")
    lib_root = str(Path(rootsys) / "lib" / "root")
    bin_dir = str(Path(rootsys) / "bin")
    env["ROOTSYS"] = rootsys
    env["PATH"] = os.pathsep.join([bin_dir, env.get("PATH", "")])
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_GUI_DIR), lib, lib_root, env.get("PYTHONPATH", "")]
    )
    for key in ("DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH"):
        env[key] = os.pathsep.join([lib, env.get(key, "")])
    return env


def _python_candidates(rootsys: str) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def add(path: str | Path | None) -> None:
        if not path:
            return
        text = str(path)
        if not text or text in seen:
            return
        if Path(text).is_file() and os.access(text, os.X_OK):
            seen.add(text)
            ordered.append(text)

    if rootsys:
        for name in ("python3", "python"):
            add(Path(rootsys) / "bin" / name)
    venv_exec = str(Path(sys.executable).resolve()) if sys.executable else ""
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found and Path(found).resolve() != Path(venv_exec):
            add(found)
    add(sys.executable)
    return ordered


def render_once(spec: dict, pdf_path: str | Path) -> Path:
    """Render one canvas to PDF with a short-lived ROOT process."""
    report = probe()
    if not report.get("python"):
        raise RuntimeError(str(report.get("error") or "ROOT is not available."))
    client = RootClient(report)
    try:
        reply = client.render(spec, outputs=("pdf",), pdf_path=str(pdf_path))
    finally:
        client.close()
    return Path(str(reply["pdf"]))


def probe() -> dict:
    """Locate ROOT, a matching Python, and the JSROOT bundle shipped with it."""
    rootsys = find_rootsys()
    report: dict = {
        "rootsys": rootsys,
        "root": find_root_binary(rootsys),
        "jsroot": find_jsroot(rootsys),
        "python": "",
        "version": "",
        "error": "",
    }
    if not rootsys:
        report["error"] = "ROOTSYS is unset and `root` is not on PATH."
        return report
    env = root_env(rootsys)
    errors: list[str] = []
    for python in _python_candidates(rootsys):
        try:
            completed = subprocess.run(
                [python, "-c", _PROBE_CODE],
                env=env,
                capture_output=True,
                text=True,
                timeout=40,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{python}: {exc}")
            continue
        if completed.returncode == 0:
            report["python"] = python
            report["version"] = (completed.stdout or "").strip().splitlines()[-1] if completed.stdout else ""
            return report
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()
        errors.append(f"{python}: {tail[-1] if tail else 'import ROOT failed'}")
    report["error"] = errors[-1] if errors else "No Python could import ROOT."
    return report


class RootClient:
    """One long-lived ``python -m dvorak_root`` process."""

    def __init__(self, report: dict | None = None) -> None:
        self.report = report
        self._ready = threading.Event()
        self._on_ready = None
        if report is not None:
            self._ready.set()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stderr: deque[str] = deque(maxlen=40)
        self._pending = b""
        self._ids = 0

    @property
    def available(self) -> bool:
        return bool(self.report and self.report.get("python"))

    @property
    def jsroot(self) -> str:
        return str((self.report or {}).get("jsroot") or "")

    @property
    def rootsys(self) -> str:
        return str((self.report or {}).get("rootsys") or "")

    def probe_async(self, on_ready=None) -> None:
        self._on_ready = on_ready

        def run() -> None:
            try:
                self.report = probe()
            except Exception as exc:  # noqa: BLE001 — surface probe failures in the GUI
                self.report = {"error": str(exc), "python": "", "jsroot": "", "rootsys": "", "root": ""}
            finally:
                self._ready.set()
                callback = self._on_ready
                if callback is not None:
                    callback(self.report or {})

        threading.Thread(target=run, name="dvorak-root-probe", daemon=True).start()

    def wait(self, timeout: float | None = 45) -> dict:
        self._ready.wait(timeout)
        return self.report or {}

    def render(
        self,
        spec: dict,
        *,
        outputs: tuple[str, ...] | list[str] = ("json",),
        pdf_path: str | None = None,
        macro_path: str | None = None,
        directory: str | None = None,
        timeout: float = 60,
    ) -> dict:
        self.wait()
        if not self.available:
            error = (self.report or {}).get("error") or "ROOT is not available."
            raise RuntimeError(error)
        with self._lock:
            if self._ids and self._ids % 25 == 0:
                self._recycle()
            self._ensure()
            self._ids += 1
            ident = str(self._ids)
            request = {
                "op": "render",
                "id": ident,
                "spec": spec,
                "outputs": list(outputs),
            }
            if pdf_path:
                request["pdf_path"] = pdf_path
            if macro_path:
                request["macro_path"] = macro_path
            if directory:
                request["dir"] = directory
            assert self._proc is not None and self._proc.stdin is not None
            payload = json.dumps(request, separators=(",", ":"), allow_nan=False) + "\n"
            self._proc.stdin.write(payload.encode("utf-8"))
            self._proc.stdin.flush()
            reply = self._read_reply(ident, timeout)
        if not reply.get("ok"):
            raise RuntimeError(str(reply.get("error") or "ROOT render failed"))
        return reply

    def close(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            if proc is None or proc.poll() is not None:
                return
            self._stop(proc)

    def _recycle(self) -> None:
        proc = self._proc
        self._proc = None
        self._pending = b""
        if proc is not None and proc.poll() is None:
            self._stop(proc)

    def _stop(self, proc: subprocess.Popen) -> None:
        try:
            if proc.stdin:
                proc.stdin.write(b'{"op":"quit","id":"quit"}\n')
                proc.stdin.flush()
            proc.wait(timeout=2)
        except Exception:
            proc.kill()

    def _ensure(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        report = self.report or {}
        python = str(report.get("python") or "")
        rootsys = str(report.get("rootsys") or "")
        if not python or not rootsys:
            raise RuntimeError("ROOT renderer is not configured.")
        proc = subprocess.Popen(
            [python, "-m", "dvorak_root"],
            cwd=str(_GUI_DIR),
            env=root_env(rootsys),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._proc = proc
        self._pending = b""
        threading.Thread(target=self._drain_stderr, args=(proc,), daemon=True).start()

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        if proc.stderr is None:
            return
        for line in proc.stderr:
            self._stderr.append(line.decode("utf-8", errors="replace"))

    def _read_reply(self, ident: str, timeout: float) -> dict:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise RuntimeError("ROOT renderer is not running.")
        deadline = time.time() + timeout
        buf = self._pending
        while time.time() < deadline:
            if proc.poll() is not None and not buf:
                tail = "".join(self._stderr).strip()
                raise RuntimeError(tail or "ROOT renderer exited.")
            ready, _, _ = select.select([proc.stdout], [], [], 0.25)
            if ready:
                chunk = os.read(proc.stdout.fileno(), 1 << 20)
                if not chunk:
                    tail = "".join(self._stderr).strip()
                    raise RuntimeError(tail or "ROOT renderer closed its output.")
                buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.startswith(b"DVORAK "):
                    continue
                reply = json.loads(line[7:].decode("utf-8"))
                if reply.get("id") not in (None, ident):
                    continue
                self._pending = buf
                return reply
        self._pending = buf
        raise TimeoutError("ROOT renderer timed out.")

"""Marshall CV420-30X-NDI LAN driver.

Control/identity uses the Lumens/Marshall JSON CGI on HTTP. Video is pulled
from the live NDI|HX source (primary) with RTSP as a fallback if the NDI
Runtime is missing but ffmpeg can open an RTSP URL.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from instruments.camera import Camera
from lib.video import VideoCapture

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "9999"
DEFAULT_TIMEOUT_S = 3.0
NDI_FIND_TIMEOUT_S = 6.0
NDI_FIRST_FRAME_TIMEOUT_S = 12.0
RTSP_CANDIDATES = (
    "rtsp://{ip}:8557/h264",
    "rtsp://{ip}:8554/hevc",
    "rtsp://{ip}:8556/h264",
    "rtsp://{user}:{password}@{ip}:8557/h264",
    "rtsp://{user}:{password}@{ip}:8554/hevc",
)


class MarshallCV42030XNDI(Camera):
    model_id = "marshall_cv420_30x_ndi"

    def __init__(self, connection: dict | None = None) -> None:
        super().__init__(connection)
        self._uuid = ""
        self._about: dict[str, Any] = {}
        self._login: dict[str, Any] = {}
        self._connected = False

    @property
    def ip(self) -> str:
        ip = self.connection.get("ip")
        if not ip:
            raise ValueError(
                "marshall_cv420_30x_ndi connection is missing 'ip' (see instruments/lab.json)"
            )
        return str(ip)

    @property
    def username(self) -> str:
        return str(self.connection.get("username") or DEFAULT_USERNAME)

    @property
    def password(self) -> str:
        return str(self.connection.get("password") or DEFAULT_PASSWORD)

    def _cgi(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(payload or {}).encode("utf-8")
        request = Request(
            f"http://{self.ip}/cgi-bin/{name}",
            data=body,
            headers={"Content-Type": "application/json", "Connection": "close"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=DEFAULT_TIMEOUT_S) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            raise RuntimeError(f"{name} HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"{name} failed: {exc.reason}") from exc
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{name} returned non-JSON: {raw[:200]!r}") from exc
        if isinstance(parsed, dict) and parsed.get("STATUS") == "NOAUTH":
            raise PermissionError(f"{name} rejected the CGI session")
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    def connect(self) -> None:
        if self._connected:
            return
        self._uuid = str(uuid.uuid4())
        try:
            login = self._cgi(
                "lums_login.cgi",
                {
                    "cmd": "loginauth",
                    "username": self.username,
                    "password": self.password,
                    "uuid": self._uuid,
                },
            )
        except RuntimeError as exc:
            # NDI capture does not need the web session. Keep going if CGI is busy.
            self._login = {"cgi_error": str(exc)}
            self._about = {}
            self._connected = True
            return
        if login.get("STATUS") == "NG":
            raise PermissionError(
                f"Camera login failed ({login.get('ERRCODE', 'AUTH')}) at {self.ip}"
            )
        self._login = login
        try:
            self._about = self._cgi(
                "lums_aboutinq.cgi",
                {"cmd": "aboutinq", "uuid": self._uuid},
            )
        except RuntimeError:
            self._about = {}
        self._connected = True

    def close(self) -> None:
        self._uuid = ""
        self._connected = False

    def identify(self) -> str:
        about = self._about or {}
        name = about.get("cameraname") or self._login.get("cameraname") or "CV420-30X-NDI"
        model = self._login.get("model") or about.get("lumswebmodel") or ""
        fw = (
            about.get("fwversionlinux")
            or self._login.get("fwversionrtos")
            or self._login.get("softwareversion")
            or ""
        )
        parts = [str(name).strip()]
        if model:
            parts.append(str(model).strip())
        if fw:
            parts.append(str(fw).strip())
        parts.append(self.ip)
        return ", ".join(p for p in parts if p)

    def video_status(self) -> dict[str, Any]:
        return self._cgi("lums_videoinq.cgi", {"cmd": "videoinq", "uuid": self._uuid})

    def _record_ndi(self, duration_s: float, output_path: Path, quality: str = "preview") -> VideoCapture:
        from instruments.cameras.marshall_cv420_30x_ndi.ndi import (
            NDI_RECV_BANDWIDTH_HIGHEST,
            NDI_RECV_BANDWIDTH_LOWEST,
            find_sources,
            pick_source,
            record_source,
        )

        bandwidth = (
            NDI_RECV_BANDWIDTH_HIGHEST if quality == "full" else NDI_RECV_BANDWIDTH_LOWEST
        )
        print(f"NDI discover {self.ip} ...", flush=True)
        sources = find_sources(extra_ips=self.ip, timeout_s=NDI_FIND_TIMEOUT_S)
        source = pick_source(sources, ip=self.ip, name_substr="CV420")
        print(f"NDI source {source.name} ({source.url})", flush=True)
        result = record_source(
            source,
            output_path,
            duration_s=duration_s,
            first_frame_timeout_s=NDI_FIRST_FRAME_TIMEOUT_S,
            bandwidth=bandwidth,
        )
        return VideoCapture(
            video_path=output_path,
            idn=self.identify(),
            model_id=self.model_id,
            duration_s=result.duration_s,
            frame_count=result.frame_count,
            width=result.width,
            height=result.height,
            frame_rate_hz=result.frame_rate_hz,
            captured_at=datetime.now().isoformat(timespec="seconds"),
            extra={
                "backend": "ndi",
                "ndi_source": result.source_name,
                "ndi_url": result.source_url,
                "fourcc": result.fourcc,
                "quality": quality,
            },
        )

    def _record_rtsp(self, duration_s: float, output_path: Path) -> VideoCapture:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise FileNotFoundError("ffmpeg is not on PATH")
        last_error = ""
        for template in RTSP_CANDIDATES:
            url = template.format(ip=self.ip, user=self.username, password=self.password)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            cmd = [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-rtsp_transport",
                "tcp",
                "-i",
                url,
                "-t",
                f"{duration_s:.3f}",
                "-an",
                "-c",
                "copy",
                str(output_path),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 1000:
                return VideoCapture(
                    video_path=output_path,
                    idn=self.identify(),
                    model_id=self.model_id,
                    duration_s=duration_s,
                    frame_count=0,
                    width=0,
                    height=0,
                    frame_rate_hz=0.0,
                    captured_at=datetime.now().isoformat(timespec="seconds"),
                    extra={"backend": "rtsp", "rtsp_url": url.split("@")[-1]},
                )
            last_error = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
        raise RuntimeError(f"RTSP capture failed: {last_error}")

    def record(self, duration_s: float, output_path: Path, **kwargs) -> VideoCapture:
        if duration_s <= 0:
            raise ValueError("duration_s must be positive")
        quality = str(kwargs.get("quality") or "preview")
        ndi_error: Exception | None = None
        try:
            return self._record_ndi(duration_s, output_path, quality=quality)
        except Exception as exc:
            ndi_error = exc
        try:
            return self._record_rtsp(duration_s, output_path)
        except Exception as rtsp_error:
            raise RuntimeError(
                f"NDI record failed ({ndi_error}); RTSP fallback failed ({rtsp_error})"
            ) from rtsp_error

"""Thin ctypes wrapper around the NDI Runtime for finding a source and recording.

Requires the NDI Runtime (this bench has NDI 6 under Program Files). NDI|HX
cameras also need the NDI|HX driver, which ships with NDI Tools.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import tempfile
import time
from ctypes import POINTER, c_bool, c_char_p, c_float, c_int, c_int64, c_uint8, c_uint32, c_void_p
from dataclasses import dataclass
from pathlib import Path

NDI_FRAME_NONE = 0
NDI_FRAME_VIDEO = 1
NDI_RECV_BGRX_BGRA = 0
NDI_RECV_BANDWIDTH_LOWEST = 0
NDI_RECV_BANDWIDTH_HIGHEST = 100

_FOURCC_BGRX = int.from_bytes(b"BGRX", "little")
_FOURCC_BGRA = int.from_bytes(b"BGRA", "little")
_FOURCC_UYVY = int.from_bytes(b"UYVY", "little")
_FOURCC_RGBA = int.from_bytes(b"RGBA", "little")
_FOURCC_RGBX = int.from_bytes(b"RGBX", "little")

_PIX_FMT = {
    _FOURCC_BGRX: "bgr0",
    _FOURCC_BGRA: "bgra",
    _FOURCC_UYVY: "uyvy422",
    _FOURCC_RGBA: "rgba",
    _FOURCC_RGBX: "rgb0",
}

_BYTES_PER_PIXEL = {
    _FOURCC_BGRX: 4,
    _FOURCC_BGRA: 4,
    _FOURCC_UYVY: 2,
    _FOURCC_RGBA: 4,
    _FOURCC_RGBX: 4,
}


class NDIlib_source_t(ctypes.Structure):
    _fields_ = [
        ("p_ndi_name", c_char_p),
        ("p_url_address", c_char_p),
    ]


class NDIlib_find_create_t(ctypes.Structure):
    _fields_ = [
        ("show_local_sources", c_bool),
        ("p_groups", c_char_p),
        ("p_extra_ips", c_char_p),
    ]


class NDIlib_recv_create_v3_t(ctypes.Structure):
    _fields_ = [
        ("source_to_connect_to", NDIlib_source_t),
        ("color_format", c_int),
        ("bandwidth", c_int),
        ("allow_video_fields", c_bool),
        ("p_ndi_recv_name", c_char_p),
    ]


class NDIlib_video_frame_v2_t(ctypes.Structure):
    _fields_ = [
        ("xres", c_int),
        ("yres", c_int),
        ("FourCC", c_int),
        ("frame_rate_N", c_int),
        ("frame_rate_D", c_int),
        ("picture_aspect_ratio", c_float),
        ("frame_format_type", c_int),
        ("timecode", c_int64),
        ("p_data", POINTER(c_uint8)),
        ("line_stride_in_bytes", c_int),
        ("p_metadata", c_char_p),
        ("timestamp", c_int64),
    ]


@dataclass(frozen=True)
class NdiSource:
    name: str
    url: str


@dataclass
class NdiRecordResult:
    source_name: str
    source_url: str
    frame_count: int
    width: int
    height: int
    frame_rate_hz: float
    duration_s: float
    fourcc: str


def _ndi_dll_candidates() -> list[Path]:
    env_dirs = [
        os.environ.get("NDI_RUNTIME_DIR_V6", ""),
        os.environ.get("NDI_RUNTIME_DIR_V5", ""),
        os.environ.get("NDI_RUNTIME_DIR", ""),
    ]
    hardcoded = [
        Path(r"C:\Program Files\NDI\NDI 6 Runtime\v6"),
        Path(r"C:\Program Files\NDI\NDI 5 Runtime\v5"),
        Path(r"C:\Program Files\NDI\NDI 6 Tools\Runtime"),
    ]
    names = ("Processing.NDI.Lib.x64.dll", "Processing.NDI.Lib.dll")
    out: list[Path] = []
    for folder in [Path(p) for p in env_dirs if p] + hardcoded:
        for name in names:
            out.append(folder / name)
    return out


def load_ndi_library() -> ctypes.WinDLL:
    extra_dirs = [
        Path(r"C:\Program Files\NDI\NDI 6 Tools\HX Driver"),
        Path(r"C:\Program Files\NDI\NDI 6 Runtime\v6"),
        Path(r"C:\Program Files\NDI\NDI 6 Tools\Runtime"),
    ]
    os.environ["PATH"] = (
        os.pathsep.join(str(p) for p in extra_dirs if p.is_dir())
        + os.pathsep
        + os.environ.get("PATH", "")
    )
    last_error: Exception | None = None
    for path in _ndi_dll_candidates():
        if not path.is_file():
            continue
        try:
            return ctypes.WinDLL(str(path))
        except OSError as exc:
            last_error = exc
    searched = ", ".join(str(p) for p in _ndi_dll_candidates())
    detail = f" Last error: {last_error}" if last_error else ""
    raise FileNotFoundError(
        "NDI Runtime DLL not found or could not be loaded. "
        f"Looked in: {searched}.{detail}"
    )


def _bind(lib: ctypes.WinDLL) -> None:
    lib.NDIlib_initialize.restype = c_bool
    lib.NDIlib_initialize.argtypes = []
    lib.NDIlib_destroy.restype = None
    lib.NDIlib_destroy.argtypes = []

    lib.NDIlib_find_create_v2.restype = c_void_p
    lib.NDIlib_find_create_v2.argtypes = [POINTER(NDIlib_find_create_t)]
    lib.NDIlib_find_destroy.restype = None
    lib.NDIlib_find_destroy.argtypes = [c_void_p]
    lib.NDIlib_find_wait_for_sources.restype = c_bool
    lib.NDIlib_find_wait_for_sources.argtypes = [c_void_p, c_uint32]
    lib.NDIlib_find_get_current_sources.restype = POINTER(NDIlib_source_t)
    lib.NDIlib_find_get_current_sources.argtypes = [c_void_p, POINTER(c_uint32)]

    lib.NDIlib_recv_create_v3.restype = c_void_p
    lib.NDIlib_recv_create_v3.argtypes = [POINTER(NDIlib_recv_create_v3_t)]
    lib.NDIlib_recv_destroy.restype = None
    lib.NDIlib_recv_destroy.argtypes = [c_void_p]
    lib.NDIlib_recv_capture_v2.restype = c_int
    lib.NDIlib_recv_capture_v2.argtypes = [
        c_void_p,
        POINTER(NDIlib_video_frame_v2_t),
        c_void_p,
        c_void_p,
        c_uint32,
    ]
    lib.NDIlib_recv_free_video_v2.restype = None
    lib.NDIlib_recv_free_video_v2.argtypes = [c_void_p, POINTER(NDIlib_video_frame_v2_t)]
    lib.NDIlib_recv_recording_start.restype = c_bool
    lib.NDIlib_recv_recording_start.argtypes = [c_void_p, c_char_p]
    lib.NDIlib_recv_recording_stop.restype = c_bool
    lib.NDIlib_recv_recording_stop.argtypes = [c_void_p]
    lib.NDIlib_recv_recording_is_recording.restype = c_bool
    lib.NDIlib_recv_recording_is_recording.argtypes = [c_void_p]
    lib.NDIlib_recv_get_no_connections.restype = c_int
    lib.NDIlib_recv_get_no_connections.argtypes = [c_void_p]


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def find_sources(
    extra_ips: str | None = None,
    timeout_s: float = 5.0,
    lib: ctypes.WinDLL | None = None,
) -> list[NdiSource]:
    lib = lib or load_ndi_library()
    _bind(lib)
    if not lib.NDIlib_initialize():
        raise RuntimeError("NDIlib_initialize failed")

    settings = NDIlib_find_create_t()
    settings.show_local_sources = True
    settings.p_groups = None
    extra = extra_ips.encode("ascii") if extra_ips else None
    settings.p_extra_ips = extra

    finder = lib.NDIlib_find_create_v2(ctypes.byref(settings))
    if not finder:
        if own_lib:
            lib.NDIlib_destroy()
        raise RuntimeError("NDIlib_find_create_v2 failed")

    try:
        deadline = time.monotonic() + timeout_s
        sources: list[NdiSource] = []
        while time.monotonic() < deadline:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            lib.NDIlib_find_wait_for_sources(finder, min(remaining_ms, 1000))
            count = c_uint32(0)
            ptr = lib.NDIlib_find_get_current_sources(finder, ctypes.byref(count))
            sources = []
            for i in range(count.value):
                item = ptr[i]
                sources.append(
                    NdiSource(
                        name=_decode(item.p_ndi_name),
                        url=_decode(item.p_url_address),
                    )
                )
            if sources:
                break
        return sources
    finally:
        lib.NDIlib_find_destroy(finder)


def pick_source(sources: list[NdiSource], ip: str | None = None, name_substr: str | None = None) -> NdiSource:
    if not sources:
        raise RuntimeError("No NDI sources found")
    if ip:
        needle = ip.lower()
        matched = [s for s in sources if needle in s.name.lower() or needle in s.url.lower()]
        if len(matched) == 1:
            return matched[0]
        if len(matched) > 1:
            names = ", ".join(s.name for s in matched)
            raise RuntimeError(f"Multiple NDI sources match IP {ip}: {names}")
    if name_substr:
        needle = name_substr.lower()
        matched = [s for s in sources if needle in s.name.lower()]
        if len(matched) == 1:
            return matched[0]
        if len(matched) > 1:
            names = ", ".join(s.name for s in matched)
            raise RuntimeError(f"Multiple NDI sources match {name_substr!r}: {names}")
    if len(sources) == 1:
        return sources[0]
    names = ", ".join(s.name for s in sources)
    raise RuntimeError(f"Ambiguous NDI source list: {names}")


def _ffmpeg_path() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        raise FileNotFoundError("ffmpeg is not on PATH; needed to encode NDI frames to MP4")
    return found


def _packed_frame(frame: NDIlib_video_frame_v2_t) -> tuple[bytes, str]:
    fourcc = int(frame.FourCC)
    pix_fmt = _PIX_FMT.get(fourcc)
    bpp = _BYTES_PER_PIXEL.get(fourcc)
    if pix_fmt is None or bpp is None:
        raise RuntimeError(f"Unsupported NDI FourCC 0x{fourcc:08x}")
    width = int(frame.xres)
    height = int(frame.yres)
    stride = int(frame.line_stride_in_bytes)
    raw = ctypes.string_at(frame.p_data, stride * height)
    row_bytes = width * bpp
    if stride == row_bytes:
        return raw, pix_fmt
    packed = bytearray(row_bytes * height)
    for y in range(height):
        start = y * stride
        packed[y * row_bytes : (y + 1) * row_bytes] = raw[start : start + row_bytes]
    return bytes(packed), pix_fmt


def _start_ffmpeg(path: Path, width: int, height: int, fps: float, pix_fmt: str) -> subprocess.Popen:
    cmd = [
        _ffmpeg_path(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        pix_fmt,
        "-s",
        f"{width}x{height}",
        "-r",
        f"{fps:.6f}",
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _wait_first_frame(
    lib: ctypes.WinDLL,
    recv: int,
    timeout_s: float,
) -> tuple[NDIlib_video_frame_v2_t, bytes, str, float]:
    frame = NDIlib_video_frame_v2_t()
    deadline = time.monotonic() + timeout_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("No NDI video frame before timeout")
        timeout_ms = min(1000, max(50, int(remaining * 1000)))
        kind = lib.NDIlib_recv_capture_v2(recv, ctypes.byref(frame), None, None, timeout_ms)
        if kind != NDI_FRAME_VIDEO:
            continue
        packed, pix_fmt = _packed_frame(frame)
        den = int(frame.frame_rate_D) or 1
        fps = float(frame.frame_rate_N) / float(den)
        if fps <= 0:
            fps = 30.0
        return frame, packed, pix_fmt, fps


def record_source(
    source: NdiSource,
    output_path: Path,
    duration_s: float,
    first_frame_timeout_s: float = 10.0,
    recv_name: str = "dvorak-marshall",
    bandwidth: int = NDI_RECV_BANDWIDTH_LOWEST,
) -> NdiRecordResult:
    """Receive ``source`` for ``duration_s`` after the first video frame and mux MP4.

    Default bandwidth is the NDI proxy stream (640x360 on this Marshall). Full
    4K BGRA is too large to pipe through ffmpeg in real time.
    """
    lib = load_ndi_library()
    _bind(lib)
    if not lib.NDIlib_initialize():
        raise RuntimeError("NDIlib_initialize failed")

    name_b = source.name.encode("utf-8")
    url_b = source.url.encode("utf-8") if source.url else None
    recv_name_b = recv_name.encode("ascii")
    create = NDIlib_recv_create_v3_t()
    create.source_to_connect_to.p_ndi_name = name_b
    create.source_to_connect_to.p_url_address = url_b
    create.color_format = NDI_RECV_BGRX_BGRA
    create.bandwidth = int(bandwidth)
    create.allow_video_fields = False
    create.p_ndi_recv_name = recv_name_b

    recv = lib.NDIlib_recv_create_v3(ctypes.byref(create))
    if not recv:
        raise RuntimeError(f"NDIlib_recv_create_v3 failed for {source.name!r}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="dvorak_ndi_", suffix=".mp4")
    os.close(fd)
    tmp_path = Path(tmp_name)
    ffmpeg: subprocess.Popen | None = None

    try:
        frame, first_packed, pix_fmt, fps = _wait_first_frame(lib, recv, first_frame_timeout_s)
        width = int(frame.xres)
        height = int(frame.yres)
        lib.NDIlib_recv_free_video_v2(recv, ctypes.byref(frame))
        print(f"NDI first frame {width}x{height} {pix_fmt} @ {fps:.2f} fps", flush=True)

        ffmpeg = _start_ffmpeg(tmp_path, width, height, fps, pix_fmt)
        assert ffmpeg.stdin is not None
        ffmpeg.stdin.write(first_packed)
        frame_count = 1
        started = time.monotonic()
        while True:
            remaining = duration_s - (time.monotonic() - started)
            if remaining <= 0:
                break
            timeout_ms = min(1000, max(20, int(remaining * 1000)))
            kind = lib.NDIlib_recv_capture_v2(recv, ctypes.byref(frame), None, None, timeout_ms)
            if kind != NDI_FRAME_VIDEO:
                continue
            packed, _ = _packed_frame(frame)
            ffmpeg.stdin.write(packed)
            frame_count += 1
            lib.NDIlib_recv_free_video_v2(recv, ctypes.byref(frame))

        ffmpeg.stdin.close()
        stderr = ffmpeg.communicate(timeout=30)[1]
        if ffmpeg.returncode not in (0, None):
            detail = stderr.decode("utf-8", errors="replace") if stderr else ""
            raise RuntimeError(f"ffmpeg failed ({ffmpeg.returncode}): {detail}")
        if not tmp_path.is_file() or tmp_path.stat().st_size < 1000:
            raise RuntimeError(f"ffmpeg produced no usable file at {tmp_path}")
        shutil.copy2(tmp_path, output_path)
        return NdiRecordResult(
            source_name=source.name,
            source_url=source.url,
            frame_count=frame_count,
            width=width,
            height=height,
            frame_rate_hz=fps,
            duration_s=time.monotonic() - started,
            fourcc=pix_fmt,
        )
    except Exception:
        if ffmpeg is not None:
            try:
                if ffmpeg.stdin:
                    ffmpeg.stdin.close()
            except Exception:
                pass
            ffmpeg.kill()
            ffmpeg.wait(timeout=5)
        raise
    finally:
        lib.NDIlib_recv_destroy(recv)
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

"""Qué codificadores por GPU funcionan de verdad en este equipo. Sin Qt.

`ffmpeg -encoders` lista lo que está compilado, no lo que funciona: h264_nvenc aparece aunque
no haya gráfica NVIDIA, y av1_nvenc aparece en una RTX 3000 que no codifica AV1. Por eso se
hace una codificación de prueba de medio segundo y se guarda el resultado en caché.
"""

import json
import os
import subprocess
from pathlib import Path

from .command import Hardware

NVENC = {"h264": "h264_nvenc", "h265": "hevc_nvenc"}
VAAPI = {"h264": "h264_vaapi", "h265": "hevc_vaapi"}


def ffmpeg_version():
    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10)
        return out.stdout.splitlines()[0] if out.stdout else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _works(args):
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", *args, "-f", "null", "-"],
                           capture_output=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _render_device():
    for d in sorted(Path("/dev/dri").glob("renderD*")) if Path("/dev/dri").exists() else []:
        if os.access(d, os.R_OK | os.W_OK):
            yield str(d)


def detect():
    src = ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=0.4"]
    nvenc = {c for c, enc in NVENC.items() if _works([*src, "-c:v", enc])}
    vaapi, device = set(), "/dev/dri/renderD128"
    for dev in _render_device():
        found = {c for c, enc in VAAPI.items()
                 if _works(["-vaapi_device", dev, *src, "-vf", "format=nv12,hwupload", "-c:v", enc])}
        if found:
            vaapi, device = found, dev
            break
    return Hardware(nvenc=frozenset(nvenc), vaapi=frozenset(vaapi), vaapi_device=device)


def detect_cached(cache_file):
    """Repite la prueba solo si cambia la versión de ffmpeg o el kernel (drivers)."""
    key = f"{ffmpeg_version()} | {os.uname().release}"
    cache_file = Path(cache_file)
    try:
        data = json.loads(cache_file.read_text())
        if data.get("key") == key:
            return Hardware(frozenset(data["nvenc"]), frozenset(data["vaapi"]), data["device"])
    except (OSError, ValueError, KeyError):
        pass
    hw = detect()
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"key": key, "nvenc": sorted(hw.nvenc),
                                          "vaapi": sorted(hw.vaapi), "device": hw.vaapi_device}))
    except OSError:
        pass
    return hw

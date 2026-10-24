"""Lo que hace falta saber de un fichero para convertirlo, leído con ffprobe. Sin Qt."""

import json
import subprocess
from dataclasses import dataclass, field

from ..i18n import tr


@dataclass(frozen=True)
class VideoInfo:
    codec: str
    width: int              # ya girado: lo que se ve en pantalla
    height: int
    fps: float
    pix_fmt: str
    bit_depth: int
    interlaced: bool


@dataclass(frozen=True)
class AudioInfo:
    codec: str
    channels: int
    bitrate: int            # bits/s, 0 si no se sabe
    language: str


@dataclass(frozen=True)
class MediaInfo:
    path: str
    duration: float         # segundos, 0 si no se sabe
    size: int               # bytes
    container: str
    video: VideoInfo | None
    audio: tuple = field(default_factory=tuple)        # AudioInfo...
    subtitles: tuple = field(default_factory=tuple)    # códecs de subtítulos

    def summary(self):
        """'1920×1080 · 25 fps · H.264 · AAC estéreo · 1:23' para la lista."""
        parts = []
        if self.video:
            v = self.video
            parts.append(f"{v.width}×{v.height}")
            if v.fps:
                parts.append(f"{v.fps:.3g} fps")
            parts.append(codec_name(v.codec))
        for a in self.audio[:1]:
            ch = {1: tr("mono"), 2: tr("estéreo"), 6: "5.1", 8: "7.1"}.get(
                a.channels, tr("{n} canales", n=a.channels))
            parts.append(f"{codec_name(a.codec)} {ch}")
        if len(self.audio) > 1:
            parts.append(tr("{n} pistas de audio", n=len(self.audio)))
        if self.subtitles:
            parts.append(tr("1 subtítulo") if len(self.subtitles) == 1 else
                         tr("{n} subtítulos", n=len(self.subtitles)))
        if self.duration:
            parts.append(format_duration(self.duration))
        return " · ".join(parts)


class ProbeError(Exception):
    pass


# nombres de ffmpeg -> los que conoce la gente
CODEC_NAMES = {
    "h264": "H.264", "hevc": "H.265", "av1": "AV1", "vp9": "VP9", "vp8": "VP8",
    "mpeg2video": "MPEG-2", "mpeg1video": "MPEG-1", "mpeg4": "MPEG-4", "msmpeg4v3": "DivX",
    "prores": "ProRes", "dnxhd": "DNxHR", "ffv1": "FFV1", "mjpeg": "MJPEG", "png": "PNG",
    "wmv2": "WMV", "wmv3": "WMV", "vc1": "VC-1", "theora": "Theora", "huffyuv": "HuffYUV",
    "aac": "AAC", "mp3": "MP3", "opus": "Opus", "vorbis": "Vorbis", "flac": "FLAC",
    "ac3": "AC-3", "eac3": "E-AC-3", "dts": "DTS", "truehd": "TrueHD", "mp2": "MP2",
    "alac": "ALAC", "wmav2": "WMA",
}


def codec_name(codec):
    if codec.startswith("pcm_"):
        return "PCM"
    return CODEC_NAMES.get(codec, codec.upper())


def format_duration(seconds):
    s = int(round(seconds))
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _rate(text):
    try:
        num, _, den = str(text).partition("/")
        num, den = float(num), float(den or 1)
        return num / den if den else 0.0
    except ValueError:
        return 0.0


def _bit_depth(stream):
    raw = stream.get("bits_per_raw_sample")
    if str(raw).isdigit() and int(raw) > 0:
        return int(raw)
    pix = stream.get("pix_fmt", "")
    for depth in (16, 14, 12, 10):
        if f"p{depth}" in pix or f"{depth}le" in pix or f"{depth}be" in pix:
            return depth
    return 8


def _rotation(stream):
    for side in stream.get("side_data_list", []):
        if "rotation" in side:
            return int(round(float(side["rotation"]))) % 360
    return int(stream.get("tags", {}).get("rotate", 0) or 0) % 360


def parse(data, path=""):
    """De la salida JSON de ffprobe a MediaInfo (separado para poder probarlo sin ficheros)."""
    fmt = data.get("format", {})
    video = None
    audio, subs = [], []
    for s in data.get("streams", []):
        kind = s.get("codec_type")
        if kind == "video" and video is None and not s.get("disposition", {}).get("attached_pic"):
            w, h = int(s.get("width") or 0), int(s.get("height") or 0)
            if w and h:
                if _rotation(s) in (90, 270):
                    w, h = h, w
                fps = _rate(s.get("avg_frame_rate")) or _rate(s.get("r_frame_rate"))
                video = VideoInfo(
                    codec=s.get("codec_name", "?"), width=w, height=h, fps=round(fps, 3),
                    pix_fmt=s.get("pix_fmt", ""), bit_depth=_bit_depth(s),
                    interlaced=s.get("field_order", "progressive") in ("tt", "bb", "tb", "bt"))
        elif kind == "audio":
            audio.append(AudioInfo(
                codec=s.get("codec_name", "?"), channels=int(s.get("channels") or 2),
                bitrate=int(s.get("bit_rate") or 0),
                language=s.get("tags", {}).get("language", "")))
        elif kind == "subtitle":
            subs.append(s.get("codec_name", "?"))
    try:
        duration = float(fmt.get("duration") or 0)
    except ValueError:
        duration = 0.0
    return MediaInfo(path=path, duration=duration, size=int(fmt.get("size") or 0),
                     container=fmt.get("format_name", ""), video=video,
                     audio=tuple(audio), subtitles=tuple(subs))


def probe(path, timeout=30):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
             str(path)], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise ProbeError(tr("No se encuentra ffprobe: instala ffmpeg.")) from e
    except subprocess.TimeoutExpired as e:
        raise ProbeError(tr("ffprobe tardó demasiado en leer el fichero.")) from e
    if out.returncode != 0:
        msg = out.stderr.strip().splitlines()
        raise ProbeError(msg[-1] if msg else tr("No es un fichero de vídeo o audio que ffmpeg sepa leer."))
    info = parse(json.loads(out.stdout or "{}"), str(path))
    if info.video is None and not info.audio:
        raise ProbeError(tr("No tiene ni vídeo ni audio."))
    return info

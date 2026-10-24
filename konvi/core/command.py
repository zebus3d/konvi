"""De (MediaInfo, Preset) a los argumentos de ffmpeg. Función pura: sin Qt y sin procesos.

Reglas tomadas de HandBrake:
- calidad constante (CRF) salvo que se pida un tamaño final;
- la imagen se reduce si pasa del límite, nunca se agranda, y se mantiene la proporción;
- desentrelazado automático si el original es entrelazado;
- el audio se copia tal cual si ya está en el formato pedido (y no hay que bajarlo);
- se conservan capítulos y metadatos.
"""

from dataclasses import dataclass

from ..i18n import tr

SPEED = {
    "x26x": {"fast": "fast", "medium": "medium", "slow": "slow"},
    "svtav1": {"fast": "8", "medium": "6", "slow": "4"},
    "vp9": {"fast": "4", "medium": "2", "slow": "1"},
    "nvenc": {"fast": "p3", "medium": "p5", "slow": "p7"},
}
AUDIO_ENCODER = {"aac": "aac", "opus": "libopus", "mp3": "libmp3lame", "flac": "flac",
                 "pcm": "pcm_s24le", "ac3": "ac3", "mp2": "mp2"}
SURROUND_COPY = {"ac3", "eac3"}       # envolvente que el MP4 y las teles aceptan tal cual

# Afinado de HandBrake (preset_builtin.json): conserva más detalle a igual tamaño
X265_TUNE = "strong-intra-smoothing=0:rect=0:aq-mode=1:rd=4:psy-rd=0.75:psy-rdoq=4.0:rdoq-level=1:rskip=2"
SVTAV1_TUNE = ("enable-variance-boost=1:enable-qm=1:ac-bias=1:tf-strength=1:"
               "qp-scale-compress-strength=1:sharpness=1")
# La escala de calidad de NVENC no es la del CRF. Medido con VMAF (2026-10-24, RTX 3070, un
# render 3D y vídeo grabado, 20 s cada uno): NVENC iguala a x264 CRF 20 con CQ ≈ 23, y a
# x265 CRF 22 con CQ ≈ 25. El +5 de HandBrake (CQ 27) perdía 0,5-1,7 puntos de VMAF.
NVENC_CQ_OFFSET = {"h264": 3, "h265": 3}

# Perfiles de tamaño final: la resolución sale del bitrate que queda (como los «Social» de
# HandBrake: 25 MB en 30 s → 1080p, 1 min → 720p, 2 min → 540p, 5 min → 360p)
AUTO_HEIGHT = ((5000, 1080), (2200, 720), (1100, 540), (400, 360))
LOSSY_AUDIO = {"aac", "opus", "mp3", "ac3", "mp2"}
FASTSTART = {"mp4", "mov", "m4a"}
# códecs con submuestreo de color: exigen ancho (y alto) pares
NEEDS_EVEN = {"h264", "h265", "av1", "vp9", "mpeg2", "dnxhr", "prores"}
MPEG2_RATES = (24000 / 1001, 24, 25, 30000 / 1001, 30, 50, 60000 / 1001, 60)
TEXT_SUBS_TO_SRT = {"mov_text"}       # MKV no admite mov_text: se pasa a SRT


class ConvertError(Exception):
    """El fichero no se puede convertir con ese perfil (el mensaje es para el usuario)."""


@dataclass(frozen=True)
class Hardware:
    """Codificadores por GPU que funcionan en este equipo (los comprueba encoders.py)."""
    nvenc: frozenset = frozenset()      # {"h264", "h265"}
    vaapi: frozenset = frozenset()
    vaapi_device: str = "/dev/dri/renderD128"

    def encoder_for(self, codec):
        if codec in self.nvenc:
            return "nvenc"
        if codec in self.vaapi:
            return "vaapi"
        return None

    @property
    def any(self):
        return bool(self.nvenc or self.vaapi)


NO_HARDWARE = Hardware()


def check(info, preset):
    """Motivo por el que no se puede convertir, o None si se puede."""
    if not preset.is_audio_only and info.video is None:
        return tr("Este fichero no tiene imagen: elige un perfil de «Solo audio».")
    if preset.is_audio_only and not info.audio:
        return tr("Este fichero no tiene sonido.")
    if preset.target_mb:
        if not info.duration:
            return tr("No se sabe cuánto dura, así que no se puede ajustar a un tamaño.")
        if target_video_kbps(info, preset) < 100:
            return tr("Es demasiado largo para caber en {mb} MB con una calidad mínima. "
                      "Elige otro perfil.", mb=f"{preset.target_mb:g}")
    return None


def target_video_kbps(info, preset):
    """Bitrate de vídeo para que todo quepa en preset.target_mb.

    Con un tope de bitrate (VBV) el fichero puede pasarse como mucho lo que cabe en el búfer,
    que aquí es de 1 s: por eso se reparte el presupuesto entre la duración + 1 s. Un 3 % más
    de margen para el contenedor."""
    total_kbit = preset.target_mb * 1024 * 8 * 0.97
    audio = preset.audio_kbps if preset.audio and info.audio else 0
    return int(total_kbit / (max(info.duration, 0.1) + 1) - audio)


def auto_height(kbps):
    """Resolución que da para ese bitrate de vídeo en un perfil de tamaño final."""
    return next((h for minimum, h in AUTO_HEIGHT if kbps >= minimum), 240)


def output_fps(info, preset):
    fps = info.video.fps if info.video else 0
    if preset.max_fps and fps > preset.max_fps + 0.01:
        return preset.max_fps
    return fps


def _scale_filter(v, limit):
    """Reduce para que el lado corto no pase de `limit` (1080p también en vertical)."""
    if not limit or min(v.width, v.height) <= limit:
        return None
    if v.width >= v.height:
        return f"scale=-2:{limit}:flags=lanczos"
    return f"scale={limit}:-2:flags=lanczos"


RGB_PIX = ("rgb", "bgr", "gbr", "argb", "abgr", "rgba", "bgra", "pal8", "gray")


def _is_rgb(v):
    return v.pix_fmt.startswith(RGB_PIX)


def _pix_fmt(codec, v, hw):
    # H.265 y AV1 siempre en 10 bits, como HandBrake: comprime mejor y sin escalones en los
    # degradados aunque el original sea de 8. Todo lo que reproduce HEVC o AV1 lo admite.
    ten_bit = codec in ("h265", "av1")
    if hw == "nvenc":
        return "p010le" if ten_bit else "yuv420p"
    if codec in ("h264", "vp9", "mpeg2"):
        return "yuv420p"
    if codec in ("h265", "av1"):
        return "yuv420p10le" if ten_bit else "yuv420p"
    if codec == "prores":
        return "yuv422p10le"
    if codec == "dnxhr":
        return "yuv422p"
    return None                       # ffv1: el del original


def _video_args(info, preset, hw_kind, hardware):
    v = info.video
    codec = preset.video
    vf, args = [], []

    q = preset.effective_quality()
    if preset.target_mb:
        # Calidad constante con tope de bitrate: siempre cabe en el tamaño, y un vídeo corto
        # no se infla hasta llenarlo. El tope respeta el máximo de H.264 High@4.1.
        kbps = target_video_kbps(info, preset)
        if preset.compat:
            kbps = min(kbps, 50000)
        cap = ["-maxrate", f"{kbps}k", "-bufsize", f"{kbps}k"]
        limit = min(filter(None, (preset.max_height, auto_height(kbps))))
    else:
        kbps, cap, limit = 0, [], preset.max_height

    if v.interlaced:
        vf.append("bwdif=mode=send_frame")
    if preset.profile != "dvd":
        scale = _scale_filter(v, limit)
        if scale:
            vf.append(scale)
        elif codec in NEEDS_EVEN and (v.width % 2 or v.height % 2) and \
                not (preset.lossless and _is_rgb(v)):
            vf.append("crop=trunc(iw/2)*2:trunc(ih/2)*2")
    fps = output_fps(info, preset)
    if fps and fps != v.fps:
        vf.append(f"fps={fps:g}")
    if codec == "mpeg2" and preset.profile != "dvd" and fps and \
            min(abs(fps - r) for r in MPEG2_RATES) > 0.01:
        # MPEG-2 solo admite las cadencias de la tele
        fps = min(MPEG2_RATES, key=lambda r: abs(r - fps))
        vf.append(f"fps={fps:.6g}")

    pix = _pix_fmt(codec, v, hw_kind)
    if hw_kind == "vaapi":
        # VAAPI: 10 bits solo si el original los tiene (no todas las gráficas hacen HEVC Main10)
        ten = codec == "h265" and v.bit_depth > 8
        vf.append(f"format={'p010' if ten else 'nv12'},hwupload")
        pix = None

    if codec == "h264" and preset.lossless:
        # RGB (PNG, capturas) en RGB; vídeo normal en su propio formato de color: así no se
        # pierde nada y no se infla convirtiendo a RGB lo que ya era YUV
        rgb = _is_rgb(v)
        args += ["-c:v", "libx264rgb" if rgb else "libx264", "-preset", SPEED["x26x"][preset.speed],
                 "-qp", "0"]
        pix = None if rgb or v.pix_fmt.startswith("yuv") else "yuv420p"
    elif codec in ("h264", "h265") and hw_kind == "nvenc":
        args += ["-c:v", "h264_nvenc" if codec == "h264" else "hevc_nvenc",
                 "-preset", SPEED["nvenc"][preset.speed], "-tune", "hq", "-rc", "vbr",
                 "-rc-lookahead", "10"]
        args += ["-cq", str(q + NVENC_CQ_OFFSET[codec]), "-b:v", "0", *cap]
    elif codec in ("h264", "h265") and hw_kind == "vaapi":
        args += ["-c:v", "h264_vaapi" if codec == "h264" else "hevc_vaapi"]
        args += ["-rc_mode", "VBR", "-b:v", f"{kbps}k", *cap] if cap else ["-qp", str(q)]
    elif codec == "h264":
        args += ["-c:v", "libx264", "-preset", SPEED["x26x"][preset.speed], "-crf", str(q), *cap]
    elif codec == "h265":
        args += ["-c:v", "libx265", "-preset", SPEED["x26x"][preset.speed],
                 "-x265-params", f"log-level=error:{X265_TUNE}", "-crf", str(q), *cap]
    elif codec == "av1":
        args += ["-c:v", "libsvtav1", "-preset", SPEED["svtav1"][preset.speed],
                 "-svtav1-params", SVTAV1_TUNE, "-crf", str(q), *cap]
    elif codec == "vp9":
        args += ["-c:v", "libvpx-vp9", "-row-mt", "1", "-deadline", "good",
                 "-cpu-used", SPEED["vp9"][preset.speed]]
        args += ["-crf", str(q), "-b:v", f"{kbps}k" if cap else "0"]   # con tope: calidad restringida
    elif codec == "prores":
        args += ["-c:v", "prores_ks", "-profile:v", "0" if preset.profile == "proxy" else "3",
                 "-vendor", "apl0"]
    elif codec == "dnxhr":
        args += ["-c:v", "dnxhd", "-profile:v", f"dnxhr_{preset.profile or 'hq'}"]
    elif codec == "ffv1":
        args += ["-c:v", "ffv1", "-level", "3", "-g", "1", "-slicecrc", "1"]
    elif codec == "mpeg2":
        if preset.profile == "dvd":
            aspect = "16:9" if v.width / v.height > 1.5 else "4:3"
            args += ["-target", "pal-dvd", "-aspect", aspect]
        else:
            args += ["-c:v", "mpeg2video", "-q:v", str(q)]
    else:
        raise ConvertError(tr("Códec de vídeo desconocido: {codec}", codec=codec))

    if pix:
        args += ["-pix_fmt", pix]
    if preset.compat and codec == "h264" and not preset.lossless:
        args += ["-profile:v", "high", "-level:v", "4.1"]
    if preset.gop_seconds and fps:
        args += ["-g", str(max(1, round(fps * preset.gop_seconds))), "-bf", "2"]
    if codec == "h265" and preset.container in ("mp4", "mov"):
        args += ["-tag:v", "hvc1"]       # para que lo abran QuickTime y los Apple
    if vf:
        args = ["-vf", ",".join(vf)] + args
    return args


def _audio_args(info, preset):
    if not preset.audio or not info.audio:
        return ["-an"]
    tracks = list(enumerate(info.audio)) if preset.all_audio else [(0, info.audio[0])]
    if preset.audio == "copy":
        # tal cual, sin recodificar: no se pierde nada (MKV admite cualquier audio)
        args = []
        for out_i, (src_i, _) in enumerate(tracks):
            args += ["-map", f"0:a:{src_i}", f"-c:a:{out_i}", "copy"]
        return args
    first = info.audio[0]
    if preset.surround and first.channels > 2 and not preset.all_audio:
        # Como los perfiles «Surround» de HandBrake: estéreo AAC para que suene en todo,
        # y además el envolvente original (o en AC-3 si no se puede copiar)
        surround = ["copy"] if first.codec in SURROUND_COPY else ["ac3", "-b:a:1", "640k"]
        return ["-map", "0:a:0", "-c:a:0", "aac", "-b:a:0", f"{preset.audio_kbps}k", "-ac:a:0", "2",
                "-map", "0:a:0", "-c:a:1", *surround]
    args = []
    for out_i, (src_i, a) in enumerate(tracks):
        args += ["-map", f"0:a:{src_i}"]
        must_downmix = preset.stereo and a.channels > 2
        same = a.codec == preset.audio or (preset.audio == "pcm" and a.codec in ("pcm_s16le", "pcm_s24le"))
        small_enough = (preset.audio not in LOSSY_AUDIO or a.bitrate == 0 or
                        a.bitrate <= preset.audio_kbps * 1000 * 1.1)
        if same and small_enough and not must_downmix and not preset.target_mb:
            args += [f"-c:a:{out_i}", "copy"]
            continue
        args += [f"-c:a:{out_i}", AUDIO_ENCODER[preset.audio]]
        if preset.audio in LOSSY_AUDIO:
            kbps = min(preset.audio_kbps, 320) if preset.audio == "mp3" else preset.audio_kbps
            args += [f"-b:a:{out_i}", f"{kbps}k"]
        if must_downmix:
            args += [f"-ac:a:{out_i}", "2"]
        elif preset.audio == "opus" and a.channels > 2:
            # libopus no admite las variantes "(side)" de 5.1/7.1
            layout = {6: "5.1", 8: "7.1"}.get(a.channels)
            if layout:
                args += [f"-filter:a:{out_i}", f"aformat=channel_layouts={layout}"]
            else:
                args += [f"-ac:a:{out_i}", "2"]
    return args


def _subtitle_args(info, preset):
    if not (preset.subtitles and info.subtitles and preset.container == "mkv"):
        return ["-sn"]
    args = ["-map", "0:s?"]
    for i, codec in enumerate(info.subtitles):
        args += [f"-c:s:{i}", "srt" if codec in TEXT_SUBS_TO_SRT else "copy"]
    return args


def build(info, preset, output, hardware=NO_HARDWARE, use_hardware=False):
    """Lista de argumentos para subprocess/QProcess. El progreso sale por stdout (-progress)."""
    problem = check(info, preset)
    if problem:
        raise ConvertError(problem)

    hw_kind = None
    if use_hardware and not preset.is_audio_only and preset.profile != "dvd" and not preset.lossless:
        hw_kind = hardware.encoder_for(preset.video)

    args = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-progress", "pipe:1", "-nostats"]
    if hw_kind == "vaapi":
        args += ["-vaapi_device", hardware.vaapi_device]
    args += ["-i", str(info.path)]

    if preset.is_audio_only:
        args += ["-vn"]
    else:
        args += ["-map", "0:V:0"]        # V mayúscula: vídeo que no sea una carátula
        args += _video_args(info, preset, hw_kind, hardware)
    args += _audio_args(info, preset)
    args += _subtitle_args(info, preset)
    args += ["-dn", "-map_metadata", "0", "-map_chapters", "0"]
    if preset.container in FASTSTART:
        args += ["-movflags", "+faststart"]
    args.append(str(output))
    return args


def uses_hardware(args):
    return any(a.endswith(("_nvenc", "_vaapi")) for a in args)

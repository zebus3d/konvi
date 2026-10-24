"""Perfiles de conversión: los de fábrica, agrupados por destino, y los del usuario.

Un perfil dice *qué* se quiere (contenedor, códec, calidad, límites); cómo se traduce a
argumentos de ffmpeg es cosa de command.py. Sin Qt.
"""

import json
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

# Códecs de vídeo que entiende command.py
VIDEO_CODECS = ("h264", "h265", "av1", "vp9", "prores", "dnxhr", "ffv1", "mpeg2")
# Códecs de imagen: el resultado es una carpeta con un fichero por fotograma
IMAGE_CODECS = ("png", "jpeg", "tiff")
AUDIO_CODECS = ("aac", "opus", "mp3", "flac", "pcm", "ac3", "mp2", "copy")

# Calidad de "Máxima" a "Baja" para cada códec que trabaja con calidad constante
# (CRF en x264/x265/SVT-AV1/VP9, cuantizador en MPEG-2). La del medio es la de fábrica.
QUALITY_STEPS = {
    "h264": (16, 18, 20, 23, 26),
    "h265": (18, 20, 22, 25, 28),
    "av1": (22, 26, 30, 35, 40),
    "vp9": (24, 28, 32, 36, 40),
    "mpeg2": (2, 3, 4, 6, 8),
    "jpeg": (1, 2, 3, 5, 8),
}
QUALITY_NAMES = ("Máxima", "Muy alta", "Alta", "Media", "Baja")


@dataclass(frozen=True)
class Preset:
    id: str
    category: str
    name: str
    description: str
    container: str                      # extensión de salida: mp4, mkv, mov, mp3...
    video: str | None = "h264"          # None = solo audio
    quality: int | None = None          # None = la "Alta" del códec
    speed: str = "medium"               # fast | medium | slow
    max_height: int | None = None       # reducir si el original es más alto (nunca agrandar)
    max_fps: float | None = None        # bajar los fps si el original tiene más
    target_mb: float | None = None      # tamaño final en vez de calidad constante
    profile: str = ""                   # hq/lb (DNxHR), hq/proxy (ProRes), dvd (MPEG-2)
    compat: bool = False                # H.264 High@4.1: lo abre cualquier móvil o tele
    gop_seconds: float | None = None    # fotograma clave cada N s (lo pide YouTube)
    audio: str | None = "aac"           # None = sin audio; "copy" = tal cual, sin recodificar
    audio_kbps: int = 192
    stereo: bool = False                # mezclar a 2 canales
    all_audio: bool = False             # todas las pistas de audio, no solo la primera
    surround: bool = False              # estéreo AAC + el envolvente original (como HandBrake)
    subtitles: bool = False             # copiar los subtítulos (solo MKV)
    lossless: bool = False              # H.264 sin pérdida (-qp 0): idéntico al original
    custom: bool = False

    @property
    def is_audio_only(self):
        return self.video is None

    @property
    def is_image_sequence(self):
        return self.video in IMAGE_CODECS

    def effective_quality(self):
        if self.lossless:
            return 0
        if self.quality is not None:
            return self.quality
        steps = QUALITY_STEPS.get(self.video)
        return steps[2] if steps else None


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    icon: str          # konvi/icons/<icon>.svg
    tooltip: str


CATEGORIES = (  # por orden de uso: lo de todos los días primero
    Category("mp4", "MP4", "mp4",
             "MP4: el formato que abre todo\n(móviles, teles, navegadores, Windows, Mac)."),
    Category("web", "Web y redes", "web",
             "YouTube, Vimeo, WhatsApp, Discord, correo..."),
    Category("device", "Móvil y TV", "device",
             "Para verlo en el móvil, la tablet o la tele."),
    Category("audio", "Solo audio", "audio",
             "Saca solo el sonido: MP3, M4A, Opus, FLAC o WAV."),
    Category("edit", "Edición", "edit",
             "Para editar en DaVinci Resolve, Premiere o Final Cut:\n"
             "códecs intermedios (DNxHR, ProRes) que van fluidos en la línea de tiempo.\n"
             "Ficheros muy grandes."),
    Category("images", "Imágenes", "images",
             "Saca los fotogramas del vídeo como imágenes (PNG, JPEG o TIFF),\n"
             "cada vídeo en su propia carpeta."),
    Category("mkv", "MKV", "mkv",
             "MKV (Matroska): guarda todas las pistas de audio y los subtítulos.\n"
             "Ideal para películas y series."),
    Category("webm", "WebM", "webm",
             "WebM: vídeo para páginas web (VP9 o AV1, audio Opus)."),
    Category("mpeg", "MPEG / DVD", "mpeg",
             "MPEG-2: reproductores antiguos y DVD."),
    Category("custom", "Mis perfiles", "custom",
             "Los perfiles que has guardado desde «Personalizar…»."),
)


def _p(*args, **kw):
    return Preset(*args, **kw)


BUILTIN = (
    # --- MP4
    _p("mp4_h264", "mp4", "H.264 · misma resolución",
       "El más compatible. Mantiene la resolución y la calidad del original.", "mp4"),
    _p("mp4_h264_1080", "mp4", "H.264 · 1080p",
       "Full HD. Si el original es más grande, se reduce; nunca se agranda.", "mp4", max_height=1080),
    _p("mp4_h264_720", "mp4", "H.264 · 720p",
       "HD: buen equilibrio entre calidad y tamaño.", "mp4", max_height=720),
    _p("mp4_h264_480", "mp4", "H.264 · 480p (pequeño)",
       "Calidad de DVD, ficheros pequeños.", "mp4", max_height=480, audio_kbps=128),
    _p("mp4_h265", "mp4", "H.265 (HEVC) · misma resolución",
       "Más o menos la mitad de tamaño que H.264 con la misma calidad.\n"
       "Lo abren los aparatos de los últimos años.", "mp4", video="h265"),
    _p("mp4_av1", "mp4", "AV1 · misma resolución",
       "El que menos ocupa. Los aparatos de menos de unos 4 años lo abren;\n"
       "los más viejos, no.", "mp4", video="av1"),
    _p("mp4_lossless", "mp4", "H.264 sin pérdida",
       "Copia exacta, píxel a píxel. Ocupa mucho. Lo abren VLC, mpv y DaVinci,\n"
       "pero no todos los reproductores (es H.264 High 4:4:4).", "mp4",
       lossless=True, speed="slow", audio="flac"),
    # --- MKV
    _p("mkv_h264", "mkv", "H.264 · todas las pistas",
       "Conserva todos los idiomas y subtítulos; el sonido se copia tal cual.", "mkv",
       audio="copy", all_audio=True, subtitles=True),
    _p("mkv_h265", "mkv", "H.265 · todas las pistas",
       "Para guardar películas y series: la mitad de tamaño que H.264,\n"
       "con todos los idiomas y subtítulos.", "mkv", video="h265", audio="copy",
       all_audio=True, subtitles=True),
    _p("mkv_av1", "mkv", "AV1 · todas las pistas",
       "Lo que menos ocupa, con todos los idiomas y subtítulos.", "mkv",
       video="av1", audio="copy", all_audio=True, subtitles=True),
    _p("mkv_ffv1", "mkv", "Sin pérdida (FFV1)",
       "Copia exacta, píxel a píxel, para archivar. Ocupa muchísimo.", "mkv",
       video="ffv1", audio="copy", all_audio=True, subtitles=True),
    # --- WebM
    _p("webm_vp9", "webm", "VP9",
       "El vídeo de la web: lo abren todos los navegadores.", "webm",
       video="vp9", audio="opus", audio_kbps=128),
    _p("webm_av1", "webm", "AV1",
       "Más pequeño que VP9; navegadores actuales.", "webm",
       video="av1", audio="opus", audio_kbps=128),
    # --- Edición
    _p("edit_dnxhr_hq", "edit", "DaVinci Resolve · DNxHR HQ",
       "Calidad de edición. DaVinci gratuito en Linux no abre H.264/H.265 ni AAC:\n"
       "esto sí. Ficheros muy grandes.", "mov", video="dnxhr", profile="hq", audio="pcm"),
    _p("edit_dnxhr_lb", "edit", "DaVinci Resolve · DNxHR LB (proxy)",
       "Calidad de proxy: ligero para editar en equipos modestos.", "mov",
       video="dnxhr", profile="lb", audio="pcm"),
    _p("edit_prores_hq", "edit", "ProRes 422 HQ",
       "El intermedio de Apple (Final Cut, Premiere, DaVinci).", "mov",
       video="prores", profile="hq", audio="pcm"),
    _p("edit_prores_proxy", "edit", "ProRes Proxy",
       "ProRes ligero para editar con proxies.", "mov", video="prores", profile="proxy", audio="pcm"),
    # --- Imágenes
    _p("img_png", "images", "PNG · todos los fotogramas",
       "Cada fotograma como una imagen PNG, sin pérdida, en una carpeta con el nombre\n"
       "del vídeo. Para Blender, para retocar fotogramas o para un juego.", "png",
       video="png", audio=None),
    _p("img_jpeg", "images", "JPEG · todos los fotogramas",
       "Cada fotograma como una imagen JPEG: ocupa mucho menos que PNG.", "jpg",
       video="jpeg", audio=None),
    _p("img_tiff", "images", "TIFF · todos los fotogramas",
       "Sin pérdida y en 16 bits si el original tiene más de 8: para GIMP, Krita,\n"
       "Photoshop o composición.", "tif", video="tiff", audio=None),
    _p("img_jpeg_1s", "images", "JPEG · una imagen por segundo",
       "Una captura de cada segundo del vídeo: para elegir la mejor o ver de un\n"
       "vistazo lo que tiene.", "jpg", video="jpeg", audio=None, max_fps=1.0),
    # --- MPEG
    _p("mpeg_mpeg2", "mpeg", "MPEG-2 · misma resolución",
       "Para reproductores y programas antiguos.", "mpg", video="mpeg2", audio="mp2", audio_kbps=224),
    _p("mpeg_dvd", "mpeg", "DVD (PAL, 720×576)",
       "MPEG-2 listo para grabar en un DVD (PAL, 25 fps).", "mpg",
       video="mpeg2", profile="dvd", audio="ac3", audio_kbps=224),
    # --- Web y redes
    _p("web_youtube", "web", "YouTube / Vimeo",
       "Lo que recomiendan YouTube y Vimeo: H.264 High, fotograma clave cada\n"
       "medio segundo y audio AAC a 320 kbps.", "mp4", quality=18, gop_seconds=0.5, audio_kbps=320),
    _p("web_whatsapp", "web", "WhatsApp / Telegram",
       "720p y compresión alta: se envía rápido y se ve bien en el móvil.", "mp4",
       quality=26, max_height=720, max_fps=30, compat=True, audio_kbps=128, stereo=True),
    # Discord gratis: 20 MB por fichero desde el 14-08-2026 (antes 10). Se apunta a 18,5 MiB
    # (19,4 MB) por si Discord cuenta en MB y no en MiB.
    _p("web_discord", "web", "Discord (menos de 20 MB)",
       "Para compartirlo en Discord sin pagar: cabe en los 20 MB de la cuenta gratuita\n"
       "y se ve dentro del chat. La resolución se elige sola según lo que dure.", "mp4",
       max_fps=30, target_mb=18.5, compat=True, audio_kbps=96, stereo=True),
    _p("web_email", "web", "Correo (menos de 25 MB)",
       "Ajusta la calidad para que el vídeo quepa como adjunto de 25 MB. La resolución\n"
       "se elige sola según lo que dure: cuanto más largo, más pequeña.", "mp4",
       max_fps=30, target_mb=24, compat=True, audio_kbps=128, stereo=True),
    # --- Móvil y TV
    _p("device_phone", "device", "Móvil o tablet · 1080p",
       "Lo abre cualquier móvil o tablet, también los antiguos.", "mp4",
       max_height=1080, max_fps=60, compat=True, stereo=True),
    _p("device_phone_720", "device", "Móvil · 720p (ocupa poco)",
       "Para llevar muchos vídeos en el móvil.", "mp4",
       quality=22, max_height=720, max_fps=30, compat=True, audio_kbps=128, stereo=True),
    _p("device_tv", "device", "Smart TV · hasta 4K (H.265)",
       "Para la tele: H.265 hasta 4K. Si tiene sonido envolvente, lo conserva\n"
       "y añade una pista en estéreo para que suene en cualquier sitio.", "mp4",
       video="h265", max_height=2160, surround=True),
    # --- Solo audio
    _p("audio_mp3", "audio", "MP3 · 320 kbps", "El más compatible.", "mp3",
       video=None, audio="mp3", audio_kbps=320),
    _p("audio_m4a", "audio", "M4A (AAC) · 256 kbps", "Más calidad que MP3 al mismo tamaño.", "m4a",
       video=None, audio="aac", audio_kbps=256),
    _p("audio_opus", "audio", "Opus · 160 kbps", "El que mejor suena por tamaño.", "opus",
       video=None, audio="opus", audio_kbps=160),
    _p("audio_flac", "audio", "FLAC (sin pérdida)", "Sonido exacto, comprimido sin pérdida.", "flac",
       video=None, audio="flac"),
    _p("audio_wav", "audio", "WAV (sin comprimir)", "Para editar audio. Ocupa mucho.", "wav",
       video=None, audio="pcm"),
)

DEFAULT_ID = "mp4_h264"


def builtin_by_id():
    return {p.id: p for p in BUILTIN}


# ------------------------------------------------------------------ perfiles del usuario

def load_custom(path):
    """Lee los perfiles guardados. Ignora entradas rotas en vez de fallar."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    known = {f.name for f in fields(Preset)}
    presets = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            p = Preset(**{k: v for k, v in item.items() if k in known})
        except TypeError:
            continue
        presets.append(replace(p, category="custom", custom=True))
    return presets


def save_custom(path, presets):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(p) for p in presets], ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)


def new_custom_id(existing):
    n = 1
    ids = {p.id for p in existing}
    while f"custom_{n}" in ids:
        n += 1
    return f"custom_{n}"

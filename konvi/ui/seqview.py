"""Visor de secuencias de imágenes dentro de Konvi (seqview, de dolphin-video-tools).

Con las secuencias de la lista se abre con sus fotogramas exactos; con una carpeta de
imágenes recién sacadas, la secuencia más larga que haya dentro. Lee los fotogramas en
varios hilos y los guarda en RAM (hasta la mitad de la memoria libre) para reproducir a
tiempo real. Lo que Qt no sabe leer (EXR sin kimageformats, DPX…) lo lee ffmpeg.
"""

import json
import math
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from PySide6.QtCore import (QElapsedTimer, QObject, QPointF, QRectF, QRunnable, QSettings, Qt,
                            QThreadPool, QTimer, Signal)
from PySide6.QtGui import (QActionGroup, QBrush, QColor, QColorSpace, QFont, QIcon, QImage,
                           QImageReader, QPainter, QPixmap)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QFrame, QHBoxLayout, QLabel, QMenu, QProgressBar,
                               QPushButton, QScrollArea, QSlider, QStyle, QStyleOptionSlider, QToolButton,
                               QVBoxLayout, QWidget)

from ..core import look as fx
from ..i18n import number as fmt_number, tr

APP = "Konvi"
DEFAULT_FPS = 24.0
ICONS = Path(__file__).resolve().parent.parent / "icons"
EXTS = {"png", "jpg", "jpeg", "tif", "tiff", "exr", "tga", "bmp", "webp", "psd", "hdr",
        "jxl", "avif", "heic", "heif", "jp2", "qoi", "pfm", "dds"}
FRAME_RE = re.compile(r"^(.*?)(\d+)(\.[^.]+)$")


# ------------------------------------------------------------------ secuencias

def is_image(path):
    return path.suffix[1:].lower() in EXTS


def natural_key(path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def sequence_of(path):
    """La secuencia a la que pertenece una imagen: mismo prefijo, número y extensión."""
    m = FRAME_RE.match(path.name)
    if not m:
        return [path]
    prefix, _, ext = m.groups()
    frames = []
    for p in path.parent.iterdir():
        n = FRAME_RE.match(p.name)
        if n and n.group(1) == prefix and n.group(3).lower() == ext.lower() and p.is_file():
            frames.append((int(n.group(2)), p))
    return [p for _, p in sorted(frames)] or [path]


def longest_sequence_in(folder):
    groups = {}
    for p in folder.iterdir():
        if p.is_file() and is_image(p):
            m = FRAME_RE.match(p.name)
            key = (m.group(1), m.group(3).lower()) if m else (None, p.suffix.lower())
            groups.setdefault(key, []).append(p)
    if not groups:
        return []
    return sorted(max(groups.values(), key=len), key=natural_key)


def collect(args):
    paths = [Path(a) for a in args]
    if len(paths) == 1 and paths[0].is_dir():
        return longest_sequence_in(paths[0])
    files = [p for p in paths if p.is_file() and is_image(p)]
    if len(files) == 1:
        return sequence_of(files[0])
    return sorted(files, key=natural_key)


def frame_number(path, index):
    m = FRAME_RE.match(path.name)
    return int(m.group(2)) if m else index + 1


def describe(paths):
    """'clip_####.png  [1-48]' para el título."""
    first, last = paths[0], paths[-1]
    m = FRAME_RE.match(first.name)
    if not m or len(paths) == 1:
        return first.name
    prefix, digits, ext = m.groups()
    return f"{prefix}{'#' * len(digits)}{ext}  [{frame_number(first, 0)}-{frame_number(last, 0)}]"


def memory_budget():
    """La mitad de la memoria disponible, entre 512 MB y 16 GB."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) * 1024
                    return max(512 << 20, min(avail // 2, 16 << 30))
    except OSError:
        pass
    return 2 << 30


# ------------------------------------------------------------------ caché

def _ffmpeg_image(path):
    """Lo que Qt no lee (DPX...), por ffmpeg, que Konvi ya necesita."""
    try:
        out = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-frames:v", "1",
                              "-f", "image2pipe", "-c:v", "png", "-"],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return QImage(), str(e)
    image = QImage()
    image.loadFromData(out.stdout, "PNG")
    lines = out.stderr.decode("utf-8", "replace").strip().splitlines()
    return image, "" if not image.isNull() else (lines[-1] if lines else tr("No es una imagen que ffmpeg sepa leer."))


def _ffmpeg_exr(path):
    """Un EXR que Qt no lee (sin kimageformats), por ffmpeg y en coma flotante: en lineal,
    sin recortar, como lo deja kimageformats."""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-frames:v", "1",
                              "-f", "rawvideo", "-pix_fmt", "gbrapf32le", "-"],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return QImage(), str(e)
    err = out.stderr.decode("utf-8", "replace")
    size = re.findall(r"Video: rawvideo\b.*?, (\d+)x(\d+)\b", err)
    if not size or not out.stdout:
        lines = err.strip().splitlines()
        return QImage(), lines[-1] if lines else tr("No es una imagen que ffmpeg sepa leer.")
    w, h = map(int, size[-1])
    g, b, r, a = np.frombuffer(out.stdout, np.float32, count=4 * w * h).reshape(4, h, w)
    data = np.stack([r, g, b, a], axis=-1).astype(np.float16).tobytes()
    image = QImage(data, w, h, w * 8, QImage.Format_RGBA16FPx4).copy()
    image.setColorSpace(QColorSpace(QColorSpace.SRgbLinear))
    return image, ""


def load_image(path):
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull() and path.suffix.lower() == ".exr":
        image, error = _ffmpeg_exr(path)
        if image.isNull():
            return image, error
    if image.isNull():
        image, error = _ffmpeg_image(path)
        if image.isNull():
            return image, error
    if image.format() in FLOAT_FORMATS:
        return as_linear_half(image), ""
    cs = image.colorSpace()
    if cs.isValid() and cs != QColorSpace(QColorSpace.SRgb):
        image.convertToColorSpace(QColorSpace(QColorSpace.SRgb))
    fmt = QImage.Format_ARGB32_Premultiplied if image.hasAlphaChannel() else QImage.Format_RGB32
    if image.format() != fmt:
        image = image.convertToFormat(fmt)
    return image, ""


def file_stamp(path):
    """Fecha y tamaño del fichero: si cambian, alguien lo ha vuelto a escribir (un re-render)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


class _Bridge(QObject):
    loaded = Signal(int, int, QImage, str, object)     # generación, índice, imagen, error, sello


class _LoadTask(QRunnable):
    def __init__(self, bridge, generation, index, path):
        super().__init__()
        self.bridge, self.generation, self.index, self.path = bridge, generation, index, path

    def run(self):
        stamp = file_stamp(self.path)   # antes de leer: si cambia mientras, el refresco lo ve
        image, error = load_image(self.path)
        self.bridge.loaded.emit(self.generation, self.index, image, error, stamp)


class _FinderBridge(QObject):
    found = Signal(int, object, object)        # turno, lista de rutas, rutas que han cambiado


class _FindTask(QRunnable):
    """Buscar la secuencia recorre la carpeta: en un NAS con miles de ficheros tarda, así que
    va en un hilo y la ventana se abre antes."""

    def __init__(self, bridge, token, args, stamps=None):
        super().__init__()
        self.bridge, self.token, self.args = bridge, token, args
        self.stamps = dict(stamps or {})        # ruta -> sello de lo que hay en memoria

    def run(self):
        try:
            paths = collect(self.args)
        except OSError:
            paths = []
        changed = {p for p, stamp in self.stamps.items() if file_stamp(p) != stamp}
        self.bridge.found.emit(self.token, paths, changed)


class FrameCache(QObject):
    """Ventana de fotogramas en RAM alrededor del cabezal, leídos en un pool de hilos."""

    frame_ready = Signal(int)
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max(2, (os.cpu_count() or 4) - 2))
        self.bridge = _Bridge()
        self.bridge.loaded.connect(self._on_loaded, Qt.QueuedConnection)
        self.budget = memory_budget()
        self.paths = []
        self.images = {}
        self.stamps = {}                # índice -> sello del fichero cuando se leyó
        self.errors = {}
        self.inflight = set()
        self.generation = 0
        self.frame_bytes = 0
        # fotogramas ya revelados con los efectos y la vista de ahora, para que la segunda
        # vuelta salga de memoria; solo si cabe la secuencia entera, leída y revelada
        self.developed = {}             # índice -> (cacheKey del fotograma, imagen revelada)
        self.view_key = None
        self.developed_bytes = 0
        self._wanted = set()
        self._current = 0
        self._loop = True

    def set_paths(self, paths):
        self.generation += 1
        self.pool.clear()
        self.paths = list(paths)
        self.images.clear()
        self.stamps.clear()
        self.errors.clear()
        self.inflight.clear()
        self.frame_bytes = 0
        self.forget_developed()
        self._wanted = set()

    def loaded_stamps(self):
        """Ruta -> sello de los fotogramas que hay en memoria, para ver al refrescar cuáles han cambiado."""
        return {self.paths[i]: self.stamps.get(i) for i in self.images}

    def remap(self, paths, stale=()):
        """Cambia la lista de fotogramas conservando en memoria los que siguen en ella, para no
        volver a leerlos al refrescar, salvo los de `stale` (re-renderizados desde que se leyeron).
        Los que fallaron se reintentan: en un render en marcha suelen ser fotogramas que aún se
        estaban escribiendo."""
        stale = set(stale)
        kept = {p: (self.images[i], self.stamps.get(i)) for i, p in enumerate(self.paths)
                if i in self.images and p not in stale}
        self.generation += 1
        self.pool.clear()
        self.paths = list(paths)
        dev = {self.paths[i]: d for i, d in self.developed.items()}
        self.images = {i: kept[p][0] for i, p in enumerate(self.paths) if p in kept}
        self.stamps = {i: kept[p][1] for i, p in enumerate(self.paths) if p in kept}
        self.developed = {i: dev[p] for i, p in enumerate(self.paths) if p in kept and p in dev}
        self.errors.clear()
        self.inflight.clear()
        self._wanted = set()

    def capacity(self):
        if not self.frame_bytes:
            return self.pool.maxThreadCount()
        return max(4, self.budget // self.frame_bytes)

    def forget_developed(self):
        self.developed.clear()
        self.developed_bytes = 0

    def developed_image(self, index, image, view_key, make):
        """El fotograma `index` revelado con `view_key`: de memoria si ya se reveló así, y si no
        con `make()`. Cambiar cualquier ajuste (`view_key`) tira todo lo revelado."""
        if view_key != self.view_key:
            self.view_key = view_key
            self.forget_developed()
        hit = self.developed.get(index)
        if hit is not None and hit[0] == image.cacheKey():
            return hit[1]
        shown = make()
        if shown is not image:          # un 8 bits sin tocar ya es lo que se pinta
            nbytes = max(self.developed_bytes, shown.sizeInBytes())
            if (max(self.frame_bytes, image.sizeInBytes()) + nbytes) * len(self.paths) <= self.budget:
                self.developed_bytes = nbytes
                self.developed[index] = (image.cacheKey(), shown)
            else:
                self.forget_developed()
        return shown

    def wanted(self, current, loop):
        """Índices a tener en memoria, por prioridad: el actual, los siguientes y unos pocos de atrás."""
        n = len(self.paths)
        cap = min(self.capacity(), n)
        ahead = cap - (cap // 10 if cap < n else 0)
        order = []
        for i in range(ahead):
            j = current + i
            if j >= n:
                if not loop:
                    break
                j %= n
            order.append(j)
        # lo que no se ha usado delante (sin bucle, al final) se gasta hacia atrás
        j = current - 1
        while len(order) < cap and j >= 0:
            if j not in order:
                order.append(j)
            j -= 1
        return order

    def update(self, current, loop):
        self._current, self._loop = current, loop
        order = self.wanted(current, loop)
        self._wanted = set(order)
        evicted = [i for i in self.images if i not in self._wanted]
        for i in evicted:
            del self.images[i]
            self.stamps.pop(i, None)
            self.developed.pop(i, None)
        limit = self.pool.maxThreadCount() * 2
        for i in order:
            if len(self.inflight) >= limit:
                break
            if i in self.images or i in self.inflight or i in self.errors:
                continue
            self.inflight.add(i)
            self.pool.start(_LoadTask(self.bridge, self.generation, i, self.paths[i]))
        if evicted:
            self.changed.emit()

    def _on_loaded(self, generation, index, image, error, stamp=None):
        if generation != self.generation:
            return
        self.inflight.discard(index)
        if error:
            self.errors[index] = error
        else:
            self.frame_bytes = max(self.frame_bytes, image.sizeInBytes())
            if index in self._wanted or index == self._current:
                self.images[index] = image
                self.stamps[index] = stamp
        self.frame_ready.emit(index)
        self.update(self._current, self._loop)
        self.changed.emit()

    def wait(self):
        self.pool.waitForDone()


# ------------------------------------------------------------------ vista (exposición, gamma, canales)

FLOAT_FORMATS = {QImage.Format_RGBA16FPx4, QImage.Format_RGBA16FPx4_Premultiplied, QImage.Format_RGBX16FPx4,
                 QImage.Format_RGBA32FPx4, QImage.Format_RGBA32FPx4_Premultiplied, QImage.Format_RGBX32FPx4}
CHANNELS = ("rgb", "r", "g", "b", "a")
EXPOSURE_STEP, GAMMA_STEP = 0.5, 0.1


def as_linear_half(image):
    """EXR/HDR: se guardan en lineal y en coma flotante (media precisión, sin premultiplicar),
    para que la exposición saque lo que hay por encima de 1 en vez de lo ya recortado."""
    cs = image.colorSpace()
    if cs.isValid() and cs != QColorSpace(QColorSpace.SRgbLinear):
        image.convertToColorSpace(QColorSpace(QColorSpace.SRgbLinear))
    return image.convertToFormat(QImage.Format_RGBA16FPx4)


def is_hdr(image):
    return image.format() == QImage.Format_RGBA16FPx4


def _srgb_encode(v):
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(v, 1 / 2.4) - 0.055)


_luts = {}


def view_lut(hdr, exposure, gamma):
    """Tabla de cada valor posible del fichero (los 65536 de un half, o los 256 de 8 bits) a
    8 bits sRGB, con la exposición (en pasos) y la gamma aplicadas en lineal."""
    key = (hdr, exposure, gamma)
    if key not in _luts:
        if hdr:
            v = np.arange(65536, dtype=np.uint16).view(np.float16).astype(np.float32)
            v = np.nan_to_num(v, nan=0.0, posinf=65504.0, neginf=0.0)
        else:
            v = np.arange(256, dtype=np.float32) / 255
            v = np.where(v <= 0.04045, v / 12.92, np.power((v + 0.055) / 1.055, 2.4))
        v = np.power(np.maximum(v * 2.0 ** exposure, 0), 1 / gamma)
        _luts.clear() if len(_luts) > 64 else None
        _luts[key] = np.round(_srgb_encode(np.minimum(v, 1)) * 255).astype(np.uint8)
    return _luts[key]


def _alpha_lut():
    if "alpha" not in _luts:
        v = np.arange(65536, dtype=np.uint16).view(np.float16).astype(np.float32)
        _luts["alpha"] = np.round(np.clip(np.nan_to_num(v), 0, 1) * 255).astype(np.uint8)
    return _luts["alpha"]


def _by_bands(height, work):
    """Reparte el trabajo por franjas de filas entre varios hilos (numpy suelta el GIL): un
    1080p en coma flotante pasa de ~34 ms a ~11 ms y cabe en lo que dura un fotograma."""
    fx.by_bands(height, work, rows=64)


def develop(image, exposure=0.0, gamma=1.0, channel="rgb", look=None, frame=0):
    """Lo que se pinta: el fotograma con los efectos de cámara (si hay), la exposición y la
    gamma de la vista, y solo el canal elegido (en gris). Una imagen de 8 bits sin tocar nada
    se devuelve tal cual."""
    has_alpha = image.hasAlphaChannel() and channel == "rgb"
    if look is not None and look.active():
        # con efectos se revela como un EXR: lineal en media precisión, por la misma tabla
        rgba = look.apply(linear_rgba(image), frame)
        half = np.empty(rgba.shape, np.float16)
        _by_bands(rgba.shape[0], lambda rows: np.copyto(half[rows], rgba[rows], casting="unsafe"))
        return _reveal(half.view(np.uint16), True, has_alpha, exposure, gamma, channel)
    hdr = is_hdr(image)
    if not hdr and exposure == 0 and gamma == 1 and channel == "rgb":
        return image
    if not hdr:
        image = image.convertToFormat(QImage.Format_RGBA8888)
    return _reveal(_pixels(image, np.uint16 if hdr else np.uint8), hdr, has_alpha, exposure, gamma, channel)


def _reveal(src, hdr, has_alpha, exposure, gamma, channel):
    """src: (alto, ancho, 4) en 8 bits o los bits de un half → QImage de 8 bits para pintar."""
    h, w = src.shape[:2]
    lut = _alpha_lut() if channel == "a" else view_lut(hdr, exposure, gamma)
    out = np.empty((h, w, 4), np.uint8)

    def work(rows):
        if channel == "rgb":
            np.take(lut, src[rows], out=out[rows])
            if not has_alpha:
                out[rows, :, 3] = 255
            elif hdr:
                np.take(_alpha_lut(), src[rows, :, 3], out=out[rows, :, 3])
            else:
                out[rows, :, 3] = src[rows, :, 3]
        else:
            plane = src[rows, :, "rgba".index(channel)]
            gray = plane if channel == "a" and not hdr else np.take(lut, plane)
            out[rows, :, :3] = gray[..., None]
            out[rows, :, 3] = 255

    _by_bands(h, work)
    fmt = QImage.Format_ARGB32_Premultiplied if has_alpha else QImage.Format_RGB32
    return QImage(out.data, w, h, w * 4, QImage.Format_RGBA8888).convertToFormat(fmt)


# ------------------------------------------------------------------ lineal, para los efectos

def _srgb_decode(v):
    return np.where(v <= 0.04045, v / 12.92, np.power((v + 0.055) / 1.055, 2.4))


_DECODE8 = _srgb_decode(np.arange(256, dtype=np.float32) / 255).astype(np.float32)
_DECODE16 = None
DEEP_FORMATS = {QImage.Format_RGBA64, QImage.Format_RGBA64_Premultiplied, QImage.Format_RGBX64,
                QImage.Format_Grayscale16}


def _pixels(image, dtype):
    """Los píxeles del QImage, sin copiar: el QImage tiene que seguir vivo mientras se usen."""
    w, h, size = image.width(), image.height(), np.dtype(dtype).itemsize
    src = np.frombuffer(image.constBits(), dtype, count=h * image.bytesPerLine() // size)
    return src.reshape(h, image.bytesPerLine() // size)[:, :w * 4].reshape(h, w, 4)


def linear_rgba(image):
    """El fotograma como float32 (alto, ancho, 4) en luz lineal, sin premultiplicar."""
    global _DECODE16
    if is_hdr(image):
        return _pixels(image, np.float16).astype(np.float32)
    if image.format() in DEEP_FORMATS:
        if _DECODE16 is None:
            _DECODE16 = _srgb_decode(np.arange(65536, dtype=np.float32) / 65535).astype(np.float32)
        image = image.convertToFormat(QImage.Format_RGBA64)    # que viva mientras se lee
        px = _pixels(image, np.uint16)
        out = np.take(_DECODE16, px)
        out[..., 3] = px[..., 3] / np.float32(65535)
        return out
    image = image.convertToFormat(QImage.Format_RGBA8888)
    px = _pixels(image, np.uint8)
    out = np.empty(px.shape, np.float32)

    def work(rows):
        np.take(_DECODE8, px[rows], out=out[rows])
        out[rows, :, 3] = px[rows, :, 3] * np.float32(1 / 255)

    _by_bands(px.shape[0], work)
    return out


def half_image(rgba):
    """De vuelta a un QImage lineal en coma flotante, que es lo que revela develop()."""
    h, w = rgba.shape[:2]
    half = np.ascontiguousarray(np.nan_to_num(rgba, nan=0.0, posinf=65504.0), dtype=np.float16)
    image = QImage(half.data, w, h, w * 8, QImage.Format_RGBA16FPx4).copy()
    image.setColorSpace(QColorSpace(QColorSpace.SRgbLinear))
    return image


# ------------------------------------------------------------------ widgets

def checker_brush():
    tile = QPixmap(16, 16)
    tile.fill(QColor("#9a9a9a"))
    p = QPainter(tile)
    try:
        p.fillRect(0, 0, 8, 8, QColor("#666666"))
        p.fillRect(8, 8, 8, 8, QColor("#666666"))
    finally:
        p.end()
    return QBrush(tile)


class Viewer(QWidget):
    """Pinta el fotograma: ajustado a la ventana, o con zoom (rueda) y encuadre (botón central).

    Arrastrar con el botón izquierdo recorre la secuencia, como en la línea de tiempo."""

    zoom_changed = Signal()
    double_clicked = Signal()
    scrub_started = Signal()
    scrub_moved = Signal(float)         # píxeles en horizontal desde donde se pulsó
    scrub_finished = Signal()
    clicked = Signal()              # clic quieto, sin arrastrar
    menu_requested = Signal(object)     # clic derecho: posición global

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 180)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.image = None
        self.message = ""
        self.zoom = None            # None = ajustar a la ventana
        self.pan = QPointF(0, 0)
        self.checker = False
        self.fast = False           # sin suavizado mientras se reproduce
        self.exposure = 0.0         # pasos de diafragma, en lineal
        self.gamma = 1.0
        self.channel = "rgb"        # o solo "r", "g", "b", "a" (en gris)
        self.look = fx.Look()       # efectos de cámara
        self.bypass = False         # ver sin efectos (antes / después)
        self.frame = 0              # número del fotograma, para que el grano sea siempre el mismo
        self._developed = None      # (clave, imagen) del último fotograma revelado
        self.store = None           # FrameCache donde guardar lo revelado de cada fotograma
        self.index = 0              # índice del fotograma que se ve, en esa caché
        self._drag = None               # encuadre (botón central)
        self._scrub_x = None            # recorrer la secuencia (botón izquierdo)
        self._scrubbing = False         # ya se ha movido lo bastante para no ser un clic
        self._checker = checker_brush()

    def set_image(self, image, message=""):
        self.image, self.message = image, message
        self.update()

    def scale(self):
        if self.zoom is not None or self.image is None:
            return self.zoom or 1.0
        return min(self.width() / self.image.width(), self.height() / self.image.height())

    def image_rect(self):
        s = self.scale()
        w, h = self.image.width() * s, self.image.height() * s
        return QRectF((self.width() - w) / 2 + self.pan.x(), (self.height() - h) / 2 + self.pan.y(), w, h)

    def fit(self):
        self.zoom, self.pan = None, QPointF(0, 0)
        self.update()
        self.zoom_changed.emit()

    def actual_size(self):
        self.zoom, self.pan = 1.0 / self.devicePixelRatioF(), QPointF(0, 0)
        self.update()
        self.zoom_changed.emit()

    def zoom_percent(self):
        return round(self.scale() * self.devicePixelRatioF() * 100)

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            self._paint(p)
        finally:
            p.end()

    def displayed(self):
        """El fotograma tal como se ve, revelado una sola vez aunque se repinte muchas."""
        look = None if self.bypass or not self.look.active() else self.look
        # silenciado se ve el original sin pasar por lo revelado: si no, la vista «sin
        # efectos» contaría como un ajuste nuevo y tiraría todo lo revelado con ellos
        if self.store is not None and not self.bypass:
            return self.store.developed_image(
                self.index, self.image, (self.exposure, self.gamma, self.channel, look and look.key()),
                lambda: develop(self.image, self.exposure, self.gamma, self.channel, look, self.frame))
        key = (self.image.cacheKey(), self.exposure, self.gamma, self.channel,
               look and look.key(), look and self.frame)
        if self._developed is None or self._developed[0] != key:
            self._developed = (key, develop(self.image, self.exposure, self.gamma, self.channel,
                                            look, self.frame))
        return self._developed[1]

    def contextMenuEvent(self, event):
        self.menu_requested.emit(event.globalPos())

    def _paint(self, p):
        p.fillRect(self.rect(), QColor("#1b1b1b"))
        if self.image is not None:
            rect = self.image_rect()
            shown = self.displayed()
            if self.checker and shown.hasAlphaChannel():
                p.fillRect(rect, self._checker)
            p.setRenderHint(QPainter.SmoothPixmapTransform, not self.fast)
            p.drawImage(rect, shown)
        if self.message:
            p.setPen(QColor("#e0e0e0"))
            p.drawText(self.rect(), Qt.AlignCenter, self.message)

    def wheelEvent(self, event):
        if self.image is None:
            return
        steps = event.angleDelta().y() / 120
        old = self.scale()
        new = max(0.02, min(old * 1.25 ** steps, 64.0))
        # que el punto bajo el cursor no se mueva
        c = event.position() - QPointF(self.width() / 2, self.height() / 2)
        self.pan = c - (c - self.pan) * (new / old)
        self.zoom = new
        self.update()
        self.zoom_changed.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._scrub_x = event.position().x()
            self._scrubbing = False
            self.setCursor(Qt.SizeHorCursor)
            self.scrub_started.emit()
        elif event.button() == Qt.MiddleButton:
            self._drag = event.position() - self.pan
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._scrub_x is not None:
            dx = event.position().x() - self._scrub_x
            if not self._scrubbing and abs(dx) >= QApplication.startDragDistance():
                self._scrubbing = True
            if self._scrubbing:
                self.scrub_moved.emit(dx)
        elif self._drag is not None:
            self.pan = event.position() - self._drag
            self.update()

    def mouseReleaseEvent(self, event):
        if self._scrub_x is not None:
            if self._scrubbing:
                self.scrub_finished.emit()
            else:
                self.clicked.emit()
        self._drag = None
        self._scrub_x = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit()


class Timeline(QWidget):
    """Línea de tiempo: lo que ya está en caché, el cabezal, y clic/arrastre para ir a un fotograma."""

    seek = Signal(int)
    pressed = Signal()
    released = Signal()

    def __init__(self, cache, parent=None):
        super().__init__(parent)
        self.cache = cache
        self.count = 0
        self.current = 0
        self.setFixedHeight(22)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tr("Clic o arrastrar para ir a un fotograma.\n"
                           "La franja verde es lo que ya está en memoria; la azul, lo que\n"
                           "ya está revelado con los efectos y se reproduce sin recalcular."))

    def _x(self, index):
        return 0 if self.count <= 1 else index * (self.width() - 1) / (self.count - 1)

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            self._paint(p)
        finally:
            p.end()

    def _paint(self, p):
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#2a2a2a"))
        if self.count == 0:
            return
        step = w / self.count
        for indices, y, color in ((self.cache.images, h - 5, "#3fa34d"),
                                  (self.cache.developed, h - 8, "#4a90d9")):
            start = prev = None
            for i in sorted(indices) + [None]:
                if start is not None and (i is None or i != prev + 1):
                    p.fillRect(QRectF(start * step, y, (prev - start + 1) * step, 3 if y < h - 5 else 5),
                               QColor(color))
                    start = None
                if i is not None and start is None:
                    start = i
                prev = i
        for i in self.cache.errors:
            p.fillRect(QRectF(i * step, h - 5, max(step, 1), 5), QColor("#d04040"))
        x = (self.current + 0.5) * step
        p.fillRect(QRectF(x - 1, 0, 2, h), QColor("#f0a030"))

    def _seek_to(self, x):
        if self.count:
            self.seek.emit(max(0, min(self.count - 1, int(x / self.width() * self.count))))

    def mousePressEvent(self, event):
        self.pressed.emit()
        self._seek_to(event.position().x())

    def mouseMoveEvent(self, event):
        if event.buttons():
            self._seek_to(event.position().x())

    def mouseReleaseEvent(self, event):
        self.released.emit()


class JumpSlider(QSlider):
    """Un deslizador que salta adonde se pincha en la barra (no hay que apuntar a la bolita)
    y desde ahí se sigue arrastrando. Con Mayús pulsada va diez veces más despacio, para
    afinar, como en Blender (se puede pulsar y soltar a mitad del arrastre). Ctrl+clic pide
    volver al valor por defecto."""

    reset_requested = Signal()
    FINE = 0.1                          # con Mayús, una décima parte de la velocidad

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self._drag_x = None
        self._drag_value = 0.0

    def _geometry(self):
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        style = self.style()
        handle = style.subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)
        groove = style.subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
        return opt, handle, groove, max(1, groove.width() - handle.width())

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        if event.modifiers() & Qt.ControlModifier:
            self.reset_requested.emit()
            return
        opt, handle, groove, span = self._geometry()
        if not handle.contains(event.position().toPoint()) and not event.modifiers() & Qt.ShiftModifier:
            x = event.position().x() - groove.x() - handle.width() / 2
            self.setValue(QStyle.sliderValueFromPosition(self.minimum(), self.maximum(),
                                                         round(x), span, opt.upsideDown))
        self._drag_x = event.position().x()
        self._drag_value = float(self.value())
        self.setSliderDown(True)

    def mouseMoveEvent(self, event):
        if self._drag_x is None:
            return super().mouseMoveEvent(event)
        _, _, _, span = self._geometry()
        dx = event.position().x() - self._drag_x
        self._drag_x = event.position().x()
        speed = self.FINE if event.modifiers() & Qt.ShiftModifier else 1.0
        self._drag_value += dx * (self.maximum() - self.minimum()) / span * speed
        self._drag_value = min(max(self._drag_value, self.minimum()), self.maximum())
        self.setValue(round(self._drag_value))

    def mouseReleaseEvent(self, event):
        if self._drag_x is None:
            return super().mouseReleaseEvent(event)
        self._drag_x = None
        self.setSliderDown(False)


class ValueRow(QWidget):
    """Un ajuste numérico: deslizador y casilla, enlazados. Doble clic en el nombre lo devuelve
    a su valor por defecto."""

    changed = Signal(float)

    def __init__(self, label, lo, hi, step, decimals, default, suffix="", tooltip="", scale=1.0):
        super().__init__()
        self.default, self.scale = default, scale       # scale: lo que se ve / lo que se guarda
        self.label = QLabel(label)
        self.label.setToolTip((tooltip + "\n" if tooltip else "") +
                              tr("Doble clic aquí o Ctrl+clic en el deslizador para volver a {value}.",
                                 value=fmt_number(default * scale, decimals) + suffix))
        self.label.mouseDoubleClickEvent = lambda e: self.set_value(self.default, emit=True)
        self._mult = 10 ** decimals
        self.slider = JumpSlider()
        self.slider.reset_requested.connect(lambda: self.set_value(self.default, emit=True))
        self.slider.setRange(round(lo * self._mult), round(hi * self._mult))
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.spin = QDoubleSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setSingleStep(step)
        self.spin.setDecimals(decimals)
        self.spin.setSuffix(suffix)
        self.spin.setFocusPolicy(Qt.ClickFocus)
        for w in (self.slider, self.spin):
            w.setToolTip(self.label.toolTip())
        self.slider.valueChanged.connect(lambda v: self.spin.setValue(v / self._mult))
        self.spin.valueChanged.connect(self._spin_changed)
        grid = QVBoxLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.addWidget(self.label)
        row = QHBoxLayout()
        row.addWidget(self.slider, 1)
        row.addWidget(self.spin)
        grid.addLayout(row)
        self.set_value(default)

    def _spin_changed(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(round(v * self._mult))
        self.slider.blockSignals(False)
        self.changed.emit(v / self.scale)

    def value(self):
        return self.spin.value() / self.scale

    def set_value(self, v, emit=False):
        self.blockSignals(not emit)
        self.spin.setValue(v * self.scale)
        self.blockSignals(False)


class LookPanel(QWidget):
    """Barra lateral con los efectos de cámara: cada uno con su casilla para encenderlo y sus
    ajustes debajo (solo se ven los de los que están encendidos), y las cámaras completas
    arriba. Los valores por defecto son los de un objetivo y una película reales (ver
    seqview_look.py)."""

    changed = Signal()
    folding_changed = Signal()          # se ha plegado o desplegado una sección
    mute_toggled = Signal(bool)
    done_editing = Signal()             # el teclado vuelve al visor

    TITLES = {"distortion": "Distorsión", "aberration": "Aberración cromática", "glow": "Glow",
              "vignette": "Viñeteado", "halation": "Halación", "grain": "Grano / ruido",
              "grade": "Color, brillo y contraste"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self.boxes, self.bodies, self.rows = {}, {}, {}
        self.arrows, self.expanded = {}, {}

        self.preset = QComboBox()
        self.preset.addItem(tr("Personalizado"))
        for name in fx.PRESETS:
            self.preset.addItem(tr(name), name)
        self.preset.setToolTip(tr("Una cámara completa: enciende los efectos que lleva con sus valores.\n"
                               "Después se puede retocar cada uno."))
        self.preset.setFocusPolicy(Qt.ClickFocus)
        self.preset.activated.connect(self._preset_chosen)

        content = QWidget()
        col = QVBoxLayout(content)
        col.setContentsMargins(10, 8, 10, 8)
        col.setSpacing(6)
        self.title = QLabel(tr("Efectos de cámara"))
        f = self.title.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 1.15)
        self.title.setFont(f)
        # silenciar: se ven sin efectos, pero cada uno conserva si está encendido y sus valores
        self.mute = QToolButton()
        self.mute.setCheckable(True)
        self.mute.setAutoRaise(True)
        self.mute.setFocusPolicy(Qt.NoFocus)
        self.mute.toggled.connect(self._mute_changed)
        header = QHBoxLayout()
        header.addWidget(self.title, 1)
        header.addWidget(self.mute)
        col.addLayout(header)
        col.addWidget(QLabel(tr("Cámara")))
        col.addWidget(self.preset)

        D = fx.DEFAULTS
        self._section(col, "distortion", [
            ("amount", ValueRow(tr("Cantidad (TV distortion)"), -40, 15, 0.1, 1, D["distortion"]["amount"], " %",
                                tr("Negativo = barril (las rectas se curvan hacia fuera), positivo = cojín.\n"
                                "Un 50 mm f/1.4 da -1 a -1,3 %; una GoPro sin corregir («Wide»), unos -30 %.\n"
                                "(Las cifras de LensTip son la distorsión en la esquina: divídelas por 1,4-1,5.)")))],
            extra=self._fill_box())
        self._section(col, "aberration", [
            ("amount", ValueRow(tr("Cantidad"), -2, 2, 0.01, 2, D["aberration"]["amount"], " %",
                                tr("Aberración cromática lateral, en % de la distancia del centro a la esquina\n"
                                "(la medida de Imatest y LensTip): 0,04 % es baja, 0,08 % entre baja y media,\n"
                                "0,12 % media. Negativo cambia qué color queda por fuera.")))])
        self.glow_only = QCheckBox(tr("Ver solo el glow"))
        self.glow_only.setToolTip(tr("Para ajustarlo: se ve solo lo que el glow añade, sobre negro."))
        self.glow_only.setFocusPolicy(Qt.NoFocus)
        self.glow_only.toggled.connect(self._emit)
        self._section(col, "glow", [
            ("amount", ValueRow(tr("Intensidad"), 0, 400, 5, 0, D["glow"]["amount"], " %",
                                tr("Cuánto glow se suma a la imagen."), scale=100)),
            ("threshold", ValueRow(tr("Desde (pasos sobre el gris medio)"), -3, 8, 0.1, 1, D["glow"]["threshold"], " EV",
                                   tr("Solo brilla lo que pasa de aquí (0 = el gris medio; por debajo, casi todo).\n"
                                   "En un PNG el blanco está a unos 2,5 pasos; en un EXR las luces pasan de ahí."))),
            ("knee", ValueRow(tr("Suavidad del umbral"), 0, 100, 5, 0, D["glow"]["knee"], " %",
                              tr("0 % = corte en seco; más = empieza a brillar poco a poco, sin escalón."), scale=100)),
            ("size", ValueRow(tr("Tamaño"), 5, 100, 5, 0, D["glow"]["size"], " %",
                              tr("Hasta dónde puede llegar el halo, en proporción a la imagen."), scale=100)),
            ("falloff", ValueRow(tr("Cola: larga ← → corta"), 1, 4, 0.1, 1, D["glow"]["falloff"], "",
                                 tr("Cómo cae el halo con la distancia (1/r^p). 2 = como el deslumbramiento de\n"
                                    "un objetivo real; menos = neblina larga; más = halo recogido."))),
            ("saturation", ValueRow(tr("Saturación"), 0, 200, 5, 0, D["glow"]["saturation"], " %",
                                    tr("Color del halo: 0 % = blanco; 100 % = el de la luz que lo produce."),
                                    scale=100))],
            extra=self.glow_only)
        self._section(col, "vignette", [
            # se enseña en positivo (pasos que se pierden): a la derecha, más oscuro
            ("amount", ValueRow(tr("Cantidad (pasos que pierde la esquina)"), -2, 4, 0.1, 1, D["vignette"]["amount"], " EV",
                                tr("Pasos de luz que se pierden en la esquina: a la derecha, más oscuro.\n"
                                "Un 50 mm f/1.4 abierto pierde entre 0,75 y 3 (mediana de LensTip: 1,3);\n"
                                "cerrado a f/4, medio paso. Por debajo de 0 aclara los bordes."), scale=-1)),
            ("midpoint", ValueRow(tr("Punto medio"), 0, 90, 1, 0, 0, " %",
                                  tr("Hasta dónde se deja el centro sin oscurecer. 0 = como un objetivo."), scale=100)),
            ("roundness", ValueRow(tr("Forma: redonda → cuadrada"), 0, 100, 1, 0, 0, " %",
                                   tr("0 = círculo; 100 = un cuadrado con las esquinas redondeadas."), scale=100)),
            ("fit", ValueRow(tr("Encuadre: círculo → elipse"), 0, 100, 1, 0, 0, " %",
                             tr("0 = redondo, como un objetivo de verdad (las esquinas quedan más oscuras que\n"
                             "los lados); 100 = estirado a la forma de la imagen."), scale=100))])
        self._section(col, "halation", [
            ("amount", ValueRow(tr("Cantidad"), 0, 30, 0.5, 1, D["halation"]["amount"], " %",
                                tr("Cuánta luz vuelve rebotada del soporte. Un negativo de cine con su capa\n"
                                "antihalo: 1-3 %. Sin ella (CineStill y parecidas): 10-20 %."), scale=100)),
            ("radius", ValueRow(tr("Radio en la película"), 0.05, 1.5, 0.05, 2, D["halation"]["radius"], " mm",
                                tr("Tamaño del halo sobre el negativo: 0,1-0,2 mm con capa antihalo, 0,3-1 mm sin\n"
                                "ella. En 16 mm o Super 8 el mismo halo ocupa más de la imagen (usa el\n"
                                "formato del grano)."))),
            ("threshold", ValueRow(tr("Desde (pasos sobre el gris medio)"), 0, 8, 0.5, 1, D["halation"]["threshold"], " EV",
                                   tr("Solo las luces más fuertes que esto dejan halo. En un PNG el blanco está a\n"
                                   "unos 2,5 pasos sobre el gris medio; en un EXR las luces pueden pasar de ahí.")))])
        self.kind = QComboBox()
        self.kind.addItems([tr("Película"), tr("Sensor digital")])
        self.stock = QComboBox()
        for key, (name, _) in fx.STOCKS.items():
            self.stock.addItem(f"Kodak {name}", key)
        self.gauge = QComboBox()
        for key, (name, _) in fx.GAUGES.items():
            self.gauge.addItem(name, key)
        self.iso = QComboBox()
        for iso in fx.ISOS:
            self.iso.addItem(f"ISO {iso}", iso)
        self.kind.setToolTip(tr("Película: grano en la densidad del negativo, más en la capa azul.\n"
                             "Sensor: ruido de fotones, más visible en las sombras."))
        self.stock.setToolTip(tr("El grano de cada emulsión sale de las curvas de granularidad de Kodak."))
        self.gauge.setToolTip(tr("El mismo negativo en un fotograma más pequeño se amplía más: más grano y más grueso."))
        self.iso.setToolTip(tr("Ruido de una full frame actual a esa sensibilidad (un móvil o una cámara\n"
                            "de acción, con sensor pequeño, equivale a unos 5 pasos más)."))
        combos = QWidget()
        form = QFormLayout(combos)
        form.setContentsMargins(0, 0, 0, 0)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.addRow(tr("Tipo"), self.kind)
        form.addRow(tr("Emulsión"), self.stock)
        form.addRow(tr("Formato"), self.gauge)
        form.addRow(tr("Sensibilidad"), self.iso)
        self._grain_form = form
        for c in (self.kind, self.stock, self.gauge, self.iso):
            c.setFocusPolicy(Qt.ClickFocus)
            c.activated.connect(self._emit)
        self._section(col, "grain", [
            ("amount", ValueRow(tr("Cantidad"), 0, 300, 5, 0, 1.0, " %",
                                tr("100 % = lo que da esa película o ese sensor de verdad."), scale=100)),
            ("color", ValueRow(tr("Color"), 0, 100, 5, 0, 1.0, " %",
                               tr("Película: 100 % = cada capa con su grano (algo correlado entre capas, como\n"
                               "en la real); 0 % = grano en gris. Sensor: cuánto del ruido es de color\n"
                               "(una cámara de cine, poco; un móvil, bastante)."), scale=100)),
            ("nr", ValueRow(tr("Reducción de ruido"), 0, 100, 5, 0, 0.0, " %",
                            tr("La que hacen un móvil o una cámara de acción: menos ruido de luminancia y\n"
                            "el de color en manchas más grandes y suaves. 0 % = una cámara grande."),
                            scale=100))],
            extra=combos, extra_first=True)
        self._section(col, "grade", [
            ("temperature", ValueRow(tr("Temperatura"), 2000, 12000, 100, 0, 6500.0, " K",
                                     tr("Balance de blancos, como en la cámara o en Lightroom: de qué color era la\n"
                                     "luz. 3200 K (bombilla) enfría la imagen para compensarla; por encima de\n"
                                     "6500 K (luz de día nublado) se calienta."))),
            ("tint", ValueRow(tr("Tinte"), -100, 100, 1, 0, 0.0, "",
                              tr("Verde (negativo) ↔ magenta (positivo), en perpendicular a la temperatura.\n"
                              "Corrige la luz verdosa de fluorescentes y LED."))),
            ("exposure", ValueRow(tr("Brillo"), -4, 4, 0.1, 1, 0.0, " EV",
                                  tr("Pasos de luz: +1 dobla la luz, -1 la reduce a la mitad."))),
            ("contrast", ValueRow(tr("Contraste"), 25, 200, 5, 0, 1.0, " %",
                                  tr("Alrededor del gris medio, como en un etalonaje: el gris no se mueve, las\n"
                                  "luces suben y las sombras bajan. 100 % = sin cambio."), scale=100)),
            ("gamma", ValueRow(tr("Gamma"), 0.2, 2, 0.05, 2, 1.0, "",
                               tr("Más de 1 aclara los medios tonos, menos de 1 los oscurece; el negro y el\n"
                               "blanco no se mueven."))),
            ("saturation", ValueRow(tr("Saturación"), 0, 200, 5, 0, 1.0, " %",
                                    tr("0 % = blanco y negro; 100 % = sin cambio. La luminancia no cambia."),
                                    scale=100))])
        col.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # sin barra horizontal, el panel tiene que medir lo que pide su contenido (con el estilo
        # que toque: en Breeze las casillas y los desplegables son más anchos que en Fusion) más
        # la barra vertical; si no, se corta por la derecha
        for combo in (self.preset, self.kind, self.stock, self.gauge, self.iso):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(14)
        # todas las casillas del mismo ancho (el que pide la más larga, «-25,0 %» con sus
        # flechas), para que los deslizadores queden alineados
        spin_w = max(row.spin.sizeHint().width() for row in self.rows.values())
        for row in self.rows.values():
            row.spin.setFixedWidth(spin_w)
        content.adjustSize()
        bar = scroll.style().pixelMetric(scroll.style().PixelMetric.PM_ScrollBarExtent)
        self.setFixedWidth(max(280, content.minimumSizeHint().width() + bar + 2 * scroll.frameWidth() + 4))

        reset = QPushButton(tr("Quitar todo"))
        reset.setToolTip(tr("Apaga todos los efectos y vuelve a los valores por defecto"))
        reset.setFocusPolicy(Qt.NoFocus)
        reset.clicked.connect(lambda: self.set_settings(fx.default_settings(), emit=True))

        bottom = QVBoxLayout()
        bottom.setContentsMargins(10, 6, 10, 8)
        bottom.addWidget(reset)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(scroll, 1)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        outer.addWidget(line)
        outer.addLayout(bottom)
        self.set_settings(fx.default_settings())
        self._mute_changed(False)

    def _mute_changed(self, muted):
        self.mute.setIcon(icon("eye-off" if muted else "eye"))
        self.mute.setToolTip((tr("Efectos silenciados: se ve el original. Clic o M para volver a verlos.")
                              if muted else tr("Silenciar los efectos para ver el original (M).\n"
                                            "Cada uno se queda como está; otra vez M los vuelve a poner.")))
        self.title.setText(tr("Efectos de cámara — silenciados") if muted else tr("Efectos de cámara"))
        self.title.setEnabled(not muted)
        self.mute_toggled.emit(muted)

    def _fill_box(self):
        self.fill = QCheckBox(tr("Rellenar el encuadre"))
        self.fill.setToolTip(tr("Acerca la imagen lo justo para que no queden esquinas vacías,\n"
                             "como hace la cámara al corregir o al grabar recortando el sensor."))
        self.fill.setFocusPolicy(Qt.NoFocus)
        self.fill.toggled.connect(self._emit)
        return self.fill

    def _section(self, col, effect, rows, extra=None, extra_first=False):
        box = QCheckBox(tr(self.TITLES[effect]))
        f = box.font()
        f.setBold(True)
        box.setFont(f)
        box.setFocusPolicy(Qt.NoFocus)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 0, 0, 6)
        lay.setSpacing(4)
        if extra is not None and extra_first:
            lay.addWidget(extra)
        for key, row in rows:
            lay.addWidget(row)
            row.changed.connect(self._emit)
            row.spin.editingFinished.connect(self.done_editing)
            self.rows[(effect, key)] = row
        if extra is not None and not extra_first:
            lay.addWidget(extra)
        # la flecha pliega los ajustes para que el panel no se haga tan largo
        arrow = QToolButton()
        arrow.setAutoRaise(True)
        arrow.setFocusPolicy(Qt.NoFocus)
        arrow.setToolTip(tr("Plegar o desplegar los ajustes"))
        arrow.clicked.connect(lambda: self.set_expanded(effect, not self.expanded[effect]))
        box.toggled.connect(lambda on: self._effect_toggled(effect, on))
        box.toggled.connect(self._emit)
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        reset = QToolButton()
        reset.setIcon(icon("retry"))
        reset.setAutoRaise(True)
        reset.setFocusPolicy(Qt.NoFocus)
        reset.setToolTip(tr("Volver a los valores por defecto de este efecto"))
        reset.clicked.connect(lambda: self.reset_effect(effect))
        header = QHBoxLayout()
        header.setSpacing(2)
        header.addWidget(arrow)
        header.addWidget(box, 1)
        header.addWidget(reset)
        col.addWidget(sep)
        col.addLayout(header)
        col.addWidget(body)
        self.boxes[effect], self.bodies[effect], self.arrows[effect] = box, body, arrow
        self.expanded[effect] = False           # al abrir, todo plegado: el panel cabe sin scroll
        self._refresh_section(effect)

    def settings(self):
        out = fx.default_settings()
        for effect in fx.EFFECTS:
            out[effect]["on"] = self.boxes[effect].isChecked()
        for (effect, key), row in self.rows.items():
            out[effect][key] = round(row.value(), 4)
        out["distortion"]["fill"] = self.fill.isChecked()
        out["glow"]["only"] = self.glow_only.isChecked()
        g = out["grain"]
        g["kind"] = "film" if self.kind.currentIndex() == 0 else "digital"
        g["stock"], g["gauge"], g["iso"] = self.stock.currentData(), self.gauge.currentData(), self.iso.currentData()
        return out

    def set_settings(self, settings, emit=False):
        s = fx.merged(settings)
        self._loading = True
        for effect in fx.EFFECTS:
            self.boxes[effect].blockSignals(True)
            self.boxes[effect].setChecked(s[effect]["on"])
            self.boxes[effect].blockSignals(False)
            self._refresh_section(effect)
        for (effect, key), row in self.rows.items():
            row.set_value(s[effect][key])
        self.fill.setChecked(s["distortion"]["fill"])
        self.glow_only.setChecked(s["glow"]["only"])
        g = s["grain"]
        self.kind.setCurrentIndex(0 if g["kind"] == "film" else 1)
        for combo, value in ((self.stock, g["stock"]), (self.gauge, g["gauge"]), (self.iso, g["iso"])):
            i = combo.findData(value)
            combo.setCurrentIndex(i if i >= 0 else 0)
        self._loading = False
        self._sync()
        if emit:
            self.changed.emit()

    def _refresh_section(self, effect):
        """Los ajustes se ven si el efecto está encendido y su sección desplegada."""
        open_ = self.expanded[effect]
        self.arrows[effect].setArrowType(Qt.DownArrow if open_ else Qt.RightArrow)
        self.arrows[effect].setEnabled(self.boxes[effect].isChecked())
        self.bodies[effect].setVisible(self.boxes[effect].isChecked() and open_)

    def _effect_toggled(self, effect, on):
        if on:
            self.mute.setChecked(False)         # se ve el efecto, aunque estuvieran silenciados
        self._refresh_section(effect)

    def set_expanded(self, effect, open_):
        self.expanded[effect] = open_
        self._refresh_section(effect)
        self.folding_changed.emit()

    def folded(self):
        return [e for e in fx.EFFECTS if not self.expanded[e]]

    def set_folded(self, effects):
        for e in fx.EFFECTS:
            self.expanded[e] = e not in effects
            self._refresh_section(e)

    def reset_effect(self, effect):
        """Los valores por defecto de un efecto, sin apagarlo ni tocar los demás."""
        settings = self.settings()
        on = settings[effect]["on"]
        settings[effect] = fx.default_settings()[effect]
        settings[effect]["on"] = on
        self.set_settings(settings, emit=True)

    def set_effect(self, effect, on):
        self.boxes[effect].setChecked(on)

    def apply_preset(self, name):
        """Elegir una cámara es querer verla: si los efectos estaban silenciados, dejan de estarlo."""
        self.set_settings(fx.preset_settings(name), emit=True)
        self.mute.setChecked(False)

    def _preset_chosen(self, index):
        if index > 0:
            self.apply_preset(self.preset.itemData(index))
        self.done_editing.emit()

    def _sync(self):
        film = self.kind.currentIndex() == 0
        for w, show in ((self.stock, film), (self.gauge, film), (self.iso, not film)):
            self._grain_form.setRowVisible(w, show)
        self.rows[("grain", "nr")].setVisible(not film)
        name = fx.matching_preset(self.settings())
        self.preset.setCurrentIndex(self.preset.findData(name) if name else 0)

    def _emit(self, *_):
        if self._loading:
            return
        self._sync()
        self.changed.emit()


def icon(name):
    return QIcon(str(ICONS / f"{name}.svg"))


def tool_button(name, tooltip, slot, checkable=False):
    b = QToolButton()
    b.setIcon(icon(name))
    b.setToolTip(tooltip)
    b.setAutoRaise(True)
    b.setFocusPolicy(Qt.NoFocus)        # que Espacio y las flechas sean del visor
    b.setCheckable(checkable)
    (b.toggled if checkable else b.clicked).connect(slot)
    return b


def view_slider(box, log=False):
    """Un deslizador pegado a la casilla de exposición o de gamma. La gamma va en escala
    logarítmica, para que 1 quede en el centro (0,2 y 5 en los extremos). Doble clic: valor
    neutro."""
    slider = JumpSlider()
    slider.setFixedWidth(60)            # cortito: la casilla de al lado es para afinar
    slider.setFocusPolicy(Qt.NoFocus)
    slider.setToolTip(box.toolTip() + "\n" + tr("Doble clic o Ctrl+clic: valor neutro."))
    lo, hi = box.minimum(), box.maximum()
    if log:
        to_slider = lambda v: round(math.log(v, hi) * 100)
        from_slider = lambda i: hi ** (i / 100)
        slider.setRange(-100, 100)
        neutral = 1.0
    else:
        to_slider = lambda v: round(v * 10)
        from_slider = lambda i: i / 10
        slider.setRange(round(lo * 10), round(hi * 10))
        neutral = 0.0

    def from_box(v):
        slider.blockSignals(True)
        slider.setValue(to_slider(v))
        slider.blockSignals(False)

    box.valueChanged.connect(from_box)
    slider.valueChanged.connect(lambda i: box.setValue(from_slider(i)))
    slider.mouseDoubleClickEvent = lambda e: box.setValue(neutral)
    slider.reset_requested.connect(lambda: box.setValue(neutral))
    from_box(box.value())
    return slider


class Player(QWidget):
    def __init__(self, paths, fps=DEFAULT_FPS, autoplay=False, parent=None):
        super().__init__(parent, Qt.Window)         # ventana propia, que se cierra con Konvi
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.cache = FrameCache(self)
        self.paths = []
        self.current = 0
        self.loop = True
        self.fps = fps
        self.autoplay = autoplay        # empezar a reproducir al abrir, sin pulsar Play
        self._sized = False
        self._shown_frames = 0
        self._fps_clock = QElapsedTimer()
        self._play_clock = QElapsedTimer()
        self._next_due = 0.0
        self.real_fps = 0.0
        self._resume = False            # al soltar tras arrastrar, seguir reproduciendo

        self.viewer = Viewer()
        self.timeline = Timeline(self.cache)
        self.prefs = QSettings("konvi", "konvi")
        self.panel = LookPanel()
        try:
            self.panel.set_settings(json.loads(self.prefs.value("seqview/look", "{}")))
        except (TypeError, ValueError):
            pass
        self.viewer.look.set(self.panel.settings())
        self.panel.setVisible(self.prefs.value("seqview/look_panel", False, type=bool))
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)

        self.btn_play = tool_button("play", tr("Reproducir / pausa (Espacio)"), self.toggle_play)
        self.btn_loop = tool_button("loop", tr("Repetir en bucle (L)"), self._set_loop, True)
        self.btn_loop.setChecked(True)
        self.btn_look = tool_button("camera", tr("Efectos de cámara: distorsión, aberración cromática,\n"
                                                 "viñeteado, halación, grano y color (V)"),
                                    self.panel.setVisible, True)
        self.btn_look.setChecked(self.panel.isVisible())
        self.panel.changed.connect(self._look_changed)
        self.panel.mute_toggled.connect(self._mute)
        self.panel.done_editing.connect(self.setFocus)
        self.btn_refresh = tool_button("refresh", tr("Buscar fotogramas nuevos o cambiados (Ctrl+R, F5)\n"
                                                     "Para ver un render que sigue en marcha sin volver a abrirlo."),
                                       self.refresh)
        self.frame_label = QLabel()
        self.frame_label.setFont(QFont("monospace"))
        self.frame_label.setToolTip(tr("Fotograma actual (número del fichero) / total"))
        self.fps_box = QComboBox()
        self.fps_box.setEditable(True)
        self.fps_box.addItems(["12", "15", "23.976", "24", "25", "29.97", "30", "48", "50", "59.94", "60"])
        self.fps_box.setCurrentText(f"{round(fps, 3):g}")
        self.fps_box.setToolTip(tr("Fotogramas por segundo ([ y ] para cambiar)"))
        self.fps_box.currentTextChanged.connect(self._fps_edited)
        self.fps_box.setFocusPolicy(Qt.ClickFocus)
        self.fps_box.lineEdit().setFocusPolicy(Qt.ClickFocus)
        # al elegir o escribir un valor, el teclado vuelve al visor (Espacio, flechas...)
        self.fps_box.activated.connect(lambda _: self.setFocus())
        self.fps_box.lineEdit().editingFinished.connect(self.setFocus)
        self.exposure_box = QDoubleSpinBox()
        self.exposure_box.setRange(-10.0, 10.0)
        self.exposure_box.setSingleStep(EXPOSURE_STEP)
        self.exposure_box.setDecimals(1)
        self.exposure_box.setToolTip(tr("Exposición en pasos, en lineal (E / Mayús+E; 0 la deja a cero).\n"
                                             "En un EXR saca lo que hay en las luces quemadas."))
        self.gamma_box = QDoubleSpinBox()
        self.gamma_box.setRange(0.2, 5.0)
        self.gamma_box.setSingleStep(GAMMA_STEP)
        self.gamma_box.setDecimals(1)
        self.gamma_box.setValue(1.0)
        self.gamma_box.setToolTip(tr("Gamma de la vista (Y / Mayús+Y; 0 la deja en 1)"))
        self.channel_box = QComboBox()
        self.channel_box.addItems(["RGB", "R", "G", "B", "A"])
        self.channel_box.setToolTip(tr("Canal que se ve: C color, R rojo, G verde, B azul, Mayús+A alfa"))
        for box in (self.exposure_box, self.gamma_box, self.channel_box):
            box.setFocusPolicy(Qt.ClickFocus)
        self.exposure_box.valueChanged.connect(self._view_changed)
        self.gamma_box.valueChanged.connect(self._view_changed)
        self.channel_box.currentIndexChanged.connect(self._view_changed)
        self.exposure_box.editingFinished.connect(self.setFocus)
        self.gamma_box.editingFinished.connect(self.setFocus)
        self.channel_box.activated.connect(lambda _: self.setFocus())
        self.zoom_label = QLabel()
        self.zoom_label.setToolTip(tr("Zoom: rueda del ratón; mover la imagen: botón central.\n"
                                      "F = ajustar, 1 = 100 %"))
        self.info_label = QLabel()
        self.info_label.setStyleSheet("color: palette(placeholder-text);")
        self.load_bar = QProgressBar()
        self.load_bar.setFixedWidth(190)
        self.load_bar.setFormat(tr("Cargando %v de %m"))
        self.load_bar.setToolTip(tr("Leyendo las imágenes a memoria para reproducirlas a tiempo real.\n"
                                    "Ya se puede reproducir: avanza según van llegando."))
        self.load_bar.hide()
        self._finder = _FinderBridge()
        self._finder.found.connect(self._on_found, Qt.QueuedConnection)
        self._find_token = 0
        self._source = None             # lo que se abrió (imagen, carpeta...), para volver a buscar
        self._refresher = _FinderBridge()
        self._refresher.found.connect(self._on_refreshed, Qt.QueuedConnection)
        self._refresh_token = 0
        self._notice = ""               # aviso pasajero en la barra de estado
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(lambda: self._show_notice(""))

        controls = QHBoxLayout()
        controls.setContentsMargins(6, 2, 6, 4)
        for b in (tool_button("first", tr("Primer fotograma (Inicio)"), lambda: self.seek(0)),
                  tool_button("prev", tr("Fotograma anterior (←)"), lambda: self.step(-1)),
                  self.btn_play,
                  tool_button("next", tr("Fotograma siguiente (→)"), lambda: self.step(1)),
                  tool_button("last", tr("Último fotograma (Fin)"), lambda: self.seek(len(self.paths) - 1)),
                  self.btn_loop,
                  self.btn_refresh,
                  self.btn_look):
            controls.addWidget(b)
        controls.addSpacing(8)
        controls.addWidget(self.frame_label)
        controls.addSpacing(12)
        controls.addWidget(QLabel("FPS"))
        controls.addWidget(self.fps_box)
        controls.addSpacing(12)
        controls.addWidget(QLabel(tr("Exp.")))
        controls.addWidget(view_slider(self.exposure_box))
        controls.addWidget(self.exposure_box)
        controls.addSpacing(8)
        controls.addWidget(QLabel("γ"))
        controls.addWidget(view_slider(self.gamma_box, log=True))
        controls.addWidget(self.gamma_box)
        controls.addSpacing(8)
        controls.addWidget(self.channel_box)
        controls.addSpacing(12)
        controls.addWidget(self.zoom_label)
        controls.addStretch(1)
        controls.addWidget(self.load_bar)
        controls.addSpacing(8)
        controls.addWidget(self.info_label)

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        main.addWidget(self.viewer, 1)
        main.addWidget(self.timeline)
        main.addLayout(controls)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(main, 1)
        layout.addWidget(self.panel)

        self.cache.frame_ready.connect(self._on_frame_ready)
        self.cache.changed.connect(self._refresh_status)
        self.timeline.seek.connect(self._scrub)
        self.timeline.pressed.connect(self._scrub_start)
        self.timeline.released.connect(self._scrub_end)
        self.viewer.zoom_changed.connect(self._refresh_status)
        self.viewer.double_clicked.connect(self.toggle_fullscreen)
        self.viewer.scrub_started.connect(self._scrub_start)
        self.viewer.scrub_moved.connect(self._scrub_drag)
        self.viewer.scrub_finished.connect(self._scrub_end)
        self.viewer.clicked.connect(self._click)
        self.viewer.menu_requested.connect(self._context_menu)
        # al abrir, los efectos guardados vienen silenciados: se ve el render tal cual y con M
        # (o el ojo del panel) vuelven tal como se dejaron
        if self.viewer.look.active():
            self.panel.mute.setChecked(True)
        self.open(paths)

    # ---- abrir

    def open(self, paths, source=None):
        self.pause()
        self._source = source
        self._refresh_token += 1        # un refresco pendiente sería de la secuencia anterior
        self.btn_refresh.setEnabled(True)
        self.paths = list(paths)
        self.cache.set_paths(self.paths)
        self.timeline.count = len(self.paths)
        self.current = 0
        self._sized = False
        self.viewer.set_image(None, tr("Cargando «{name}» ({n} imágenes)…", name=describe(self.paths),
                                       n=len(self.paths))
                              if self.paths else
                              tr("Arrastra aquí una imagen o una carpeta con una secuencia"))
        self.setWindowTitle(f"{describe(self.paths)} — {APP}" if self.paths else APP)
        if self.paths:
            self.seek(0)
            if self.autoplay and len(self.paths) > 1:
                self.toggle_play()      # mientras carga, avanza según llegan los fotogramas
        self._refresh_status()

    def open_async(self, args):
        """Abre lo que diga el usuario sin bloquear la ventana mientras se busca la secuencia."""
        self.pause()
        self._find_token += 1
        self._find_args = list(args)
        self.viewer.set_image(None, tr("Buscando la secuencia…"))
        QThreadPool.globalInstance().start(_FindTask(self._finder, self._find_token, self._find_args))

    def _on_found(self, token, paths, _changed=()):
        if token != self._find_token:
            return                      # llegó otra petición después
        if paths:
            self.open(paths, self._find_args)
        else:
            self.viewer.set_image(None, tr("No hay imágenes que mostrar en la selección"))

    def refresh(self):
        """Vuelve a buscar la secuencia y añade los fotogramas que hayan aparecido desde que se
        abrió, sin perder lo que ya está en memoria, el cabezal ni la reproducción."""
        if not self.paths:
            return
        self._refresh_token += 1
        self.btn_refresh.setEnabled(False)
        self._show_notice(tr("Buscando fotogramas nuevos…"), 0)
        args = self._source or [str(self.paths[0])]
        QThreadPool.globalInstance().start(_FindTask(self._refresher, self._refresh_token, args,
                                                     self.cache.loaded_stamps()))

    def _on_refreshed(self, token, paths, changed=()):
        if token != self._refresh_token:
            return                      # entretanto se abrió otra cosa
        self.btn_refresh.setEnabled(True)
        if not paths:
            self._show_notice(tr("No se encuentra la secuencia"))
            return
        if paths == self.paths and not self.cache.errors and not changed:
            self._show_notice(tr("No hay fotogramas nuevos"))
            return
        added = len(set(paths) - set(self.paths))
        current = self.paths[self.current]
        self.paths = list(paths)
        self.cache.remap(self.paths, changed)
        self.timeline.count = len(self.paths)
        self.setWindowTitle(f"{describe(self.paths)} — {APP}")
        self.seek(self.paths.index(current) if current in self.paths
                  else min(self.current, len(self.paths) - 1))
        changed = len(set(changed) & set(paths))
        parts = ([tr("1 fotograma nuevo") if added == 1 else tr("{n} fotogramas nuevos", n=added)] * bool(added)
                 + [tr("1 fotograma cambiado") if changed == 1 else tr("{n} fotogramas cambiados", n=changed)]
                 * bool(changed))
        self._show_notice(", ".join(parts) or tr("Secuencia actualizada"))

    def _show_notice(self, text, ms=4000):
        self._notice = text
        if text and ms:
            self._notice_timer.start(ms)
        self._refresh_status()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        files = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if files:
            self.open_async(files)

    # ---- navegación

    def seek(self, index):
        if not self.paths:
            return
        self.current = max(0, min(index, len(self.paths) - 1))
        self.cache.update(self.current, self.loop)
        self._show_current()

    def _scrub_start(self):
        """Al empezar a arrastrar se para; si estaba reproduciendo, sigue al soltar."""
        self._resume = self.timer.isActive()
        self.pause()
        self._scrub_origin = self.current

    def _scrub_end(self):
        if self._resume:
            self._resume = False
            self.toggle_play()

    def _click(self):
        """Un clic quieto en la imagen: pausa si estaba reproduciendo y reproduce si no."""
        was_playing, self._resume = self._resume, False
        if not was_playing:
            self.toggle_play()

    def _scrub_drag(self, dx):
        """Arrastrar sobre la imagen: entre 2 y 10 píxeles por fotograma, según lo larga que
        sea la secuencia, para que una secuencia corta no vaya a saltos y una larga se pueda
        recorrer entera sin salirse de la ventana."""
        n = len(self.paths)
        if n < 2:
            return
        px_per_frame = max(2.0, min(10.0, self.viewer.width() / n))
        self.seek(self._scrub_origin + int(round(dx / px_per_frame)))

    def _scrub(self, index):
        self.pause()
        self.seek(index)

    def step(self, delta):
        self.pause()
        if self.paths:
            n = len(self.paths)
            self.seek((self.current + delta) % n if self.loop else self.current + delta)

    def _show_current(self):
        image = self.cache.images.get(self.current)
        if image is not None:
            self.viewer.index = self.current
            self.viewer.set_image(image)
            if not self._sized:
                self._sized = True
                self._fit_window(image)
        elif self.current in self.cache.errors:
            self.viewer.set_image(None, tr("No se pudo leer {name}\n{error}", name=self.paths[self.current].name,
                                           error=self.cache.errors[self.current]))
        # si aún no está, se queda el anterior hasta que llegue (_on_frame_ready)
        self.viewer.frame = frame_number(self.paths[self.current], self.current)
        self.timeline.current = self.current
        self.timeline.update()
        self._refresh_status()

    def _on_frame_ready(self, index):
        if index == self.current:
            self._show_current()

    def _fit_window(self, image):
        """Tamaño inicial: la imagen a 100 % si cabe en el 80 % de la pantalla."""
        if self.isFullScreen() or self.isMaximized():
            return
        screen = self.screen().availableGeometry()
        dpr = self.devicePixelRatioF()
        extra = self.height() - self.viewer.height()
        w = min(image.width() / dpr, screen.width() * 0.8)
        h = min(image.height() / dpr, screen.height() * 0.8 - extra)
        s = min(w / (image.width() / dpr), h / (image.height() / dpr))
        self.resize(max(640, int(image.width() / dpr * s)), int(image.height() / dpr * s) + extra)

    # ---- reproducción

    def toggle_play(self):
        if self.timer.isActive():
            self.pause()
        elif self.paths:
            if not self.loop and self.current == len(self.paths) - 1:
                self.seek(0)
            self.viewer.fast = True
            self._shown_frames = 0
            self._fps_clock.start()
            self._play_clock.start()
            self._next_due = 1000 / self.fps
            # el temporizador solo despierta; quien marca la cadencia es el reloj
            self.timer.start(max(1, int(250 / self.fps)))
            self.btn_play.setIcon(icon("pause"))

    def pause(self):
        if self.timer.isActive():
            self.timer.stop()
            self.btn_play.setIcon(icon("play"))
            self.real_fps = 0.0
        if self.viewer.fast:
            self.viewer.fast = False
            self.viewer.update()
            self._refresh_status()

    def _tick(self):
        now = self._play_clock.elapsed()
        if now < self._next_due:
            return
        period = 1000 / self.fps
        self._next_due += period
        if now - self._next_due > period:
            self._next_due = now + period      # tras una espera, no intentar recuperar
        n = len(self.paths)
        nxt = self.current + 1
        if nxt >= n:
            if not self.loop:
                self.pause()
                return
            nxt = 0
        if nxt not in self.cache.images and nxt not in self.cache.errors:
            self.cache.update(self.current, self.loop)
            return                      # todavía cargando: espera en vez de saltarse fotogramas
        self.seek(nxt)
        self._shown_frames += 1
        ms = self._fps_clock.elapsed()
        if ms >= 1000:
            self.real_fps = self._shown_frames * 1000 / ms
            self._shown_frames = 0
            self._fps_clock.restart()

    def _set_loop(self, on):
        self.loop = on
        self.cache.update(self.current, self.loop)

    def _fps_edited(self, text):
        try:
            fps = float(text.replace(",", "."))
        except ValueError:
            return
        if 0.1 <= fps <= 240:
            self.fps = fps
            if self.timer.isActive():
                self.timer.setInterval(max(1, int(250 / fps)))
                self._next_due = self._play_clock.elapsed() + 1000 / fps

    def _nudge_fps(self, direction):
        items = [self.fps_box.itemText(i) for i in range(self.fps_box.count())]
        values = [float(t) for t in items]
        if direction > 0:
            bigger = [i for i, v in enumerate(values) if v > self.fps + 1e-6]
            idx = bigger[0] if bigger else len(values) - 1
        else:
            smaller = [i for i, v in enumerate(values) if v < self.fps - 1e-6]
            idx = smaller[-1] if smaller else 0
        self.fps_box.setCurrentIndex(idx)

    # ---- ventana y teclado

    def _view_changed(self, *_):
        self.viewer.exposure = self.exposure_box.value()
        self.viewer.gamma = self.gamma_box.value()
        self.viewer.channel = CHANNELS[self.channel_box.currentIndex()]
        self.viewer.update()

    def set_channel(self, channel):
        """Pulsar otra vez la tecla del canal que ya se ve vuelve al color."""
        if channel == self.viewer.channel:
            channel = "rgb"
        self.channel_box.setCurrentIndex(CHANNELS.index(channel))

    def nudge_view(self, box, step):
        box.setValue(box.value() + step)

    def reset_view(self):
        self.exposure_box.setValue(0.0)
        self.gamma_box.setValue(1.0)

    # ---- efectos de cámara

    def _look_changed(self):
        self.viewer.look.set(self.panel.settings())
        self.viewer.update()
        self.prefs.setValue("seqview/look", json.dumps(self.panel.settings()))

    def _mute(self, on):
        self.viewer.bypass = on
        if self.viewer.look.active():       # con el panel cerrado, que se sepa qué se está viendo
            self._show_notice(tr("Efectos silenciados (M)") if on else tr("Efectos activos"))
        self.viewer.update()

    def toggle_panel(self):
        self.btn_look.toggle()

    def _context_menu(self, pos):
        menu = QMenu(self)

        def add(text, slot, shortcut="", checked=None, enabled=True, target=menu):
            action = target.addAction(text)
            if shortcut:
                action.setShortcut(shortcut)        # solo para que se vea la tecla
            if checked is not None:
                action.setCheckable(True)
                action.setChecked(checked)
            action.setEnabled(enabled)
            action.triggered.connect(slot)
            return action

        add(tr("Pausa") if self.timer.isActive() else tr("Reproducir"), self.toggle_play, "Space")
        menu.addSeparator()
        effects = menu.addMenu(icon("camera"), tr("Efectos de cámara"))
        add(tr("Panel de efectos"), self.toggle_panel, "V", self.panel.isVisible(), target=effects)
        add(tr("Silenciar efectos (ver el original)"), self.panel.mute.toggle, "M",
            self.viewer.bypass, target=effects)
        effects.addSeparator()
        settings = self.panel.settings()
        for effect in fx.EFFECTS:
            add(tr(LookPanel.TITLES[effect]), lambda on, e=effect: self.panel.set_effect(e, on),
                checked=settings[effect]["on"], target=effects)
        effects.addSeparator()
        cameras = effects.addMenu(tr("Cámara"))
        current = fx.matching_preset(settings)
        presets = QActionGroup(cameras)         # se marcan como opciones, una sola a la vez
        for name in fx.PRESETS:
            presets.addAction(add(tr(name), lambda _=False, n=name: self.panel.apply_preset(n),
                                  checked=name == current, target=cameras))
        add(tr("Quitar todos"), lambda: self.panel.set_settings(fx.default_settings(), emit=True),
            enabled=self.viewer.look.active(), target=effects)
        channels = menu.addMenu(tr("Canal"))
        group = QActionGroup(channels)
        for ch, text, key in (("rgb", tr("Color (RGB)"), "C"), ("r", tr("Rojo"), "R"), ("g", tr("Verde"), "G"),
                              ("b", tr("Azul"), "B"), ("a", tr("Alfa"), "Shift+A")):
            group.addAction(add(text, lambda _=False, c=ch: self.channel_box.setCurrentIndex(CHANNELS.index(c)),
                                key, self.viewer.channel == ch, target=channels))
        add(tr("Fondo de cuadros (alfa)"), self._toggle_checker, "A", self.viewer.checker)
        add(tr("Exposición 0 y gamma 1"), self.reset_view, "0",
            enabled=self.viewer.exposure != 0 or self.viewer.gamma != 1)
        menu.addSeparator()
        add(tr("Ajustar a la ventana"), self.viewer.fit, "F")
        add(tr("Tamaño real (100 %)"), self.viewer.actual_size, "1")
        add(tr("Pantalla completa"), self.toggle_fullscreen, "F11", self.isFullScreen())
        menu.addSeparator()
        add(tr("Abrir…"), self._ask_open, "Ctrl+O")
        add(tr("Buscar fotogramas nuevos"), self.refresh, "F5", enabled=bool(self.paths))
        menu.exec(pos)

    def _toggle_checker(self):
        self.viewer.checker = not self.viewer.checker
        self.viewer.update()

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def keyPressEvent(self, event):
        key, text = event.key(), event.text().lower()
        if key == Qt.Key_Space:
            self.toggle_play()
        elif key == Qt.Key_End or (key == Qt.Key_Right and event.modifiers() & Qt.ShiftModifier):
            self._scrub(len(self.paths) - 1)
        elif key == Qt.Key_Home or (key == Qt.Key_Left and event.modifiers() & Qt.ShiftModifier):
            self._scrub(0)
        elif key == Qt.Key_Right:
            self.step(1)
        elif key == Qt.Key_Left:
            self.step(-1)
        elif text == "l":
            self.btn_loop.toggle()
        elif text == "f":
            self.viewer.fit()
        elif text == "1":
            self.viewer.actual_size()
        elif text in ("r", "g", "b") and not event.modifiers() & Qt.ControlModifier:
            self.set_channel(text)
        elif text == "c":
            self.set_channel("rgb")
        elif text == "a" and event.modifiers() & Qt.ShiftModifier:
            self.set_channel("a")
        elif text == "m":
            self.panel.mute.toggle()
        elif text == "v":
            self.toggle_panel()
        elif text == "e":
            shift = event.modifiers() & Qt.ShiftModifier
            self.nudge_view(self.exposure_box, -EXPOSURE_STEP if shift else EXPOSURE_STEP)
        elif text == "y":
            shift = event.modifiers() & Qt.ShiftModifier
            self.nudge_view(self.gamma_box, -GAMMA_STEP if shift else GAMMA_STEP)
        elif text == "0":
            self.reset_view()
        elif text == "a":
            self._toggle_checker()
        elif text == "[":
            self._nudge_fps(-1)
        elif text == "]":
            self._nudge_fps(1)
        elif key in (Qt.Key_F11, Qt.Key_Return, Qt.Key_Enter):
            self.toggle_fullscreen()
        elif key == Qt.Key_O and event.modifiers() & Qt.ControlModifier:
            self._ask_open()
        elif key == Qt.Key_F5 or (key == Qt.Key_R and event.modifiers() & Qt.ControlModifier):
            self.refresh()
        elif key == Qt.Key_Escape:
            self.showNormal() if self.isFullScreen() else self.close()
        else:
            super().keyPressEvent(event)

    def _ask_open(self):
        name, _ = QFileDialog.getOpenFileName(
            self, tr("Abrir secuencia"), str(self.paths[0].parent if self.paths else Path.home()),
            tr("Imágenes ({exts})", exts=" ".join(f"*.{e}" for e in sorted(EXTS))))
        if name:
            self.open_async([name])

    def _refresh_status(self):
        n = len(self.paths)
        if n:
            number = frame_number(self.paths[self.current], self.current)
            self.frame_label.setText(f"{number:>5}  ({self.current + 1}/{n})")
        else:
            self.frame_label.setText("")
        self.zoom_label.setText(tr("Ajustar") if self.viewer.zoom is None and self.viewer.image is not None
                                else f"{self.viewer.zoom_percent()} %")
        parts = [self._notice] if self._notice else []
        if self.viewer.image is not None:
            parts.append(f"{self.viewer.image.width()}×{self.viewer.image.height()}")
        if n:
            parts.append(tr("caché {loaded}/{n}", loaded=len(self.cache.images), n=n))
        if self.timer.isActive() and self.real_fps:
            parts.append(tr("{fps} fps reales", fps=fmt_number(self.real_fps)))
        self.info_label.setText("  ·  ".join(parts))
        # barra de carga mientras falten imágenes por llegar a memoria
        total = min(n, self.cache.capacity()) if self.cache.frame_bytes else n
        loaded = len(self.cache.images) + len(self.cache.errors)
        if n and loaded < total:
            self.load_bar.setRange(0, total)
            self.load_bar.setValue(loaded)
            self.load_bar.show()
        else:
            self.load_bar.hide()
        self.timeline.update()

    def closeEvent(self, event):
        self.prefs.setValue("seqview/look_panel", self.panel.isVisible())
        self.timer.stop()
        self.cache.pool.clear()
        super().closeEvent(event)

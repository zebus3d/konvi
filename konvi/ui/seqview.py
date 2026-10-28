"""Visor de secuencias de imágenes dentro de Konvi (seqview, de dolphin-video-tools).

Con las secuencias de la lista se abre con sus fotogramas exactos; con una carpeta de
imágenes recién sacadas, la secuencia más larga que haya dentro. Lee los fotogramas en
varios hilos y los guarda en RAM (hasta la mitad de la memoria libre) para reproducir a
tiempo real. Lo que Qt no sabe leer (EXR sin kimageformats, DPX…) lo lee ffmpeg.
"""

import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from PySide6.QtCore import (QElapsedTimer, QObject, QPointF, QRectF, QRunnable, Qt,
                            QThreadPool, QTimer, Signal)
from PySide6.QtGui import (QBrush, QColor, QColorSpace, QFont, QIcon, QImage, QImageReader,
                           QPainter, QPixmap)
from PySide6.QtWidgets import (QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel,
                               QProgressBar, QToolButton, QVBoxLayout, QWidget)

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
        self.images = {i: kept[p][0] for i, p in enumerate(self.paths) if p in kept}
        self.stamps = {i: kept[p][1] for i, p in enumerate(self.paths) if p in kept}
        self.errors.clear()
        self.inflight.clear()
        self._wanted = set()

    def capacity(self):
        if not self.frame_bytes:
            return self.pool.maxThreadCount()
        return max(4, self.budget // self.frame_bytes)

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


_bands_pool = ThreadPoolExecutor(max(2, min(8, (os.cpu_count() or 4) // 2)))


def _by_bands(height, work):
    """Reparte el trabajo por franjas de filas entre varios hilos (numpy suelta el GIL): un
    1080p en coma flotante pasa de ~34 ms a ~11 ms y cabe en lo que dura un fotograma."""
    step = max(64, -(-height // _bands_pool._max_workers))
    list(_bands_pool.map(lambda y: work(slice(y, y + step)), range(0, height, step)))


def develop(image, exposure=0.0, gamma=1.0, channel="rgb"):
    """Lo que se pinta: el fotograma con la exposición y la gamma de la vista, y solo el canal
    elegido (en gris). Una imagen de 8 bits sin tocar nada se devuelve tal cual."""
    hdr = is_hdr(image)
    if not hdr and exposure == 0 and gamma == 1 and channel == "rgb":
        return image
    has_alpha = image.hasAlphaChannel() and channel == "rgb"
    if not hdr:
        image = image.convertToFormat(QImage.Format_RGBA8888)
    dtype = np.uint16 if hdr else np.uint8
    w, h, size = image.width(), image.height(), np.dtype(dtype).itemsize
    src = np.frombuffer(image.constBits(), dtype, count=h * image.bytesPerLine() // size)
    src = src.reshape(h, image.bytesPerLine() // size)[:, :w * 4].reshape(h, w, 4)
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
        self._developed = None      # (clave, imagen) del último fotograma revelado
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
        key = (self.image.cacheKey(), self.exposure, self.gamma, self.channel)
        if self._developed is None or self._developed[0] != key:
            self._developed = (key, develop(self.image, self.exposure, self.gamma, self.channel))
        return self._developed[1]

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
                           "La franja verde es lo que ya está en memoria."))

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
        cached = sorted(self.cache.images)
        start = prev = None
        for i in cached + [None]:
            if start is not None and (i is None or i != prev + 1):
                p.fillRect(QRectF(start * step, h - 5, (prev - start + 1) * step, 5), QColor("#3fa34d"))
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
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)

        self.btn_play = tool_button("play", tr("Reproducir / pausa (Espacio)"), self.toggle_play)
        self.btn_loop = tool_button("loop", tr("Repetir en bucle (L)"), self._set_loop, True)
        self.btn_loop.setChecked(True)
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
                  self.btn_refresh):
            controls.addWidget(b)
        controls.addSpacing(8)
        controls.addWidget(self.frame_label)
        controls.addSpacing(12)
        controls.addWidget(QLabel("FPS"))
        controls.addWidget(self.fps_box)
        controls.addSpacing(12)
        controls.addWidget(QLabel(tr("Exp.")))
        controls.addWidget(self.exposure_box)
        controls.addSpacing(8)
        controls.addWidget(QLabel("γ"))
        controls.addWidget(self.gamma_box)
        controls.addSpacing(8)
        controls.addWidget(self.channel_box)
        controls.addSpacing(12)
        controls.addWidget(self.zoom_label)
        controls.addStretch(1)
        controls.addWidget(self.load_bar)
        controls.addSpacing(8)
        controls.addWidget(self.info_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.viewer, 1)
        layout.addWidget(self.timeline)
        layout.addLayout(controls)

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
            self.viewer.set_image(image)
            if not self._sized:
                self._sized = True
                self._fit_window(image)
        elif self.current in self.cache.errors:
            self.viewer.set_image(None, tr("No se pudo leer {name}\n{error}", name=self.paths[self.current].name,
                                           error=self.cache.errors[self.current]))
        # si aún no está, se queda el anterior hasta que llegue (_on_frame_ready)
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
        elif text == "e":
            shift = event.modifiers() & Qt.ShiftModifier
            self.nudge_view(self.exposure_box, -EXPOSURE_STEP if shift else EXPOSURE_STEP)
        elif text == "y":
            shift = event.modifiers() & Qt.ShiftModifier
            self.nudge_view(self.gamma_box, -GAMMA_STEP if shift else GAMMA_STEP)
        elif text == "0":
            self.reset_view()
        elif text == "a":
            self.viewer.checker = not self.viewer.checker
            self.viewer.update()
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
        self.timer.stop()
        self.cache.pool.clear()
        super().closeEvent(event)

"""Visor de secuencias de imágenes dentro de Konvi (seqview, de dolphin-video-tools).

Con las secuencias de la lista se abre con sus fotogramas exactos; con una carpeta de
imágenes recién sacadas, la secuencia más larga que haya dentro. Lee los fotogramas en
varios hilos y los guarda en RAM (hasta la mitad de la memoria libre) para reproducir a
tiempo real. Lo que Qt no sabe leer (EXR sin kimageformats, DPX…) lo lee ffmpeg.
"""

import os
import re
import subprocess
from pathlib import Path

from PySide6.QtCore import (QElapsedTimer, QObject, QPointF, QRectF, QRunnable, Qt,
                            QThreadPool, QTimer, Signal)
from PySide6.QtGui import (QBrush, QColor, QColorSpace, QFont, QIcon, QImage, QImageReader,
                           QPainter, QPixmap)
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QProgressBar,
                               QToolButton, QVBoxLayout, QWidget)

from ..core import command
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
    """Lo que Qt no lee, por ffmpeg (que Konvi ya necesita); los EXR, pasados a sRGB."""
    vf = command.LINEAR_TO_SRGB.format(pix="gbrapf32le") if path.suffix.lower() == ".exr" else "null"
    try:
        out = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-frames:v", "1",
                              "-vf", vf, "-f", "image2pipe", "-c:v", "png", "-"],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return QImage(), str(e)
    image = QImage()
    image.loadFromData(out.stdout, "PNG")
    lines = out.stderr.decode("utf-8", "replace").strip().splitlines()
    return image, "" if not image.isNull() else (lines[-1] if lines else tr("No es una imagen que ffmpeg sepa leer."))


def load_image(path):
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        image, error = _ffmpeg_image(path)
        if image.isNull():
            return image, error
    # EXR/HDR vienen en lineal: a sRGB para que se vean como en cualquier visor
    cs = image.colorSpace()
    if cs.isValid() and cs != QColorSpace(QColorSpace.SRgb):
        image.convertToColorSpace(QColorSpace(QColorSpace.SRgb))
    fmt = QImage.Format_ARGB32_Premultiplied if image.hasAlphaChannel() else QImage.Format_RGB32
    if image.format() != fmt:
        image = image.convertToFormat(fmt)
    return image, ""


class _Bridge(QObject):
    loaded = Signal(int, int, QImage, str)     # generación, índice, imagen, error


class _LoadTask(QRunnable):
    def __init__(self, bridge, generation, index, path):
        super().__init__()
        self.bridge, self.generation, self.index, self.path = bridge, generation, index, path

    def run(self):
        image, error = load_image(self.path)
        self.bridge.loaded.emit(self.generation, self.index, image, error)


class _FinderBridge(QObject):
    found = Signal(int, object)                # turno, lista de rutas


class _FindTask(QRunnable):
    """Buscar la secuencia recorre la carpeta: en un NAS con miles de ficheros tarda, así que
    va en un hilo y la ventana se abre antes."""

    def __init__(self, bridge, token, args):
        super().__init__()
        self.bridge, self.token, self.args = bridge, token, args

    def run(self):
        try:
            paths = collect(self.args)
        except OSError:
            paths = []
        self.bridge.found.emit(self.token, paths)


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
        self.errors.clear()
        self.inflight.clear()
        self.frame_bytes = 0
        self._wanted = set()

    def remap(self, paths):
        """Cambia la lista de fotogramas conservando en memoria los que siguen en ella, para no
        volver a leerlos al refrescar. Los que fallaron se reintentan: en un render en marcha
        suelen ser fotogramas que aún se estaban escribiendo."""
        kept = {p: self.images[i] for i, p in enumerate(self.paths) if i in self.images}
        self.generation += 1
        self.pool.clear()
        self.paths = list(paths)
        self.images = {i: kept[p] for i, p in enumerate(self.paths) if p in kept}
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

    def _on_loaded(self, generation, index, image, error):
        if generation != self.generation:
            return
        self.inflight.discard(index)
        if error:
            self.errors[index] = error
        else:
            self.frame_bytes = max(self.frame_bytes, image.sizeInBytes())
            if index in self._wanted or index == self._current:
                self.images[index] = image
        self.frame_ready.emit(index)
        self.update(self._current, self._loop)
        self.changed.emit()

    def wait(self):
        self.pool.waitForDone()


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
        self._drag = None               # encuadre (botón central)
        self._scrub_x = None            # recorrer la secuencia (botón izquierdo)
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

    def _paint(self, p):
        p.fillRect(self.rect(), QColor("#1b1b1b"))
        if self.image is not None:
            rect = self.image_rect()
            if self.checker and self.image.hasAlphaChannel():
                p.fillRect(rect, self._checker)
            p.setRenderHint(QPainter.SmoothPixmapTransform, not self.fast)
            p.drawImage(rect, self.image)
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
            self.setCursor(Qt.SizeHorCursor)
            self.scrub_started.emit()
        elif event.button() == Qt.MiddleButton:
            self._drag = event.position() - self.pan
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._scrub_x is not None:
            self.scrub_moved.emit(event.position().x() - self._scrub_x)
        elif self._drag is not None:
            self.pan = event.position() - self._drag
            self.update()

    def mouseReleaseEvent(self, event):
        if self._scrub_x is not None:
            self.scrub_finished.emit()
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
        self.btn_refresh = tool_button("refresh", tr("Buscar fotogramas nuevos (Ctrl+R, F5)\n"
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

    def _on_found(self, token, paths):
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
        QThreadPool.globalInstance().start(_FindTask(self._refresher, self._refresh_token, args))

    def _on_refreshed(self, token, paths):
        if token != self._refresh_token:
            return                      # entretanto se abrió otra cosa
        self.btn_refresh.setEnabled(True)
        if not paths:
            self._show_notice(tr("No se encuentra la secuencia"))
            return
        if paths == self.paths and not self.cache.errors:
            self._show_notice(tr("No hay fotogramas nuevos"))
            return
        added = len(set(paths) - set(self.paths))
        current = self.paths[self.current]
        self.paths = list(paths)
        self.cache.remap(self.paths)
        self.timeline.count = len(self.paths)
        self.setWindowTitle(f"{describe(self.paths)} — {APP}")
        self.seek(self.paths.index(current) if current in self.paths
                  else min(self.current, len(self.paths) - 1))
        self._show_notice(tr("1 fotograma nuevo") if added == 1 else
                          tr("{n} fotogramas nuevos", n=added) if added else
                          tr("Secuencia actualizada"))

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

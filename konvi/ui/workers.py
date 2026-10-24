"""Trabajo en segundo plano: leer ficheros, sacar miniaturas y comprobar la tarjeta gráfica.

Los hilos solo emiten señales; nunca tocan widgets.
"""

import os
import subprocess

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage

from ..core import command, encoders, probe
from ..core.sequence import Sequence
from ..i18n import tr


class _Bridge(QObject):
    probed = Signal(int, object, str)        # id del trabajo, MediaInfo o None, error
    thumbnail = Signal(int, QImage)
    hardware = Signal(object)                # command.Hardware


class _Task(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        self.fn()


def _grab_frame(path, seconds, height=108, pre=""):
    vf = ",".join(filter(None, [pre, f"scale=-2:{height}"]))
    try:
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{seconds:.2f}", "-i", str(path),
             "-frames:v", "1", "-vf", vf, "-f", "image2pipe", "-c:v", "png", "-"],
            capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return QImage()
    image = QImage()
    image.loadFromData(out.stdout, "PNG")
    return image


class Workers(QObject):
    probed = Signal(int, object, str)
    thumbnail = Signal(int, QImage)
    hardware_ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max(2, min(4, (os.cpu_count() or 4) // 2)))
        self._bridge = _Bridge()
        self._bridge.probed.connect(self.probed, Qt.QueuedConnection)
        self._bridge.thumbnail.connect(self.thumbnail, Qt.QueuedConnection)
        self._bridge.hardware.connect(self.hardware_ready, Qt.QueuedConnection)

    def probe(self, job_id, path, fps):
        """`fps` solo cuenta para las secuencias de imágenes, que no lo traen."""
        def work():
            try:
                if isinstance(path, Sequence):
                    info, error = probe.probe_sequence(path, fps), ""
                else:
                    info, error = probe.probe(path), ""
            except probe.ProbeError as e:
                info, error = None, str(e)
            except Exception as e:      # un fichero raro no puede tumbar la app
                info, error = None, tr("No se pudo leer: {error}", error=e)
            self._bridge.probed.emit(job_id, info, error)
        self.pool.start(_Task(work))

    def thumbnail_for(self, job_id, info):
        if info.video is None:
            return
        if info.preview:            # secuencia: un fotograma de verdad, no el patrón
            pre = command.linear_to_srgb(info.video) if info.video.codec in command.LINEAR_CODECS else ""
            def work():
                image = _grab_frame(info.preview, 0, pre=pre)
                if not image.isNull():
                    self._bridge.thumbnail.emit(job_id, image)
            self.pool.start(_Task(work))
            return
        def work():
            image = _grab_frame(info.path, info.duration * 0.1 if info.duration > 1 else 0)
            if image.isNull() and info.duration > 1:
                image = _grab_frame(info.path, 0)
            if not image.isNull():
                self._bridge.thumbnail.emit(job_id, image)
        self.pool.start(_Task(work))

    def detect_hardware(self, cache_file):
        self.pool.start(_Task(lambda: self._bridge.hardware.emit(encoders.detect_cached(cache_file))))

    def wait(self):
        self.pool.waitForDone()

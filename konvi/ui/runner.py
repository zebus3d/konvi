"""Ejecuta la cola: un ffmpeg detrás de otro, con progreso, pausa y cancelar."""

import os
import shutil
import signal
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from ..core import command, naming, probe
from ..core.jobs import Status
from ..core.progress import Progress
from ..i18n import tr


def _error_text(stderr, code):
    """Las últimas líneas útiles de ffmpeg, para el usuario."""
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    useful = [ln for ln in lines if "Conversion failed" not in ln]
    return "\n".join(useful[-3:]) or tr("ffmpeg terminó con el código {code}.", code=code)


def _discard(path):
    """Borra el resultado a medias: un fichero o la carpeta de una secuencia de imágenes."""
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _size(path):
    if path.is_dir():
        return sum(f.stat().st_size for f in path.iterdir() if f.is_file())
    return path.stat().st_size


def output_extension(preset):
    """Las secuencias de imágenes se guardan en una carpeta, sin extensión."""
    return "" if preset.is_image_sequence else preset.container


def hardware_fallback_message():
    return tr("La tarjeta gráfica falló; se ha convertido sin ella.")


class Runner(QObject):
    changed = Signal(int)                 # id del trabajo que cambió (progreso o estado)
    batch_finished = Signal(int, int, bool)   # hechos, fallidos, detenido por el usuario

    def __init__(self, parent=None):
        super().__init__(parent)
        self.jobs_provider = lambda: []
        self.hardware = command.NO_HARDWARE
        self.use_hardware = False
        self.output_folder = None
        self.proc = None
        self.job = None
        self.paused = False
        self.logs = {}                    # id -> salida de error completa de ffmpeg
        self._stopping = False
        self._cancel = False
        self._tmp = None
        self._links = None                # enlaces sin huecos de una secuencia (ver _input)
        self._progress = None
        self._stderr = ""
        self._done = self._failed = 0
        self._inhibit = None

    @property
    def running(self):
        return self.job is not None

    # ---- cola

    def start(self, jobs_provider):
        if self.running:
            return
        self.jobs_provider = jobs_provider
        self._stopping = False
        self._done = self._failed = 0
        self._inhibit_sleep(True)
        self._next()

    def _next(self):
        job = None if self._stopping else next(
            (j for j in self.jobs_provider() if j.status == Status.READY), None)
        if job is None:
            self._inhibit_sleep(False)
            self.batch_finished.emit(self._done, self._failed, self._stopping)
            return
        self._run(job, self.use_hardware)

    def _run(self, job, use_hw):
        taken = [j.output for j in self.jobs_provider() if j.output and j is not job]
        final = naming.output_path(job.source, output_extension(job.preset), self.output_folder, taken)
        tmp = naming.temp_path(final)
        target = tmp
        try:
            if job.preset.is_image_sequence:
                _discard(tmp)                 # de una vez anterior que se cortó
                tmp.mkdir(parents=True)
                target = naming.frame_pattern(tmp, final, job.preset.container,
                                              command.frame_digits(job.info, job.preset))
            args = command.build(self._input(job), job.preset, target, self.hardware, use_hw)
        except (command.ConvertError, OSError) as e:
            self._drop_links()
            job.status, job.message = Status.FAILED, str(e)
            self._failed += 1
            self.changed.emit(job.id)
            self._next()
            return
        job.output, job.used_hardware = final, command.uses_hardware(args)
        job.status, job.fraction, job.eta, job.speed = Status.RUNNING, 0.0, None, 0.0
        if job.message != hardware_fallback_message():
            job.message = ""
        self.job, self._tmp, self._stderr, self._cancel = job, tmp, "", False
        self._progress = Progress(job.info.duration)
        self.paused = False

        proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("SVT_LOG", "1")            # SVT-AV1: solo errores, no su informe de arranque
        proc.setProcessEnvironment(env)
        proc.setProgram(args[0])
        proc.setArguments(args[1:])
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self.proc = proc
        proc.start()
        self.changed.emit(job.id)

    def _input(self, job):
        """Lo que lee ffmpeg. Una secuencia con huecos se lee de enlaces numerados seguidos
        (el %04d de ffmpeg se pararía en el primer hueco); se borran al terminar."""
        if not (job.is_sequence and job.source.missing()):
            return job.info
        self._links = Path(tempfile.mkdtemp(prefix="konvi-frames-"))
        return probe.linked_info(job.info, job.source, self._links)

    def _drop_links(self):
        if self._links:
            shutil.rmtree(self._links, ignore_errors=True)
            self._links = None

    def _on_stdout(self):
        if not self.proc:
            return
        text = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        if self._progress.feed(text):
            self.job.fraction = self._progress.fraction
            self.job.eta = self._progress.eta
            self.job.speed = self._progress.speed
            self.changed.emit(self.job.id)

    def _on_stderr(self):
        if self.proc:
            text = bytes(self.proc.readAllStandardError()).decode("utf-8", "replace")
            self._stderr = (self._stderr + text)[-20000:]

    def _on_error(self, error):
        if error == QProcess.FailedToStart and self.job:
            self._stderr = tr("No se pudo ejecutar ffmpeg. ¿Está instalado?")
            self._on_finished(-1, QProcess.CrashExit)

    def _on_finished(self, code, exit_status):
        job, proc, tmp = self.job, self.proc, self._tmp
        if job is None:
            return
        self.job = self.proc = None
        self.paused = False
        self._drop_links()
        if proc:
            proc.deleteLater()
        self.logs[job.id] = self._stderr

        if self._cancel:
            _discard(tmp)
            job.status, job.output, job.fraction = Status.CANCELLED, None, 0.0
        elif code == 0 and exit_status == QProcess.NormalExit and tmp.exists() and \
                not (tmp.is_dir() and not any(tmp.iterdir())):
            final = job.output
            if final.exists():          # alguien creó ese nombre mientras tanto
                final = naming.output_path(job.source, output_extension(job.preset), final.parent)
            tmp.replace(final)
            job.output, job.output_size = final, _size(final)
            job.status, job.fraction, job.eta = Status.DONE, 1.0, None
            self._done += 1
        else:
            _discard(tmp)
            if job.used_hardware and not self._stopping:
                # Un driver que falla no debe dejar al usuario sin su vídeo
                job.message = hardware_fallback_message()
                self._run(job, use_hw=False)
                return
            job.status, job.output = Status.FAILED, None
            job.message = _error_text(self._stderr, code)
            self._failed += 1
        self.changed.emit(job.id)
        self._next()

    # ---- control

    def pause(self):
        if self.proc and not self.paused and self.proc.processId() > 0:
            os.kill(self.proc.processId(), signal.SIGSTOP)
            self.paused = True
            self.job.status = Status.PAUSED
            self.changed.emit(self.job.id)

    def resume(self):
        if self.proc and self.paused and self.proc.processId() > 0:
            os.kill(self.proc.processId(), signal.SIGCONT)
            self.paused = False
            self.job.status = Status.RUNNING
            self.changed.emit(self.job.id)

    def cancel_current(self):
        if self.proc:
            self._cancel = True
            if self.paused and self.proc.processId() > 0:
                os.kill(self.proc.processId(), signal.SIGCONT)
            self.proc.kill()

    def stop(self):
        """Detiene la cola: cancela el que está en marcha y no empieza más."""
        self._stopping = True
        self.cancel_current()

    def wait(self, ms=5000):
        if self.proc:
            self.proc.waitForFinished(ms)

    # ---- que el equipo no se suspenda a mitad

    def _inhibit_sleep(self, on):
        if on and self._inhibit is None and shutil.which("systemd-inhibit"):
            self._inhibit = QProcess(self)
            self._inhibit.start("systemd-inhibit", [
                "--what=sleep:idle", "--who=Konvi", "--why=" + tr("Convirtiendo vídeos"), "--mode=block",
                "sleep", "infinity"])
        elif not on and self._inhibit is not None:
            self._inhibit.kill()
            self._inhibit.waitForFinished(1000)
            self._inhibit = None


def open_path(path):
    """Abre un fichero o carpeta con la aplicación del sistema."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def reveal(path):
    """Abre la carpeta con el fichero seleccionado (Dolphin), o solo la carpeta."""
    path = Path(path)
    if shutil.which("dolphin"):
        QProcess.startDetached("dolphin", ["--select", str(path)])
    else:
        open_path(path.parent)


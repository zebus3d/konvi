"""Ejecuta la cola: un ffmpeg detrás de otro, con progreso, pausa y cancelar."""

import os
import shutil
import signal
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from ..core import command, naming
from ..core.jobs import Status
from ..core.progress import Progress


def _error_text(stderr, code):
    """Las últimas líneas útiles de ffmpeg, para el usuario."""
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    useful = [ln for ln in lines if "Conversion failed" not in ln]
    return "\n".join(useful[-3:]) or f"ffmpeg terminó con el código {code}."


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
        final = naming.output_path(job.source, job.preset.container, self.output_folder, taken)
        tmp = naming.temp_path(final)
        try:
            args = command.build(job.info, job.preset, tmp, self.hardware, use_hw)
        except command.ConvertError as e:
            job.status, job.message = Status.FAILED, str(e)
            self._failed += 1
            self.changed.emit(job.id)
            self._next()
            return
        job.output, job.used_hardware = final, command.uses_hardware(args)
        job.status, job.fraction, job.eta, job.speed = Status.RUNNING, 0.0, None, 0.0
        if not job.message.startswith("La tarjeta gráfica"):
            job.message = ""
        self.job, self._tmp, self._stderr, self._cancel = job, tmp, "", False
        self._progress = Progress(job.info.duration)
        self.paused = False

        proc = QProcess(self)
        proc.setProgram(args[0])
        proc.setArguments(args[1:])
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self.proc = proc
        proc.start()
        self.changed.emit(job.id)

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
            self._stderr = "No se pudo ejecutar ffmpeg. ¿Está instalado?"
            self._on_finished(-1, QProcess.CrashExit)

    def _on_finished(self, code, exit_status):
        job, proc, tmp = self.job, self.proc, self._tmp
        if job is None:
            return
        self.job = self.proc = None
        self.paused = False
        if proc:
            proc.deleteLater()
        self.logs[job.id] = self._stderr

        if self._cancel:
            tmp.unlink(missing_ok=True)
            job.status, job.output, job.fraction = Status.CANCELLED, None, 0.0
        elif code == 0 and exit_status == QProcess.NormalExit and tmp.exists():
            final = job.output
            if final.exists():          # alguien creó ese nombre mientras tanto
                final = naming.output_path(job.source, job.preset.container, final.parent)
            tmp.replace(final)
            job.output, job.output_size = final, final.stat().st_size
            job.status, job.fraction, job.eta = Status.DONE, 1.0, None
            self._done += 1
        else:
            tmp.unlink(missing_ok=True)
            if job.used_hardware and not self._stopping:
                # Un driver que falla no debe dejar al usuario sin su vídeo
                job.message = "La tarjeta gráfica falló; se ha convertido sin ella."
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
                "--what=sleep:idle", "--who=Konvi", "--why=Convirtiendo vídeos", "--mode=block",
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

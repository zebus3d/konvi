"""Un trabajo de la cola: un fichero, su perfil y en qué estado está. Sin Qt."""

from dataclasses import dataclass, field
from enum import Enum
from itertools import count
from pathlib import Path

from . import command
from .naming import TMP_TAG


class Status(Enum):
    PROBING = "probing"         # leyendo el fichero
    READY = "ready"             # listo para convertir
    INVALID = "invalid"         # no se puede (no se lee, o no encaja con el perfil)
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE = {Status.RUNNING, Status.PAUSED}
FINISHED = {Status.DONE, Status.FAILED, Status.CANCELLED}
# a los que se les puede cambiar el perfil (no a los que están en marcha ni a los hechos)
EDITABLE = {Status.PROBING, Status.READY, Status.INVALID, Status.FAILED, Status.CANCELLED}

MEDIA_EXTENSIONS = {
    "mp4", "m4v", "mkv", "mov", "avi", "wmv", "flv", "webm", "mpg", "mpeg", "ts", "mts", "m2ts",
    "3gp", "vob", "ogv", "mxf", "dv", "divx", "asf", "rm", "rmvb", "y4m",
    "mp3", "wav", "flac", "aac", "m4a", "ogg", "opus", "wma", "aif", "aiff", "ac3", "mka",
}

_ids = count(1)


@dataclass
class Job:
    source: Path
    preset: object
    id: int = field(default_factory=lambda: next(_ids))
    info: object = None
    status: Status = Status.PROBING
    message: str = ""           # motivo de INVALID/FAILED, o aviso
    fraction: float = 0.0
    eta: float | None = None
    speed: float = 0.0
    output: Path | None = None
    output_size: int = 0
    used_hardware: bool = False

    def set_probed(self, info, error=""):
        if error:
            self.info, self.status, self.message = None, Status.INVALID, error
        else:
            self.info = info
            self.revalidate()

    def set_preset(self, preset):
        self.preset = preset
        if self.status in EDITABLE and self.status != Status.PROBING:
            self.revalidate()

    def revalidate(self):
        problem = command.check(self.info, self.preset) if self.info else None
        self.status = Status.INVALID if problem else Status.READY
        self.message = problem or ""
        self.fraction, self.eta, self.speed = 0.0, None, 0.0

    def reset(self):
        """'Volver a convertir'."""
        if self.status in FINISHED and self.info:
            self.output, self.output_size = None, 0
            self.revalidate()


def expand_paths(paths):
    """Ficheros tal cual; de las carpetas, los vídeos y audios que haya dentro (recursivo)."""
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(f for f in p.rglob("*")
                          if f.is_file() and f.suffix[1:].lower() in MEDIA_EXTENSIONS
                          and TMP_TAG not in f.name)
        elif p.is_file():
            out.append(p)
    return out

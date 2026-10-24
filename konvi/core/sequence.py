"""Secuencias de imágenes (render_0001.png, render_0002.png…): encontrarlas y leerlas como
un vídeo. Sin Qt.

Una secuencia son los ficheros de una carpeta que solo se diferencian en el número de antes
de la extensión. El relleno (0001 o 1) se deduce del número más corto: con «####», el
fotograma 10000 se escribe 10000 y sigue siendo de la misma secuencia.
"""

import re
from dataclasses import dataclass
from pathlib import Path

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "exr", "tif", "tiff", "bmp", "tga", "webp", "dpx"}
SEQUENCE_FPS = (12.0, 15.0, 23.976, 24.0, 25.0, 29.97, 30.0, 48.0, 50.0, 59.94, 60.0)
DEFAULT_FPS = 24.0          # el de Blender
NTSC = {23.976: "24000/1001", 29.97: "30000/1001", 59.94: "60000/1001"}

_FRAME = re.compile(r"^(.*?)(\d+)(\.[^.]+)$")


def is_image(path):
    return Path(path).suffix[1:].lower() in IMAGE_EXTENSIONS


@dataclass(frozen=True)
class Sequence:
    folder: Path
    prefix: str             # lo de antes del número: "render_"
    suffix: str             # la extensión con el punto: ".png"
    padding: int            # cifras del número: 4 en 0001; 1 si no lleva ceros; 0 si no hay
    frames: tuple           # números de fotograma, ordenados

    @property
    def first(self):
        return self.frames[0]

    @property
    def last(self):
        return self.frames[-1]

    @property
    def count(self):
        return len(self.frames)

    @property
    def name(self):
        """'render_####.png', como lo escribe Blender."""
        return f"{self.prefix}{'#' * self.padding}{self.suffix}"

    @property
    def parent(self):
        return self.folder

    @property
    def stem(self):
        """Nombre para lo que salga de ella: el prefijo sin separadores, o el de la carpeta."""
        return self.prefix.rstrip(" ._-") or self.folder.name or "secuencia"

    def __str__(self):
        return str(self.folder / self.name)

    def frame_path(self, n):
        if not self.padding:        # una imagen sin número
            return self.folder / f"{self.prefix}{self.suffix}"
        return self.folder / f"{self.prefix}{str(n).zfill(self.padding)}{self.suffix}"

    def missing(self):
        have = set(self.frames)
        return [n for n in range(self.first, self.last + 1) if n not in have]

    def input_args(self, fps, links=None):
        """Opciones de entrada de ffmpeg y la ruta que va tras -i.

        El patrón %04d de ffmpeg se para en el primer hueco. Con huecos, la secuencia se
        lee de `links`, una carpeta de enlaces numerados sin huecos (link_frames): el vídeo
        se salta los que faltan (y se avisa), con cualquier numeración."""
        rate = ["-f", "image2", "-framerate", fps_arg(fps)]
        if links is not None:
            return rate + ["-start_number", "1"], \
                f"{links}/".replace("%", "%%") + "%06d" + self.suffix.replace("%", "%%")
        digits = f"%0{self.padding}d" if self.padding > 1 else "%d"
        pattern = f"{self.folder}/{self.prefix}".replace("%", "%%") + digits + \
            self.suffix.replace("%", "%%")       # con prefijo vacío, `folder / ""` perdería la barra
        return rate + ["-start_number", str(self.first)], pattern

    def link_frames(self, folder):
        """Enlaces 000001.png, 000002.png… a los fotogramas, en orden y sin huecos."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        for i, n in enumerate(self.frames, 1):
            (folder / f"{i:06d}{self.suffix}").symlink_to(self.frame_path(n).resolve())
        return folder


def fps_arg(fps):
    return NTSC.get(round(fps, 3), f"{fps:g}")


def _split(path):
    m = _FRAME.match(path.name)
    return (m.group(1), m.group(2), m.group(3)) if m else None


def find(files, min_frames=2):
    """Agrupa imágenes en secuencias. Las que no forman una de `min_frames` se descartan."""
    groups, out = {}, []
    for f in map(Path, files):
        parts = _split(f) if is_image(f) else None
        if not parts and min_frames <= 1 and is_image(f):
            out.append(Sequence(f.parent, f.stem, f.suffix, 0, (0,)))
        if parts:
            prefix, digits, suffix = parts
            groups.setdefault((f.parent, prefix, suffix), []).append(digits)
    for (folder, prefix, suffix), numbers in groups.items():
        while numbers:
            padding = min(map(len, numbers))
            fits = [d for d in numbers if d == str(int(d)).zfill(padding)]
            numbers = [d for d in numbers if d not in fits]
            if not fits:            # «01» y «001» a la vez: cada uno es otra secuencia
                fits, numbers = [d for d in numbers if len(d) == padding], \
                    [d for d in numbers if len(d) != padding]
            frames = tuple(sorted({int(d) for d in fits}))
            if len(frames) >= min_frames:
                out.append(Sequence(folder, prefix, suffix, padding, frames))
    return sorted(out, key=str)


def from_selection(images):
    """Imágenes sueltas que el usuario ha arrastrado o elegido.

    Si de una secuencia solo viene un fotograma, se coge la secuencia entera de la carpeta
    (como DaVinci); si vienen varios, solo esos. Una imagen sin secuencia queda sola, para
    que la lista explique por qué no se puede convertir."""
    out = []
    for seq in find(images, min_frames=1):
        if seq.count == 1 and seq.padding:
            siblings = (f for f in seq.folder.iterdir() if f.name.startswith(seq.prefix)
                        and f.name.endswith(seq.suffix))
            whole = next((s for s in find(siblings, min_frames=1)
                          if s.prefix == seq.prefix and s.padding == seq.padding
                          and seq.first in s.frames), None)
            seq = whole or seq
        out.append(seq)
    return out


def describe_missing(numbers, limit=4):
    """[3, 4, 5, 9] -> '3-5, 9'."""
    ranges = []
    for n in numbers:
        if ranges and n == ranges[-1][1] + 1:
            ranges[-1][1] = n
        else:
            ranges.append([n, n])
    text = [f"{a}" if a == b else f"{a}-{b}" for a, b in ranges[:limit]]
    return ", ".join(text) + ("…" if len(ranges) > limit else "")

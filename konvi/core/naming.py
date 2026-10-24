"""Dónde se guarda cada resultado. Sin Qt."""

from pathlib import Path

from .sequence import Sequence

TMP_TAG = ".konvi-tmp"


def output_path(source, extension, folder=None, taken=()):
    """`folder/nombre.ext` (por defecto junto al original) sin pisar nada: ni ficheros que ya
    existen, ni el propio original, ni las salidas de otros trabajos de la cola (`taken`).

    Sin extensión es una carpeta (para guardar una secuencia de imágenes)."""
    if not isinstance(source, Sequence):
        source = Path(source)
    folder = Path(folder) if folder else source.parent
    taken = {str(Path(t)) for t in taken}
    stem = source.stem
    ext = f".{extension}" if extension else ""
    candidate = folder / f"{stem}{ext}"
    n = 2
    while candidate.exists() or str(candidate) in taken or candidate == source:
        candidate = folder / f"{stem} ({n}){ext}"
        n += 1
    return candidate


def temp_path(final):
    """Se convierte a un nombre temporal y se renombra al terminar: así nunca queda un
    fichero a medias con aspecto de terminado. Conserva la extensión (ffmpeg la usa)."""
    final = Path(final)
    return final.with_name(f"{final.stem}{TMP_TAG}{final.suffix}")


def frame_pattern(folder, final, extension, digits):
    """Patrón de ffmpeg para los fotogramas: `carpeta/nombre_%04d.png`. Los ficheros llevan
    el nombre definitivo aunque se escriban en la carpeta temporal. Un % de la ruta se
    escribe %% (si no, ffmpeg lo toma por parte del patrón)."""
    return str(Path(folder) / Path(final).name).replace("%", "%%") + f"_%0{digits}d.{extension}"

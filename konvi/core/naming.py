"""Dónde se guarda cada resultado. Sin Qt."""

from pathlib import Path

TMP_TAG = ".konvi-tmp"


def output_path(source, extension, folder=None, taken=()):
    """`folder/nombre.ext` (por defecto junto al original) sin pisar nada: ni ficheros que ya
    existen, ni el propio original, ni las salidas de otros trabajos de la cola (`taken`)."""
    source = Path(source)
    folder = Path(folder) if folder else source.parent
    taken = {str(Path(t)) for t in taken}
    stem = source.stem
    candidate = folder / f"{stem}.{extension}"
    n = 2
    while candidate.exists() or str(candidate) in taken or candidate == source:
        candidate = folder / f"{stem} ({n}).{extension}"
        n += 1
    return candidate


def temp_path(final):
    """Se convierte a un nombre temporal y se renombra al terminar: así nunca queda un
    fichero a medias con aspecto de terminado. Conserva la extensión (ffmpeg la usa)."""
    final = Path(final)
    return final.with_name(f"{final.stem}{TMP_TAG}{final.suffix}")

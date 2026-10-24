"""Idioma de la interfaz: español si el sistema está en español; si no, inglés. Sin Qt.

El texto de origen, en el código, está en español. `tr()` devuelve la traducción inglesa
(de i18n_en.py) cuando toca. Los textos con datos llevan huecos con nombre:
    tr("Perfil para los {n} vídeos:", n=3)
"""

import os

from .i18n_en import EN

_language = "es"


def detect(env=None):
    """'es' o 'en' según las variables de idioma del sistema (las que fija Plasma)."""
    env = os.environ if env is None else env
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = env.get(var, "")
        if value:
            first = value.split(":")[0].lower()
            return "es" if first.startswith("es") else "en"
    return "en"


def set_language(lang):
    global _language
    _language = "es" if lang == "es" else "en"


def language():
    return _language


def tr(text, **values):
    if _language != "es":
        text = EN.get(text, text)
    return text.format(**values) if values else text


def number(value, decimals=1):
    """2,3 en español; 2.3 en inglés."""
    text = f"{value:.{decimals}f}"
    return text.replace(".", ",") if _language == "es" else text
